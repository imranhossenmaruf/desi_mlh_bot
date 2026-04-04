import asyncio
from datetime import datetime, timezone, timedelta

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, ADMIN_ID, nightmode_col, app
from helpers import log_event, _is_admin_msg, bot_api

BST = timezone(timedelta(hours=6))


def _parse_hhmm(text: str):
    try:
        h, m = map(int, text.strip().split(":"))
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        pass
    return None


@app.on_message(filters.command("nightmode") & filters.group)
async def nightmode_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage:\n"
            "<code>/nightmode on HH:MM HH:MM</code>  — e.g. <code>/nightmode on 23:00 06:00</code> (BST)\n"
            "<code>/nightmode off</code>              — Disable\n"
            "<code>/nightmode status</code>           — Show schedule",
            parse_mode=HTML,
        )
        return

    sub = args[0].lower()

    if sub == "off":
        await nightmode_col.update_one(
            {"chat_id": message.chat.id},
            {"$set": {"enabled": False, "is_restricted": False}},
            upsert=True,
        )
        await bot_api("setChatPermissions", {
            "chat_id": message.chat.id,
            "permissions": {
                "can_send_messages": True, "can_send_media_messages": True,
                "can_send_polls": True, "can_add_web_page_previews": True,
                "can_change_info": False, "can_invite_users": True,
                "can_pin_messages": False,
            }
        })
        await message.reply_text("☀️ <b>Night Mode disabled.</b> Chat is now open.", parse_mode=HTML)
        return

    if sub == "status":
        doc = await nightmode_col.find_one({"chat_id": message.chat.id})
        if not doc or not doc.get("enabled"):
            await message.reply_text("🌙 Night Mode is <b>disabled</b> for this chat.", parse_mode=HTML)
        else:
            await message.reply_text(
                f"🌙 Night Mode: <b>ON</b>\n"
                f"🌘 Closes: {doc.get('start_h', '?'):02d}:{doc.get('start_m', '?'):02d} BST\n"
                f"☀️ Opens : {doc.get('end_h',   '?'):02d}:{doc.get('end_m',   '?'):02d} BST",
                parse_mode=HTML,
            )
        return

    if sub == "on":
        if len(args) < 3:
            await message.reply_text(
                "Please provide start and end times:\n"
                "<code>/nightmode on HH:MM HH:MM</code>  (BST)",
                parse_mode=HTML,
            )
            return
        start_t = _parse_hhmm(args[1])
        end_t   = _parse_hhmm(args[2])
        if not start_t or not end_t:
            await message.reply_text("❌ Invalid time format. Use <code>HH:MM</code>.", parse_mode=HTML)
            return
        sh, sm = start_t
        eh, em = end_t
        await nightmode_col.update_one(
            {"chat_id": message.chat.id},
            {"$set": {
                "enabled": True,
                "chat_id": message.chat.id,
                "start_h": sh, "start_m": sm,
                "end_h":   eh, "end_m":   em,
            }},
            upsert=True,
        )
        await message.reply_text(
            f"🌙 <b>Night Mode Enabled</b>\n"
            f"🌘 Closes at: {sh:02d}:{sm:02d} BST\n"
            f"☀️ Opens  at: {eh:02d}:{em:02d} BST",
            parse_mode=HTML,
        )
        asyncio.create_task(log_event(client,
            f"🌙 <b>Night Mode Enabled</b>  "
            f"{sh:02d}:{sm:02d}–{eh:02d}:{em:02d} BST  "
            f"📍 {message.chat.title or message.chat.id}"
        ))
        return

    await message.reply_text(
        "Unknown subcommand. Use <code>on HH:MM HH:MM</code>, <code>off</code>, or <code>status</code>.",
        parse_mode=HTML,
    )


_RESTRICTED_PERMS = {
    "can_send_messages": False, "can_send_media_messages": False,
    "can_send_polls": False, "can_add_web_page_previews": False,
    "can_change_info": False, "can_invite_users": False, "can_pin_messages": False,
}
_OPEN_PERMS = {
    "can_send_messages": True, "can_send_media_messages": True,
    "can_send_polls": True, "can_add_web_page_previews": True,
    "can_change_info": False, "can_invite_users": True, "can_pin_messages": False,
}


async def nightmode_loop(client: Client):
    print("[NIGHTMODE] Loop started.")
    while True:
        try:
            now_bst = datetime.now(BST)
            h, m    = now_bst.hour, now_bst.minute
            docs    = await nightmode_col.find({"enabled": True}).to_list(length=None)
            for doc in docs:
                chat_id = doc["chat_id"]
                sh, sm  = doc.get("start_h", 23), doc.get("start_m", 0)
                eh, em  = doc.get("end_h", 6),   doc.get("end_m", 0)

                is_night_time        = _in_night_window(h, m, sh, sm, eh, em)
                currently_restricted = doc.get("is_restricted", False)

                if is_night_time and not currently_restricted:
                    r = await bot_api("setChatPermissions", {
                        "chat_id": chat_id, "permissions": _RESTRICTED_PERMS
                    })
                    if r.get("ok") or "CHAT_NOT_MODIFIED" in str(r):
                        await nightmode_col.update_one(
                            {"chat_id": chat_id}, {"$set": {"is_restricted": True}}
                        )
                        await bot_api("sendMessage", {
                            "chat_id": chat_id, "parse_mode": "HTML",
                            "text": f"🌙 <b>Night Mode Activated</b>\n"
                                    f"Chat will reopen at {eh:02d}:{em:02d} BST.",
                        })
                        print(f"[NIGHTMODE] Closed chat={chat_id}")

                elif not is_night_time and currently_restricted:
                    r = await bot_api("setChatPermissions", {
                        "chat_id": chat_id, "permissions": _OPEN_PERMS
                    })
                    if r.get("ok") or "CHAT_NOT_MODIFIED" in str(r):
                        await nightmode_col.update_one(
                            {"chat_id": chat_id}, {"$set": {"is_restricted": False}}
                        )
                        await bot_api("sendMessage", {
                            "chat_id": chat_id, "parse_mode": "HTML",
                            "text": "☀️ <b>Night Mode Ended</b>\nChat is now open!",
                        })
                        print(f"[NIGHTMODE] Opened chat={chat_id}")
        except Exception as e:
            print(f"[NIGHTMODE] Loop error: {e}")
        await asyncio.sleep(60)


def _in_night_window(h: int, m: int, sh: int, sm: int, eh: int, em: int) -> bool:
    now_mins   = h  * 60 + m
    start_mins = sh * 60 + sm
    end_mins   = eh * 60 + em

    if start_mins <= end_mins:
        return start_mins <= now_mins < end_mins
    else:
        return now_mins >= start_mins or now_mins < end_mins
