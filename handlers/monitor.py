"""
Monitor Group — সব পরিচালিত গ্রুপের মেসেজ Monitor Group-এ ফরওয়ার্ড করে,
এবং সেখান থেকে Admin রিপ্লাই দিলে সেটা আবার মূল গ্রুপে পাঠায়।
"""

import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, app, db, groups_col
from helpers import bot_api, _auto_del, BOT_TOKEN

_monitor_relay_col = db["monitor_relay_messages"]


async def _get_monitor_id() -> int | None:
    from handlers.control_group import get_monitor_group
    return await get_monitor_group()


async def _get_control_id() -> int | None:
    from handlers.control_group import get_control_group
    return await get_control_group()


# ── ১. সব গ্রুপের মেসেজ Monitor Group-এ পাঠানো ────────────────────────────────

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

        # Command skip
        raw = (message.text or message.caption or "").lstrip()
        if raw.startswith("/"):
            return

        chat_title = message.chat.title or str(chat_id)

        # সরাসরি মেসেজ ফরওয়ার্ড করো (header ছাড়া)
        fwd_res = await bot_api("forwardMessage", {
            "chat_id":      monitor_id,
            "from_chat_id": chat_id,
            "message_id":   message.id,
        })

        if fwd_res.get("ok"):
            fwd_msg_id = fwd_res["result"]["message_id"]

            # Reaction দাও
            for emoji in ("👁", "👀", "👍"):
                r = await bot_api("setMessageReaction", {
                    "chat_id":    monitor_id,
                    "message_id": fwd_msg_id,
                    "reaction":   [{"type": "emoji", "emoji": emoji}],
                })
                if r.get("ok"):
                    break

            await _monitor_relay_col.insert_one({
                "monitor_msg_id":   fwd_msg_id,
                "original_chat_id": chat_id,
                "original_msg_id":  message.id,
                "monitor_group_id": monitor_id,
                "sender_id":        sender.id,
                "chat_title":       chat_title,
            })

    except Exception as e:
        print(f"[MONITOR_RELAY] Error: {e}")


# ── ২. Monitor Group থেকে রিপ্লাই দিলে মূল গ্রুপে পাঠানো ─────────────────────

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
            err = result.get("description", "অজানা ত্রুটি")
            m = await message.reply_text(
                f"❌ পাঠানো যায়নি: <code>{err}</code>", parse_mode=HTML
            )
            asyncio.create_task(_auto_del(m, 15))

    except Exception as e:
        print(f"[MONITOR_REPLY] Error: {e}")
