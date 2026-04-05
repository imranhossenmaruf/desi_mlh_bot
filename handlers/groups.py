import asyncio
from datetime import datetime

from pyrogram import Client, filters, StopPropagation
from pyrogram.enums import MessageEntityType, ChatMemberStatus
from pyrogram.types import (
    Message, ChatMemberUpdated,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import HTML, ADMIN_ID, groups_col, app
from helpers import log_event, _auto_del, get_bot_username, admin_filter

_URL_ENTITY_TYPES = {MessageEntityType.URL, MessageEntityType.TEXT_LINK}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _is_privileged(client: Client, chat_id: int, user_id: int) -> bool:
    """True if user is the bot's admin OR a group admin/owner."""
    if user_id == ADMIN_ID:
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False


def _msg_has_link(message: Message) -> bool:
    entities = (message.entities or []) + (message.caption_entities or [])
    return any(e.type in _URL_ENTITY_TYPES for e in entities)


def _msg_is_forwarded(message: Message) -> bool:
    return bool(
        message.forward_from
        or message.forward_from_chat
        or message.forward_sender_name
        or message.forward_date
    )


# ── Anti-forward / Anti-link (highest priority, group=-5) ────────────────────

@app.on_message(filters.group, group=-5)
async def anti_forward_link(client: Client, message: Message):
    if not message.from_user:
        return
    if not (_msg_is_forwarded(message) or _msg_has_link(message)):
        return

    user_id = message.from_user.id
    if await _is_privileged(client, message.chat.id, user_id):
        return

    try:
        await message.delete()
    except Exception as e:
        print(f"[ANTI-FL] Delete failed in {message.chat.id}: {e}")
    # Message deleted silently — no warning sent.


# ── Group command guard (group=-4) ────────────────────────────────────────────

@app.on_message(filters.group, group=-4)
async def group_command_guard(client: Client, message: Message):
    """Block all commands in groups for non-privileged users.
    /video → redirect message (auto-del 3 min).
    All other commands → silently deleted.
    """
    text = message.text or message.caption or ""
    if not text.startswith("/"):
        return

    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return

    if await _is_privileged(client, message.chat.id, user_id):
        return

    cmd = text.split()[0].lstrip("/").split("@")[0].lower()

    if cmd == "video":
        bot_username = await get_bot_username(client)
        user    = message.from_user
        u_name  = (user.first_name or "User") if user else "User"
        u_id    = user.id if user else 0
        mention = f"<a href='tg://user?id={u_id}'>{u_name}</a>"

        # ── Send BIG reply BEFORE deleting the command ────────────────────
        m = await client.send_message(
            message.chat.id,
            f"╔══════════════════════╗\n"
            f"        🎬 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 𝑽𝑰𝑫𝑬𝑶\n"
            f"╚══════════════════════╝\n\n"
            f"👋 Hey {mention}!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚫 <b>Videos are only available</b>\n"
            f"    in <b>Private Chat</b> with the bot!\n\n"
            f"🎬 <b>What you get in private:</b>\n"
            f"   ✅ HD Videos with Spoiler Protection\n"
            f"   ✅ Daily Limits Based on Your Plan\n"
            f"   ✅ Premium = More Videos Per Day\n"
            f"   ✅ Exclusive Premium-Only Content\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 <b>Upgrade for more daily videos!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 <b>Tap a button below to proceed:</b>",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🎬 Get Video Now",
                        url=f"https://t.me/{bot_username}?start=video",
                    ),
                    InlineKeyboardButton(
                        "🔵⃝𝐂𝐎𝐔𝐏𝐋𝐄⃝🔵",
                        url="https://t.me/+PnUkO8waIEcyNDY1",
                    ),
                ],
            ]),
            reply_to_message_id=message.id,
            parse_mode=HTML,
        )
        # ── Delete the /video command after replying ───────────────────────
        try:
            await message.delete()
        except Exception:
            pass
        asyncio.create_task(_auto_del(m, 180))

    else:
        try:
            await message.delete()
        except Exception:
            pass

    raise StopPropagation

_BOT_ID: int = 0


async def _get_bot_id(client: Client) -> int:
    global _BOT_ID
    if not _BOT_ID:
        me = await client.get_me()
        _BOT_ID = me.id
    return _BOT_ID


async def _upsert_group(chat, added_by=None, bot_is_admin: bool = False, can_invite: bool = False):
    added_by_id   = getattr(added_by, "id", None)
    added_by_name = getattr(added_by, "first_name", None) or str(added_by_id)
    # member_count: available on Chat objects for supergroups/channels
    member_count  = getattr(chat, "members_count", None)
    set_data = {
        "chat_id":          chat.id,
        "title":            chat.title or str(chat.id),
        "type":             str(chat.type),
        "bot_is_admin":     bot_is_admin,
        "can_invite_users": can_invite,
        "added_by_id":      added_by_id,
        "added_by_name":    added_by_name,
        "updated_at":       datetime.utcnow(),
    }
    if member_count is not None:
        set_data["member_count"] = member_count
    await groups_col.update_one(
        {"chat_id": chat.id},
        {
            "$set": set_data,
            "$setOnInsert": {"added_at": datetime.utcnow()},
        },
        upsert=True,
    )


async def _remove_group(chat_id: int):
    await groups_col.delete_one({"chat_id": chat_id})


async def _try_add_admin(client: Client, chat_id: int):
    """If bot has can_invite_users, invite the bot's admin to the group."""
    try:
        await client.add_chat_members(chat_id, ADMIN_ID)
        print(f"[GROUPS] Admin {ADMIN_ID} added to {chat_id}")
        await log_event(client,
            f"✅ <b>Admin Added to Group</b>\n"
            f"🆔 Chat: <code>{chat_id}</code>"
        )
    except Exception as e:
        print(f"[GROUPS] Could not add admin to {chat_id}: {e}")


async def _handle_bot_added(client: Client, chat, added_by=None):
    """Common logic when bot is added to a group."""
    bot_id = await _get_bot_id(client)

    # Check bot admin status
    bot_is_admin = False
    can_invite   = False
    try:
        member = await client.get_chat_member(chat.id, bot_id)
        priv   = getattr(member, "privileges", None)
        if priv:
            bot_is_admin = True
            can_invite   = bool(getattr(priv, "can_invite_users", False))
    except Exception:
        pass

    await _upsert_group(chat, added_by, bot_is_admin, can_invite)

    adder_name = getattr(added_by, "first_name", None) or "Unknown"
    adder_id   = getattr(added_by, "id", None) or 0
    adder_mention = f"<a href='tg://user?id={adder_id}'>{adder_name}</a>"

    await log_event(client,
        f"🤖 <b>Bot Added to Group</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Group:</b> {chat.title or chat.id}\n"
        f"🆔 <b>Chat ID:</b> <code>{chat.id}</code>\n"
        f"👤 <b>Added by:</b> {adder_mention}\n"
        f"👑 <b>Bot is Admin:</b> {'Yes ✅' if bot_is_admin else 'No ❌'}\n"
        f"📨 <b>Can Invite:</b> {'Yes ✅' if can_invite else 'No ❌'}"
    )

    if can_invite:
        asyncio.create_task(_try_add_admin(client, chat.id))

    print(f"[GROUPS] Added to '{chat.title}' ({chat.id}) admin={bot_is_admin} invite={can_invite}")


# ── Handler: new_chat_members (works for regular groups & some supergroups) ──

@app.on_message(filters.new_chat_members, group=50)
async def on_new_members(client: Client, message: Message):
    bot_id = await _get_bot_id(client)
    human_count = 0
    for user in message.new_chat_members:
        if user.id == bot_id:
            asyncio.create_task(_handle_bot_added(client, message.chat, message.from_user))
        elif not user.is_bot:
            human_count += 1
    if human_count:
        await groups_col.update_one(
            {"chat_id": message.chat.id},
            {"$inc": {"member_count": human_count}},
        )


# ── Handler: ChatMemberUpdated (supergroups / channels) ──────────────────────

@app.on_chat_member_updated(group=50)
async def on_chat_member_updated(client: Client, update: ChatMemberUpdated):
    bot_id = await _get_bot_id(client)
    if not update.new_chat_member or update.new_chat_member.user.id != bot_id:
        return

    new_status = str(update.new_chat_member.status)
    old_status = str(update.old_chat_member.status) if update.old_chat_member else ""

    # Bot was added / promoted
    if new_status in ("ChatMemberStatus.MEMBER", "ChatMemberStatus.ADMINISTRATOR"):
        if old_status in ("ChatMemberStatus.LEFT", "ChatMemberStatus.BANNED", ""):
            asyncio.create_task(
                _handle_bot_added(client, update.chat, update.from_user)
            )

    # Bot was removed / banned
    elif new_status in ("ChatMemberStatus.LEFT", "ChatMemberStatus.BANNED", "ChatMemberStatus.KICKED"):
        await _remove_group(update.chat.id)
        await log_event(client,
            f"🚪 <b>Bot Removed from Group</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Group:</b> {update.chat.title or update.chat.id}\n"
            f"🆔 <b>Chat ID:</b> <code>{update.chat.id}</code>"
        )
        print(f"[GROUPS] Removed from '{update.chat.title}' ({update.chat.id})")


# ── Handler: left_chat_member (for regular groups) ───────────────────────────

@app.on_message(filters.left_chat_member, group=50)
async def on_left_member(client: Client, message: Message):
    bot_id = await _get_bot_id(client)
    user   = message.left_chat_member
    if not user:
        return
    if user.id == bot_id:
        # Bot was removed from the group
        await _remove_group(message.chat.id)
        await log_event(client,
            f"🚪 <b>Bot Removed from Group</b>\n"
            f"📌 <b>Group:</b> {message.chat.title or message.chat.id}\n"
            f"🆔 <b>Chat ID:</b> <code>{message.chat.id}</code>"
        )
        print(f"[GROUPS] Removed from '{message.chat.title}' ({message.chat.id})")
    elif not user.is_bot:
        # Human member left — decrement stored member count
        await groups_col.update_one(
            {"chat_id": message.chat.id},
            {"$inc": {"member_count": -1}},
        )


# ── Admin command: /groups ────────────────────────────────────────────────────

@app.on_message(filters.command("groups") & admin_filter & filters.private)
async def groups_cmd(client: Client, message: Message):
    docs = await groups_col.find({}).sort("added_at", -1).to_list(length=None)
    if not docs:
        await message.reply_text("📭 Bot is not a member of any group yet.", parse_mode=HTML)
        return

    lines = [f"🤖 <b>Bot Groups ({len(docs)})</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    for d in docs:
        title  = d.get("title", "Unknown")
        cid    = d.get("chat_id", "?")
        status = "👑 Admin" if d.get("bot_is_admin") else "👤 Member"
        lines.append(f"• <b>{title}</b>\n  <code>{cid}</code>  —  {status}")

    await message.reply_text("\n".join(lines), parse_mode=HTML)
