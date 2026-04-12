"""
Monitor Group — সব পরিচালিত গ্রুপের মেসেজ Monitor Group-এ ফরওয়ার্ড করে,
এবং সেখান থেকে Admin রিপ্লাই দিলে সেটা আবার মূল গ্রুপে পাঠায়।

Features:
- Per-group tracking toggle: /trackchats_on, /trackchats_off
- Global tracking toggle: /trackall_on, /trackall_off
- Rate limiting: 1 message per 5 seconds per group (spam control)
- Smart message update: Edit previous message instead of sending new one
- Ignore groups: IGNORED_GROUPS in config.py
- Message format with group name, chat_id, user info
"""

import asyncio
import time
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from config import (
    HTML, app, db, groups_col, ADMIN_IDS, IGNORED_GROUPS,
    monitor_settings_col, monitor_last_msg_col,
)
from helpers import bot_api, _auto_del, BOT_TOKEN, is_any_admin

_monitor_relay_col = db["monitor_relay_messages"]

# ── Rate limiting: {chat_id: last_forward_timestamp} ────────────────────────────
_rate_limit_cache: dict[int, float] = {}
RATE_LIMIT_SECONDS = 5  # 1 message per 5 seconds per group

# ── In-memory cache for tracking settings ────────────────────────────────────────
_tracking_cache: dict[int, bool] = {}       # per-group: chat_id -> enabled
_global_tracking: bool | None = None        # global tracking flag


async def _get_monitor_id() -> int | None:
    from handlers.control_group import get_monitor_group
    return await get_monitor_group()


async def _get_control_id() -> int | None:
    from handlers.control_group import get_control_group
    return await get_control_group()


# ══════════════════════════════════════════════════════════════════════════════════
# ═══════════════════════ TRACKING SETTINGS ════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════════

async def _is_global_tracking_on() -> bool:
    """Check if global tracking is enabled."""
    global _global_tracking
    if _global_tracking is not None:
        return _global_tracking
    doc = await monitor_settings_col.find_one({"key": "global_tracking"})
    _global_tracking = doc.get("enabled", False) if doc else False
    return _global_tracking


async def _set_global_tracking(enabled: bool):
    """Set global tracking on/off."""
    global _global_tracking
    _global_tracking = enabled
    await monitor_settings_col.update_one(
        {"key": "global_tracking"},
        {"$set": {"key": "global_tracking", "enabled": enabled}},
        upsert=True,
    )


async def _is_group_tracking_on(chat_id: int) -> bool:
    """Check if tracking is enabled for a specific group."""
    if chat_id in _tracking_cache:
        return _tracking_cache[chat_id]
    doc = await monitor_settings_col.find_one({"chat_id": chat_id})
    enabled = doc.get("enabled", True) if doc else True  # default ON
    _tracking_cache[chat_id] = enabled
    return enabled


async def _set_group_tracking(chat_id: int, enabled: bool):
    """Set per-group tracking on/off."""
    _tracking_cache[chat_id] = enabled
    await monitor_settings_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, "enabled": enabled}},
        upsert=True,
    )


async def _should_track_group(chat_id: int) -> bool:
    """Determine if a group should be tracked based on global and per-group settings."""
    # Check ignored groups first
    if chat_id in IGNORED_GROUPS:
        return False
    
    # If global tracking is ON, track all groups (except ignored)
    if await _is_global_tracking_on():
        return True
    
    # Otherwise, check per-group setting
    return await _is_group_tracking_on(chat_id)


# ══════════════════════════════════════════════════════════════════════════════════
# ═══════════════════════ RATE LIMITING ════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════════

def _check_rate_limit(chat_id: int) -> bool:
    """Check if we should forward (True) or skip due to rate limit (False)."""
    now = time.time()
    last_time = _rate_limit_cache.get(chat_id, 0)
    
    if now - last_time < RATE_LIMIT_SECONDS:
        return False  # Rate limited, skip
    
    _rate_limit_cache[chat_id] = now
    return True


# ══════════════════════════════════════════════════════════════════════════════════
# ═══════════════════════ SMART MESSAGE UPDATE ═════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════════

async def _get_last_monitor_msg(chat_id: int, user_id: int) -> dict | None:
    """Get the last monitor message for this (chat_id, user_id) pair."""
    return await monitor_last_msg_col.find_one({
        "chat_id": chat_id,
        "user_id": user_id,
    })


async def _save_last_monitor_msg(chat_id: int, user_id: int, monitor_msg_id: int, monitor_group_id: int):
    """Save the monitor message ID for smart editing."""
    await monitor_last_msg_col.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$set": {
            "chat_id": chat_id,
            "user_id": user_id,
            "monitor_msg_id": monitor_msg_id,
            "monitor_group_id": monitor_group_id,
            "updated_at": datetime.utcnow(),
        }},
        upsert=True,
    )


async def _try_edit_message(monitor_group_id: int, msg_id: int, new_text: str) -> bool:
    """Try to edit an existing message. Returns True if successful."""
    try:
        result = await bot_api("editMessageText", {
            "chat_id": monitor_group_id,
            "message_id": msg_id,
            "text": new_text,
            "parse_mode": "HTML",
        })
        return result.get("ok", False)
    except Exception as e:
        print(f"[MONITOR] Edit failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════════
# ═══════════════════════ TRACKING COMMANDS ════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command(["trackchats_on", "trackchats_off"]) & filters.group, group=1)
async def trackchats_toggle_cmd(client: Client, message: Message):
    """Per-group tracking toggle."""
    uid = message.from_user.id if message.from_user else 0
    if not await is_any_admin(uid):
        # Check if group admin
        try:
            from pyrogram.enums import ChatMemberStatus
            member = await client.get_chat_member(message.chat.id, uid)
            if member.status not in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR):
                return
        except Exception:
            return

    cmd = message.command[0].lower()
    enabled = cmd == "trackchats_on"
    chat_id = message.chat.id

    await _set_group_tracking(chat_id, enabled)

    status = "enabled" if enabled else "disabled"
    icon = "\u2705" if enabled else "\u274C"
    m = await message.reply_text(
        f"{icon} <b>Tracking {status}</b> for this group.\n"
        f"Messages will {'now be' if enabled else 'NO LONGER be'} forwarded to monitor group.",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 15))
    try:
        await message.delete()
    except Exception:
        pass


@app.on_message(filters.command(["trackall_on", "trackall_off"]), group=1)
async def trackall_toggle_cmd(client: Client, message: Message):
    """Global tracking toggle (admin only)."""
    uid = message.from_user.id if message.from_user else 0
    if uid not in ADMIN_IDS:
        return

    cmd = message.command[0].lower()
    enabled = cmd == "trackall_on"

    await _set_global_tracking(enabled)

    status = "enabled" if enabled else "disabled"
    icon = "\U0001F30D" if enabled else "\U0001F6AB"
    m = await message.reply_text(
        f"{icon} <b>Global tracking {status}!</b>\n"
        f"{'All groups will now be tracked (except ignored groups).' if enabled else 'Only individually enabled groups will be tracked.'}",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 15))


# ══════════════════════════════════════════════════════════════════════════════════
# ═══════════════════════ MESSAGE FORWARDING ═══════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════════

def _format_monitor_message(group_name: str, chat_id: int, user_name: str, user_id: int, text: str) -> str:
    """Format the monitor message with required format."""
    return (
        f"\U0001F4CD <b>Group:</b> {group_name}\n"
        f"\U0001F194 <b>ID:</b> <code>{chat_id}</code>\n"
        f"\U0001F464 <b>User:</b> <a href='tg://user?id={user_id}'>{user_name}</a>\n\n"
        f"\U0001F4AC <b>Message:</b>\n{text or '(no text)'}"
    )


@app.on_message(filters.group & filters.incoming, group=20)
async def relay_group_msg_to_monitor(client: Client, message: Message):
    """প্রতিটি পরিচালিত গ্রুপের মেসেজ Monitor Group-এ ফরওয়ার্ড করে।"""
    try:
        monitor_id = await _get_monitor_id()
        if not monitor_id:
            return

        chat_id = message.chat.id

        # Monitor Group বা Control Group থেকে মেসেজ relay করবো না
        if chat_id == monitor_id:
            return
        ctrl_id = await _get_control_id()
        if ctrl_id and chat_id == ctrl_id:
            return

        # Check if in ignored groups
        if chat_id in IGNORED_GROUPS:
            return

        # Check if this group should be tracked
        if not await _should_track_group(chat_id):
            return

        # এই গ্রুপ পরিচালিত গ্রুপের তালিকায় আছে কিনা চেক করো
        is_managed = await groups_col.find_one({"chat_id": chat_id})
        if not is_managed:
            return

        # Bot বা নিজের পাঠানো মেসেজ skip
        sender = message.from_user
        if not sender:
            return
        if sender.is_bot or sender.is_self:
            return

        # Forwarded messages skip (per requirement)
        if message.forward_date or message.forward_from or message.forward_from_chat or message.forward_sender_name:
            return

        # Command skip
        raw = (message.text or message.caption or "").lstrip()
        if raw.startswith("/"):
            return

        # Only process text messages (per requirement)
        if not message.text:
            return

        # Rate limiting: 1 message per 5 seconds per group
        if not _check_rate_limit(chat_id):
            return

        chat_title = message.chat.title or str(chat_id)
        user_name = sender.first_name or "User"
        user_id = sender.id
        text = message.text or ""

        # Format the monitor message
        formatted_msg = _format_monitor_message(chat_title, chat_id, user_name, user_id, text)

        # Smart message update: check for existing message from same user in same group
        last_msg_doc = await _get_last_monitor_msg(chat_id, user_id)
        
        sent_msg_id = None
        
        if last_msg_doc:
            # Try to edit the previous message
            edit_success = await _try_edit_message(
                monitor_id,
                last_msg_doc["monitor_msg_id"],
                formatted_msg
            )
            if edit_success:
                sent_msg_id = last_msg_doc["monitor_msg_id"]
                print(f"[MONITOR] Edited existing message for user {user_id} in {chat_id}")
        
        if not sent_msg_id:
            # Send new message (either no previous or edit failed)
            send_res = await bot_api("sendMessage", {
                "chat_id": monitor_id,
                "text": formatted_msg,
                "parse_mode": "HTML",
            })
            
            if send_res.get("ok"):
                sent_msg_id = send_res["result"]["message_id"]
                print(f"[MONITOR] Sent new message for user {user_id} in {chat_id}")
            else:
                print(f"[MONITOR] Send failed: {send_res.get('description')}")
                return

        # Save/update the last monitor message reference
        if sent_msg_id:
            await _save_last_monitor_msg(chat_id, user_id, sent_msg_id, monitor_id)

            # Add reaction to the message
            for emoji in ("\U0001F441", "\U0001F440", "\U0001F44D"):
                r = await bot_api("setMessageReaction", {
                    "chat_id": monitor_id,
                    "message_id": sent_msg_id,
                    "reaction": [{"type": "emoji", "emoji": emoji}],
                })
                if r.get("ok"):
                    break

            # Store in relay collection for reply functionality
            await _monitor_relay_col.update_one(
                {"monitor_msg_id": sent_msg_id},
                {"$set": {
                    "monitor_msg_id": sent_msg_id,
                    "original_chat_id": chat_id,
                    "original_msg_id": message.id,
                    "monitor_group_id": monitor_id,
                    "sender_id": sender.id,
                    "chat_title": chat_title,
                    "updated_at": datetime.utcnow(),
                }},
                upsert=True,
            )

    except Exception as e:
        print(f"[MONITOR_RELAY] Error: {e}")


# ══════════════════════════════════════════════════════════════════════════════════
# ═══════════════════════ REPLY HANDLER ════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.group & filters.incoming, group=21)
async def monitor_group_reply_handler(client: Client, message: Message):
    """Monitor Group-এ reply করলে তা মূল গ্রুপে পাঠায়।"""
    try:
        monitor_id = await _get_monitor_id()
        if not monitor_id:
            return
        if message.chat.id != monitor_id:
            return

        replied = message.reply_to_message
        if not replied:
            return

        # কমান্ড হলে skip করো
        raw_text = message.text or message.caption or ""
        if raw_text.startswith("/"):
            return

        # Original মেসেজের mapping খোঁজো
        mapping = await _monitor_relay_col.find_one({
            "monitor_msg_id": replied.id,
            "monitor_group_id": monitor_id,
        })
        if not mapping:
            return

        original_chat_id = mapping["original_chat_id"]
        original_msg_id = mapping.get("original_msg_id")
        chat_title = mapping.get("chat_title", str(original_chat_id))

        params: dict = {"chat_id": original_chat_id, "parse_mode": "HTML"}
        if original_msg_id:
            params["reply_to_message_id"] = original_msg_id

        result = None
        if message.text:
            params["text"] = message.text
            result = await bot_api("sendMessage", params)
        elif message.photo:
            params["photo"] = message.photo.file_id
            params["caption"] = message.caption or ""
            result = await bot_api("sendPhoto", params)
        elif message.video:
            params["video"] = message.video.file_id
            params["caption"] = message.caption or ""
            result = await bot_api("sendVideo", params)
        elif message.voice:
            params["voice"] = message.voice.file_id
            result = await bot_api("sendVoice", params)
        elif message.audio:
            params["audio"] = message.audio.file_id
            params["caption"] = message.caption or ""
            result = await bot_api("sendAudio", params)
        elif message.document:
            params["document"] = message.document.file_id
            params["caption"] = message.caption or ""
            result = await bot_api("sendDocument", params)
        elif message.sticker:
            params["sticker"] = message.sticker.file_id
            result = await bot_api("sendSticker", params)
        else:
            return

        if result and result.get("ok"):
            try:
                await bot_api("setMessageReaction", {
                    "chat_id": monitor_id,
                    "message_id": message.id,
                    "reaction": [{"type": "emoji", "emoji": "\U0001F44D"}],
                })
            except Exception:
                pass
        elif result:
            err = result.get("description", "\u0985\u099C\u09BE\u09A8\u09BE \u09A4\u09CD\u09B0\u09C1\u099F\u09BF")
            m = await message.reply_text(
                f"\u274C \u09AA\u09BE\u09A0\u09BE\u09A8\u09CB \u09AF\u09BE\u09DF\u09A8\u09BF: <code>{err}</code>", parse_mode=HTML
            )
            asyncio.create_task(_auto_del(m, 15))

    except Exception as e:
        print(f"[MONITOR_REPLY] Error: {e}")


# ══════════════════════════════════════════════════════════════════════════════════
# ═══════════════════════ STATUS COMMAND ═══════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("trackstatus") & filters.group, group=1)
async def track_status_cmd(client: Client, message: Message):
    """Show tracking status for current group."""
    uid = message.from_user.id if message.from_user else 0
    if uid not in ADMIN_IDS:
        try:
            from pyrogram.enums import ChatMemberStatus
            member = await client.get_chat_member(message.chat.id, uid)
            if member.status not in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR):
                return
        except Exception:
            return

    chat_id = message.chat.id
    global_on = await _is_global_tracking_on()
    group_on = await _is_group_tracking_on(chat_id)
    in_ignored = chat_id in IGNORED_GROUPS
    effective = await _should_track_group(chat_id)

    m = await message.reply_text(
        f"\U0001F4CA <b>Tracking Status</b>\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001F30D <b>Global Tracking:</b> {'\u2705 ON' if global_on else '\u274C OFF'}\n"
        f"\U0001F4CD <b>Group Tracking:</b> {'\u2705 ON' if group_on else '\u274C OFF'}\n"
        f"\U0001F6AB <b>In Ignored List:</b> {'\u2705 YES' if in_ignored else '\u274C NO'}\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\u2705 <b>Effective:</b> {'\u2705 TRACKING' if effective else '\u274C NOT TRACKING'}",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
