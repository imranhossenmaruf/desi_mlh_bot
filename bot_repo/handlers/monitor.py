"""
Monitor Group — relay managed group messages to the Monitor Group.

Features:
  - Clear, prominent source group header (group name + group ID).
  - Smart batching: when many messages arrive from the same group in a short
    window, they are grouped into ONE compact summary message in the Monitor
    Group instead of flooding it with separate messages.
  - Rate limiting safety net: if a group keeps spamming after batching,
    monitoring of that group is paused for 1 minute automatically.
  - Admin replies from the Monitor Group are relayed back to the original
    group (text reply works for both single and batched relays).
"""

import asyncio
import time
from collections import deque

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, app, db, groups_col
from helpers import bot_api, _auto_del, BOT_TOKEN

_monitor_relay_col = db["monitor_relay_messages"]

# ── Hard rate-limit safety net ────────────────────────────────────────────────
# If a group sends more than _RATE_MAX messages in _RATE_WINDOW seconds, pause
# its monitoring for _PAUSE_DURATION seconds. This is the last line of defense
# AFTER batching, so the threshold is intentionally high.
_group_msg_times:    dict[int, deque] = {}
_group_paused_until: dict[int, float] = {}
_RATE_MAX       = 20
_RATE_WINDOW    = 10.0   # seconds
_PAUSE_DURATION = 60.0   # seconds to pause after rate limit exceeded

# ── Smart batching ────────────────────────────────────────────────────────────
# Per-group buffer of messages waiting to be flushed. Messages are collected
# for _BATCH_WINDOW seconds (or until _BATCH_MAX items) and then sent as ONE
# compact summary to the Monitor Group.
_BATCH_WINDOW = 4.0   # seconds to wait before flushing a batch
_BATCH_MAX    = 8     # max messages per batch summary

# chat_id -> {
#   "items": [ {sender_name, sender_id, kind, content}, ... ],
#   "chat_title": str,
#   "task": asyncio.Task | None,
# }
_batch_buffers: dict[int, dict] = {}
_batch_lock = asyncio.Lock()


def _check_rate_limit(chat_id: int) -> bool:
    """
    True  → group is allowed through.
    False → group is being rate-limited (1 min pause).
    """
    now = time.monotonic()

    if now < _group_paused_until.get(chat_id, 0):
        return False

    if chat_id not in _group_msg_times:
        _group_msg_times[chat_id] = deque()

    times = _group_msg_times[chat_id]
    while times and now - times[0] > _RATE_WINDOW:
        times.popleft()

    if len(times) >= _RATE_MAX:
        _group_paused_until[chat_id] = now + _PAUSE_DURATION
        print(f"[MONITOR] Rate limit hit for chat {chat_id} — paused {int(_PAUSE_DURATION)}s")
        return False

    times.append(now)
    return True


async def _get_monitor_id() -> int | None:
    from handlers.control_group import get_monitor_group
    return await get_monitor_group()


async def _get_control_id() -> int | None:
    from handlers.control_group import get_control_group
    return await get_control_group()


def _kind_of(message: Message) -> str:
    if message.text:     return "text"
    if message.photo:    return "Photo"
    if message.video:    return "Video"
    if message.voice:    return "Voice"
    if message.audio:    return "Audio"
    if message.document: return "File"
    if message.sticker:  return "Sticker"
    if message.animation:return "GIF"
    return "Media"


def _format_header(chat_title: str, chat_id: int, count: int) -> str:
    """Big, clear source-group header that's impossible to miss."""
    if count > 1:
        badge = f"📨 <b>{count} new</b>"
    else:
        badge = "📨 <b>1 new</b>"
    return (
        "🔔━━━━━━━━━━━━━━━━━━━━🔔\n"
        f"📍 <b>GROUP:</b> <b>{chat_title}</b>\n"
        f"🆔 <code>{chat_id}</code>   |   {badge}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━"
    )


async def _flush_batch(chat_id: int) -> None:
    """Send the buffered messages for a single group to the Monitor Group."""
    await asyncio.sleep(_BATCH_WINDOW)

    async with _batch_lock:
        buf = _batch_buffers.pop(chat_id, None)
    if not buf or not buf["items"]:
        return

    monitor_id = await _get_monitor_id()
    if not monitor_id:
        return

    items      = buf["items"]
    chat_title = buf["chat_title"]
    header     = _format_header(chat_title, chat_id, len(items))

    # Single-message case → relay verbatim with the new header.
    if len(items) == 1:
        item = items[0]
        sender_link = f'<a href="tg://user?id={item["sender_id"]}">{item["sender_name"]}</a>'
        relay_msg_id = None

        if item["kind"] == "text":
            text_body = item["content"][:1000]
            formatted = (
                f"{header}\n"
                f"👤 {sender_link}\n"
                f"💬 {text_body}"
            )
            res = await bot_api("sendMessage", {
                "chat_id":    monitor_id,
                "text":       formatted,
                "parse_mode": "HTML",
            })
            if res.get("ok"):
                relay_msg_id = res["result"]["message_id"]
        else:
            label = item["kind"]
            head_msg = (
                f"{header}\n"
                f"👤 {sender_link}\n"
                f"🗂 <b>{label}</b>"
            )
            await bot_api("sendMessage", {
                "chat_id":    monitor_id,
                "text":       head_msg,
                "parse_mode": "HTML",
            })
            fwd = await bot_api("forwardMessage", {
                "chat_id":      monitor_id,
                "from_chat_id": chat_id,
                "message_id":   item["msg_id"],
            })
            if fwd.get("ok"):
                relay_msg_id = fwd["result"]["message_id"]

        if relay_msg_id:
            await _monitor_relay_col.insert_one({
                "monitor_msg_id":   relay_msg_id,
                "original_chat_id": chat_id,
                "original_msg_id":  item["msg_id"],
                "monitor_group_id": monitor_id,
                "sender_id":        item["sender_id"],
                "chat_title":       chat_title,
                "batched":          False,
            })
        return

    # Multi-message case → ONE compact summary with all messages bulleted.
    lines = [header]
    for it in items:
        link = f'<a href="tg://user?id={it["sender_id"]}">{it["sender_name"]}</a>'
        if it["kind"] == "text":
            preview = it["content"].strip().replace("\n", " ")
            if len(preview) > 140:
                preview = preview[:140] + "…"
            lines.append(f"• {link}: {preview}")
        else:
            lines.append(f"• {link}: <i>[{it['kind']}]</i>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("ℹ️ <i>Reply to this message to respond to the group.</i>")
    summary = "\n".join(lines)

    res = await bot_api("sendMessage", {
        "chat_id":               monitor_id,
        "text":                  summary,
        "parse_mode":            "HTML",
        "disable_web_page_preview": True,
    })
    if res.get("ok"):
        relay_msg_id = res["result"]["message_id"]
        # Map the batched relay to the LAST original message so admin replies
        # land in a sensible thread context.
        last_item = items[-1]
        await _monitor_relay_col.insert_one({
            "monitor_msg_id":   relay_msg_id,
            "original_chat_id": chat_id,
            "original_msg_id":  last_item["msg_id"],
            "monitor_group_id": monitor_id,
            "sender_id":        last_item["sender_id"],
            "chat_title":       chat_title,
            "batched":          True,
            "batch_size":       len(items),
        })


async def _enqueue_for_batch(chat_id: int, chat_title: str, item: dict) -> None:
    """Add an item to the per-group buffer, scheduling a flush if needed."""
    async with _batch_lock:
        buf = _batch_buffers.get(chat_id)
        if not buf:
            buf = {"items": [], "chat_title": chat_title, "task": None}
            _batch_buffers[chat_id] = buf

        buf["chat_title"] = chat_title
        buf["items"].append(item)

        # Hit the cap → flush immediately.
        if len(buf["items"]) >= _BATCH_MAX:
            if buf["task"] and not buf["task"].done():
                buf["task"].cancel()
            _batch_buffers.pop(chat_id, None)
            asyncio.create_task(_flush_now(chat_id, buf))
            return

        # Schedule a delayed flush only if one isn't already pending.
        if buf["task"] is None or buf["task"].done():
            buf["task"] = asyncio.create_task(_flush_batch(chat_id))


async def _flush_now(chat_id: int, buf: dict) -> None:
    """Immediate flush path used when the per-group cap is hit."""
    monitor_id = await _get_monitor_id()
    if not monitor_id:
        return

    items      = buf["items"]
    chat_title = buf["chat_title"]
    if not items:
        return

    header = _format_header(chat_title, chat_id, len(items))
    lines  = [header]
    for it in items:
        link = f'<a href="tg://user?id={it["sender_id"]}">{it["sender_name"]}</a>'
        if it["kind"] == "text":
            preview = it["content"].strip().replace("\n", " ")
            if len(preview) > 140:
                preview = preview[:140] + "…"
            lines.append(f"• {link}: {preview}")
        else:
            lines.append(f"• {link}: <i>[{it['kind']}]</i>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("ℹ️ <i>Reply to this message to respond to the group.</i>")
    summary = "\n".join(lines)

    res = await bot_api("sendMessage", {
        "chat_id":               monitor_id,
        "text":                  summary,
        "parse_mode":            "HTML",
        "disable_web_page_preview": True,
    })
    if res.get("ok"):
        relay_msg_id = res["result"]["message_id"]
        last_item    = items[-1]
        await _monitor_relay_col.insert_one({
            "monitor_msg_id":   relay_msg_id,
            "original_chat_id": chat_id,
            "original_msg_id":  last_item["msg_id"],
            "monitor_group_id": monitor_id,
            "sender_id":        last_item["sender_id"],
            "chat_title":       chat_title,
            "batched":          True,
            "batch_size":       len(items),
        })


# ── Relay group messages → Monitor Group ──────────────────────────────────────

@app.on_message(filters.group & filters.incoming, group=20)
async def relay_group_msg_to_monitor(client: Client, message: Message):
    """Forward managed group messages to Monitor Group with source group info."""
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

        if not _check_rate_limit(chat_id):
            return

        chat_title  = message.chat.title or str(chat_id)
        sender_name = sender.first_name or "Unknown"

        item = {
            "msg_id":      message.id,
            "sender_id":   sender.id,
            "sender_name": sender_name,
            "kind":        _kind_of(message),
            "content":     (message.text or message.caption or "")[:500],
        }

        await _enqueue_for_batch(chat_id, chat_title, item)

    except Exception as e:
        print(f"[MONITOR_RELAY] Error: {e}")


# ── Relay admin reply from Monitor Group → original group ─────────────────────

@app.on_message(filters.group & filters.incoming, group=21)
async def monitor_group_reply_handler(client: Client, message: Message):
    """When an admin replies in Monitor Group, relay it back to the source group."""
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
        was_batched      = bool(mapping.get("batched"))

        params: dict = {"chat_id": original_chat_id, "parse_mode": "HTML"}
        # For batched relays, replying to a specific original msg can be confusing
        # (the admin replied to a summary, not a single message). Send as a fresh
        # message in that group instead.
        if original_msg_id and not was_batched:
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

    except Exception as e:
        print(f"[MONITOR_REPLY] Error: {e}")
