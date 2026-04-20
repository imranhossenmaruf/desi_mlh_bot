"""
Monitor Group — relay managed group messages to the Monitor Group.
- Text messages are sent as formatted: "From Group: [Name] | [content]"
- Media messages are forwarded with a source header
- Per-group rate throttle: max 5 messages per minute (12s minimum interval)
- Admin replies from Monitor Group are relayed back to the original group.
"""

import asyncio
import time

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, app, db, groups_col
from helpers import bot_api, _auto_del, BOT_TOKEN

_monitor_relay_col = db["monitor_relay_messages"]

# Per-group rate throttle: one message per 12 seconds maximum (= 5/min)
_last_fwd_time: dict[int, float] = {}
_FWD_MIN_INTERVAL = 12.0


async def _get_monitor_id() -> int | None:
    from handlers.control_group import get_monitor_group
    return await get_monitor_group()


async def _get_control_id() -> int | None:
    from handlers.control_group import get_control_group
    return await get_control_group()


# ── Relay group messages to Monitor Group ──────────────────────────────────────

@app.on_message(filters.group & filters.incoming, group=20)
async def relay_group_msg_to_monitor(client: Client, message: Message):
    """Forward managed group messages to Monitor Group with source info."""
    try:
        monitor_id = await _get_monitor_id()
        if not monitor_id:
            return

        chat_id = message.chat.id

        if chat_id == monitor_id:
            return
        ctrl_id = await _get_control_id()
        if ctrl_id and chat_id == ctrl_id:
            return

        is_managed = await groups_col.find_one({"chat_id": chat_id})
        if not is_managed:
            return

        sender = message.from_user
        if not sender or sender.is_bot or sender.is_self:
            return

        raw = (message.text or message.caption or "").lstrip()
        if raw.startswith("/"):
            return

        # Per-group rate throttle
        now  = time.monotonic()
        last = _last_fwd_time.get(chat_id, 0)
        if now - last < _FWD_MIN_INTERVAL:
            return
        _last_fwd_time[chat_id] = now

        chat_title   = message.chat.title or str(chat_id)
        sender_name  = sender.first_name or "Unknown"
        sender_link  = f'<a href="tg://user?id={sender.id}">{sender_name}</a>'

        relay_msg_id = None

        if message.text:
            # Format: "From Group: [Name] | [content]"
            content = message.text[:500]  # cap long messages
            formatted = (
                f"📡 <b>From Group: {chat_title}</b>\n"
                f"👤 {sender_link}\n"
                f"━━━━━━━━━━━━━━\n"
                f"{content}"
            )
            res = await bot_api("sendMessage", {
                "chat_id":    monitor_id,
                "text":       formatted,
                "parse_mode": "HTML",
            })
            if res.get("ok"):
                relay_msg_id = res["result"]["message_id"]

        else:
            # For media: send a header first, then forward the media
            media_type = (
                "📷 Photo"   if message.photo    else
                "🎬 Video"   if message.video    else
                "🎙 Voice"   if message.voice    else
                "🎵 Audio"   if message.audio    else
                "📄 File"    if message.document else
                "🎭 Sticker" if message.sticker  else
                "📎 Media"
            )
            header = (
                f"📡 <b>From Group: {chat_title}</b> | {media_type}\n"
                f"👤 {sender_link}"
            )
            await bot_api("sendMessage", {
                "chat_id":    monitor_id,
                "text":       header,
                "parse_mode": "HTML",
            })

            fwd = await bot_api("forwardMessage", {
                "chat_id":      monitor_id,
                "from_chat_id": chat_id,
                "message_id":   message.id,
            })
            if fwd.get("ok"):
                relay_msg_id = fwd["result"]["message_id"]

        if relay_msg_id:
            await _monitor_relay_col.insert_one({
                "monitor_msg_id":   relay_msg_id,
                "original_chat_id": chat_id,
                "original_msg_id":  message.id,
                "monitor_group_id": monitor_id,
                "sender_id":        sender.id,
                "chat_title":       chat_title,
            })

    except Exception as e:
        print(f"[MONITOR_RELAY] Error: {e}")


# ── Relay admin reply from Monitor Group back to original group ────────────────

@app.on_message(filters.group & filters.incoming, group=21)
async def monitor_group_reply_handler(client: Client, message: Message):
    """When admin replies in Monitor Group, relay the reply to the source group."""
    try:
        monitor_id = await _get_monitor_id()
        if not monitor_id:
            return
        if message.chat.id != monitor_id:
            return

        replied = message.reply_to_message
        if not replied:
            return

        raw_text = message.text or message.caption or ""
        if raw_text.startswith("/"):
            return

        mapping = await _monitor_relay_col.find_one({
            "monitor_msg_id":   replied.id,
            "monitor_group_id": monitor_id,
        })
        if not mapping:
            return

        original_chat_id = mapping["original_chat_id"]
        original_msg_id  = mapping.get("original_msg_id")
        chat_title       = mapping.get("chat_title", str(original_chat_id))

        params: dict = {"chat_id": original_chat_id, "parse_mode": "HTML"}
        if original_msg_id:
            params["reply_to_message_id"] = original_msg_id

        result = None
        if message.text:
            params["text"] = message.text
            result = await bot_api("sendMessage", params)
        elif message.photo:
            params["photo"]   = message.photo.file_id
            params["caption"] = message.caption or ""
            result = await bot_api("sendPhoto", params)
        elif message.video:
            params["video"]   = message.video.file_id
            params["caption"] = message.caption or ""
            result = await bot_api("sendVideo", params)
        elif message.voice:
            params["voice"] = message.voice.file_id
            result = await bot_api("sendVoice", params)
        elif message.audio:
            params["audio"]   = message.audio.file_id
            params["caption"] = message.caption or ""
            result = await bot_api("sendAudio", params)
        elif message.document:
            params["document"] = message.document.file_id
            params["caption"]  = message.caption or ""
            result = await bot_api("sendDocument", params)
        elif message.sticker:
            params["sticker"] = message.sticker.file_id
            result = await bot_api("sendSticker", params)
        else:
            return

        if result and result.get("ok"):
            try:
                await bot_api("setMessageReaction", {
                    "chat_id":    monitor_id,
                    "message_id": message.id,
                    "reaction":   [{"type": "emoji", "emoji": "👍"}],
                })
            except Exception:
                pass
        elif result:
            err = result.get("description", "Unknown error")
            m = await message.reply_text(
                f"❌ Failed to send: <code>{err}</code>", parse_mode=HTML
            )
            asyncio.create_task(_auto_del(m, 15))

    except Exception as e:
        print(f"[MONITOR_REPLY] Error: {e}")
