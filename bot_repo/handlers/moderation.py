import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton

from config import HTML, ADMIN_ID, users_col, app
from helpers import (
    log_event, _parse_duration, _resolve_target,
    _is_admin_msg, _auto_del, _FULL_PERMS, MAX_WARNS,
    BOT_TOKEN, _clone_config_ctx,
)

# ── In-process message ID cache ───────────────────────────────────────────────
# Bots cannot use MTProto search_messages(from_user=…) or get_chat_history in
# supergroups (BOT_METHOD_INVALID / empty result from Telegram).
# Solution: record every incoming group message here and use it for /del all.
#
# Structure: {chat_id → {user_id → deque([msg_id, …], maxlen=1000)}}
# Shared across main bot and clone bots (same Python process).
# Data is lost on restart — only covers messages received since last start.
_msg_cache: dict[int, dict[int, deque]] = defaultdict(
    lambda: defaultdict(lambda: deque(maxlen=1000))
)

# ── Promotional button সরানো হয়েছে — third-party channel promote করা ToS violation ──
COUPLE_BTN = None

# ── Service-message filter ────────────────────────────────────────────────────
_SVC = (
    filters.new_chat_members
    | filters.left_chat_member
    | filters.new_chat_title
    | filters.new_chat_photo
    | filters.delete_chat_photo
    | filters.pinned_message
)


@app.on_message(_SVC & filters.group, group=20)
async def _delete_service_messages(client: Client, message: Message):
    """Service messages — এগুলো আর auto-delete করা হয় না।
    Telegram ToS অনুযায়ী join/leave notification delete করা নিষেধ
    এবং এটি bot ban এর একটি কারণ হতে পারে।
    """
    pass  # intentionally disabled


@app.on_message(filters.group, group=4)
async def _track_group_msg(client: Client, message: Message):
    """Record each group message's (chat_id, user_id, msg_id) into the cache."""
    if message.chat and message.from_user:
        _msg_cache[message.chat.id][message.from_user.id].append(message.id)


def _get_bot_token(client: Client) -> str:
    cfg = getattr(client, "_clone_config", None) or _clone_config_ctx.get()
    if cfg and cfg.get("token"):
        return cfg["token"]
    return BOT_TOKEN


async def _bot_api(client: Client, method: str, params: dict) -> dict:
    token = _get_bot_token(client)
    url   = f"https://api.telegram.org/bot{token}/{method}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=params) as resp:
            return await resp.json()


@app.on_message(filters.command("mute") & filters.group)
async def mute_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    try:
        user_id, fname, remaining = await _resolve_target(client, message, list(args))
    except ValueError as e:
        m = await message.reply_text(str(e), parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 20))
        return

    if remaining:
        secs, label = _parse_duration(remaining[0])
    else:
        secs, label = None, "permanent"

    # until_date=None crashes Pyrogram (tries to call None.to_bytes()).
    # Omit the argument entirely for permanent mutes — Pyrogram defaults to 0 (forever).
    until_date = (datetime.utcnow() + timedelta(seconds=secs)) if secs else None
    try:
        if until_date:
            await client.restrict_chat_member(
                message.chat.id, user_id,
                ChatPermissions(can_send_messages=False),
                until_date=until_date,
            )
        else:
            await client.restrict_chat_member(
                message.chat.id, user_id,
                ChatPermissions(can_send_messages=False),
            )
    except Exception as e:
        m = await message.reply_text(f"❌ Could not mute: {e}")
        asyncio.create_task(_auto_del(m, 15))
        return

    m = await message.reply_text(
        f"🔇 <b>User Muted</b>\n"
        f"👤 {fname} — <code>{user_id}</code>\n"
        f"⏱ Duration: {label}",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
    asyncio.create_task(log_event(client,
        f"🔇 <b>Muted</b>  👤 {fname} <code>{user_id}</code>  ⏱ {label}"
        f"  📍 {message.chat.title or message.chat.id}"
    ))


@app.on_message(filters.command("unmute") & filters.group)
async def unmute_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    try:
        user_id, fname, _ = await _resolve_target(client, message, list(args))
    except ValueError as e:
        m = await message.reply_text(str(e), parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 20))
        return

    try:
        await client.restrict_chat_member(message.chat.id, user_id, _FULL_PERMS)
    except Exception as e:
        m = await message.reply_text(f"❌ Could not unmute: {e}")
        asyncio.create_task(_auto_del(m, 15))
        return

    m = await message.reply_text(
        f"🔊 <b>User Unmuted</b>\n"
        f"👤 {fname} — <code>{user_id}</code>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
    asyncio.create_task(log_event(client,
        f"🔊 <b>Unmuted</b>  👤 {fname} <code>{user_id}</code>"
        f"  📍 {message.chat.title or message.chat.id}"
    ))


@app.on_message(filters.command("ban") & filters.group)
async def ban_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    reason_start = 1
    try:
        user_id, fname, rest = await _resolve_target(client, message, list(args))
    except ValueError as e:
        m = await message.reply_text(str(e), parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 20))
        return

    reason = " ".join(rest) if rest else "No reason given"
    try:
        await client.ban_chat_member(message.chat.id, user_id)
    except Exception as e:
        m = await message.reply_text(f"❌ Could not ban: {e}")
        asyncio.create_task(_auto_del(m, 15))
        return

    m = await message.reply_text(
        f"🚫 <b>User Banned</b>\n"
        f"👤 {fname} — <code>{user_id}</code>\n"
        f"📋 Reason: {reason}",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
    asyncio.create_task(log_event(client,
        f"🚫 <b>Banned</b>  👤 {fname} <code>{user_id}</code>"
        f"  📋 {reason}  📍 {message.chat.title or message.chat.id}"
    ))


@app.on_message(filters.command("unban") & filters.group)
async def unban_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    try:
        user_id, fname, _ = await _resolve_target(client, message, list(args))
    except ValueError as e:
        m = await message.reply_text(str(e), parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 20))
        return

    try:
        await client.unban_chat_member(message.chat.id, user_id)
    except Exception as e:
        m = await message.reply_text(f"❌ Could not unban: {e}")
        asyncio.create_task(_auto_del(m, 15))
        return

    m = await message.reply_text(
        f"✅ <b>User Unbanned</b>\n"
        f"👤 {fname} — <code>{user_id}</code>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
    asyncio.create_task(log_event(client,
        f"✅ <b>Unbanned</b>  👤 {fname} <code>{user_id}</code>"
        f"  📍 {message.chat.title or message.chat.id}"
    ))


@app.on_message(filters.command("warn") & filters.group)
async def warn_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    try:
        user_id, fname, rest = await _resolve_target(client, message, list(args))
    except ValueError as e:
        m = await message.reply_text(str(e), parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 20))
        return

    reason = " ".join(rest) if rest else "No reason given"
    doc    = await users_col.find_one({"user_id": user_id})
    warns  = (doc or {}).get("warn_count", 0) + 1

    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"warn_count": warns}},
        upsert=True,
    )

    if warns >= MAX_WARNS:
        try:
            await client.ban_chat_member(message.chat.id, user_id)
            auto_ban_note = f"\n\n⛔ Auto-banned after {MAX_WARNS} warnings!"
        except Exception:
            auto_ban_note = "\n\n⚠️ Could not auto-ban — please ban manually."
        await users_col.update_one(
            {"user_id": user_id},
            {"$set": {"warn_count": 0}},
        )
    else:
        auto_ban_note = ""

    m = await message.reply_text(
        f"⚠️ <b>Warning {warns}/{MAX_WARNS}</b>\n"
        f"👤 {fname} — <code>{user_id}</code>\n"
        f"📋 Reason: {reason}"
        f"{auto_ban_note}",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 60))
    asyncio.create_task(log_event(client,
        f"⚠️ <b>Warned</b>  👤 {fname} <code>{user_id}</code>"
        f"  {warns}/{MAX_WARNS}  📋 {reason}"
        f"  📍 {message.chat.title or message.chat.id}"
        f"{auto_ban_note}"
    ))


@app.on_message(filters.command("clearwarn") & filters.group)
async def clearwarn_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    try:
        user_id, fname, _ = await _resolve_target(client, message, list(args))
    except ValueError as e:
        m = await message.reply_text(str(e), parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 20))
        return

    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"warn_count": 0}},
        upsert=True,
    )
    m = await message.reply_text(
        f"🗑️ <b>Warnings Cleared</b>\n"
        f"👤 {fname} — <code>{user_id}</code>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
    asyncio.create_task(log_event(client,
        f"🗑️ <b>Warnings Cleared</b>  👤 {fname} <code>{user_id}</code>"
        f"  📍 {message.chat.title or message.chat.id}"
    ))

# ─── /kick ────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("kick") & filters.group)
async def kick_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    try:
        user_id, fname, rest = await _resolve_target(client, message, list(args))
    except ValueError as e:
        m = await message.reply_text(str(e), parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 20))
        return

    reason = " ".join(rest) if rest else "No reason given"
    try:
        await client.ban_chat_member(message.chat.id, user_id)
        await asyncio.sleep(1)
        await client.unban_chat_member(message.chat.id, user_id)
    except Exception as e:
        m = await message.reply_text(f"❌ Could not kick: {e}")
        asyncio.create_task(_auto_del(m, 15))
        return

    m = await message.reply_text(
        f"👢 <b>User Kicked</b>\n"
        f"👤 {fname} — <code>{user_id}</code>\n"
        f"📋 Reason: {reason}",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
    asyncio.create_task(log_event(client,
        f"👢 <b>Kicked</b>  👤 {fname} <code>{user_id}</code>"
        f"  📋 {reason}  📍 {message.chat.title or message.chat.id}"
    ))


# ─── /del  &  /del all ────────────────────────────────────────────────────────

@app.on_message(filters.command("del") & filters.group)
async def del_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args    = message.command[1:]
    replied = message.reply_to_message

    # ── /del all — delete every message from the replied-to user ──────────────
    if args and args[0].lower() == "all":
        if not replied:
            m = await message.reply_text(
                "❌ Reply to a message from the user whose messages you want to delete.",
            )
            asyncio.create_task(_auto_del(m, 10))
            return

        target_user = replied.from_user
        if not target_user:
            m = await message.reply_text(
                "❌ Cannot identify the user (anonymous or channel message)."
            )
            asyncio.create_task(_auto_del(m, 10))
            return

        uid   = target_user.id
        fname = target_user.first_name or str(uid)

        chat_id    = message.chat.id
        status_msg = await message.reply_text(
            f"🔍 Searching messages from {fname}…"
        )

        # ── Collect message IDs from in-process cache ─────────────────────
        # The _track_group_msg handler (group=4) records every group message
        # into _msg_cache since bot started.  MTProto search_messages/
        # get_chat_history are unavailable for bots in supergroups.
        cached = _msg_cache.get(chat_id, {}).get(uid, deque())
        msg_ids = list(cached)

        # Also include the replied-to message itself (in case it predates cache)
        replied_id = replied.id
        if replied_id not in msg_ids:
            msg_ids.append(replied_id)

        if not msg_ids:
            await status_msg.edit_text(
                f"⚠️ No messages tracked from <b>{fname}</b> in this group.\n"
                f"<i>Bot can only delete messages received since its last restart. "
                f"Messages sent before that cannot be found.</i>",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(status_msg, 25))
        else:
            await status_msg.edit_text(
                f"🗑️ Found <b>{len(msg_ids)}</b> message(s) — deleting…"
            )
            # ── Delete in batches of 100 (Telegram limit per call) ─────────
            deleted = 0
            failed  = 0
            for i in range(0, len(msg_ids), 100):
                batch = msg_ids[i:i + 100]
                try:
                    await client.delete_messages(chat_id, batch)
                    deleted += len(batch)
                except Exception as batch_err:
                    print(f"[DEL_ALL] Batch {i//100+1} failed: {batch_err}")
                    # Try individually
                    for mid in batch:
                        try:
                            await client.delete_messages(chat_id, [mid])
                            deleted += 1
                        except Exception:
                            failed += 1
                await asyncio.sleep(0.3)  # gentle rate-limiting

            result = (
                f"✅ <b>Deleted {deleted} message(s)</b> from "
                f"<b>{fname}</b> (<code>{uid}</code>)"
            )
            if failed:
                result += f"\n⚠️ {failed} message(s) could not be deleted."

            await status_msg.edit_text(result, parse_mode=HTML)
            asyncio.create_task(_auto_del(status_msg, 25))
            asyncio.create_task(log_event(client,
                f"🗑️ <b>Del All</b>  👤 {fname} <code>{uid}</code>"
                f"  🔢 {deleted} msgs  📍 {message.chat.title or message.chat.id}"
            ))

        try:
            await message.delete()
        except Exception:
            pass
        return

    # ── /del — delete only the replied-to message ─────────────────────────────
    if not replied:
        m = await message.reply_text("❌ Reply to the message you want to delete.")
        asyncio.create_task(_auto_del(m, 10))
        return

    try:
        await replied.delete()
    except Exception as e:
        m = await message.reply_text(f"❌ Could not delete: {e}")
        asyncio.create_task(_auto_del(m, 10))
        return

    try:
        await message.delete()
    except Exception:
        pass


# ─── /ro — read-only restriction ──────────────────────────────────────────────

@app.on_message(filters.command("ro") & filters.group)
async def ro_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    try:
        user_id, fname, remaining = await _resolve_target(client, message, list(args))
    except ValueError as e:
        m = await message.reply_text(str(e), parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 20))
        return

    if remaining:
        secs, label = _parse_duration(remaining[0])
    else:
        secs, label = 3600, "1h"

    until_date = (datetime.utcnow() + timedelta(seconds=secs)) if secs else None
    _ro_perms  = ChatPermissions(
        can_send_messages        = False,
        can_send_media_messages  = False,
        can_send_polls           = False,
        can_add_web_page_previews= False,
        can_change_info          = False,
        can_invite_users         = False,
        can_pin_messages         = False,
    )
    try:
        if until_date:
            await client.restrict_chat_member(
                message.chat.id, user_id, _ro_perms, until_date=until_date,
            )
        else:
            await client.restrict_chat_member(
                message.chat.id, user_id, _ro_perms,
            )
    except Exception as e:
        m = await message.reply_text(f"❌ Could not restrict: {e}")
        asyncio.create_task(_auto_del(m, 15))
        return

    m = await message.reply_text(
        f"👁 <b>Read-Only Mode</b>\n"
        f"👤 {fname} — <code>{user_id}</code>\n"
        f"⏱ Duration: {label}\n"
        f"<i>User can read but not send messages.</i>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
    asyncio.create_task(log_event(client,
        f"👁 <b>Read-Only</b>  👤 {fname} <code>{user_id}</code>"
        f"  ⏱ {label}  📍 {message.chat.title or message.chat.id}"
    ))


# ─── /pin ─────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("pin") & filters.group)
async def pin_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    replied = message.reply_to_message
    if not replied:
        m = await message.reply_text("❌ Reply to the message you want to pin.")
        asyncio.create_task(_auto_del(m, 10))
        return

    silent = "silent" in [a.lower() for a in message.command[1:]]
    try:
        await client.pin_chat_message(
            message.chat.id, replied.id,
            disable_notification=silent,
        )
    except Exception as e:
        m = await message.reply_text(f"❌ Could not pin: {e}")
        asyncio.create_task(_auto_del(m, 10))
        return

    m = await message.reply_text(
        f"📌 <b>Message Pinned</b>"
        + (" <i>(silent)</i>" if silent else ""),
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 20))
    try:
        await message.delete()
    except Exception:
        pass


# ─── /unpin ───────────────────────────────────────────────────────────────────

@app.on_message(filters.command("unpin") & filters.group)
async def unpin_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    replied = message.reply_to_message
    try:
        if replied:
            await client.unpin_chat_message(message.chat.id, replied.id)
            note = "📌 Unpinned that message."
        else:
            await client.unpin_all_chat_messages(message.chat.id)
            note = "📌 All pinned messages unpinned."
    except Exception as e:
        m = await message.reply_text(f"❌ Could not unpin: {e}")
        asyncio.create_task(_auto_del(m, 10))
        return

    m = await message.reply_text(note, parse_mode=HTML)
    asyncio.create_task(_auto_del(m, 20))
    try:
        await message.delete()
    except Exception:
        pass


# ─── /warns — check warning count ─────────────────────────────────────────────

@app.on_message(filters.command("warns") & filters.group)
async def warns_cmd(client: Client, message: Message):
    args = message.command[1:]
    replied = message.reply_to_message

    if args or replied:
        if not await _is_admin_msg(client, message):
            return
        try:
            user_id, fname, _ = await _resolve_target(client, message, list(args))
        except ValueError as e:
            m = await message.reply_text(str(e), parse_mode=HTML)
            asyncio.create_task(_auto_del(m, 20))
            return
    else:
        user_id = message.from_user.id
        fname   = message.from_user.first_name or str(user_id)

    doc   = await users_col.find_one({"user_id": user_id})
    warns = (doc or {}).get("warn_count", 0)

    m = await message.reply_text(
        f"⚠️ <b>Warnings</b>\n"
        f"👤 {fname} — <code>{user_id}</code>\n"
        f"📊 {warns}/{MAX_WARNS} warnings",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))


# ─── /report — user reports a message to admin ────────────────────────────────

@app.on_message(filters.command("report") & filters.group)
async def report_cmd(client: Client, message: Message):
    user    = message.from_user
    if not user:
        return

    replied = message.reply_to_message
    if not replied:
        m = await message.reply_text(
            "ℹ️ Reply to the message you want to report with /report.",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 15))
        return

    reporter_name = user.first_name or str(user.id)
    target_user   = replied.from_user
    target_name   = (target_user.first_name or str(target_user.id)) if target_user else "Unknown"
    target_id     = target_user.id if target_user else "?"
    chat_name     = message.chat.title or str(message.chat.id)
    reason_args   = message.command[1:]
    reason        = " ".join(reason_args) if reason_args else "No reason given"

    asyncio.create_task(log_event(client,
        f"🚨 <b>User Report</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Group       : {chat_name}\n"
        f"👤 Reporter    : {reporter_name} <code>{user.id}</code>\n"
        f"🎯 Reported    : {target_name} <code>{target_id}</code>\n"
        f"📋 Reason      : {reason}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    ))

    m = await message.reply_text(
        f"✅ <b>Report Submitted</b>\n"
        f"<i>Admins have been notified. Thank you.</i>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 15))
    try:
        await message.delete()
    except Exception:
        pass
