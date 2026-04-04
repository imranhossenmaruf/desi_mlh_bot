import asyncio
from datetime import datetime, timezone, timedelta

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, ADMIN_ID, nightmode_col, app
from helpers import log_event, _is_admin_msg, bot_api

# Bangladesh Standard Time (UTC+6)
BD_TZ = timezone(timedelta(hours=6))

_RESTRICTED_PERMS = {
    "can_send_messages":         False,
    "can_send_audios":           False,
    "can_send_documents":        False,
    "can_send_photos":           False,
    "can_send_videos":           False,
    "can_send_video_notes":      False,
    "can_send_voice_notes":      False,
    "can_send_polls":            False,
    "can_send_other_messages":   False,
    "can_add_web_page_previews": False,
    "can_change_info":           False,
    "can_invite_users":          False,
    "can_pin_messages":          False,
}

_OPEN_PERMS = {
    "can_send_messages":         True,
    "can_send_audios":           True,
    "can_send_documents":        True,
    "can_send_photos":           True,
    "can_send_videos":           True,
    "can_send_video_notes":      True,
    "can_send_voice_notes":      True,
    "can_send_polls":            True,
    "can_send_other_messages":   True,
    "can_add_web_page_previews": True,
    "can_change_info":           False,
    "can_invite_users":          True,
    "can_pin_messages":          False,
}


def _parse_hhmm(text: str):
    try:
        parts = text.strip().split(":")
        if len(parts) == 2:
            h, m = int(parts[0]), int(parts[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h, m
    except Exception:
        pass
    return None


def _in_night_window(h: int, m: int, sh: int, sm: int, eh: int, em: int) -> bool:
    now_mins   = h  * 60 + m
    start_mins = sh * 60 + sm
    end_mins   = eh * 60 + em
    if start_mins == end_mins:
        return False
    if start_mins > end_mins:
        return now_mins >= start_mins or now_mins < end_mins
    return start_mins <= now_mins < end_mins


def _night_activate_msg(sh: int, sm: int, eh: int, em: int) -> str:
    return (
        "🌙━━━━━━━━━━━━━━━━━━━━━━🌙\n"
        "      🔒  NIGHT MODE ON  🔒\n"
        "🌙━━━━━━━━━━━━━━━━━━━━━━🌙\n\n"
        "⛔ Messaging is now <b>DISABLED</b>.\n"
        "🛏️ Please take a rest — see you tomorrow!\n\n"
        f"⏰ Active : <b>{sh:02d}:{sm:02d}</b> → <b>{eh:02d}:{em:02d}</b>\n"
        "🕐 Timezone: Bangladesh (UTC+6)\n\n"
        "😴 Good Night! Sweet Dreams! 🌃\n\n"
        "🌙━━━━━━━━━━━━━━━━━━━━━━🌙\n"
        "🤖 DESI MLH SYSTEM"
    )


def _night_deactivate_msg(sh: int, sm: int, eh: int, em: int) -> str:
    return (
        "☀️━━━━━━━━━━━━━━━━━━━━━━☀️\n"
        "     🔓  NIGHT MODE OFF  🔓\n"
        "☀️━━━━━━━━━━━━━━━━━━━━━━☀️\n\n"
        "✅ Messaging is now <b>ENABLED</b>!\n"
        "💬 Feel free to chat again.\n\n"
        f"⏰ Next night mode at: <b>{sh:02d}:{sm:02d}</b>\n"
        "🕐 Timezone: Bangladesh (UTC+6)\n\n"
        "🌅 Good Morning! Have a great day! 🌞\n\n"
        "☀️━━━━━━━━━━━━━━━━━━━━━━☀️\n"
        "🤖 DESI MLH SYSTEM"
    )


async def _set_permissions(chat_id: int, perms: dict) -> tuple[bool, str]:
    r = await bot_api("setChatPermissions", {
        "chat_id":    chat_id,
        "permissions": perms,
        "use_independent_chat_permissions": True,
    })
    if r.get("ok"):
        return True, ""
    desc = r.get("description", "unknown error")
    if "CHAT_NOT_MODIFIED" in desc:
        return True, ""
    return False, desc


@app.on_message(filters.command("nightmode") & filters.group)
async def nightmode_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        await message.reply_text(
            "❌ Only group admins can use this command.",
            parse_mode=HTML,
        )
        return

    args = message.command[1:]

    # ── NO ARGS → show usage + current status ────────────────────────────────
    if not args:
        now_bd = datetime.now(BD_TZ)
        doc    = await nightmode_col.find_one({"chat_id": message.chat.id})
        if doc and doc.get("enabled"):
            sh, sm = doc.get("start_h", 0), doc.get("start_m", 0)
            eh, em = doc.get("end_h", 0),   doc.get("end_m", 0)
            is_restricted = doc.get("is_restricted", False)
            state = "🔒 LOCKED (night mode active)" if is_restricted else "🔓 OPEN (daytime)"
            status_block = (
                f"\n\n📌 <b>Current Status:</b> {state}\n"
                f"🌙 Locks  : <b>{sh:02d}:{sm:02d}</b> (BD Time)\n"
                f"☀️ Unlocks: <b>{eh:02d}:{em:02d}</b> (BD Time)"
            )
        else:
            status_block = "\n\n📌 <b>Current Status:</b> ❌ Night Mode is disabled"

        await message.reply_text(
            "🌙 <b>Night Mode — Usage Guide</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "▶️ <b>Enable:</b>\n"
            "<code>/nightmode on HH:MM HH:MM</code>\n"
            "Example: <code>/nightmode on 23:00 06:00</code>\n"
            "(Locks at 11 PM → Unlocks at 6 AM)\n\n"
            "⏹ <b>Disable:</b>\n"
            "<code>/nightmode off</code>\n\n"
            "📊 <b>Status:</b>\n"
            "<code>/nightmode status</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🕐 <b>All times are Bangladesh Time (UTC+6)</b>\n"
            f"⏱ Current BD Time: <b>{now_bd.strftime('%I:%M %p')}</b>"
            f"{status_block}",
            parse_mode=HTML,
        )
        return

    sub = args[0].lower()

    # ── OFF ──────────────────────────────────────────────────────────────────
    if sub == "off":
        ok, err = await _set_permissions(message.chat.id, _OPEN_PERMS)
        await nightmode_col.update_one(
            {"chat_id": message.chat.id},
            {"$set": {"enabled": False, "is_restricted": False}},
            upsert=True,
        )
        if not ok:
            await message.reply_text(
                "⚠️ Night Mode disabled in DB, but chat permissions could not be changed.\n"
                f"❌ Reason: <code>{err}</code>\n\n"
                "💡 Make sure the bot is an admin with <b>Restrict Members</b> permission.",
                parse_mode=HTML,
            )
            return
        await message.reply_text(
            "☀️━━━━━━━━━━━━━━━━━━━━━━☀️\n"
            "   ✅  NIGHT MODE DISABLED\n"
            "☀️━━━━━━━━━━━━━━━━━━━━━━☀️\n\n"
            "💬 Chat is now open for everyone.\n"
            "🤖 DESI MLH SYSTEM",
        )
        asyncio.create_task(log_event(client,
            f"🌙 <b>Night Mode Disabled</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 Chat : {message.chat.title or message.chat.id}\n"
            f"👤 By   : {message.from_user.first_name} "
            f"(<code>{message.from_user.id}</code>)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
        return

    # ── STATUS ────────────────────────────────────────────────────────────────
    if sub == "status":
        doc    = await nightmode_col.find_one({"chat_id": message.chat.id})
        now_bd = datetime.now(BD_TZ)
        if not doc or not doc.get("enabled"):
            await message.reply_text(
                "📊 <b>Night Mode Status</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "❌ Night Mode is <b>disabled</b> for this group.\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏱ Current BD Time: <b>{now_bd.strftime('%I:%M %p')}</b>",
                parse_mode=HTML,
            )
        else:
            sh, sm = doc.get("start_h", 0), doc.get("start_m", 0)
            eh, em = doc.get("end_h", 0),   doc.get("end_m", 0)
            is_restricted = doc.get("is_restricted", False)
            state = "🔒 LOCKED — night mode is active" if is_restricted else "🔓 OPEN — daytime"
            await message.reply_text(
                "📊 <b>Night Mode Status</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Night Mode  : <b>Enabled</b>\n"
                f"🌙 Locks at   : <b>{sh:02d}:{sm:02d}</b> (BD Time)\n"
                f"☀️ Unlocks at : <b>{eh:02d}:{em:02d}</b> (BD Time)\n"
                f"📌 Right Now  : {state}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⏱ Current BD Time: <b>{now_bd.strftime('%I:%M %p')}</b>",
                parse_mode=HTML,
            )
        return

    # ── ON ────────────────────────────────────────────────────────────────────
    if sub == "on":
        if len(args) < 3:
            await message.reply_text(
                "❌ Please provide start and end times.\n\n"
                "✅ Correct format:\n"
                "<code>/nightmode on 23:00 06:00</code>\n"
                "(Locks at 11:00 PM → Unlocks at 06:00 AM)\n\n"
                "⚠️ All times must be in <b>Bangladesh Time (UTC+6)</b>",
                parse_mode=HTML,
            )
            return

        start_t = _parse_hhmm(args[1])
        end_t   = _parse_hhmm(args[2])

        if not start_t:
            await message.reply_text(
                f"❌ Invalid start time: <code>{args[1]}</code>\n"
                "✅ Format must be <code>HH:MM</code>  e.g. <code>23:00</code>",
                parse_mode=HTML,
            )
            return
        if not end_t:
            await message.reply_text(
                f"❌ Invalid end time: <code>{args[2]}</code>\n"
                "✅ Format must be <code>HH:MM</code>  e.g. <code>06:00</code>",
                parse_mode=HTML,
            )
            return

        sh, sm = start_t
        eh, em = end_t

        if (sh * 60 + sm) == (eh * 60 + em):
            await message.reply_text(
                "❌ Start time and end time cannot be the same!",
                parse_mode=HTML,
            )
            return

        await nightmode_col.update_one(
            {"chat_id": message.chat.id},
            {"$set": {
                "enabled":       True,
                "chat_id":       message.chat.id,
                "start_h":       sh, "start_m": sm,
                "end_h":         eh, "end_m":   em,
                "is_restricted": False,
            }},
            upsert=True,
        )

        # If we're currently in the night window, lock immediately
        now_bd = datetime.now(BD_TZ)
        h, m   = now_bd.hour, now_bd.minute
        in_night_now = _in_night_window(h, m, sh, sm, eh, em)

        if in_night_now:
            ok, _ = await _set_permissions(message.chat.id, _RESTRICTED_PERMS)
            if ok:
                await nightmode_col.update_one(
                    {"chat_id": message.chat.id}, {"$set": {"is_restricted": True}}
                )

        instant_note = (
            "⚡ <b>Currently within night window — chat locked immediately!</b>\n\n"
            if in_night_now else ""
        )

        await message.reply_text(
            "🌙━━━━━━━━━━━━━━━━━━━━━━🌙\n"
            "   ✅  NIGHT MODE ENABLED\n"
            "🌙━━━━━━━━━━━━━━━━━━━━━━🌙\n\n"
            f"🔒 Locks at   : <b>{sh:02d}:{sm:02d}</b>\n"
            f"🔓 Unlocks at : <b>{eh:02d}:{em:02d}</b>\n"
            "🕐 Timezone   : Bangladesh Time (UTC+6)\n\n"
            + instant_note
            + "🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        asyncio.create_task(log_event(client,
            f"🌙 <b>Night Mode Enabled</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 Chat : {message.chat.title or message.chat.id}\n"
            f"👤 By   : {message.from_user.first_name} "
            f"(<code>{message.from_user.id}</code>)\n"
            f"⏰ Time : {sh:02d}:{sm:02d} → {eh:02d}:{em:02d} (BD Time)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
        return

    # ── UNKNOWN ───────────────────────────────────────────────────────────────
    await message.reply_text(
        "❌ Unknown subcommand.\n\n"
        "Valid options:\n"
        "<code>/nightmode on HH:MM HH:MM</code>\n"
        "<code>/nightmode off</code>\n"
        "<code>/nightmode status</code>",
        parse_mode=HTML,
    )


async def nightmode_loop(client: Client):
    print("[NIGHTMODE] Loop started.")
    while True:
        try:
            now_bd = datetime.now(BD_TZ)
            h, m   = now_bd.hour, now_bd.minute
            docs   = await nightmode_col.find({"enabled": True}).to_list(length=None)

            for doc in docs:
                chat_id = doc["chat_id"]
                sh, sm  = doc.get("start_h", 23), doc.get("start_m", 0)
                eh, em  = doc.get("end_h",   6),  doc.get("end_m",  0)

                is_night_time        = _in_night_window(h, m, sh, sm, eh, em)
                currently_restricted = doc.get("is_restricted", False)

                if is_night_time and not currently_restricted:
                    ok, err = await _set_permissions(chat_id, _RESTRICTED_PERMS)
                    if ok:
                        await nightmode_col.update_one(
                            {"chat_id": chat_id}, {"$set": {"is_restricted": True}}
                        )
                        await bot_api("sendMessage", {
                            "chat_id":    chat_id,
                            "parse_mode": "HTML",
                            "text":       _night_activate_msg(sh, sm, eh, em),
                        })
                        print(f"[NIGHTMODE] Locked  chat={chat_id}  {h:02d}:{m:02d} BD")
                    else:
                        print(f"[NIGHTMODE] Lock failed  chat={chat_id}: {err}")

                elif not is_night_time and currently_restricted:
                    ok, err = await _set_permissions(chat_id, _OPEN_PERMS)
                    if ok:
                        await nightmode_col.update_one(
                            {"chat_id": chat_id}, {"$set": {"is_restricted": False}}
                        )
                        await bot_api("sendMessage", {
                            "chat_id":    chat_id,
                            "parse_mode": "HTML",
                            "text":       _night_deactivate_msg(sh, sm, eh, em),
                        })
                        print(f"[NIGHTMODE] Unlocked chat={chat_id}  {h:02d}:{m:02d} BD")
                    else:
                        print(f"[NIGHTMODE] Open failed  chat={chat_id}: {err}")

        except Exception as e:
            print(f"[NIGHTMODE] Loop error: {e}")

        await asyncio.sleep(60)
