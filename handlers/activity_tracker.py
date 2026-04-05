"""
Chat Monitor Group System
──────────────────────────
Group messages go to a dedicated MONITOR GROUP (separate from inbox & log).
Admin can reply there → bot DMs the original user.

Setup:
  1. Add bot to your dedicated monitor group as admin
  2. Send /setmonitorgroup in that group

Log group  → bot operational logs only (bot added/removed, errors)
Inbox group → private DMs from users (unchanged)
Monitor group → all group chat messages + reply-to-user feature
"""
import asyncio
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from config import app, HTML, settings_col, clones_col
from helpers import (
    log_event, _is_admin_msg, _auto_del,
    get_cfg, _clone_config_ctx, BOT_TOKEN,
)

# ── MongoDB collection for monitor message mappings ───────────────────────────
_monitor_col = None


def _mon_col():
    global _monitor_col
    if _monitor_col is None:
        from config import db
        _monitor_col = db["chat_monitor_msgs"]
    return _monitor_col


# ── Clone-aware monitor group helpers ─────────────────────────────────────────

async def _get_monitor_group(client=None) -> int | None:
    """Return monitor group chat_id (clone-aware, same priority chain as inbox)."""
    if client is not None:
        cfg = getattr(client, "_clone_config", None) or _clone_config_ctx.get()
        if cfg and cfg.get("monitor_group"):
            return cfg["monitor_group"]
    clone_mg = get_cfg("monitor_group")
    if clone_mg:
        return clone_mg
    doc = await settings_col.find_one({"key": "chat_monitor_group"})
    return doc.get("chat_id") if doc else None


async def _set_monitor_group(chat_id: int, client=None):
    """Save monitor group (clone-aware)."""
    cfg = _clone_config_ctx.get()
    if cfg:
        from clone_manager import reload_clone_config
        tok = cfg.get("token")
        await clones_col.update_one(
            {"token": tok},
            {"$set": {"monitor_group": chat_id}},
            upsert=True,
        )
        await reload_clone_config(tok)
        return
    await settings_col.update_one(
        {"key": "chat_monitor_group"},
        {"$set": {"chat_id": chat_id}},
        upsert=True,
    )


def _now() -> str:
    return datetime.utcnow().strftime("%d %b %Y %H:%M UTC")


def _link(user) -> str:
    name = (user.first_name or "User") if user else "User"
    uid  = user.id if user else "?"
    return f'<a href="tg://user?id={uid}">{name}</a>'


# ── /setmonitorgroup  — run inside the desired monitor group ──────────────────

@app.on_message(filters.command("setmonitorgroup") & filters.group)
async def set_monitor_group_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return
    chat_id = message.chat.id
    await _set_monitor_group(chat_id, client)
    m = await message.reply_text(
        f"✅ <b>Monitor group set!</b>\n"
        f"All group messages will now be forwarded here.\n"
        f"Reply to any forwarded message to DM that user.",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
    try:
        await message.delete()
    except Exception:
        pass


# ── Forward group messages → monitor group ────────────────────────────────────

_MEDIA_FILTER = (
    filters.text
    | filters.photo
    | filters.video
    | filters.voice
    | filters.sticker
    | filters.document
    | filters.audio
    | filters.animation
    | filters.video_note
)


@app.on_message(
    filters.group & ~filters.service & _MEDIA_FILTER,
    group=25,
)
async def forward_to_monitor(client: Client, message: Message):
    if not message.from_user:
        return
    if message.from_user.is_bot:
        return
    if (message.text or "").startswith("/"):
        return

    monitor_id = await _get_monitor_group(client)
    if not monitor_id:
        return

    user  = message.from_user
    uid   = user.id
    uname = f"@{user.username}" if user.username else "no username"
    name  = user.first_name or "User"
    chat  = message.chat.title or str(message.chat.id)

    header = (
        f"💬 <b>Group Message</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {_link(user)}  <code>{uid}</code>\n"
        f"🔖 {uname}\n"
        f"📍 {chat}\n"
        f"🕒 {_now()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Reply here to DM this user</i>"
    )

    try:
        header_msg = await client.send_message(monitor_id, header, parse_mode=HTML)
        fwd_msg    = await message.forward(monitor_id)

        # Store mapping so admin replies can reach the user
        await _mon_col().insert_one({
            "header_msg_id":  header_msg.id,
            "forward_msg_id": fwd_msg.id if fwd_msg else None,
            "user_id":        uid,
            "group_title":    chat,
            "monitor_id":     monitor_id,
            "created_at":     datetime.utcnow(),
        })
    except Exception as exc:
        print(f"[MONITOR] Forward failed: {exc}")


# ── Admin replies in monitor group → DM to original user ─────────────────────

@app.on_message(filters.reply & filters.group, group=12)
async def monitor_reply_handler(client: Client, message: Message):
    if not message.from_user:
        return

    monitor_id = await _get_monitor_group(client)
    if not monitor_id or message.chat.id != monitor_id:
        return

    # Only admins can reply
    if not await _is_admin_msg(client, message):
        return

    replied_id = message.reply_to_message.id if message.reply_to_message else None
    if not replied_id:
        return

    # Find original user from either header or forwarded message
    doc = await _mon_col().find_one({
        "$or": [
            {"header_msg_id":  replied_id},
            {"forward_msg_id": replied_id},
        ]
    })
    if not doc:
        return   # Not a monitored message — ignore

    user_id     = doc["user_id"]
    group_title = doc.get("group_title", "the group")
    admin_name  = message.from_user.first_name or "Admin"

    # Send admin's reply to user as DM
    try:
        intro = (
            f"📩 <b>Message from admin</b>\n"
            f"<i>(regarding your message in {group_title})</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
        )
        await client.send_message(user_id, intro, parse_mode=HTML)
        await message.copy(user_id)

        # Confirm to admin
        confirm = await message.reply_text(
            f"✅ Reply sent to user <code>{user_id}</code>.",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(confirm, 10))
    except Exception as exc:
        err = await message.reply_text(
            f"❌ Could not DM user <code>{user_id}</code>: {exc}",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(err, 20))
        print(f"[MONITOR] Reply DM failed: {exc}")
