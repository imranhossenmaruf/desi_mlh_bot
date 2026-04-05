"""
Activity Tracker System
────────────────────────
Logs all group events to the configured log group:
  • Member join / leave
  • Message types (photo, video, voice, document, sticker)
  • Command usage by regular users

Chat messages (text from group members) forwarded to inbox group
if /trackchats is enabled for that group (default: off).

Admin commands:
  /trackchats on   — start forwarding group text messages → inbox group
  /trackchats off  — stop forwarding
"""
import asyncio
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from config import app, HTML
from helpers import log_event, _is_admin_msg, _auto_del


async def _get_inbox_group(client):
    from handlers.inbox import _get_inbox_group as _ig
    return await _ig(client)

# ── In-memory toggle per chat {chat_id: bool} ─────────────────────────────────
# Persisted to MongoDB collection "activity_settings"
_track_chat_enabled: dict[int, bool] = {}

_activity_col = None  # lazy-init


def _act_col():
    global _activity_col
    if _activity_col is None:
        from config import db
        _activity_col = db["activity_settings"]
    return _activity_col


async def _is_trackchats(chat_id: int) -> bool:
    if chat_id in _track_chat_enabled:
        return _track_chat_enabled[chat_id]
    doc = await _act_col().find_one({"chat_id": chat_id})
    val = bool(doc.get("track_chats")) if doc else False
    _track_chat_enabled[chat_id] = val
    return val


def _now() -> str:
    return datetime.utcnow().strftime("%d %b %Y %H:%M UTC")


def _link(user) -> str:
    name = (user.first_name or "User") if user else "User"
    uid  = user.id if user else "?"
    return f'<a href="tg://user?id={uid}">{name}</a>'


# ── /trackchats command ───────────────────────────────────────────────────────

@app.on_message(filters.command("trackchats") & filters.group)
async def trackchats_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return
    args = message.command[1:]
    if not args or args[0].lower() not in ("on", "off"):
        m = await message.reply_text(
            "Usage: <code>/trackchats on</code> or <code>/trackchats off</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 20))
        return

    enabled = args[0].lower() == "on"
    chat_id = message.chat.id
    _track_chat_enabled[chat_id] = enabled
    await _act_col().update_one(
        {"chat_id": chat_id},
        {"$set": {"track_chats": enabled}},
        upsert=True,
    )
    status = "✅ Enabled" if enabled else "❌ Disabled"
    m = await message.reply_text(
        f"{status} — group chat messages will "
        f"{'now be forwarded to inbox group.' if enabled else 'no longer be forwarded.'}",
    )
    asyncio.create_task(_auto_del(m, 20))
    try:
        await message.delete()
    except Exception:
        pass


# ── Track: member join ────────────────────────────────────────────────────────

@app.on_message(filters.new_chat_members & filters.group, group=25)
async def track_join(client: Client, message: Message):
    for user in (message.new_chat_members or []):
        if user.is_bot:
            continue
        asyncio.create_task(log_event(client,
            f"➕ <b>Member Joined</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {_link(user)} <code>{user.id}</code>\n"
            f"📍 {message.chat.title or message.chat.id}\n"
            f"🕒 {_now()}"
        ))


# ── Track: member left ────────────────────────────────────────────────────────

@app.on_message(filters.left_chat_member & filters.group, group=25)
async def track_leave(client: Client, message: Message):
    user = message.left_chat_member
    if not user or user.is_bot:
        return
    asyncio.create_task(log_event(client,
        f"➖ <b>Member Left</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {_link(user)} <code>{user.id}</code>\n"
        f"📍 {message.chat.title or message.chat.id}\n"
        f"🕒 {_now()}"
    ))


# ── Track: media activity ─────────────────────────────────────────────────────

@app.on_message(
    filters.group
    & ~filters.service
    & (
        filters.photo
        | filters.video
        | filters.sticker
        | filters.voice
        | filters.document
        | filters.audio
    ),
    group=25,
)
async def track_media(client: Client, message: Message):
    if not message.from_user:
        return
    mtype = (
        "📸 Photo"    if message.photo    else
        "🎬 Video"    if message.video    else
        "🎭 Sticker"  if message.sticker  else
        "🎤 Voice"    if message.voice    else
        "🎵 Audio"    if message.audio    else
        "📎 Document"
    )
    user = message.from_user
    asyncio.create_task(log_event(client,
        f"📤 <b>Media Activity</b> — {mtype}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {_link(user)} <code>{user.id}</code>\n"
        f"📍 {message.chat.title or message.chat.id}\n"
        f"🕒 {_now()}"
    ))


# ── Track + forward: text chat messages ──────────────────────────────────────

@app.on_message(
    filters.group & filters.text & ~filters.service,
    group=25,
)
async def track_text_message(client: Client, message: Message):
    if not message.from_user:
        return
    # Skip commands (already handled elsewhere)
    if (message.text or "").startswith("/"):
        return

    chat_id = message.chat.id
    if not await _is_trackchats(chat_id):
        return

    inbox_id = await _get_inbox_group(client)
    if not inbox_id:
        return

    user = message.from_user
    name = user.first_name or "User"
    uid  = user.id
    header = (
        f"💬 <b>Group Message</b>\n"
        f"👤 <a href='tg://user?id={uid}'>{name}</a> "
        f"<code>{uid}</code>\n"
        f"📍 {message.chat.title or chat_id}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    try:
        from config import HTML
        await client.send_message(inbox_id, header, parse_mode=HTML)
        await message.forward(inbox_id)
    except Exception as exc:
        print(f"[ACTIVITY] Forward to inbox failed: {exc}")
