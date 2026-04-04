import re
import asyncio
import aiohttp
from datetime import datetime, timedelta

from pyrogram import Client, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions

from config import (
    HTML, BOT_TOKEN, ADMIN_ID,
    users_col, settings_col, scheduled_col,
    broadcast_sessions,
    STATE_CUSTOMIZE,
)

BOT_USERNAME_CACHE: str = ""


async def get_bot_username(client: Client) -> str:
    import config
    if not config.BOT_USERNAME:
        me = await client.get_me()
        config.BOT_USERNAME = me.username or ""
    return config.BOT_USERNAME


async def get_log_channel() -> int | None:
    doc = await settings_col.find_one({"key": "log_channel"})
    return doc.get("chat_id") if doc else None


async def log_event(client: Client, text: str):
    try:
        cid = await get_log_channel()
        if cid:
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M") + " UTC"
            await bot_api("sendMessage", {
                "chat_id":    cid,
                "text":       f"🗒 <b>LOG</b> | {now}\n\n{text}",
                "parse_mode": "HTML",
            })
    except Exception as e:
        print(f"[LOG_EVENT] Failed: {e}")


def get_rank(ref_count: int) -> str:
    if ref_count >= 25: return "Platinum 💎"
    if ref_count >= 10: return "Gold 🥇"
    if ref_count >= 5:  return "Silver 🥈"
    return "Bronze 🥉"


def get_status(points: int) -> str:
    if points >= 100: return "Elite 🔥"
    if points >= 50:  return "VIP ⭐"
    if points >= 10:  return "Active ✅"
    return "New Member 👤"


async def save_user(user) -> bool:
    if await users_col.find_one({"user_id": user.id}):
        return False
    await users_col.insert_one({
        "user_id":       user.id,
        "username":      user.username,
        "first_name":    user.first_name,
        "last_name":     user.last_name,
        "language_code": getattr(user, "language_code", None),
        "ref_count":     0,
        "points":        0,
        "joined_at":     datetime.utcnow(),
    })
    return True


def parse_date(text: str):
    for fmt in ("%d.%m.%Y %H:%M", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
    return None


def parse_buttons(text: str):
    rows = []
    for line in text.strip().splitlines():
        row = []
        for part in line.split("&&"):
            part = part.strip()
            if "|" in part:
                bits = part.split("|", 1)
            elif " - " in part:
                bits = part.split(" - ", 1)
            else:
                bits = [part]
            if len(bits) == 2:
                label, url = bits[0].strip(), bits[1].strip()
                if label and url:
                    row.append({"text": label, "url": url})
        if row:
            rows.append(row)
    return rows or None


def has_media(message: Message) -> bool:
    return bool(
        message.photo or message.video or message.document
        or message.audio or message.voice or message.animation
        or message.sticker or message.video_note
    )


def audience_label(session: dict) -> str:
    if session["audience"] == "all":
        return "All Users"
    dt = session.get("join_after")
    return f"Joined after {dt.strftime('%d.%m.%Y %H:%M')}" if dt else "—"


async def count_targets(session: dict) -> int:
    if session["audience"] == "all":
        return await users_col.count_documents({})
    dt = session.get("join_after")
    return await users_col.count_documents({"joined_at": {"$gt": dt}}) if dt else 0


async def get_target_users(session: dict) -> list[dict]:
    if session["audience"] == "all":
        return await users_col.find({}, {"user_id": 1}).to_list(length=None)
    dt = session.get("join_after")
    if dt:
        return await users_col.find({"joined_at": {"$gt": dt}}, {"user_id": 1}).to_list(length=None)
    return []


async def delete_msg_safe(client: Client, chat_id: int, msg_id):
    if not msg_id:
        return
    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass


def kb_customize(extra_buttons=None, mode: str = "broadcast"):
    rows = []
    if extra_buttons:
        for row in extra_buttons:
            rows.append([InlineKeyboardButton(b["text"], url=b["url"]) for b in row])
    rows.append([
        InlineKeyboardButton("➕ Add Button",   callback_data="bc_add_button"),
        InlineKeyboardButton("🖼 Attach Media", callback_data="bc_attach_media"),
    ])
    rows.append([
        InlineKeyboardButton("💎 + Buy Premium", callback_data="bc_quick_buypremium"),
        InlineKeyboardButton("👤 + My Profile",  callback_data="bc_quick_profile"),
    ])
    if mode == "sbc":
        rows.append([
            InlineKeyboardButton("👁 Preview",          callback_data="bc_preview"),
            InlineKeyboardButton("⏰ Set Schedule",     callback_data="sbc_set_schedule"),
        ])
    else:
        rows.append([
            InlineKeyboardButton("👁 Preview",       callback_data="bc_preview"),
            InlineKeyboardButton("🚀 Send Now",      callback_data="bc_send_now"),
            InlineKeyboardButton("⏰ Schedule",      callback_data="bc_schedule"),
        ])
    if extra_buttons:
        rows.append([InlineKeyboardButton("🗑 Remove Buttons", callback_data="bc_remove_buttons")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel")])
    return InlineKeyboardMarkup(rows)


def kb_audience():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 All Users",       callback_data="bc_all"),
            InlineKeyboardButton("📅 Joined After...", callback_data="bc_join_after"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel")],
    ])


def kb_confirm():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm & Send", callback_data="bc_confirm_send"),
            InlineKeyboardButton("✏️ Edit Post",      callback_data="bc_edit_post"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="bc_cancel")],
    ])


async def refresh_preview(client: Client, session: dict):
    chat_id  = session["chat_id"]
    kb       = kb_customize(session.get("extra_buttons"), mode=session.get("mode", "broadcast"))
    msg_type = session.get("msg_type")
    text     = session.get("text") or None
    entities = session.get("entities") or None

    await delete_msg_safe(client, chat_id, session.get("preview_msg_id"))
    session["preview_msg_id"] = None

    sent = None
    try:
        if msg_type == "text":
            sent = await client.send_message(
                chat_id=chat_id,
                text=text or "(empty)",
                entities=entities,
                reply_markup=kb,
            )
        elif msg_type == "media":
            sent = await client.copy_message(
                chat_id=chat_id,
                from_chat_id=session["media_chat_id"],
                message_id=session["media_msg_id"],
                caption=text,
                caption_entities=entities,
                reply_markup=kb,
            )
    except Exception as e:
        print(f"refresh_preview error: {e}")

    if sent:
        session["preview_msg_id"] = sent.id


async def auto_delete(client: Client, chat_id: int, msg_id: int, delay: float = 5):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass


async def send_to_user(client: Client, uid: int, session: dict, reply_markup=None):
    msg_type = session.get("msg_type")
    text     = session.get("text") or None
    entities = session.get("entities") or None

    if msg_type == "text":
        return await client.send_message(
            chat_id=uid,
            text=text,
            entities=entities,
            reply_markup=reply_markup,
        )
    elif msg_type == "media":
        return await client.copy_message(
            chat_id=uid,
            from_chat_id=session["media_chat_id"],
            message_id=session["media_msg_id"],
            caption=text,
            caption_entities=entities,
            reply_markup=reply_markup,
        )
    return None


async def do_broadcast(client: Client, session: dict, status_msg: Message):
    targets  = await get_target_users(session)
    total    = len(targets)
    sent = failed = 0
    extra_kb = None
    if session.get("extra_buttons"):
        extra_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(b["text"], url=b["url"]) for b in row]
            for row in session["extra_buttons"]
        ])
    last_edit = asyncio.get_event_loop().time()

    async def refresh_status():
        pct = int((sent + failed) / total * 100) if total else 100
        await status_msg.edit_text(
            "📡 <b>Broadcasting in progress...</b>\n\n"
            f"👥 Target Users: <b>{total:,}</b>\n"
            f"✅ Sent: <b>{sent:,}</b>\n"
            f"❌ Failed: <b>{failed:,}</b>\n"
            f"⏳ Progress: <b>{pct}%</b>",
            parse_mode=HTML,
        )

    for doc in targets:
        uid = doc["user_id"]
        try:
            await send_to_user(client, uid, session, reply_markup=extra_kb)
            sent += 1
        except Exception:
            failed += 1

        now = asyncio.get_event_loop().time()
        if (sent + failed) % 10 == 0 or (now - last_edit) >= 5:
            try:
                await refresh_status()
                last_edit = now
            except Exception:
                pass
        await asyncio.sleep(0.05)

    try:
        await refresh_status()
    except Exception:
        pass

    aud = audience_label(session)
    await status_msg.edit_text(
        "✅ <b>Broadcast Sent Successfully!</b>\n\n"
        f"📨 Delivered to <b>{sent:,} users.</b>\n"
        f"❌ Failed / Blocked: <b>{failed:,}</b>\n\n"
        "Use /broadcast to start a new broadcast anytime.",
        parse_mode=HTML,
    )
    broadcast_sessions.pop(ADMIN_ID, None)
    await log_event(client,
        f"📢 <b>Broadcast Completed</b>\n"
        f"👥 Filter: {aud}\n"
        f"✅ Sent: <b>{sent:,}</b>  ❌ Failed: <b>{failed:,}</b>"
    )


async def bot_api(method: str, params: dict) -> dict:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=params, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    print(f"Bot API [{method}] failed: {data.get('description')}")
                return data
    except Exception as e:
        print(f"Bot API [{method}] error: {e}")
        return {"ok": False}


def _parse_duration(text: str):
    if not text:
        return None, "permanent"
    m = re.match(r"^(\d+)([DHMdhm])$", text.strip())
    if not m:
        return None, "permanent"
    amount = int(m.group(1))
    unit   = m.group(2).upper()
    if unit == "D":
        secs  = amount * 86400
        label = f"{amount} day(s)"
    elif unit == "H":
        secs  = amount * 3600
        label = f"{amount} hour(s)"
    else:
        secs  = amount * 60
        label = f"{amount} minute(s)"
    return secs, label


async def _resolve_target(client: Client, message: Message, args: list):
    if message.reply_to_message and message.reply_to_message.from_user:
        ru = message.reply_to_message.from_user
        return ru.id, ru.first_name or "User", args

    if args:
        first = args[0]
        if first.startswith("@"):
            uname = first.lstrip("@")
            try:
                user  = await client.get_users(uname)
                return user.id, user.first_name or "User", args[1:]
            except Exception:
                raise ValueError(f"❌ User <code>@{uname}</code> not found.")

        if first.lstrip("-").isdigit():
            uid = int(first)
            try:
                member = await client.get_chat_member(message.chat.id, uid)
                fname  = (member.user.first_name or "User") if member.user else "User"
            except Exception:
                fname = str(uid)
            return uid, fname, args[1:]

    raise ValueError(
        "❌ Reply to a message, or provide <code>@username</code> / user ID.\n"
        "Example: <code>/mute @username 2D</code>"
    )


async def _is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        m = await client.get_chat_member(chat_id, user_id)
        return m.status in (
            enums.ChatMemberStatus.OWNER,
            enums.ChatMemberStatus.ADMINISTRATOR,
        )
    except Exception:
        return False


async def _is_admin_msg(client: Client, message: Message) -> bool:
    if message.from_user is None:
        sc = getattr(message, "sender_chat", None)
        if sc and sc.id == message.chat.id:
            return True
        return False
    return await _is_admin(client, message.chat.id, message.from_user.id)


async def _auto_del(msg: Message, delay: int):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass


_FULL_PERMS = ChatPermissions(
    can_send_messages        = True,
    can_send_media_messages  = True,
    can_send_polls           = True,
    can_add_web_page_previews= True,
    can_change_info          = False,
    can_invite_users         = True,
    can_pin_messages         = False,
)

MAX_WARNS = 5
