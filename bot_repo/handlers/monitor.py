"""
Monitor Group — relay managed group messages to the Monitor Group.

Features:
- Shows source group name in every forwarded message.
- Format: "Source: [Group Name] | Message: [Text]"
- Rate Limiting: If one group sends more than 5 messages in 10 seconds,
  that group's monitoring is paused for 1 minute.
- Admin replies from Monitor Group are relayed back to the original group.
"""

import asyncio
import time
from collections import deque

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, app, db, groups_col
from helpers import bot_api, _auto_del, BOT_TOKEN

_monitor_relay_col = db["monitor_relay_messages"]

# Per-group rate tracking: 5 messages per 10 seconds max
_group_msg_times: dict[int, deque] = {}
_group_paused_until: dict[int, float] = {}
_RATE_MAX = 5
_RATE_WINDOW = 10.0   # seconds
_PAUSE_DURATION = 60.0  # seconds to pause after exceeding rate limit


def _check_rate_limit(chat_id: int) -> bool:
    """
    Returns True if the group should be allowed through, False if rate-limited.
    Pauses that group for 1 minute when it exceeds 5 messages in 10 seconds.
    """
    now = time.monotonic()

    # Check if currently paused
    pause_until = _group_paused_until.get(chat_id, 0)
    if now < pause_until:
        return False

    if chat_id not in _group_msg_times:
        _group_msg_times[chat_id] = deque()

    times = _group_msg_times[chat_id]

    # Remove timestamps older than the rate window
    while times and now - times[0] > _RATE_WINDOW:
        times.popleft()

    if len(times) >= _RATE_MAX:
        # Rate exceeded — pause this group for 1 minute
        _group_paused_until[chat_id] = now + _PAUSE_DURATION
        print(f"[MONITOR] Rate limit exceeded for chat {chat_id} — pausing for {int(_PAUSE_DURATION)}s")
        return False

    times.append(now)
    return True


async def _get_monitor_id() -> int | None:
    from handlers.control_group import get_monitor_group
    return await get_monitor_group()


async def _get_control_id() -> int | None:
    from handlers.control_group import get_control_group
    return await get_control_group()


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

        # Rate limit check — 5 msgs per 10s, then 1 min pause per group
        if not _check_rate_limit(chat_id):
            return

        chat_title = message.chat.title or str(chat_id)
        sender_name = sender.first_name or "Unknown"
        sender_link = f'<a href="tg://user?id={sender.id}">{sender_name}</a>'

        relay_msg_id = None

        if message.text:
            content = message.text[:500]
            # Required format: "Source: [Group Name] | Message: [Text]"
            formatted = (
                f"📍 <b>Source: {chat_title}</b> | <b>Message:</b> {content}\n"
                f"👤 Sender: {sender_link}"
            )
            res = await bot_api("sendMessage", {
                "chat_id": monitor_id,
                "text": formatted,
                "parse_mode": "HTML",
            })
            if res.get("ok"):
                relay_msg_id = res["result"]["message_id"]
        else:
            media_type = (
                "Photo" if message.photo else
                "Video" if message.video else
                "Voice" if message.voice else
                "Audio" if message.audio else
                "File" if message.document else
                "Sticker" if message.sticker else
                "Media"
            )
            header = (
                f"📍 <b>Source: {chat_title}</b> | <b>{media_type}</b>\n"
                f"👤 Sender: {sender_link}"
            )
            await bot_api("sendMessage", {
                "chat_id": monitor_id,
                "text": header,
                "parse_mode": "HTML",
            })

            fwd = await bot_api("forwardMessage", {
                "chat_id": monitor_id,
                "from_chat_id": chat_id,
                "message_id": message.id,
            })
            if fwd.get("ok"):
                relay_msg_id = fwd["result"]["message_id"]

        if relay_msg_id:
            await _monitor_relay_col.insert_one({
                "monitor_msg_id": relay_msg_id,
                "original_chat_id": chat_id,
                "original_msg_id": message.id,
                "monitor_group_id": monitor_id,
                "sender_id": sender.id,
                "chat_title": chat_title,
            })

    except Exception as e:
        print(f"[MONITOR_RELAY] Error: {e}")


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
            "monitor_msg_id": replied.id,
            "monitor_group_id": monitor_id,
        })
        if not mapping:
            return

        original_chat_id = mapping["original_chat_id"]
        original_msg_id = mapping.get("original_msg_id")

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
                    "reaction": [{"type": "emoji", "emoji": "👍"}],
                })
            except Exception:
                pass

    except Exception as e:
        print(f"[MONITOR_REPLY] Error: {e}")
