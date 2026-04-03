import os
import re
import io
import csv
import time
import random
import asyncio
import aiohttp
import urllib.parse
from datetime import datetime, timedelta

from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery,
    ChatPermissions,
)
from motor.motor_asyncio import AsyncIOMotorClient

HTML = enums.ParseMode.HTML

mongo_client   = AsyncIOMotorClient(
    os.environ["MONGO_URI"],
    serverSelectionTimeoutMS=8000,
    connectTimeoutMS=8000,
    socketTimeoutMS=10000,
)
db             = mongo_client["telegram_bot"]
users_col      = db["users"]
videos_col     = db["channel_videos"]      # {channel_id, message_id, added_at}
vid_hist_col   = db["user_video_history"]  # {user_id, message_id, sent_at}
settings_col   = db["bot_settings"]        # {key, value, ...}
scheduled_col  = db["scheduled_broadcasts"] # {id, send_at, session, created_at}
nightmode_col  = db["nightmode_settings"]  # {chat_id, enabled, start, end}
shadowban_col  = db["shadowban"]           # {chat_id, user_id}
filters_col    = db["group_filters"]       # {chat_id, pattern, action, extra}
antiflood_col  = db["antiflood_settings"] # {chat_id, enabled, limit, seconds}
welcome_col    = db["welcome_messages"]   # {chat_id, enabled, text}
rules_col      = db["group_rules"]        # {chat_id, rules}

API_ID    = int(os.environ["TELEGRAM_API_ID"])
API_HASH  = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID  = int(os.environ["ADMIN_ID"])

VIDEO_CHANNEL     = -1002623940581   # source channel for /video
DAILY_VIDEO_LIMIT = 10               # max uses per user per UTC day
VIDEO_REPEAT_DAYS = 7                # days before a video can be re-sent

app = Client("telegram_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

BOT_USERNAME: str = ""   # filled on first need

REPLIES = {
    "hello":     "Hey there! 👋 How can I help you?",
    "hi":        "Hi! 😊 Type /help to see what I can do.",
    "help":      "Send me a message and I'll do my best to reply!\n\nCommands:\n/start — Register and get started\n/help — Show this message",
    "bye":       "Goodbye! See you next time 👋",
    "thanks":    "You're welcome! 😊",
    "thank you": "You're welcome! 😊",
}

# ─── Broadcast session ────────────────────────────────────────────────────────
# {
#   state          : STATE_* constant
#   audience       : "all" | "after_date"
#   join_after     : datetime | None
#
#   msg_type       : "text" | "media" | None
#   text           : str            ← text body or caption
#   entities       : list           ← Pyrogram entities for formatting
#   media_chat_id  : int | None     ← source chat for the media message
#   media_msg_id   : int | None     ← source message id for the media
#
#   extra_buttons  : [[{text,url}]] | None
#   preview_msg_id : int | None     ← the live preview message shown to admin
#   chat_id        : int            ← admin chat id
# }
broadcast_sessions: dict[int, dict] = {}

# Force-join link wizard sessions  {admin_id: {"state": ..., "pending_link": ..., "wizard_msg_id": ...}}
fj_sessions: dict[int, dict] = {}

# Anti-flood tracker: {(chat_id, user_id): [timestamp, ...]}
flood_tracker: dict[tuple, list] = {}

# Stores group welcome messages that should be deleted once the user taps
# "Start Bot" — keyed by user_id → (chat_id, message_id)
pending_welcome_msgs: dict[int, tuple[int, int]] = {}

STATE_AUDIENCE  = "audience"
STATE_JOIN_DATE = "join_date"
STATE_CONTENT   = "content"
STATE_CUSTOMIZE = "customize"
STATE_BUTTONS   = "buttons"
STATE_CONFIRM   = "confirm"
STATE_SCHEDULE  = "schedule"


# ═════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═════════════════════════════════════════════════════════════════════════════

async def get_bot_username(client: Client) -> str:
    global BOT_USERNAME
    if not BOT_USERNAME:
        me = await client.get_me()
        BOT_USERNAME = me.username or ""
    return BOT_USERNAME


async def get_log_channel() -> int | None:
    doc = await settings_col.find_one({"key": "log_channel"})
    return doc.get("chat_id") if doc else None


async def log_event(client: Client, text: str):
    """Send a log message to the configured log channel (if any)."""
    try:
        cid = await get_log_channel()
        if cid:
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M") + " UTC"
            await client.send_message(cid, f"🗒 <b>LOG</b> | {now}\n\n{text}", parse_mode=HTML)
    except Exception as e:
        print(f"[LOG_EVENT] Failed: {e}")


def get_rank(ref_count: int) -> str:
    if ref_count >= 25:  return "Platinum 💎"
    if ref_count >= 10:  return "Gold 🥇"
    if ref_count >= 5:   return "Silver 🥈"
    return "Bronze 🥉"


def get_status(points: int) -> str:
    if points >= 100:  return "Elite 🔥"
    if points >= 50:   return "VIP ⭐"
    if points >= 10:   return "Active ✅"
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
            # Try "|" first, then " - " as fallback separator
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


# ─── Keyboard: URL buttons on top, action buttons below ──────────────────────

def kb_customize(extra_buttons=None, mode: str = "broadcast"):
    rows = []
    # Show configured URL buttons first so admin can see them
    if extra_buttons:
        for row in extra_buttons:
            rows.append([InlineKeyboardButton(b["text"], url=b["url"]) for b in row])
    # Action buttons
    rows.append([
        InlineKeyboardButton("➕ Add Button",   callback_data="bc_add_button"),
        InlineKeyboardButton("🖼 Attach Media", callback_data="bc_attach_media"),
    ])
    if mode == "sbc":
        # Scheduled broadcast: no "Send Now", show "Set Schedule" only
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


# ─── Build and send the live preview (always shows latest state) ──────────────

async def refresh_preview(client: Client, session: dict):
    """Delete old preview and send a fresh copy reflecting the current state."""
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


# ─── Auto-delete helper ───────────────────────────────────────────────────────

async def auto_delete(client: Client, chat_id: int, msg_id: int, delay: float = 5):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass


# ─── Send broadcast message to one user ──────────────────────────────────────

async def send_to_user(client: Client, uid: int, session: dict, reply_markup=None):
    """Send the broadcast message to a user. Returns the sent Message or None."""
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


# ─── Broadcast engine ─────────────────────────────────────────────────────────

async def do_broadcast(client: Client, session: dict, status_msg: Message):
    targets   = await get_target_users(session)
    total     = len(targets)
    sent = failed = 0
    extra_kb  = None
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


# ═════════════════════════════════════════════════════════════════════════════
#  /start
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user = message.from_user
    is_new = not await users_col.find_one({"user_id": user.id})
    await save_user(user)
    name         = user.first_name or "Guest"
    bot_username = await get_bot_username(client)

    # Detect deep-link parameters
    start_param  = message.command[1] if len(message.command) > 1 else ""
    from_join    = start_param == "joined"
    from_video   = start_param == "video"

    # ── /start video — send a video immediately then return ───────────────────
    if from_video:
        not_joined = await _check_force_join(user.id)
        if not_joined:
            buttons = _fj_join_buttons(not_joined)
            await message.reply_text(
                "📢 JOIN REQUIRED\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"You must join all {len(not_joined)} channel(s) below\n"
                "before you can receive videos.\n\n"
                "1️⃣ Join each channel using the buttons\n"
                "2️⃣ Tap ✅ to verify and get your video\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🤖 DESI MLH SYSTEM",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return
        err = await _send_video_to_user(client, user.id)
        if err:
            await message.reply_text(err)
        return

    # ── Referral credit ──────────────────────────────────────────────────────
    # When someone opens the bot via t.me/bot?start={referrer_id} for the
    # first time, credit the referrer with +10 points and notify them.
    if is_new:
        asyncio.create_task(log_event(client,
            f"👤 <b>New User Joined</b>\n"
            f"Name: {user.first_name} {user.last_name or ''}\n"
            f"Username: @{user.username or 'none'}\n"
            f"🆔 <code>{user.id}</code>"
        ))

    if is_new and start_param.isdigit():
        ref_id = int(start_param)
        if ref_id != user.id:                          # no self-referral
            ref_doc = await users_col.find_one({"user_id": ref_id})
            if ref_doc:
                new_points = ref_doc.get("points", 0) + 10
                new_rc     = ref_doc.get("ref_count", 0) + 1
                await users_col.update_one(
                    {"user_id": ref_id},
                    {"$set": {"points": new_points, "ref_count": new_rc}},
                )
                notif = (
                    "🎉 New Referral Joined!\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "Congratulations! Someone just joined using your link.\n\n"
                    f"💰 You earned: +10 Points\n"
                    f"⭐ Current Balance: {new_points}\n\n"
                    "Keep sharing to earn more! 🚀"
                )
                asyncio.create_task(bot_api("sendMessage", {
                    "chat_id": ref_id,
                    "text":    notif,
                }))
                asyncio.create_task(log_event(client,
                    f"🔗 <b>Referral Credit</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👤 New User  : {user.first_name} {user.last_name or ''}\n"
                    f"🆔 User ID   : <code>{user.id}</code>\n"
                    f"🎯 Referred by: <code>{ref_id}</code>\n"
                    f"💰 Referrer pts: <b>{new_points}</b> (+10)\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🤖 DESI MLH SYSTEM"
                ))

    if from_join:
        # Delete the group welcome mention immediately (user has now seen it)
        if user.id in pending_welcome_msgs:
            grp_chat_id, grp_msg_id = pending_welcome_msgs.pop(user.id)
            asyncio.create_task(bot_api("deleteMessage", {
                "chat_id":    grp_chat_id,
                "message_id": grp_msg_id,
            }))

        # User came via the "Start Bot" button from join-request approval DM
        welcome_msg = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "✨🎬  𝑾𝑬𝑳𝑪𝑶𝑴𝑬 𝑻𝑶 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 🎬✨\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"🎉 Congrats {name}! You're officially in! 🎊\n\n"
            "You are now a verified member of\n"
            "𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 Video Community 🎥\n\n"
            "🔥 To watch videos, use the command:\n"
            "👉 /video\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📜 GROUP RULES\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "✅ Be respectful to all members\n"
            "✅ No spam or self-promotion\n"
            "✅ No adult/illegal content\n"
            "✅ Follow admin instructions\n"
            "⚠️ Rule violation = Instant remove\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🎬 Stay Active | Enjoy Watching\n"
            "— 🤖 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 𝑩𝒐𝒕\n"
            "━━━━━━━━━━━━━━━━━━━"
        )
    else:
        welcome_msg = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "✨🎬  𝑾𝑬𝑳𝑪𝑶𝑴𝑬 𝑻𝑶 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 🎬✨\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"👑 Welcome {name}! 👑\n"
            "You are now a member of 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 Video Community 🎥\n\n"
            "🔥 To watch videos, use the command:\n"
            "👉 /video\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📜 RULES\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "✅ Be respectful\n"
            "✅ No spam\n"
            "✅ No adult/illegal content\n"
            "✅ Follow admin rules\n"
            "⚠️ Rule violation = Instant remove\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🎬 Stay Active | Enjoy Watching\n"
            "— 🤖 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 𝑩𝒐𝒕\n"
            "━━━━━━━━━━━━━━━━━━━"
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Me To Group",
                              url=f"https://t.me/{bot_username}?startgroup=true")],
        [
            InlineKeyboardButton("💎 VIP",       url="https://t.me/your_vip_channel"),
            InlineKeyboardButton("📊 My Status", callback_data="status"),
        ],
        [
            InlineKeyboardButton("💰 Buy Premium", url="https://t.me/your_admin_bot"),
            InlineKeyboardButton("📤 Share Bot",
                                 url=f"https://t.me/share/url?url=https://t.me/{bot_username}"
                                     f"&text=Join%20this%20awesome%20bot%20%F0%9F%8E%AC"),
        ],
    ])
    await message.reply_text(welcome_msg, reply_markup=keyboard)


# ═════════════════════════════════════════════════════════════════════════════
#  My Status
# ═════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex("^status$"))
async def status_callback(client: Client, cq: CallbackQuery):
    user_id    = cq.from_user.id
    doc        = await users_col.find_one({"user_id": user_id})
    ref_count  = (doc or {}).get("ref_count", 0)
    points     = (doc or {}).get("points",    0)
    joined_at  = (doc or {}).get("joined_at")
    joined_str = joined_at.strftime("%d %b %Y") if joined_at else "—"
    bot_uname  = await get_bot_username(client)

    today      = datetime.utcnow().strftime("%Y-%m-%d")
    vid_date   = (doc or {}).get("video_date", "")
    vid_count  = (doc or {}).get("video_count", 0) if vid_date == today else 0

    last_daily  = (doc or {}).get("last_daily")
    now         = datetime.utcnow()
    if last_daily and (now - last_daily).total_seconds() < 86400:
        rem_secs   = 86400 - int((now - last_daily).total_seconds())
        hrs, r     = divmod(rem_secs, 3600)
        daily_line = f"📅 Daily Bonus: claimed (next in {hrs}h {r//60}m)"
    else:
        daily_line = "📅 Daily Bonus: available ✅  →  /daily"

    rank      = get_rank(ref_count)
    status    = get_status(points)
    ref_link  = f"https://t.me/{bot_uname}?start={user_id}"

    await cq.edit_message_text(
        "━━━━━━━━━━━━━━━━━━━\n"
        "👤 MY PROFILE — 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID       : {user_id}\n"
        f"📅 Joined   : {joined_str}\n\n"
        "📊 STATISTICS:\n"
        f"💰 Points   : {points}\n"
        f"👥 Referrals: {ref_count}\n"
        f"🏅 Rank     : {rank}\n"
        f"✨ Status   : {status}\n\n"
        f"📹 Videos Today: {vid_count}/{DAILY_VIDEO_LIMIT}\n"
        f"{daily_line}\n\n"
        f"🔗 Referral Link:\n{ref_link}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )
    await cq.answer()


# ═════════════════════════════════════════════════════════════════════════════
#  /help
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("help") & filters.private)
async def help_handler(client: Client, message: Message):
    is_admin = message.from_user.id == ADMIN_ID

    user_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "📋 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 — COMMANDS\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
        "👤 YOUR COMMANDS:\n"
        "/start  — Register & get welcome message\n"
        "/video  — 🎬 Get a random video\n"
        "/daily  — 📅 Claim daily +5 points\n"
        "/help   — 📋 Show this help message\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "💡 TIP: Use /video every day to enjoy\n"
        "new content. Invite friends to earn points!\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )

    admin_text = (
        "━━━━━━━━━━━━━━━━━━━\n"
        "📋 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 — ALL COMMANDS\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"

        "👤 USER COMMANDS:\n"
        "/start  — Register & welcome message\n"
        "/video  — 🎬 Get a random video\n"
        "/daily  — 📅 Claim daily +5 points\n"
        "/help   — 📋 Show this help message\n\n"

        "🛡️ GROUP MODERATION (reply to user):\n"
        "/mute [2D/3H/30M]    — 🔇 Mute a user\n"
        "/unmute [all]        — 🔊 Unmute a user\n"
        "/ban                 — 🚫 Ban a user\n"
        "/unban [all]         — ✅ Unban a user\n"
        "/kick                — 👢 Kick (remove, not ban)\n"
        "/warn [reason]       — ⚠️ Warn user (3 = auto-ban)\n"
        "/clearwarn           — 🗑️ Clear user warnings\n\n"

        "🌙 NIGHT MODE:\n"
        "/nightmode on HH:MM HH:MM — Enable (BST times)\n"
        "/nightmode off             — Disable night mode\n"
        "/nightmode status          — Show current schedule\n\n"

        "🕵️ SHADOW BAN:\n"
        "/shadowban           — Silently hide messages\n"
        "/unshadowban         — Remove shadow ban\n"
        "/shadowbans          — 📋 List shadow banned users\n"
        "/clearshadowbans     — 🧹 Clear all shadow bans\n\n"

        "⚙️ FILTERS (auto-action on keywords):\n"
        "/addfilter [word] [delete|warn|mute|ban] — Add filter\n"
        "/delfilter [#num | pattern]              — Delete filter\n"
        "/filters                                 — List all filters\n"
        "/clearfilters                            — Clear all filters\n\n"

        "🌊 ANTI-FLOOD:\n"
        "/antiflood on [msgs] [secs] [action] — Enable\n"
        "/antiflood off                        — Disable\n"
        "/antiflood status                     — Show settings\n\n"

        "👋 WELCOME MESSAGE:\n"
        "/welcome set [text]  — Set welcome (use {name}, {group})\n"
        "/welcome off         — Disable welcome\n"
        "/welcome status      — Show current message\n\n"

        "📜 GROUP RULES:\n"
        "/setrules [text]     — Set group rules\n"
        "/rules               — Show rules (anyone)\n"
        "/clearrules          — Clear rules\n\n"

        "👑 ADMIN ONLY (private chat):\n"
        "/stats                     — 📊 Full bot stats\n"
        "/user [id/@user]           — 👤 Look up a user\n"
        "/addpoints [id] [amt]      — 📈 Add points\n"
        "/removepoints [id] [amt]   — 📉 Remove points\n"
        "/setlimit @user unlimited  — ♾️ Unlimited videos\n"
        "/setlimit @user 20         — 🔢 Custom video limit\n"
        "/blockuser @user           — 🚫 Ban from bot\n"
        "/unblockuser @user         — ✅ Restore bot access\n"
        "/clearhistory @user        — 🗑️ Reset video history\n"
        "/export                    — 📁 Download users CSV\n\n"

        "📹 VIDEO LIBRARY (private chat):\n"
        "Forward video from channel   — 💾 Save to library\n"
        "/listvideos                  — 📋 List all videos\n"
        "/delvideo [#num | msg_id]    — 🗑️ Delete one video\n"
        "/clearvideos confirm         — 🧹 Wipe entire library\n\n"

        "📢 BROADCAST:\n"
        "/broadcast                   — 📢 Send to all users\n"
        "/sbc                         — 🎯 Scheduled broadcast\n"
        "/cancel                      — ❌ Cancel broadcast\n\n"

        "📡 FORCE-JOIN:\n"
        "/forcejoin on|off            — Toggle join check\n"
        "/forcejoinadd                — ➕ Add a channel\n"
        "/forcebuttondel              — 🗑️ Remove a channel\n"
        "/forcejoin list              — 📋 Show channels\n\n"

        "📝 LOG CHANNEL:\n"
        "/logchannel set [id]         — Set log channel\n"
        "/logchannel off              — Disable logging\n"
        "/logchannel status           — Show current channel\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )

    await message.reply_text(admin_text if is_admin else user_text)


# ═════════════════════════════════════════════════════════════════════════════
#  /daily  — claim +5 points every 24 hours
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("daily") & filters.private)
async def daily_handler(client: Client, message: Message):
    user_id = message.from_user.id
    now     = datetime.utcnow()

    doc        = await users_col.find_one({"user_id": user_id})

    if (doc or {}).get("bot_banned"):
        await message.reply_text(
            "🚫 ACCESS RESTRICTED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your access to this bot has been\n"
            "suspended by the admin.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        return

    last_daily = (doc or {}).get("last_daily")

    # ── Already claimed within 24 h ──────────────────────────────────────────
    if last_daily and (now - last_daily).total_seconds() < 86400:
        remaining = timedelta(seconds=86400) - (now - last_daily)
        hrs, rem  = divmod(int(remaining.total_seconds()), 3600)
        mins      = rem // 60
        await message.reply_text(
            "⏳ ALREADY CLAIMED TODAY\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Daily bonus already collected.\n\n"
            f"🕐 Next claim in: {hrs}h {mins}m\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        return

    # ── Give +5 points ────────────────────────────────────────────────────────
    current_points = (doc or {}).get("points", 0)
    new_points     = current_points + 5
    ref_count      = (doc or {}).get("ref_count", 0)
    rank           = get_rank(ref_count)
    status         = get_status(new_points)

    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"points": new_points, "last_daily": now}},
        upsert=True,
    )

    await message.reply_text(
        "🎉 DAILY BONUS CLAIMED!\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📅 Check-in Reward:  +5 Points\n"
        f"💰 New Balance  :  {new_points} Points\n"
        f"🏅 Rank         :  {rank}\n"
        f"✨ Status       :  {status}\n\n"
        "🔄 Come back in 24 hours!\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )
    print(f"[DAILY] user={user_id} claimed +5 pts → total={new_points}")


# ═════════════════════════════════════════════════════════════════════════════
#  ADMIN PANEL  — /stats  /user  /addpoints  /removepoints
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("stats") & filters.user(ADMIN_ID) & filters.private)
async def stats_handler(client: Client, message: Message):
    now        = datetime.utcnow()
    t0_today   = now.replace(hour=0, minute=0, second=0, microsecond=0)
    t0_7d      = t0_today - timedelta(days=7)
    t0_30d     = t0_today - timedelta(days=30)
    today_str  = now.strftime("%Y-%m-%d")

    # ── User counts ───────────────────────────────────────────────────────────
    total_users    = await users_col.count_documents({})
    new_today      = await users_col.count_documents({"joined_at": {"$gte": t0_today}})
    new_7d         = await users_col.count_documents({"joined_at": {"$gte": t0_7d}})
    new_30d        = await users_col.count_documents({"joined_at": {"$gte": t0_30d}})

    # ── Video counts ──────────────────────────────────────────────────────────
    total_vids     = await videos_col.count_documents({})
    vids_sent_today = await vid_hist_col.count_documents({"sent_at": {"$gte": t0_today}})
    vids_sent_7d   = await vid_hist_col.count_documents({"sent_at": {"$gte": t0_7d}})
    vid_users_today = await users_col.count_documents({"video_date": today_str})

    # ── Activity ──────────────────────────────────────────────────────────────
    daily_today    = await users_col.count_documents({"last_daily": {"$gte": t0_today}})

    await message.reply_text(
        "📊 BOT REPORT — 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👥 USER REGISTRATIONS:\n"
        f"📌 Total       : {total_users:,}\n"
        f"🆕 Today       : {new_today:,}\n"
        f"📅 Last 7 Days : {new_7d:,}\n"
        f"📆 Last 30 Days: {new_30d:,}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📹 VIDEO SYSTEM:\n"
        f"📦 Library     : {total_vids:,} videos\n"
        f"▶️  Sent Today  : {vids_sent_today:,} requests\n"
        f"▶️  Sent 7 Days : {vids_sent_7d:,} requests\n"
        f"👤 Users (today): {vid_users_today:,} users\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 TODAY'S ENGAGEMENT:\n"
        f"🎁 Daily Claims: {daily_today:,} users\n"
        f"🎬 Video Users : {vid_users_today:,} users\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {now.strftime('%d %b %Y  %H:%M')} UTC\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )


@app.on_message(filters.command("user") & filters.user(ADMIN_ID) & filters.private)
async def user_lookup_handler(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage:\n"
            "/user 123456789\n"
            "/user @username"
        )
        return

    query_str = args[0].lstrip("@")
    if query_str.isdigit():
        doc = await users_col.find_one({"user_id": int(query_str)})
    else:
        doc = await users_col.find_one({"username": query_str})

    if not doc:
        await message.reply_text("❌ User not found in the database.")
        return

    user_id   = doc.get("user_id")
    fname     = doc.get("first_name", "") or ""
    lname     = doc.get("last_name",  "") or ""
    uname     = doc.get("username")
    points    = doc.get("points",    0)
    ref_count = doc.get("ref_count", 0)
    joined_at = doc.get("joined_at")
    joined_str = joined_at.strftime("%d %b %Y  %H:%M") if joined_at else "—"

    today_str  = datetime.utcnow().strftime("%Y-%m-%d")
    vid_date   = doc.get("video_date", "")
    vid_count  = doc.get("video_count", 0) if vid_date == today_str else 0

    last_daily    = doc.get("last_daily")
    now           = datetime.utcnow()
    daily_status  = (
        "✅ Claimed today"
        if last_daily and (now - last_daily).total_seconds() < 86400
        else "⭕ Not claimed today"
    )

    full_name = f"{fname} {lname}".strip() or "Unknown"
    uname_str = f"@{uname}" if uname else "No username"
    rank      = get_rank(ref_count)
    status    = get_status(points)
    bot_uname = await get_bot_username(client)
    ref_link  = f"https://t.me/{bot_uname}?start={user_id}"

    await message.reply_text(
        "👤 USER PROFILE — 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Name     : {full_name}\n"
        f"🔗 Username : {uname_str}\n"
        f"🆔 ID       : {user_id}\n"
        f"📅 Joined   : {joined_str} UTC\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 STATISTICS:\n"
        f"💰 Points   : {points}\n"
        f"👥 Referrals: {ref_count}\n"
        f"🏅 Rank     : {rank}\n"
        f"✨ Status   : {status}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📹 TODAY'S USAGE:\n"
        f"🎬 Videos      : {vid_count}/{('♾️ Unlimited' if doc.get('video_limit') == -1 else doc.get('video_limit') or DAILY_VIDEO_LIMIT)}\n"
        f"🎁 Daily Bonus : {daily_status}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 {ref_link}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )


@app.on_message(filters.command("addpoints") & filters.user(ADMIN_ID) & filters.private)
async def addpoints_handler(client: Client, message: Message):
    await _change_points(message, positive=True)


@app.on_message(filters.command("removepoints") & filters.user(ADMIN_ID) & filters.private)
async def removepoints_handler(client: Client, message: Message):
    await _change_points(message, positive=False)


async def _change_points(message: Message, positive: bool):
    args = message.command[1:]
    cmd  = "addpoints" if positive else "removepoints"
    if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
        await message.reply_text(f"Usage: /{cmd} [user_id] [amount]")
        return

    target_id = int(args[0])
    amount    = int(args[1])
    if not positive:
        amount = -amount

    doc = await users_col.find_one({"user_id": target_id})
    if not doc:
        await message.reply_text("❌ User not found.")
        return

    old_points = doc.get("points", 0)
    new_points = max(0, old_points + amount)       # floor at 0
    await users_col.update_one(
        {"user_id": target_id},
        {"$set": {"points": new_points}}
    )

    sign  = "+" if amount >= 0 else ""
    emoji = "📈" if amount >= 0 else "📉"
    rank  = get_rank(doc.get("ref_count", 0))
    status = get_status(new_points)

    await message.reply_text(
        f"{'✅ POINTS ADDED' if amount >= 0 else '🔻 POINTS REMOVED'}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 User ID : {target_id}\n"
        f"💰 Before  : {old_points}\n"
        f"{emoji} Change  : {sign}{amount}\n"
        f"💰 After   : {new_points}\n"
        f"🏅 Rank    : {rank}\n"
        f"✨ Status  : {status}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )
    # Notify the user
    asyncio.create_task(bot_api("sendMessage", {
        "chat_id": target_id,
        "text": (
            f"{emoji} POINTS UPDATE\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{'Added' if amount >= 0 else 'Removed'}: {sign}{amount} Points\n"
            f"💰 New Balance: {new_points} Points\n"
            f"🏅 Rank: {rank}\n"
            f"✨ Status: {status}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        ),
    }))
    print(f"[ADMIN] points {sign}{amount} for user={target_id}  ({old_points}→{new_points})")
    action_label = "Points Added" if amount >= 0 else "Points Removed"
    asyncio.create_task(log_event(app,
        f"{'📈' if amount >= 0 else '📉'} <b>{action_label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 User ID : <code>{target_id}</code>\n"
        f"💰 Before  : {old_points}\n"
        f"{'+'  if amount >= 0 else ''}{amount} Change\n"
        f"💰 After   : <b>{new_points}</b>\n"
        f"🏅 Rank    : {rank}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


@app.on_message(filters.command("setlimit") & filters.user(ADMIN_ID) & filters.private)
async def setlimit_handler(client: Client, message: Message):
    """
    /setlimit @username|user_id unlimited|<number>
    Sets a per-user daily video limit.
    """
    args = message.command[1:]
    if len(args) < 2:
        await message.reply_text(
            "Usage:\n"
            "/setlimit @username unlimited\n"
            "/setlimit @username 20\n"
            "/setlimit 123456789 30"
        )
        return

    raw_target = args[0].lstrip("@")
    raw_limit  = args[1].lower().strip()

    # Resolve user
    if raw_target.isdigit():
        doc = await users_col.find_one({"user_id": int(raw_target)})
    else:
        doc = await users_col.find_one({"username": raw_target})

    if not doc:
        await message.reply_text("❌ User not found in the database.")
        return

    target_id = doc["user_id"]
    fname     = doc.get("first_name", "") or ""
    uname     = doc.get("username")
    mention   = f"@{uname}" if uname else fname or str(target_id)

    # Parse limit value
    if raw_limit in ("unlimited", "∞", "-1"):
        new_limit    = -1
        limit_label  = "♾️ Unlimited"
        notify_limit = "♾️ Unlimited"
    elif raw_limit.isdigit() and int(raw_limit) > 0:
        new_limit    = int(raw_limit)
        limit_label  = str(new_limit)
        notify_limit = str(new_limit)
    else:
        await message.reply_text(
            "❌ Invalid limit. Use a positive number or 'unlimited'.\n"
            "Examples: /setlimit @user 20  |  /setlimit @user unlimited"
        )
        return

    await users_col.update_one(
        {"user_id": target_id},
        {"$set": {"video_limit": new_limit}},
    )

    # Confirm to admin
    await message.reply_text(
        "✅ VIDEO LIMIT UPDATED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User    : {mention}\n"
        f"🆔 ID      : {target_id}\n"
        f"📹 New Limit: {limit_label} videos/day\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )

    # Notify the user
    asyncio.create_task(bot_api("sendMessage", {
        "chat_id": target_id,
        "text": (
            "🎬 YOUR VIDEO LIMIT UPDATED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📹 Daily Videos: {notify_limit}\n"
            "Enjoy your access! Use /video to start.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        ),
    }))
    print(f"[ADMIN] setlimit user={target_id} → {new_limit}")
    asyncio.create_task(log_event(client,
        f"🎬 <b>Video Limit Updated</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User      : {mention}\n"
        f"🆔 ID        : <code>{target_id}</code>\n"
        f"📹 New Limit : <b>{limit_label} videos/day</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


@app.on_message(filters.command("export") & filters.user(ADMIN_ID) & filters.private)
async def export_handler(client: Client, message: Message):
    """Export all users as a CSV file and send to admin."""
    wait_msg = await message.reply_text("⏳ Preparing CSV export...")

    try:
        all_users = await users_col.find({}).to_list(length=None)

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "user_id", "username", "first_name", "last_name",
            "points", "ref_count", "video_limit",
            "video_date", "video_count", "last_daily", "joined_at",
        ])

        for u in all_users:
            raw_lim  = u.get("video_limit")
            lim_str  = ("Unlimited" if raw_lim == -1
                        else str(raw_lim) if raw_lim else "Default")
            joined   = u.get("joined_at")
            last_d   = u.get("last_daily")
            writer.writerow([
                u.get("user_id", ""),
                u.get("username", ""),
                u.get("first_name", ""),
                u.get("last_name", ""),
                u.get("points", 0),
                u.get("ref_count", 0),
                lim_str,
                u.get("video_date", ""),
                u.get("video_count", 0),
                last_d.strftime("%Y-%m-%d %H:%M") if last_d else "",
                joined.strftime("%Y-%m-%d %H:%M") if joined else "",
            ])

        csv_bytes = buf.getvalue().encode("utf-8-sig")   # BOM for Excel
        bio       = io.BytesIO(csv_bytes)
        bio.name  = f"desi_mlh_users_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.csv"

        await wait_msg.delete()
        await message.reply_document(
            document=bio,
            caption=(
                f"📊 DESI MLH — User Export\n"
                f"👥 Total: {len(all_users):,} users\n"
                f"🕐 {datetime.utcnow().strftime('%d %b %Y  %H:%M')} UTC"
            ),
        )
        print(f"[ADMIN] CSV exported: {len(all_users)} users")

    except Exception as e:
        await wait_msg.edit_text(f"❌ Export failed: {e}")


# ═════════════════════════════════════════════════════════════════════════════
#  CLEAR VIDEO HISTORY  (/clearhistory @user | user_id)
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("clearhistory") & filters.user(ADMIN_ID) & filters.private)
async def clearhistory_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage:\n"
            "/clearhistory @username\n"
            "/clearhistory 123456789\n\n"
            "Deletes the user's video history so\n"
            "they can receive previously seen videos again."
        )
        return

    raw = args[0].lstrip("@")

    # Lookup user in DB
    doc = (
        await users_col.find_one({"user_id": int(raw)})
        if raw.isdigit()
        else await users_col.find_one({"username": raw})
    )
    if not doc:
        await message.reply_text("❌ User not found in database.")
        return

    target_id = doc["user_id"]
    fname     = doc.get("first_name", "") or ""
    uname     = doc.get("username")
    mention   = f"@{uname}" if uname else fname or str(target_id)

    # Count how many history entries exist before deleting
    count_before = await vid_hist_col.count_documents({"user_id": target_id})

    # Delete all history entries for this user
    result = await vid_hist_col.delete_many({"user_id": target_id})
    deleted = result.deleted_count

    await message.reply_text(
        "🗑️ VIDEO HISTORY CLEARED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User    : {mention}\n"
        f"🆔 ID      : <code>{target_id}</code>\n"
        f"🗑️ Deleted : {deleted} history entries\n\n"
        "✅ This user can now receive all\n"
        "videos again (including previously seen).\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    print(f"[ADMIN] Cleared video history for user={target_id} ({deleted} entries deleted)")


# ═════════════════════════════════════════════════════════════════════════════
#  FORCE JOIN SYSTEM  — multiple channels, wizard, /forcejoin add/remove/list
# ═════════════════════════════════════════════════════════════════════════════

async def _fj_doc() -> dict:
    return await settings_col.find_one({"key": "force_join"}) or {}

async def get_force_join() -> bool:
    return bool((await _fj_doc()).get("enabled", False))

async def get_fj_channels() -> list:
    return (await _fj_doc()).get("channels", [])

async def get_not_joined(user_id: int) -> list:
    """
    Return list of channel dicts the user has NOT joined.
    Uses Pyrogram's MTProto get_chat_member — works on public channels without
    the bot being admin. Private channels still require bot to be a member/admin.
    """
    from pyrogram.errors import (
        UserNotParticipant, ChatAdminRequired, PeerIdInvalid,
        ChannelInvalid, ChannelPrivate, UsernameInvalid, UsernameNotOccupied,
    )
    not_joined = []
    for ch in await get_fj_channels():
        raw_cid = ch["chat_id"]
        # Convert to int if it's a numeric ID, otherwise keep as string (@username)
        try:
            cid = int(raw_cid) if str(raw_cid).lstrip("-").isdigit() else str(raw_cid)
        except Exception:
            cid = str(raw_cid)

        # If the stored id is an invite link (https://...), skip — don't block users
        if isinstance(cid, str) and cid.startswith("http"):
            print(f"[FORCEJOIN] {ch['name']}: broken entry (invite link as ID) — skipping, not blocking user")
            continue

        try:
            member = await app.get_chat_member(cid, user_id)
            if member.status in (
                enums.ChatMemberStatus.LEFT,
                enums.ChatMemberStatus.BANNED,
            ):
                not_joined.append(ch)
                print(f"[FORCEJOIN] uid={user_id} channel={ch['name']} → status={member.status} BLOCKED")
            else:
                print(f"[FORCEJOIN] uid={user_id} channel={ch['name']} → status={member.status} OK ✅")
        except UserNotParticipant:
            not_joined.append(ch)
            print(f"[FORCEJOIN] uid={user_id} channel={ch['name']} → UserNotParticipant BLOCKED")
        except (ChannelPrivate, ChatAdminRequired):
            not_joined.append(ch)
            print(f"[FORCEJOIN] uid={user_id} channel={ch['name']} cid={cid!r} → BOT NOT ADMIN ⚠️")
        except (PeerIdInvalid, ChannelInvalid, UsernameInvalid, UsernameNotOccupied) as e:
            not_joined.append(ch)
            print(f"[FORCEJOIN] uid={user_id} channel={ch['name']} cid={cid!r} → INVALID ID: {e}")
        except Exception as e:
            not_joined.append(ch)
            print(f"[FORCEJOIN] uid={user_id} channel={ch['name']} cid={cid!r} → ERROR: {type(e).__name__}: {e}")
    return not_joined

async def _check_force_join(user_id: int) -> list:
    """Returns list of unjoined channel dicts, or [] if disabled / all joined."""
    if not await get_force_join():
        return []
    channels = await get_fj_channels()
    if not channels:
        return []
    return await get_not_joined(user_id)


def _fj_extract_chat_id(url: str) -> str:
    """
    Try to extract @username from a t.me link.
    https://t.me/channelname  →  @channelname
    https://t.me/+hash        →  (invite link, returned as-is)
    """
    import re
    m = re.match(r"https?://t\.me/([A-Za-z0-9_]{5,})$", url.strip())
    return f"@{m.group(1)}" if m else url.strip()


def _fj_parse_entry(part: str) -> dict | None:
    """
    Parse one channel entry from the wizard text.

    Supported formats:
      Name | https://t.me/username            → auto-extract chat_id
      Name | https://t.me/+invitelink         → link=URL, chat_id=URL
      Name | https://t.me/+invitelink | -1001 → link=invite, chat_id=numeric
      Name - https://t.me/+link - -1001       → same with dash separator
    """
    part = part.strip()
    # Split on "|" first, then fallback to " - "
    if "|" in part:
        bits = [b.strip() for b in part.split("|")]
    elif " - " in part:
        bits = [b.strip() for b in part.split(" - ")]
    else:
        return None

    if len(bits) < 2:
        return None

    name = bits[0]
    link = bits[1]
    if not name or not link:
        return None

    # 3-part format: Name | join_link | chat_id
    if len(bits) >= 3 and bits[2]:
        chat_id = bits[2]
    else:
        chat_id = _fj_extract_chat_id(link)

    return {"name": name, "link": link, "chat_id": chat_id}


def _fj_join_buttons(not_joined: list) -> list:
    """
    Build the inline keyboard rows for force-join channels.
    Puts 2 channel buttons per row. The final 'I've Joined' button gets its own row.
    Skips channels whose link is not a valid https:// URL to avoid BUTTON_URL_INVALID.
    """
    btns = []
    for ch in not_joined:
        link = ch.get("link", "")
        if link.startswith("https://") or link.startswith("http://"):
            btns.append(InlineKeyboardButton(f"📢 {ch['name']}", url=link))
        else:
            btns.append(InlineKeyboardButton(
                f"⚠️ {ch['name']}",
                callback_data="fj_no_link",
            ))

    # Group into rows of 2
    rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton("✅ I've Joined All Channels!", callback_data="fj_check")])
    return rows


async def _fj_show_add_card(reply_fn, doc: dict):
    """Send the initial Force-Join add-channel card (with 'Set Button' button)."""
    channels = doc.get("channels", [])
    lines    = [f"  {i}. {c['name']}" for i, c in enumerate(channels, 1)]
    ch_list  = "\n".join(lines) if lines else "  (none yet)"
    await reply_fn(
        "📢 <b>Force Join — Add Channel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Channels added so far: <b>{len(channels)}</b>\n"
        f"{ch_list}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Click <b>Set Button</b> to add a new channel.\n\n"
        "Type /cancel to cancel.",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📢 Set Button", callback_data="fj_set_button"),
            InlineKeyboardButton("❌ Cancel",     callback_data="fj_cancel"),
        ]]),
    )


def _fj_status_text(doc: dict) -> str:
    enabled  = "✅ ON" if doc.get("enabled") else "❌ OFF"
    channels = doc.get("channels", [])
    lines    = [
        "📢 FORCE JOIN STATUS",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Status   : {enabled}",
        f"Channels : {len(channels)}",
    ]
    for i, ch in enumerate(channels, 1):
        cid = ch.get("chat_id", "?")
        lines.append(f"  {i}. {ch['name']}")
        lines.append(f"     🔗 {ch['link']}")
        lines.append(f"     🆔 {cid}")
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Commands:",
        "/forcejoin on          — Enable",
        "/forcejoin off         — Disable",
        "/forcejoin add         — Add a channel (wizard)",
        "/forcejoin remove <n>  — Remove channel #n",
        "/forcejoin list        — Show this list",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🤖 DESI MLH SYSTEM",
    ]
    return "\n".join(lines)


@app.on_message(filters.command("forcejoin") & filters.user(ADMIN_ID) & filters.private)
async def forcejoin_cmd(client: Client, message: Message):
    args = message.command[1:]
    doc  = await _fj_doc()

    # ── /forcejoin  or  /forcejoin list ──────────────────────────────────────
    if not args or args[0].lower() == "list":
        await message.reply_text(_fj_status_text(doc))
        return

    sub = args[0].lower()

    # ── /forcejoin on ─────────────────────────────────────────────────────────
    if sub == "on":
        channels = doc.get("channels", [])
        if not channels:
            await message.reply_text(
                "⚠️ No channels added yet!\n\n"
                "Add at least one channel first:\n"
                "/forcejoin add"
            )
            return
        await settings_col.update_one(
            {"key": "force_join"}, {"$set": {"enabled": True}}, upsert=True
        )
        await message.reply_text(
            "✅ FORCE JOIN ENABLED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 {len(channels)} channel(s) configured.\n"
            "Users must join ALL channels\n"
            "before receiving any video.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        print("[FORCEJOIN] Enabled.")
        return

    # ── /forcejoin off ────────────────────────────────────────────────────────
    if sub == "off":
        await settings_col.update_one(
            {"key": "force_join"}, {"$set": {"enabled": False}}, upsert=True
        )
        await message.reply_text(
            "❌ FORCE JOIN DISABLED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Users can now get videos without\n"
            "joining any channel.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        print("[FORCEJOIN] Disabled.")
        return

    # ── /forcejoin remove <n> ─────────────────────────────────────────────────
    if sub == "remove":
        channels = doc.get("channels", [])
        if len(args) < 2 or not args[1].isdigit():
            await message.reply_text(
                f"Usage: /forcejoin remove <number>\n\n"
                f"Current channels:\n" +
                "\n".join(f"  {i}. {c['name']}" for i, c in enumerate(channels, 1))
                or "  (none)"
            )
            return
        idx = int(args[1]) - 1
        if idx < 0 or idx >= len(channels):
            await message.reply_text(f"❌ Invalid number. Choose 1–{len(channels)}.")
            return
        removed = channels.pop(idx)
        await settings_col.update_one(
            {"key": "force_join"}, {"$set": {"channels": channels}}, upsert=True
        )
        await message.reply_text(
            "🗑️ CHANNEL REMOVED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 {removed['name']}\n"
            f"🔗 {removed['link']}\n\n"
            f"Remaining: {len(channels)} channel(s)\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        return

    # ── /forcejoin add  (or alias: link) — show initial card with Set Button ──
    if sub in ("add", "link"):
        await _fj_show_add_card(message.reply_text, doc)
        return

    # ── /forcejoin clean — remove all broken (invite-link-as-ID) entries ────────
    if sub == "clean":
        channels = doc.get("channels", [])
        broken  = [ch for ch in channels if str(ch.get("chat_id","")).startswith("http")]
        if not broken:
            await message.reply_text("✅ No broken entries found. All channels have valid IDs.")
            return
        fixed = [ch for ch in channels if not str(ch.get("chat_id","")).startswith("http")]
        await settings_col.update_one(
            {"key": "force_join"}, {"$set": {"channels": fixed}}, upsert=True
        )
        names = "\n".join(f"  🗑️ {ch['name']}" for ch in broken)
        await message.reply_text(
            "🧹 <b>CLEANED UP BROKEN ENTRIES</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Removed {len(broken)} broken channel(s):\n{names}\n\n"
            f"✅ Remaining valid channels: {len(fixed)}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        print(f"[FORCEJOIN] Cleaned {len(broken)} broken entries.")
        return

    # ── /forcejoin fix — auto-resolve invite links to numeric IDs ─────────────
    if sub == "fix":
        channels = doc.get("channels", [])
        if not channels:
            await message.reply_text("❌ No channels configured.")
            return
        msg = await message.reply_text("🔄 Resolving all invite links...")
        results = []
        changed = False
        for ch in channels:
            cid = ch.get("chat_id", "")
            if isinstance(cid, str) and cid.startswith("http"):
                try:
                    resolved = await client.get_chat(cid)
                    old_cid = cid
                    ch["chat_id"] = str(resolved.id)
                    results.append(f"✅ {ch['name']}: {old_cid[:30]}… → {ch['chat_id']}")
                    changed = True
                    print(f"[FORCEJOIN FIX] '{ch['name']}' resolved: {ch['chat_id']}")
                except Exception as e:
                    results.append(f"⚠️ {ch['name']}: failed — {e}")
            else:
                results.append(f"ℹ️ {ch['name']}: already numeric ({cid})")
        if changed:
            await settings_col.update_one(
                {"key": "force_join"}, {"$set": {"channels": channels}}, upsert=True
            )
        result_text = "\n".join(results)
        await msg.edit_text(
            "🔧 FORCE JOIN FIX RESULT\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{result_text}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{'✅ Saved!' if changed else 'ℹ️ No changes needed.'}"
        )
        return

    # ── Unknown subcommand ────────────────────────────────────────────────────
    await message.reply_text(_fj_status_text(doc))


# ═════════════════════════════════════════════════════════════════════════════
#  VIDEO SYSTEM  (/video — 19 uses per user per UTC day)
# ═════════════════════════════════════════════════════════════════════════════

async def _send_video_to_user(client: Client, user_id: int) -> str:
    """
    Core logic: pick an unseen video, copy it to user_id, update counters.
    Returns an error string on failure, or "" on success.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Bot-ban check
    doc = await users_col.find_one({"user_id": user_id})
    if (doc or {}).get("bot_banned"):
        return (
            "🚫 ACCESS RESTRICTED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your access to this bot has been\n"
            "suspended by the admin.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )

    # Daily usage
    vid_date  = (doc or {}).get("video_date", "")
    vid_count = (doc or {}).get("video_count", 0) if vid_date == today else 0

    # Per-user custom limit: -1 = unlimited, positive int = custom, None = global
    raw_limit      = (doc or {}).get("video_limit")
    is_unlimited   = (raw_limit == -1)
    effective_limit = (
        None                    # skip limit check
        if is_unlimited
        else (raw_limit if isinstance(raw_limit, int) and raw_limit > 0
              else DAILY_VIDEO_LIMIT)
    )

    if effective_limit is not None and vid_count >= effective_limit:
        midnight  = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        remaining = midnight + timedelta(days=1) - datetime.utcnow()
        hrs, rem  = divmod(int(remaining.total_seconds()), 3600)
        mins      = rem // 60
        return (
            "⚠️ DAILY LIMIT REACHED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📹 You have used all {effective_limit} video requests for today.\n\n"
            f"🔄 Resets in: {hrs}h {mins}m\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )

    # Build pool: all videos minus those seen in the last VIDEO_REPEAT_DAYS days
    cutoff    = datetime.utcnow() - timedelta(days=VIDEO_REPEAT_DAYS)
    seen_docs = vid_hist_col.find({"user_id": user_id, "sent_at": {"$gte": cutoff}})
    seen_ids  = {d["message_id"] async for d in seen_docs}

    all_docs  = await videos_col.find({}).to_list(length=None)
    pool      = [d["message_id"] for d in all_docs if d["message_id"] not in seen_ids]

    if not all_docs:
        return (
            "📭 NO VIDEOS AVAILABLE\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "There are no videos in the library yet.\n\n"
            "📩 Please contact the admin to add videos.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )

    if not pool:
        return (
            "🎬 YOU'VE WATCHED EVERYTHING!\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "You have already watched all available\n"
            "videos within the last 7 days. 🙌\n\n"
            "🔄 New videos will be available soon.\n"
            "Try again later or contact the admin.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )

    msg_id = random.choice(pool)
    used   = vid_count + 1
    if is_unlimited:
        usage_line = f"📹 Today: {used}  |  Limit: ♾️ Unlimited"
    else:
        left       = effective_limit - used
        usage_line = f"📹 Today: {used}/{effective_limit}  |  Remaining: {left}"

    try:
        # Fetch the actual message from channel to get file_id
        chan_msg = await client.get_messages(VIDEO_CHANNEL, msg_id)
        if not chan_msg or not chan_msg.video:
            # Message deleted from channel — remove from DB and retry next call
            await videos_col.delete_one({"message_id": msg_id})
            print(f"[VIDEO] msg={msg_id} missing in channel, removed from DB")
            return "❌ Could not fetch the video. Please try again."

        caption = (
            "🎬 DESI MLH Video\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{usage_line}\n"
            "⏳ This video deletes in 15 minutes.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        resp = await bot_api("sendVideo", {
            "chat_id":             user_id,
            "video":               chan_msg.video.file_id,
            "caption":             caption,
            "has_spoiler":         True,
            "supports_streaming":  True,
            "protect_content":     True,
        })
        # Schedule auto-delete after 15 minutes
        sent_msg_id = resp.get("result", {}).get("message_id")
        if sent_msg_id:
            async def _del_video(mid=sent_msg_id, uid=user_id):
                await asyncio.sleep(900)
                try:
                    await bot_api("deleteMessage", {
                        "chat_id":    uid,
                        "message_id": mid,
                    })
                except Exception:
                    pass
            asyncio.create_task(_del_video())
    except Exception as e:
        print(f"[VIDEO] send_video error: {e}")
        return "❌ Could not fetch the video. Please try again."

    # Persist usage + history
    now = datetime.utcnow()
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"video_date": today, "video_count": used}},
        upsert=True,
    )
    await vid_hist_col.insert_one(
        {"user_id": user_id, "message_id": msg_id, "sent_at": now}
    )
    print(f"[VIDEO] msg={msg_id} → user={user_id}  ({used}/{DAILY_VIDEO_LIMIT})")
    # Log to channel
    user_doc = await users_col.find_one({"user_id": user_id})
    uname_str = f"@{user_doc.get('username')}" if user_doc and user_doc.get("username") else f"<code>{user_id}</code>"
    fname_str = (user_doc.get("first_name") or "") if user_doc else ""
    asyncio.create_task(log_event(client,
        f"🎬 <b>Video Watched</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User     : {fname_str} {uname_str}\n"
        f"🆔 ID       : <code>{user_id}</code>\n"
        f"🎞 Video ID : <code>{msg_id}</code>\n"
        f"📊 Today    : <b>{used}</b> video(s)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))
    return ""   # success


# ── Auto-save new channel videos ──────────────────────────────────────────────
@app.on_message(filters.channel)
async def channel_post_handler(client: Client, message: Message):
    if message.chat.id != VIDEO_CHANNEL:
        return
    if not message.video:
        return
    exists = await videos_col.find_one({"message_id": message.id})
    if not exists:
        await videos_col.insert_one({
            "channel_id": VIDEO_CHANNEL,
            "message_id": message.id,
            "added_at":   datetime.utcnow(),
        })
        total = await videos_col.count_documents({})
        print(f"[VIDEO] Auto-saved new video msg={message.id}  total={total}")
        asyncio.create_task(log_event(client,
            f"📥 <b>Video Auto-Saved</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎞 Video ID  : <code>{message.id}</code>\n"
            f"📺 Channel   : <code>{VIDEO_CHANNEL}</code>\n"
            f"📦 Total DB  : <b>{total} video(s)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))


# ── Admin forwards a video from the channel → save it ─────────────────────────
@app.on_message(
    filters.incoming & filters.private & filters.user(ADMIN_ID)
    & filters.forwarded & filters.video
)
async def admin_forward_video(client: Client, message: Message):
    fwd_chat = getattr(message, "forward_from_chat", None)
    fwd_id   = getattr(message, "forward_from_message_id", None)
    if not fwd_chat or fwd_chat.id != VIDEO_CHANNEL or not fwd_id:
        return  # not from the configured channel — ignore silently
    exists = await videos_col.find_one({"message_id": fwd_id})
    if not exists:
        await videos_col.insert_one({
            "channel_id": VIDEO_CHANNEL,
            "message_id": fwd_id,
            "added_at":   datetime.utcnow(),
        })
    total = await videos_col.count_documents({})
    await message.reply_text(
        f"✅ Video saved!\n📦 Total in library: {total}"
    )
    status_label = "Updated (already existed)" if exists else "New"
    asyncio.create_task(log_event(client,
        f"📤 <b>Video Saved by Admin</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎞 Video ID  : <code>{fwd_id}</code>\n"
        f"📺 Channel   : <code>{VIDEO_CHANNEL}</code>\n"
        f"🗃 Status    : {status_label}\n"
        f"📦 Total DB  : <b>{total} video(s)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


# ── /listvideos — show all video IDs ──────────────────────────────────────────
@app.on_message(filters.command("listvideos") & filters.user(ADMIN_ID) & filters.private)
async def listvideos_cmd(client: Client, message: Message):
    docs = await videos_col.find({}).sort("added_at", 1).to_list(length=None)
    total = len(docs)
    if not total:
        await message.reply_text("📭 No videos in the database.")
        return

    # Send in chunks of 50
    chunk_size = 50
    for start in range(0, total, chunk_size):
        chunk = docs[start:start + chunk_size]
        lines = [f"🎬 <b>Video Library</b> ({start+1}–{start+len(chunk)} of {total})\n"
                 f"━━━━━━━━━━━━━━━━━━━━━━"]
        for i, d in enumerate(chunk, start + 1):
            added = d.get("added_at", "")
            date_str = added.strftime("%Y-%m-%d") if hasattr(added, "strftime") else "?"
            lines.append(f"<b>{i}.</b> ID: <code>{d['message_id']}</code>  📅 {date_str}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━\n"
                     "🗑 Delete: <code>/delvideo &lt;id&gt;</code> or <code>/delvideo &lt;#number&gt;</code>\n"
                     "🧹 Clear all: <code>/clearvideos</code>")
        await message.reply_text("\n".join(lines), parse_mode=HTML)


# ── /delvideo — delete one video from DB ──────────────────────────────────────
@app.on_message(filters.command("delvideo") & filters.user(ADMIN_ID) & filters.private)
async def delvideo_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "⚙️ <b>Usage:</b>\n"
            "<code>/delvideo 1234567</code>  — by message ID\n"
            "<code>/delvideo #3</code>       — by list number (from /listvideos)\n\n"
            "Use <code>/listvideos</code> to see all IDs.",
            parse_mode=HTML,
        )
        return

    query = args[0]

    # By list number (#3 or 3 after checking)
    if query.startswith("#") or query.isdigit():
        num_str = query.lstrip("#")
        if not num_str.isdigit():
            await message.reply_text("❌ Invalid number. Example: <code>/delvideo #3</code>", parse_mode=HTML)
            return
        idx = int(num_str) - 1
        docs = await videos_col.find({}).sort("added_at", 1).to_list(length=None)
        if idx < 0 or idx >= len(docs):
            await message.reply_text(f"❌ No video #{num_str} found. Use /listvideos to see the list.")
            return
        doc   = docs[idx]
        msg_id = doc["message_id"]
    else:
        if not query.lstrip("-").isdigit():
            await message.reply_text("❌ Invalid ID. Provide a numeric message ID.", parse_mode=HTML)
            return
        msg_id = int(query)

    result = await videos_col.delete_one({"message_id": msg_id})
    if result.deleted_count:
        remaining = await videos_col.count_documents({})
        await message.reply_text(
            f"✅ <b>Video Deleted</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 Message ID : <code>{msg_id}</code>\n"
            f"📦 Remaining  : <b>{remaining} video(s)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        asyncio.create_task(log_event(client,
            f"🗑 <b>Video Deleted from DB</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎞 Video ID : <code>{msg_id}</code>\n"
            f"📦 Remaining: <b>{remaining} video(s)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
    else:
        await message.reply_text(
            f"❌ No video with ID <code>{msg_id}</code> found in database.\n"
            f"Use /listvideos to see all IDs.",
            parse_mode=HTML,
        )


# ── /clearvideos — wipe entire video library ──────────────────────────────────
@app.on_message(filters.command("clearvideos") & filters.user(ADMIN_ID) & filters.private)
async def clearvideos_cmd(client: Client, message: Message):
    args = message.command[1:]
    total = await videos_col.count_documents({})

    if total == 0:
        await message.reply_text("📭 Video library is already empty.")
        return

    # Require explicit confirmation: /clearvideos confirm
    if not args or args[0].lower() != "confirm":
        await message.reply_text(
            f"⚠️ <b>Confirm Clear All Videos</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"This will permanently delete <b>{total} video(s)</b>.\n\n"
            f"To confirm, send:\n"
            f"<code>/clearvideos confirm</code>",
            parse_mode=HTML,
        )
        return

    result = await videos_col.delete_many({})
    await message.reply_text(
        f"🧹 <b>Video Library Cleared</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Deleted <b>{result.deleted_count} video(s)</b>.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    asyncio.create_task(log_event(client,
        f"🧹 <b>Entire Video Library Cleared</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗑 Deleted   : <b>{result.deleted_count} video(s)</b>\n"
        f"📦 Remaining : <b>0</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


# ── /video in PRIVATE chat ────────────────────────────────────────────────────
@app.on_message(filters.command("video") & filters.private)
async def video_handler_private(client: Client, message: Message):
    user_id    = message.from_user.id
    not_joined = await _check_force_join(user_id)

    if not_joined:
        await message.reply_text(
            "📢 JOIN REQUIRED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"You must join all {len(not_joined)} channel(s) below\n"
            "before you can receive videos.\n\n"
            "1️⃣ Join each channel using the buttons\n"
            "2️⃣ Tap ✅ to verify and get your video\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
            reply_markup=InlineKeyboardMarkup(_fj_join_buttons(not_joined)),
        )
        return

    err = await _send_video_to_user(client, user_id)
    if err:
        await message.reply_text(err)


# ── /video in GROUP chat ──────────────────────────────────────────────────────
@app.on_message(filters.command("video") & filters.group)
async def video_handler_group(client: Client, message: Message):
    bot_uname = await get_bot_username(client)
    btn = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🎬 Watch Video Now!",
            url=f"https://t.me/{bot_uname}?start=video"
        )
    ]])
    grp_msg = await message.reply_text(
        "🎬 DESI MLH VIDEO\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📹 Click the button below to receive\n"
        "a video directly in the bot! 👇\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        reply_markup=btn,
    )
    # Auto-delete group prompt after 60 seconds
    async def _del():
        await asyncio.sleep(60)
        try:
            await grp_msg.delete()
            await message.delete()
        except Exception:
            pass
    asyncio.create_task(_del())


# ═════════════════════════════════════════════════════════════════════════════
#  ANTI-SPAM  (group links & forwarded messages)
# ═════════════════════════════════════════════════════════════════════════════

def _has_link(_, __, message: Message) -> bool:
    """True if the message contains a URL or text-link entity."""
    if not message.entities:
        return False
    link_types = {enums.MessageEntityType.URL, enums.MessageEntityType.TEXT_LINK}
    return any(e.type in link_types for e in message.entities)

has_link_filter = filters.create(_has_link)


@app.on_message(
    filters.incoming & filters.group
    & (filters.forwarded | has_link_filter)
)
async def anti_spam_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return  # channel post or anonymous admin

    # ── Skip group admins/owner ───────────────────────────────────────────
    try:
        member = await client.get_chat_member(message.chat.id, user.id)
        if member.status in (
            enums.ChatMemberStatus.OWNER,
            enums.ChatMemberStatus.ADMINISTRATOR,
        ):
            return
    except Exception:
        pass  # if we can't check, proceed with deletion

    # ── Delete the offending message ──────────────────────────────────────
    try:
        await message.delete()
        print(f"[SPAM] Deleted message from {user.id} in {message.chat.id}")
    except Exception as e:
        print(f"[SPAM] Delete failed: {e}")

    # ── Determine violation type ──────────────────────────────────────────
    if message.forward_date or message.forward_from or message.forward_from_chat:
        violation = "forwarded message"
        vio_icon  = "📨"
    else:
        violation = "link / URL"
        vio_icon  = "🔗"

    # ── HTML mention (works even without a username) ──────────────────────
    name    = user.first_name or "User"
    mention = f'<a href="tg://user?id={user.id}">{name}</a>'

    warn_text = (
        "⚠️ SPAM WARNING ⚠️\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {mention}\n"
        f"{vio_icon} Violation: Sending a {violation}\n\n"
        "❌ This content has been removed.\n"
        "🔁 Repeated violations may result in a mute or ban.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )

    try:
        warn_msg = await client.send_message(
            message.chat.id,
            warn_text,
            parse_mode=HTML,
        )
        # Auto-delete warning after 40 seconds
        async def _del_warn(msg):
            await asyncio.sleep(40)
            try:
                await msg.delete()
            except Exception:
                pass

        asyncio.create_task(_del_warn(warn_msg))
    except Exception as e:
        print(f"[SPAM] Warning send failed: {e}")


# ═════════════════════════════════════════════════════════════════════════════
#  MUTE / BAN  (admin-only group commands)
# ═════════════════════════════════════════════════════════════════════════════

def _parse_duration(text: str):
    """
    Parse '7D' → 604800 s, '3H' → 10800 s, '30M' → 1800 s.
    Returns (seconds: int, label: str) or (None, 'permanent') if no match.
    """
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
    else:  # M
        secs  = amount * 60
        label = f"{amount} minute(s)"
    return secs, label


async def _resolve_target(client: Client, message: Message, args: list):
    """
    Return (user_id, first_name, remaining_args) from:
      • reply           → replied user
      • @username       → resolved via Telegram
      • numeric user_id → direct lookup
    Raises ValueError with a human-readable message on failure.
    """
    # Priority 1: reply to a message
    if message.reply_to_message and message.reply_to_message.from_user:
        ru = message.reply_to_message.from_user
        return ru.id, ru.first_name or "User", args

    if args:
        first = args[0]

        # Priority 2: @username
        if first.startswith("@"):
            uname = first.lstrip("@")
            try:
                user  = await client.get_users(uname)
                return user.id, user.first_name or "User", args[1:]
            except Exception:
                raise ValueError(f"❌ User <code>@{uname}</code> not found.")

        # Priority 3: numeric user_id
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
    """Check if the message sender is an admin.
    Handles both regular users and anonymous admins (sender_chat == chat)."""
    # Anonymous admin posts as the group itself — always admin
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


# ── /mute ─────────────────────────────────────────────────────────────────────
@app.on_message(filters.command("mute") & filters.group)
async def mute_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /mute.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]
    try:
        target_id, target_name, rest = await _resolve_target(client, message, args)
    except ValueError as e:
        r = await message.reply_text(str(e), parse_mode=HTML)
        return asyncio.create_task(_auto_del(r, 20))

    if await _is_admin(client, message.chat.id, target_id):
        r = await message.reply_text("❌ Cannot mute an admin or owner.")
        return asyncio.create_task(_auto_del(r, 15))

    secs, dur_label = _parse_duration(rest[0] if rest else "")
    until = datetime.utcnow() + timedelta(seconds=secs) if secs else datetime(2038, 1, 19)

    try:
        await client.restrict_chat_member(
            message.chat.id,
            target_id,
            ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except Exception as e:
        r = await message.reply_text(f"❌ Failed to mute: {e}")
        return asyncio.create_task(_auto_del(r, 20))

    mention = f'<a href="tg://user?id={target_id}">{target_name}</a>'
    result  = await message.reply_text(
        "🔇 USER MUTED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User   : {mention}\n"
        f"🆔 ID     : <code>{target_id}</code>\n"
        f"⏱ Duration: {dur_label}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    print(f"[MOD] Muted {target_id} for {dur_label} in {message.chat.id}")
    asyncio.create_task(_auto_del(result, 40))
    asyncio.create_task(log_event(client,
        f"🔇 <b>User Muted</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User     : {mention}\n"
        f"🆔 ID       : <code>{target_id}</code>\n"
        f"⏱ Duration : {dur_label}\n"
        f"💬 Group    : <code>{message.chat.id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


# shared full permissions used for unmuting
_FULL_PERMS = ChatPermissions(
    can_send_messages        = True,
    can_send_media_messages  = True,
    can_send_polls           = True,
    can_add_web_page_previews= True,
    can_change_info          = False,
    can_invite_users         = True,
    can_pin_messages         = False,
)


# ── /unmute ───────────────────────────────────────────────────────────────────
@app.on_message(filters.command("unmute") & filters.group)
async def unmute_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if not await _is_admin(client, chat_id, message.from_user.id):
        r = await message.reply_text("❌ Only admins can use /unmute.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]

    # ── /unmute all ───────────────────────────────────────────────────────────
    if args and args[0].lower() == "all":
        prog = await message.reply_text("⏳ Unmuting all restricted members...")
        count = 0
        async for member in client.get_chat_members(
            chat_id, filter=enums.ChatMembersFilter.RESTRICTED
        ):
            try:
                await client.restrict_chat_member(
                    chat_id, member.user.id, _FULL_PERMS
                )
                count += 1
            except Exception:
                pass
        result = await prog.edit_text(
            "🔊 ALL MEMBERS UNMUTED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Removed mute from {count} member(s).\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
        )
        print(f"[MOD] Unmuted all ({count}) in {chat_id}")
        return asyncio.create_task(_auto_del(result, 40))

    # ── single user ───────────────────────────────────────────────────────────
    try:
        target_id, target_name, _ = await _resolve_target(client, message, args)
    except ValueError as e:
        r = await message.reply_text(str(e), parse_mode=HTML)
        return asyncio.create_task(_auto_del(r, 20))

    try:
        await client.restrict_chat_member(chat_id, target_id, _FULL_PERMS)
    except Exception as e:
        r = await message.reply_text(f"❌ Failed to unmute: {e}")
        return asyncio.create_task(_auto_del(r, 20))

    mention = f'<a href="tg://user?id={target_id}">{target_name}</a>'
    result  = await message.reply_text(
        "🔊 USER UNMUTED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {mention}\n"
        f"🆔 ID  : <code>{target_id}</code>\n"
        "✅ Permissions restored.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    print(f"[MOD] Unmuted {target_id} in {chat_id}")
    asyncio.create_task(_auto_del(result, 40))
    asyncio.create_task(log_event(client,
        f"🔊 <b>User Unmuted</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User  : {mention}\n"
        f"🆔 ID    : <code>{target_id}</code>\n"
        f"💬 Group : <code>{chat_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


# ── /ban ──────────────────────────────────────────────────────────────────────
@app.on_message(filters.command("ban") & filters.group)
async def ban_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /ban.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]
    try:
        target_id, target_name, _ = await _resolve_target(client, message, args)
    except ValueError as e:
        r = await message.reply_text(str(e), parse_mode=HTML)
        return asyncio.create_task(_auto_del(r, 20))

    if await _is_admin(client, message.chat.id, target_id):
        r = await message.reply_text("❌ Cannot ban an admin or owner.")
        return asyncio.create_task(_auto_del(r, 15))

    try:
        await client.ban_chat_member(message.chat.id, target_id)
    except Exception as e:
        r = await message.reply_text(f"❌ Failed to ban: {e}")
        return asyncio.create_task(_auto_del(r, 20))

    mention = f'<a href="tg://user?id={target_id}">{target_name}</a>'
    result  = await message.reply_text(
        "🚫 USER BANNED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {mention}\n"
        f"🆔 ID  : <code>{target_id}</code>\n"
        "❌ Removed from group permanently.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    print(f"[MOD] Banned {target_id} from {message.chat.id}")
    asyncio.create_task(_auto_del(result, 40))
    asyncio.create_task(log_event(client,
        f"🚫 <b>User Banned from Group</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User  : {mention}\n"
        f"🆔 ID    : <code>{target_id}</code>\n"
        f"💬 Group : <code>{message.chat.id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


# ── /unban ────────────────────────────────────────────────────────────────────
@app.on_message(filters.command("unban") & filters.group)
async def unban_cmd(client: Client, message: Message):
    chat_id = message.chat.id
    if not await _is_admin(client, chat_id, message.from_user.id):
        r = await message.reply_text("❌ Only admins can use /unban.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]

    # ── /unban all ────────────────────────────────────────────────────────────
    if args and args[0].lower() == "all":
        prog = await message.reply_text("⏳ Unbanning all banned members...")
        count = 0
        async for member in client.get_chat_members(
            chat_id, filter=enums.ChatMembersFilter.BANNED
        ):
            try:
                await client.unban_chat_member(chat_id, member.user.id)
                count += 1
            except Exception:
                pass
        result = await prog.edit_text(
            "✅ ALL MEMBERS UNBANNED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Removed ban from {count} member(s).\n"
            "They can now rejoin the group.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
        )
        print(f"[MOD] Unbanned all ({count}) in {chat_id}")
        return asyncio.create_task(_auto_del(result, 40))

    # ── single user ───────────────────────────────────────────────────────────
    try:
        target_id, target_name, _ = await _resolve_target(client, message, args)
    except ValueError as e:
        r = await message.reply_text(str(e), parse_mode=HTML)
        return asyncio.create_task(_auto_del(r, 20))

    try:
        await client.unban_chat_member(chat_id, target_id)
    except Exception as e:
        r = await message.reply_text(f"❌ Failed to unban: {e}")
        return asyncio.create_task(_auto_del(r, 20))

    mention = f'<a href="tg://user?id={target_id}">{target_name}</a>'
    result  = await message.reply_text(
        "✅ USER UNBANNED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {mention}\n"
        f"🆔 ID  : <code>{target_id}</code>\n"
        "✅ Can rejoin the group now.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    print(f"[MOD] Unbanned {target_id} in {chat_id}")
    asyncio.create_task(_auto_del(result, 40))
    asyncio.create_task(log_event(client,
        f"✅ <b>User Unbanned</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User  : {mention}\n"
        f"🆔 ID    : <code>{target_id}</code>\n"
        f"💬 Group : <code>{chat_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


# ═════════════════════════════════════════════════════════════════════════════
#  WARN SYSTEM  (/warn  /clearwarn)
# ═════════════════════════════════════════════════════════════════════════════

MAX_WARNS = 3   # auto-ban after this many warnings

@app.on_message(filters.command("warn") & filters.group)
async def warn_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /warn.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]
    try:
        target_id, target_name, rest = await _resolve_target(client, message, args)
    except ValueError as e:
        r = await message.reply_text(str(e), parse_mode=HTML)
        return asyncio.create_task(_auto_del(r, 20))

    # Never warn an admin
    if await _is_admin(client, message.chat.id, target_id):
        r = await message.reply_text("❌ Cannot warn a group admin.")
        return asyncio.create_task(_auto_del(r, 15))

    reason = " ".join(rest) if rest else "No reason given"

    # Update warn count in DB
    doc       = await users_col.find_one({"user_id": target_id})
    old_warns = (doc or {}).get("warn_count", 0)
    new_warns = old_warns + 1

    await users_col.update_one(
        {"user_id": target_id},
        {"$set":  {"warn_count": new_warns}},
        upsert=True,
    )

    mention = f'<a href="tg://user?id={target_id}">{target_name}</a>'
    chat_id = message.chat.id

    if new_warns >= MAX_WARNS:
        # Auto-ban
        await users_col.update_one(
            {"user_id": target_id},
            {"$set": {"warn_count": 0, "bot_banned": True}},
        )
        try:
            await client.ban_chat_member(chat_id, target_id)
        except Exception:
            pass
        result = await message.reply_text(
            "🚫 USER BANNED — MAX WARNINGS\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 User   : {mention}\n"
            f"🆔 ID     : <code>{target_id}</code>\n"
            f"⚠️ Warnings: {new_warns}/{MAX_WARNS}\n"
            f"📝 Reason : {reason}\n\n"
            "User reached 3 warnings → Auto-banned.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        # Notify the banned user via DM
        asyncio.create_task(bot_api("sendMessage", {
            "chat_id": target_id,
            "text": (
                "🚫 YOU HAVE BEEN BANNED\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ You received {MAX_WARNS} warnings.\n"
                f"📝 Last reason: {reason}\n\n"
                "You have been removed from the group\n"
                "and your bot access has been restricted.\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🤖 DESI MLH SYSTEM"
            ),
        }))
        print(f"[WARN] Auto-banned user={target_id} after {MAX_WARNS} warns")
        asyncio.create_task(log_event(client,
            f"🚫 <b>Auto-Banned (Max Warnings)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 User     : {mention}\n"
            f"🆔 ID       : <code>{target_id}</code>\n"
            f"⚠️ Warnings : {new_warns}/{MAX_WARNS}\n"
            f"📝 Reason   : {reason}\n"
            f"💬 Group    : <code>{chat_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
    else:
        bars  = "🔴" * new_warns + "⚪" * (MAX_WARNS - new_warns)
        result = await message.reply_text(
            "⚠️ WARNING ISSUED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 User     : {mention}\n"
            f"🆔 ID       : <code>{target_id}</code>\n"
            f"⚠️ Warnings : {bars} {new_warns}/{MAX_WARNS}\n"
            f"📝 Reason   : {reason}\n\n"
            f"{'1 more warning = AUTO-BAN ‼️' if new_warns == MAX_WARNS - 1 else f'{MAX_WARNS - new_warns} warning(s) left before auto-ban.'}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        # Notify user via DM
        asyncio.create_task(bot_api("sendMessage", {
            "chat_id": target_id,
            "text": (
                "⚠️ YOU RECEIVED A WARNING\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ Warnings : {new_warns}/{MAX_WARNS}\n"
                f"📝 Reason   : {reason}\n\n"
                f"{'⚠️ One more warning and you will be BANNED!' if new_warns == MAX_WARNS - 1 else f'{MAX_WARNS - new_warns} warning(s) left.'}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🤖 DESI MLH SYSTEM"
            ),
        }))
        print(f"[WARN] user={target_id}  warns={new_warns}/{MAX_WARNS}  reason={reason}")
        asyncio.create_task(log_event(client,
            f"⚠️ <b>Warning Issued</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 User     : {mention}\n"
            f"🆔 ID       : <code>{target_id}</code>\n"
            f"⚠️ Count    : {new_warns}/{MAX_WARNS}\n"
            f"📝 Reason   : {reason}\n"
            f"💬 Group    : <code>{chat_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))

    asyncio.create_task(_auto_del(result, 40))


@app.on_message(filters.command("clearwarn") & filters.group)
async def clearwarn_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /clearwarn.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]
    try:
        target_id, target_name, _ = await _resolve_target(client, message, args)
    except ValueError as e:
        r = await message.reply_text(str(e), parse_mode=HTML)
        return asyncio.create_task(_auto_del(r, 20))

    await users_col.update_one(
        {"user_id": target_id},
        {"$set": {"warn_count": 0}},
        upsert=True,
    )
    mention = f'<a href="tg://user?id={target_id}">{target_name}</a>'
    result  = await message.reply_text(
        "✅ WARNINGS CLEARED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User     : {mention}\n"
        f"🆔 ID       : <code>{target_id}</code>\n"
        "⚠️ Warnings : ⚪⚪⚪ 0/3\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    print(f"[WARN] Cleared warns for user={target_id}")
    asyncio.create_task(_auto_del(result, 40))


# ═════════════════════════════════════════════════════════════════════════════
#  BOT BAN SYSTEM  (/blockuser  /unblockuser)  — admin private chat
# ═════════════════════════════════════════════════════════════════════════════

async def _resolve_user_private(args: list) -> tuple:
    """
    Resolve a user from private-chat admin command args.
    Returns (user_id_or_None, raw_str).
    """
    if not args:
        return None, ""
    raw = args[0].lstrip("@")
    return (int(raw) if raw.isdigit() else None), raw


@app.on_message(filters.command("blockuser") & filters.user(ADMIN_ID) & filters.private)
async def blockuser_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage:\n/blockuser @username\n/blockuser 123456789"
        )
        return

    raw = args[0].lstrip("@")
    doc = (
        await users_col.find_one({"user_id": int(raw)})
        if raw.isdigit()
        else await users_col.find_one({"username": raw})
    )
    if not doc:
        await message.reply_text("❌ User not found in database.")
        return

    target_id = doc["user_id"]
    fname     = doc.get("first_name", "") or ""
    uname     = doc.get("username")
    mention   = f"@{uname}" if uname else fname or str(target_id)

    await users_col.update_one(
        {"user_id": target_id},
        {"$set": {"bot_banned": True}},
    )
    await message.reply_text(
        "🚫 USER BOT-BANNED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User : {mention}\n"
        f"🆔 ID   : {target_id}\n\n"
        "❌ /video, /daily & all bot features blocked.\n"
        "Use /unblockuser to restore access.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )
    asyncio.create_task(bot_api("sendMessage", {
        "chat_id": target_id,
        "text": (
            "🚫 YOUR BOT ACCESS HAS BEEN RESTRICTED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your access to this bot has been\n"
            "suspended by the admin.\n\n"
            "You can no longer use /video or /daily.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        ),
    }))
    print(f"[BLOCK] Bot-banned user={target_id}")
    await log_event(client, f"🚫 <b>User Bot-Banned</b>\n👤 {mention} — 🆔 <code>{target_id}</code>")


@app.on_message(filters.command("unblockuser") & filters.user(ADMIN_ID) & filters.private)
async def unblockuser_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage:\n/unblockuser @username\n/unblockuser 123456789"
        )
        return

    raw = args[0].lstrip("@")
    doc = (
        await users_col.find_one({"user_id": int(raw)})
        if raw.isdigit()
        else await users_col.find_one({"username": raw})
    )
    if not doc:
        await message.reply_text("❌ User not found in database.")
        return

    target_id = doc["user_id"]
    fname     = doc.get("first_name", "") or ""
    uname     = doc.get("username")
    mention   = f"@{uname}" if uname else fname or str(target_id)

    await users_col.update_one(
        {"user_id": target_id},
        {"$set": {"bot_banned": False, "warn_count": 0}},
    )
    await message.reply_text(
        "✅ USER UNBLOCKED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User : {mention}\n"
        f"🆔 ID   : {target_id}\n\n"
        "✅ Bot access fully restored.\n"
        "⚠️ Warning count also cleared.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )
    asyncio.create_task(bot_api("sendMessage", {
        "chat_id": target_id,
        "text": (
            "✅ YOUR BOT ACCESS HAS BEEN RESTORED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your access to this bot has been\n"
            "restored by the admin. Welcome back!\n\n"
            "You can now use /video and /daily again.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        ),
    }))
    print(f"[BLOCK] Unblocked user={target_id}")
    await log_event(client, f"✅ <b>User Unblocked</b>\n👤 {mention} — 🆔 <code>{target_id}</code>")


# ═════════════════════════════════════════════════════════════════════════════
#  JOIN REQUEST HANDLER
# ═════════════════════════════════════════════════════════════════════════════

async def bot_api(method: str, params: dict) -> dict:
    """Generic HTTP Bot API caller. Returns the full JSON response."""
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


@app.on_chat_join_request()
async def join_request_handler(client: Client, request):
    user       = request.from_user
    chat       = request.chat
    user_id    = user.id
    chat_id    = chat.id
    first_name = user.first_name or "User"
    username   = f"@{user.username}" if user.username else "No username"
    group_name = chat.title or "the group"
    bot_uname  = await get_bot_username(client)

    print(f"[JOIN] Request from user_id={user_id} ({first_name}) in chat_id={chat_id} ({group_name})")

    # ── Step 1: Approve via HTTP Bot API ──────────────────────────────────────
    # MUST use HTTP Bot API (not MTProto/Pyrogram) — only the HTTP Bot API
    # approval creates the one-time DM permission that lets the bot message
    # a user who has never started the bot.
    approve_result = await bot_api("approveChatJoinRequest", {
        "chat_id": chat_id,
        "user_id": user_id,
    })
    print(f"[JOIN] HTTP approve → {approve_result}")

    if not approve_result.get("ok"):
        # Already approved by something else, or other transient error —
        # try Pyrogram as safety net so the user at least gets into the group.
        try:
            await client.approve_chat_join_request(chat_id, user_id)
            print(f"[JOIN] Pyrogram fallback approve OK for {user_id}")
        except Exception as e:
            print(f"[JOIN] All approve methods failed: {e}")
            return

    # ── Step 2: Save to MongoDB ───────────────────────────────────────────────
    now       = datetime.utcnow()
    join_date = now.strftime("%d %b %Y")
    join_time = now.strftime("%I:%M %p") + " UTC"

    doc = await users_col.find_one({"user_id": user_id})
    if not doc:
        await users_col.insert_one({
            "user_id":       user_id,
            "username":      user.username,
            "first_name":    first_name,
            "last_name":     user.last_name,
            "language_code": getattr(user, "language_code", None),
            "ref_count":     0,
            "points":        0,
            "joined_at":     now,
        })
        points    = 0
        ref_count = 0
    else:
        points    = doc.get("points", 0)
        ref_count = doc.get("ref_count", 0)

    # Compute display values
    full_name    = f"{first_name} {user.last_name or ''}".strip()
    uname_tag    = f"@{user.username}" if user.username else "No username"
    ref_link     = f"https://t.me/{bot_uname}?start={user_id}"
    if ref_count >= 10:
        status = "VIP ⭐"
    elif ref_count >= 3:
        status = "Active 🔥"
    else:
        status = "Member 👤"

    # ── Step 3: Group welcome message with user stats ─────────────────────────
    grp_text = (
        "🌟 Welcome to our community! 🌟\n\n"
        f"👤 Name: {full_name}\n"
        f"🆔 ID: {user_id}\n"
        f"🔗 Username: {uname_tag}\n\n"
        "📊 User Statistics:\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⭐ Status: {status}\n"
        f"💰 Points: {points}\n"
        f"👥 Referrals: {ref_count}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📅 Joined: {join_date} | {join_time}\n"
        f"🔗 Your Referral Link:\n{ref_link}\n\n"
        "Thank you for being with us! Use the menu below to explore."
    )
    # callback_data encodes the target user_id so the handler can verify the caller
    grp_markup = {
        "inline_keyboard": [[
            {"text": "💰 My Points",  "callback_data": f"grp_pts_{user_id}"},
            {"text": "🎁 Earn Points", "callback_data": f"grp_earn_{user_id}"},
        ]]
    }

    grp_result = await bot_api("sendMessage", {
        "chat_id":      chat_id,
        "text":         grp_text,
        "reply_markup": grp_markup,
    })
    print(f"[JOIN] Group welcome for {user_id}: {grp_result.get('ok')}")

    if grp_result.get("ok"):
        msg_id = grp_result["result"]["message_id"]
        pending_welcome_msgs[user_id] = (chat_id, msg_id)

        # Auto-delete after 5 minutes if no button is clicked
        async def _auto_delete(uid: int, cid: int, mid: int):
            await asyncio.sleep(300)
            await bot_api("deleteMessage", {"chat_id": cid, "message_id": mid})
            pending_welcome_msgs.pop(uid, None)

        asyncio.create_task(_auto_delete(user_id, chat_id, msg_id))


# ═════════════════════════════════════════════════════════════════════════════
#  GROUP WELCOME BUTTON CALLBACKS  (My Points / Earn Points)
# ═════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^grp_(pts|earn)_(\d+)$"))
async def grp_btn_callback(client: Client, cq: CallbackQuery):
    m         = re.match(r"^grp_(pts|earn)_(\d+)$", cq.data)
    action    = m.group(1)          # "pts" or "earn"
    target_id = int(m.group(2))     # the user the welcome was for
    caller_id = cq.from_user.id

    # Only the welcomed user may use these buttons
    if caller_id != target_id:
        await cq.answer("❌ These buttons are only for the welcomed member.", show_alert=True)
        return

    bot_uname = await get_bot_username(client)
    doc       = await users_col.find_one({"user_id": caller_id})
    points    = doc.get("points",    0) if doc else 0
    ref_count = doc.get("ref_count", 0) if doc else 0
    ref_link  = f"https://t.me/{bot_uname}?start={caller_id}"

    # Delete the group welcome message now that the user has engaged
    if caller_id in pending_welcome_msgs:
        grp_chat_id, grp_msg_id = pending_welcome_msgs.pop(caller_id)
        asyncio.create_task(bot_api("deleteMessage", {
            "chat_id": grp_chat_id, "message_id": grp_msg_id
        }))

    rank   = get_rank(ref_count)
    status = get_status(points)
    doc2   = await users_col.find_one({"user_id": caller_id})
    full_name = ""
    if doc2:
        fn = doc2.get("first_name", "") or ""
        ln = doc2.get("last_name",  "") or ""
        full_name = f"{fn} {ln}".strip() or fn

    if action == "pts":
        dm_text = (
            "💰 YOUR ACCOUNT WALLET\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 User: {full_name}\n"
            f"🆔 ID: {caller_id}\n\n"
            "📊 STATISTICS:\n"
            f"⭐ Current Points : {points}\n"
            f"👥 Total Referrals : {ref_count}\n"
            f"🏅 Current Rank  : {rank}\n\n"
            f"✨ STATUS: {status}\n"
            "(Collect more points to upgrade your status!)\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Invite friends to grow your balance!\n"
            f"🔗 {ref_link}\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        alert_text = f"💰 Points: {points}  |  🏅 Rank: {rank}  |  👥 Refs: {ref_count}"
    else:  # earn
        dm_text = (
            "🎁 EARN FREE POINTS & REWARDS\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Earn points by inviting your friends and staying active. "
            "Use these points to unlock Premium features!\n\n"
            "🚀 WAYS TO EARN:\n"
            "👥 Referral → +10 Points (Per join)\n"
            "✅ Group Activity → Stay active for bonus points\n"
            "📅 Daily Check-in → +5 Points (Every 24h)\n\n"
            "🔗 YOUR PERSONAL REFERRAL LINK:\n"
            f"{ref_link}\n\n"
            "📢 Share this link in groups or with friends to start earning!\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        alert_text = "🎁 Share your referral link to earn +10 pts per friend!"

    # Build the Telegram share URL — clicking it opens a chat-picker dialog
    share_text = urllib.parse.quote(
        "🎬 Join DESI MLH Video Community using my referral link and earn bonus points!"
    )
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={share_text}"
    dm_markup = {
        "inline_keyboard": [[
            {"text": "📤 Share Your Referral Link", "url": share_url}
        ]]
    }

    # Try DM first
    dm_ok = await bot_api("sendMessage", {
        "chat_id":      caller_id,
        "text":         dm_text,
        "reply_markup": dm_markup,
    })
    if dm_ok.get("ok"):
        await cq.answer("✅ Check your DM from the bot!", show_alert=False)
    else:
        await cq.answer(alert_text, show_alert=True)


# ═════════════════════════════════════════════════════════════════════════════
#  BROADCAST WIZARD
# ═════════════════════════════════════════════════════════════════════════════

def _new_session(chat_id: int, mode: str = "broadcast") -> dict:
    return {
        "state":          STATE_AUDIENCE,
        "audience":       "all",
        "join_after":     None,
        "msg_type":       None,
        "text":           "",
        "entities":       [],
        "media_chat_id":  None,
        "media_msg_id":   None,
        "extra_buttons":  None,
        "preview_msg_id": None,
        "chat_id":        chat_id,
        "mode":           mode,
    }


# ── /broadcast ────────────────────────────────────────────────────────────────

@app.on_message(
    filters.command("broadcast") & filters.user(ADMIN_ID) & filters.private
)
async def broadcast_start(client: Client, message: Message):
    session = _new_session(message.chat.id, mode="broadcast")
    await message.reply_text(
        "📢 <b>Broadcast Message</b>\n\n"
        "Choose who you want to send this broadcast to:\n\n"
        "Type /cancel to cancel the operation.",
        parse_mode=HTML,
        reply_markup=kb_audience(),
    )
    broadcast_sessions[ADMIN_ID] = session


# ── /sbc (Scheduled Broadcast) ────────────────────────────────────────────────

@app.on_message(
    filters.command("sbc") & filters.user(ADMIN_ID) & filters.private
)
async def sbc_start(client: Client, message: Message):
    session = _new_session(message.chat.id, mode="sbc")
    await message.reply_text(
        "⏰ <b>Scheduled Broadcast</b>\n\n"
        "Choose who you want to send this scheduled broadcast to:\n\n"
        "Type /cancel to cancel the operation.",
        parse_mode=HTML,
        reply_markup=kb_audience(),
    )
    broadcast_sessions[ADMIN_ID] = session


# ── All Users ─────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex("^bc_all$") & filters.user(ADMIN_ID))
async def bc_select_all(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    session["audience"]   = "all"
    session["join_after"] = None
    session["state"]      = STATE_CONTENT

    await cq.edit_message_text(
        "✍️ <b>Create Your Broadcast Post</b>\n\n"
        "Send the message you want to broadcast.\n"
        "It can be text, photo, video, file, sticker, or a forwarded channel post.\n\n"
        "Type /cancel to cancel the operation.",
        parse_mode=HTML,
    )
    await cq.answer("✅ All users selected")


# ── Joined After ──────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex("^bc_join_after$") & filters.user(ADMIN_ID))
async def bc_join_after_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    session["state"] = STATE_JOIN_DATE
    await cq.edit_message_text(
        "📅 <b>Filter Users by Join Date</b>\n\n"
        "Enter a date. Only users who joined <b>after</b> this date will receive the broadcast.\n\n"
        "Supported formats:\n"
        "<code>DD.MM.YYYY HH:MM</code>  →  e.g. <code>25.03.2025 14:30</code>\n"
        "<code>MM/DD/YYYY HH:MM</code>  →  e.g. <code>03/25/2025 14:30</code>\n\n"
        "Type /cancel to cancel the operation.",
        parse_mode=HTML,
    )
    await cq.answer()


# ── Add Button ────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex("^bc_add_button$") & filters.user(ADMIN_ID))
async def bc_add_button(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)

    session["state"] = STATE_BUTTONS
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cq.message.reply_text(
        "🔗 <b>Add Inline Button</b>\n\n"
        "Send the button(s) in this format:\n\n"
        "<code>Button Name | https://link.com</code>\n\n"
        "Two buttons in one row:\n"
        "<code>Btn 1 | link1.com && Btn 2 | link2.com</code>\n\n"
        "Multiple rows — one row per line.\n\n"
        "Type /cancel to stop.",
        parse_mode=HTML,
    )
    await cq.answer()


# ── Attach Media ──────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex("^bc_attach_media$") & filters.user(ADMIN_ID))
async def bc_attach_media(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)

    session["state"] = STATE_CONTENT
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cq.message.reply_text(
        "🖼 <b>Attach Media</b>\n\n"
        "Send a photo, video, file or sticker.\n"
        "It will be combined with your current text as one message.\n\n"
        "Type /cancel to stop.",
        parse_mode=HTML,
    )
    await cq.answer()


# ── Remove Buttons ────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex("^bc_remove_buttons$") & filters.user(ADMIN_ID))
async def bc_remove_buttons(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    session["extra_buttons"] = None
    await cq.answer("🗑 Buttons removed")
    await refresh_preview(client, session)


# ── Preview (clean copy with URL buttons only, auto-deletes in 5 s) ───────────

@app.on_callback_query(filters.regex("^bc_preview$") & filters.user(ADMIN_ID))
async def bc_preview(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    extra_kb = None
    if session.get("extra_buttons"):
        extra_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(b["text"], url=b["url"]) for b in row]
            for row in session["extra_buttons"]
        ])

    await cq.answer("Sending preview (auto-deletes in 5 s)…")
    try:
        sent = await send_to_user(client, cq.message.chat.id, session, reply_markup=extra_kb)
        if sent:
            asyncio.create_task(auto_delete(client, cq.message.chat.id, sent.id, delay=5))
    except Exception:
        pass


# ── Send Now → Confirmation ───────────────────────────────────────────────────

@app.on_callback_query(filters.regex("^bc_send_now$") & filters.user(ADMIN_ID))
async def bc_send_now(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)

    total = await count_targets(session)
    aud   = audience_label(session)
    session["state"] = STATE_CONFIRM

    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await cq.message.reply_text(
        "🚀 <b>Confirm Broadcast</b>\n\n"
        "You're about to send a broadcast to:\n"
        f"👥 <b>Recipients:</b> {total:,} users\n"
        f"📅 <b>Filter:</b> {aud}\n\n"
        "⚠️ This action cannot be undone once confirmed.",
        parse_mode=HTML,
        reply_markup=kb_confirm(),
    )
    await cq.answer()


# ── Schedule Broadcast (via button in /broadcast) ─────────────────────────────

@app.on_callback_query(filters.regex("^bc_schedule$") & filters.user(ADMIN_ID))
async def bc_schedule_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)
    session["state"] = STATE_SCHEDULE
    await cq.answer()
    await cq.message.reply_text(
        "⏰ <b>Schedule Broadcast</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Send the date and time to schedule this broadcast.\n\n"
        "Format (Bangladesh Time — BST/UTC+6):\n"
        "<code>DD.MM.YYYY HH:MM</code>\n\n"
        "Example:\n"
        "<code>25.04.2026 21:30</code>\n\n"
        "Type /cancel to cancel.",
        parse_mode=HTML,
    )


# ── Set Schedule (via button in /sbc) ─────────────────────────────────────────

@app.on_callback_query(filters.regex("^sbc_set_schedule$") & filters.user(ADMIN_ID))
async def sbc_set_schedule_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)
    session["state"] = STATE_SCHEDULE
    await cq.answer()
    await cq.message.reply_text(
        "⏰ <b>Set Broadcast Schedule</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Send the date and time for this broadcast.\n\n"
        "Format (Bangladesh Time — BST/UTC+6):\n"
        "<code>DD.MM.YYYY HH:MM</code>\n\n"
        "Example:\n"
        "<code>25.04.2026 21:30</code>\n\n"
        "Type /cancel to cancel.",
        parse_mode=HTML,
    )


# ── Edit Post (back from confirmation) ────────────────────────────────────────

@app.on_callback_query(filters.regex("^bc_edit_post$") & filters.user(ADMIN_ID))
async def bc_edit_post(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    session["state"] = STATE_CUSTOMIZE
    # Delete the confirmation message
    await delete_msg_safe(client, session["chat_id"], cq.message.id)
    # Restore action buttons on preview
    if session.get("preview_msg_id"):
        try:
            await client.edit_message_reply_markup(
                chat_id=session["chat_id"],
                message_id=session["preview_msg_id"],
                reply_markup=kb_customize(session.get("extra_buttons"), mode=session.get("mode", "broadcast")),
            )
        except Exception:
            await refresh_preview(client, session)
    await cq.answer()


# ── Confirm & Send ────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex("^bc_confirm_send$") & filters.user(ADMIN_ID))
async def bc_confirm_send(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(ADMIN_ID)
    if not session or session["state"] != STATE_CONFIRM:
        return await cq.answer("No active session.", show_alert=True)

    await cq.answer("📡 Starting broadcast…")
    await cq.edit_message_text(
        "📡 <b>Broadcasting in progress...</b>\n\n"
        "👥 Target Users: calculating...\n"
        "✅ Sent: 0\n❌ Failed: 0\n⏳ Progress: 0%",
        parse_mode=HTML,
    )
    asyncio.create_task(do_broadcast(client, session, cq.message))


# ── Cancel ────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex("^bc_cancel$") & filters.user(ADMIN_ID))
async def bc_cancel_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.pop(ADMIN_ID, None)
    if session:
        await delete_msg_safe(client, session["chat_id"], session.get("preview_msg_id"))
    await cq.edit_message_text("🚫 Cancelled.\nType /broadcast to start again.")
    await cq.answer()


# ═════════════════════════════════════════════════════════════════════════════
#  /cancel command
# ═════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex("^fj_set_button$") & filters.user(ADMIN_ID))
async def fj_set_button_cb(client: Client, cq: CallbackQuery):
    """Admin clicks 'Set Button' → enter fj_wait_btn state."""
    fj_sessions[ADMIN_ID] = {
        "state":               "fj_wait_btn",
        "wizard_msg_id":       cq.message.id,
        "pending_channels":    [],
        "unresolved_channels": [],
        "fwd_index":           0,
    }
    await cq.edit_message_text(
        "📢 <b>Step 1 — Send Channel Info</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Format: <code>Channel Name | Join Link</code>\n\n"
        "🌐 <b>Public channel:</b>\n"
        "<code>DESI MLH | https://t.me/desimlh</code>\n\n"
        "🔒 <b>Private channel (invite link):</b>\n"
        "<code>VIP Group | https://t.me/+dV5BmONTLmcxZDU1</code>\n"
        "↳ Bot will ask you to <b>forward a message</b> from it next.\n\n"
        "📦 <b>Multiple at once (use &&):</b>\n"
        "<code>DESI MLH | https://t.me/desimlh && VIP | https://t.me/+xxx</code>\n\n"
        "Type /cancel to cancel.",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="fj_cancel")
        ]]),
    )
    await cq.answer()


@app.on_callback_query(filters.regex("^fj_confirm$") & filters.user(ADMIN_ID))
async def fj_confirm_cb(client: Client, cq: CallbackQuery):
    fj = fj_sessions.pop(ADMIN_ID, None)
    if not fj or fj.get("state") != "fj_add_confirm":
        return await cq.answer("No active session.", show_alert=True)

    pending  = fj.get("pending_channels", [])
    doc      = await _fj_doc()
    existing = doc.get("channels", [])

    # Build a set of names/links being added so we can deduplicate
    new_names = {ch["name"] for ch in pending}
    new_links  = {ch["link"] for ch in pending}

    # Remove old entries that match by name OR by link, and drop any broken invite-link entries
    def _is_broken(ch):
        cid = ch.get("chat_id", "")
        return isinstance(cid, str) and cid.startswith("http")

    kept = [
        ch for ch in existing
        if ch["name"] not in new_names
        and ch["link"] not in new_links
        and not _is_broken(ch)
    ]
    channels = kept + pending

    await settings_col.update_one(
        {"key": "force_join"},
        {"$set": {"channels": channels}},
        upsert=True,
    )
    removed_count = len(existing) - len(kept)
    if removed_count:
        print(f"[FORCEJOIN] Cleaned up {removed_count} old/duplicate/broken entry(s)")
    # Test bot access for each added channel using Pyrogram
    from pyrogram.errors import (
        ChannelPrivate, PeerIdInvalid, ChannelInvalid,
        UsernameInvalid, UsernameNotOccupied,
    )
    status_lines = []
    for ch in pending:
        raw_cid = ch["chat_id"]
        try:
            cid = int(raw_cid) if str(raw_cid).lstrip("-").isdigit() else str(raw_cid)
        except Exception:
            cid = str(raw_cid)
        try:
            if isinstance(cid, str) and cid.startswith("http"):
                raise ValueError("invite link — provide numeric ID instead")
            await app.get_chat(cid)
            status_lines.append(f"  ✅ {ch['name']}")
        except (ChannelPrivate, PeerIdInvalid, ChannelInvalid,
                UsernameInvalid, UsernameNotOccupied, ValueError) as e:
            status_lines.append(f"  ⚠️ {ch['name']} — {e}")
        except Exception as e:
            status_lines.append(f"  ⚠️ {ch['name']} — {e}")

    has_warning = any("⚠️" in l for l in status_lines)
    status_text = "\n".join(status_lines)
    warning_block = (
        "\n\n⚠️ <b>ACTION REQUIRED</b>\n"
        "Bot cannot verify membership for ⚠️ channels above.\n"
        "👉 Add bot as <b>Admin</b> to those channels,\n"
        "   then users will be properly checked.\n"
        "   Until then, those channels will BLOCK everyone."
    ) if has_warning else ""

    await cq.edit_message_text(
        f"✅ {len(pending)} CHANNEL(S) ADDED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_text}\n\n"
        f"📋 Total channels now: {len(channels)}"
        f"{warning_block}\n\n"
        "Use /forcejoin on to enable the check.\n"
        "Use /forcejoinadd to add more channels.\n"
        "Use /forcebuttondel to remove a channel.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    await cq.answer(f"✅ {len(pending)} channel(s) added!")
    print(f"[FORCEJOIN] Added {len(pending)} channels: {[c['name'] for c in pending]}")


@app.on_callback_query(filters.regex("^fj_cancel$") & filters.user(ADMIN_ID))
async def fj_cancel_cb(client: Client, cq: CallbackQuery):
    fj_sessions.pop(ADMIN_ID, None)
    await cq.edit_message_text(
        "🚫 Cancelled.\n"
        "No channel was added.\n\n"
        "Use /forcejoinadd to try again."
    )
    await cq.answer("Cancelled.")


@app.on_message(filters.command("forcejoinadd") & filters.user(ADMIN_ID) & filters.private)
async def forcejoinadd_cmd(client: Client, message: Message):
    """Alias for /forcejoin add — shows the add-channel card."""
    fj_sessions.pop(ADMIN_ID, None)
    doc = await _fj_doc()
    await _fj_show_add_card(message.reply_text, doc)


@app.on_message(filters.command("forcebuttondel") & filters.user(ADMIN_ID) & filters.private)
async def forcebuttondel_cmd(client: Client, message: Message):
    """Show all force-join channels as buttons — tap one to remove it."""
    doc      = await _fj_doc()
    channels = doc.get("channels", [])
    if not channels:
        return await message.reply_text(
            "❌ No channels configured.\n\n"
            "Use /forcejoinadd to add channels first."
        )
    buttons = []
    for i, ch in enumerate(channels):
        buttons.append([InlineKeyboardButton(f"🗑️ {ch['name']}", callback_data=f"fj_del_{i}")])
    buttons.append([InlineKeyboardButton("✅ Done", callback_data="fj_del_done")])
    await message.reply_text(
        "🗑️ <b>REMOVE FORCE-JOIN CHANNEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Total channels: <b>{len(channels)}</b>\n\n"
        "Tap a channel below to <b>remove</b> it immediately:\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@app.on_callback_query(filters.regex(r"^fj_del_(\d+|done)$") & filters.user(ADMIN_ID))
async def fj_del_cb(client: Client, cq: CallbackQuery):
    data = cq.data  # "fj_del_0", "fj_del_1", ..., "fj_del_done"

    if data == "fj_del_done":
        await cq.edit_message_text("✅ Done. No more changes.")
        return await cq.answer()

    idx      = int(data.split("_")[-1])
    doc      = await _fj_doc()
    channels = doc.get("channels", [])

    if idx >= len(channels):
        await cq.answer("Channel not found — list may have changed.", show_alert=True)
        return

    removed  = channels.pop(idx)
    await settings_col.update_one(
        {"key": "force_join"},
        {"$set": {"channels": channels}},
        upsert=True,
    )

    if not channels:
        await cq.edit_message_text(
            f"🗑️ <b>{removed['name']}</b> removed.\n\n"
            "No channels left.\n"
            "Use /forcejoinadd to add new ones.",
            parse_mode=HTML,
        )
    else:
        # Rebuild buttons with updated indices
        buttons = []
        for i, ch in enumerate(channels):
            buttons.append([InlineKeyboardButton(f"🗑️ {ch['name']}", callback_data=f"fj_del_{i}")])
        buttons.append([InlineKeyboardButton("✅ Done", callback_data="fj_del_done")])
        await cq.edit_message_text(
            "🗑️ <b>REMOVE FORCE-JOIN CHANNEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Removed: <b>{removed['name']}</b>\n\n"
            f"📋 Remaining: <b>{len(channels)}</b> channel(s)\n\n"
            "Tap another to remove, or Done to finish:\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    await cq.answer(f"🗑️ {removed['name']} removed!")
    print(f"[FORCEJOIN] Removed channel: {removed}")


@app.on_callback_query(filters.regex("^fj_no_link$"))
async def fj_no_link_cb(client: Client, cq: CallbackQuery):
    """Shown when a channel has no valid join URL stored."""
    await cq.answer(
        "⚠️ No join link configured for this channel.\n"
        "Please contact the admin.",
        show_alert=True,
    )


@app.on_callback_query(filters.regex("^fj_check$"))
async def fj_check_cb(client: Client, cq: CallbackQuery):
    """User taps 'I've Joined All Channels!' — recheck and send video if done."""
    user_id    = cq.from_user.id
    not_joined = await _check_force_join(user_id)

    if not_joined:
        # Still some channels missing
        try:
            await cq.edit_message_text(
                "⚠️ NOT ALL CHANNELS JOINED\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"You still need to join {len(not_joined)} more channel(s).\n\n"
                "👇 Join them below, then tap the button.",
                reply_markup=InlineKeyboardMarkup(_fj_join_buttons(not_joined)),
            )
        except Exception:
            pass
        await cq.answer("❌ Please join all channels first!", show_alert=True)
        return

    # All joined!
    await cq.answer("✅ All channels joined! Sending your video...")
    try:
        await cq.edit_message_text(
            "✅ ALL CHANNELS JOINED!\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🎉 Thank you for joining!\n"
            "📹 Your video is being sent...\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
    except Exception:
        pass

    err = await _send_video_to_user(client, user_id)
    if err:
        await cq.message.reply_text(err)


@app.on_message(
    filters.command("cancel") & filters.user(ADMIN_ID) & filters.private
)
async def bc_cancel_cmd(client: Client, message: Message):
    # Clear force-join add-channel wizard if active
    fj = fj_sessions.pop(ADMIN_ID, None)
    if fj:
        try:
            await client.delete_messages(message.chat.id, fj["wizard_msg_id"])
        except Exception:
            pass
        await message.reply_text(
            "🚫 Force-join wizard cancelled.\n"
            "No channel was added.\n\n"
            "Use /forcejoinadd to start again."
        )
        return

    session = broadcast_sessions.pop(ADMIN_ID, None)
    if session:
        await delete_msg_safe(client, session["chat_id"], session.get("preview_msg_id"))
        await message.reply_text("🚫 Cancelled.\nType /broadcast to start again.")
    else:
        await message.reply_text("No active session to cancel.")


# ═════════════════════════════════════════════════════════════════════════════
#  Admin message handler
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(
    filters.incoming & filters.private & filters.user(ADMIN_ID)
    & ~filters.command(["broadcast", "sbc", "cancel", "start", "help", "video", "daily",
                         "stats", "user", "addpoints", "removepoints",
                         "setlimit", "export", "blockuser", "unblockuser",
                         "forcejoin", "forcejoinadd", "forcebuttondel", "clearhistory",
                         "schedule", "logchannel",
                         "nightmode", "shadowban", "unshadowban", "shadowbans", "clearshadowbans",
                         "addfilter", "delfilter", "filters", "clearfilters",
                         "antiflood", "welcome", "rules", "setrules", "clearrules",
                         "warn", "clearwarn", "mute", "unmute", "ban", "unban", "kick",
                         "listvideos", "delvideo", "clearvideos"])
)
async def admin_message_handler(client: Client, message: Message):
    fwd = getattr(message, "forward_from_chat", None)
    fj_state = fj_sessions.get(ADMIN_ID, {}).get("state", "none")
    print(f"[ADMIN_HANDLER] text={bool(message.text)} fwd_chat={fwd and fwd.id} fj_state={fj_state}")

    # ── Force-join add-channel wizard ─────────────────────────────────────
    fj = fj_sessions.get(ADMIN_ID)

    # Helper: build and show the confirm card
    async def _show_fj_confirm():
        pending = fj["pending_channels"]
        lines = []
        for i, ch in enumerate(pending, 1):
            lines.append(
                f"{i}. 📌 <b>{ch['name']}</b>\n"
                f"   🔗 {ch['link']}\n"
                f"   🆔 <code>{ch['chat_id']}</code>"
            )
        preview = "\n\n".join(lines)
        fj["state"] = "fj_add_confirm"
        try:
            await client.delete_messages(message.chat.id, fj["wizard_msg_id"])
        except Exception:
            pass
        confirm_msg = await message.reply_text(
            f"📢 <b>Confirm {len(pending)} Channel(s)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{preview}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Is everything correct?",
            parse_mode=HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"✅ Save {len(pending)} Channel(s)", callback_data="fj_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="fj_cancel"),
            ]]),
        )
        fj["wizard_msg_id"] = confirm_msg.id

    # ── Step 1: Admin sends "Name | link" text ─────────────────────────────
    if fj and fj["state"] == "fj_wait_btn":
        if not message.text:
            await message.reply_text(
                "⚠️ Please send as plain text.\n"
                "Example: <code>DESI MLH | https://t.me/desimlh</code>",
                parse_mode=HTML,
            )
            return

        raw_parts = []
        for line in message.text.strip().splitlines():
            raw_parts.extend(line.split("&&"))

        parsed = [ch for part in raw_parts if (ch := _fj_parse_entry(part))]
        if not parsed:
            await message.reply_text(
                "❌ Could not parse. Format:\n\n"
                "<code>Channel Name | https://t.me/link</code>\n\n"
                "Multiple:\n"
                "<code>Chan1 | https://t.me/a && Chan2 | https://t.me/+xxx</code>",
                parse_mode=HTML,
            )
            return

        # Split: "resolved" = numeric ID or @username; "unresolved" = raw invite URL
        def _chat_id_resolved(cid: str) -> bool:
            return cid.lstrip("-").isdigit() or cid.startswith("@")

        resolved   = [ch for ch in parsed if _chat_id_resolved(ch["chat_id"])]
        unresolved = [ch for ch in parsed if not _chat_id_resolved(ch["chat_id"])]

        fj["pending_channels"]    = resolved
        fj["unresolved_channels"] = unresolved
        fj["fwd_index"]           = 0

        if unresolved:
            # Ask admin to forward a message from the first unresolved channel
            fj["state"] = "fj_wait_fwd"
            ch = unresolved[0]
            await message.reply_text(
                f"📨 <b>Step 2 — Forward a message</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Channel: <b>{ch['name']}</b>\n\n"
                f"Go to that channel, tap any message → <b>Forward</b> → send here.\n\n"
                f"<i>This lets the bot identify the channel's ID automatically.</i>\n\n"
                "Type /cancel to cancel.",
                parse_mode=HTML,
            )
        else:
            await _show_fj_confirm()
        return

    # ── Step 2: Admin forwards a message from the unresolved channel ────────
    if fj and fj["state"] == "fj_wait_fwd":
        unresolved = fj.get("unresolved_channels", [])
        idx        = fj.get("fwd_index", 0)

        if idx >= len(unresolved):
            await _show_fj_confirm()
            return

        ch = unresolved[idx]

        # Try to get chat_id from forward, or from manually typed numeric ID
        resolved_id = None

        # Method 1: forwarded message from a channel
        fwd_chat = getattr(message, "forward_from_chat", None)
        if fwd_chat:
            resolved_id = str(fwd_chat.id)
            print(f"[FORCEJOIN] Forward-resolved '{ch['name']}' → {resolved_id}")

        # Method 2: admin typed a numeric ID as fallback
        if not resolved_id and message.text:
            typed = message.text.strip()
            if typed.lstrip("-").isdigit():
                resolved_id = typed
                print(f"[FORCEJOIN] Manual ID for '{ch['name']}' → {resolved_id}")

        if not resolved_id:
            await message.reply_text(
                f"⚠️ Could not detect the channel.\n\n"
                f"For <b>{ch['name']}</b>, try one of:\n\n"
                "1️⃣ Go to that channel → tap any message → <b>Forward</b> → send here\n\n"
                "2️⃣ Or just type the numeric channel ID:\n"
                "   <code>-1001234567890</code>\n\n"
                "Type /cancel to cancel.",
                parse_mode=HTML,
            )
            return

        ch["chat_id"] = resolved_id
        fj["pending_channels"].append(ch)
        fj["fwd_index"] += 1

        next_idx = fj["fwd_index"]
        if next_idx < len(unresolved):
            next_ch = unresolved[next_idx]
            await message.reply_text(
                f"✅ <b>{ch['name']}</b> connected! ID: <code>{ch['chat_id']}</code>\n\n"
                f"📨 Now forward a message from <b>{next_ch['name']}</b>\n"
                f"(or type its numeric ID like <code>-1001234567890</code>):",
                parse_mode=HTML,
            )
        else:
            await message.reply_text(
                f"✅ <b>{ch['name']}</b> connected! ID: <code>{ch['chat_id']}</code>",
                parse_mode=HTML,
            )
            await _show_fj_confirm()
        return

    # ── No active wizard session but looks like a forwarded channel msg ────────
    if not fj and getattr(message, "forward_from_chat", None):
        await message.reply_text(
            "⚠️ <b>Wizard session expired</b> (bot restarted).\n\n"
            "Please start again: /forcejoinadd",
            parse_mode=HTML,
        )
        return

    session = broadcast_sessions.get(ADMIN_ID)

    # ── Waiting for join-after date ───────────────────────────────────────
    if session and session["state"] == STATE_JOIN_DATE:
        if not message.text:
            return await message.reply_text("Please type the date as text.")
        dt = parse_date(message.text)
        if not dt:
            return await message.reply_text(
                "⚠️ <b>Invalid format.</b>\n\n"
                "Use:\n"
                "<code>DD.MM.YYYY HH:MM</code>  →  e.g. <code>25.03.2025 14:30</code>\n"
                "<code>MM/DD/YYYY HH:MM</code>  →  e.g. <code>03/25/2025 14:30</code>",
                parse_mode=HTML,
            )
        session["audience"]   = "after_date"
        session["join_after"] = dt
        session["state"]      = STATE_CONTENT
        await message.reply_text(
            f"✅ Filter set: joined after <b>{dt.strftime('%d.%m.%Y %H:%M')}</b>\n\n"
            "✍️ Now send the message you want to broadcast.\n\n"
            "Type /cancel to cancel the operation.",
            parse_mode=HTML,
        )
        return

    # ── Waiting for schedule date/time ────────────────────────────────────
    if session and session["state"] == STATE_SCHEDULE:
        if not message.text:
            return await message.reply_text("Please type the date and time as text.")
        dt = parse_date(message.text)
        # Try BST (UTC+6) parse: treat input as BST, convert to UTC
        if not dt:
            return await message.reply_text(
                "⚠️ <b>Invalid format.</b>\n\n"
                "Use: <code>DD.MM.YYYY HH:MM</code>\n"
                "Example: <code>25.04.2026 21:30</code>\n\n"
                "Time should be in Bangladesh Time (BST / UTC+6).",
                parse_mode=HTML,
            )
        # Convert BST → UTC (subtract 6 hours)
        dt_utc = dt - timedelta(hours=6)
        if dt_utc <= datetime.utcnow():
            return await message.reply_text(
                "⚠️ That time is already in the past!\n"
                "Please send a future date and time."
            )

        # Store session snapshot in MongoDB
        snap = {k: v for k, v in session.items() if k != "entities"}
        # Store entities as list of dicts (serializable)
        if session.get("entities"):
            try:
                snap["entities"] = [
                    {"type": str(e.type), "offset": e.offset, "length": e.length}
                    for e in session["entities"]
                ]
            except Exception:
                snap["entities"] = []
        snap["state"] = "ready"
        doc = {
            "send_at":    dt_utc,
            "session":    snap,
            "created_at": datetime.utcnow(),
            "label":      dt.strftime("%d.%m.%Y %H:%M") + " BST",
        }
        result = await scheduled_col.insert_one(doc)
        broadcast_sessions.pop(ADMIN_ID, None)

        bst_str = dt.strftime("%d.%m.%Y %H:%M")
        await message.reply_text(
            "✅ <b>Broadcast Scheduled!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 Scheduled for: <b>{bst_str} BST</b>\n"
            f"🆔 ID: <code>{result.inserted_id}</code>\n\n"
            "The broadcast will be sent automatically at that time.\n\n"
            "View scheduled: /schedule list\n"
            "Cancel one: /schedule cancel &lt;ID&gt;\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        await log_event(client,
            f"⏰ <b>Broadcast Scheduled</b>\n"
            f"📅 Time: <b>{bst_str} BST</b>\n"
            f"🆔 ID: <code>{result.inserted_id}</code>"
        )
        return

    # ── Waiting for broadcast content ─────────────────────────────────────
    if session and session["state"] == STATE_CONTENT:
        updated = False

        if message.text:
            # Text → becomes the body or caption
            session["text"]     = message.text
            session["entities"] = message.entities or []
            if not session.get("msg_type"):
                session["msg_type"] = "text"
            # If media already exists, text becomes caption (keep media)
            updated = True

        elif has_media(message):
            # Media → one combined message (text becomes its caption)
            session["msg_type"]      = "media"
            session["media_chat_id"] = message.chat.id
            session["media_msg_id"]  = message.id
            # If media has caption and no text set yet, adopt it
            if not session.get("text") and message.caption:
                session["text"]     = message.caption
                session["entities"] = message.caption_entities or []
            updated = True

        if updated and session.get("msg_type"):
            await refresh_preview(client, session)
            session["state"] = STATE_CUSTOMIZE
        return

    # ── Waiting for button config ─────────────────────────────────────────
    if session and session["state"] == STATE_BUTTONS:
        if not message.text:
            return await message.reply_text("Please send the button config as text.")
        parsed = parse_buttons(message.text)
        if not parsed:
            return await message.reply_text(
                "⚠️ Could not parse buttons. Use:\n\n"
                "<code>Button Name | https://link.com</code>\n\n"
                "Two in a row:\n"
                "<code>Btn 1 | link1 && Btn 2 | link2</code>",
                parse_mode=HTML,
            )
        session["extra_buttons"] = parsed
        session["state"]         = STATE_CUSTOMIZE
        btn_count = sum(len(r) for r in parsed)
        await message.reply_text(
            f"✅ <b>{btn_count} button(s)</b> saved.",
            parse_mode=HTML,
        )
        # Refresh preview — URL buttons will now appear on top
        await refresh_preview(client, session)
        return

    # ── No active wizard — keyword replies ────────────────────────────────
    if message.text:
        reply = REPLIES.get(message.text.lower().strip())
        if reply:
            await message.reply_text(reply)
        else:
            await message.reply_text(
                "I'm not sure how to respond to that. Try /help to see what I can do!"
            )


# ═════════════════════════════════════════════════════════════════════════════
#  SHADOW BAN SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("shadowban") & filters.group)
async def shadowban_cmd(client: Client, message: Message):
    sender_id = message.from_user.id if message.from_user else "anon"
    print(f"[SHADOWBAN] cmd from user={sender_id} chat={message.chat.id}")
    is_adm = await _is_admin_msg(client, message)
    print(f"[SHADOWBAN] is_admin={is_adm}")
    if not is_adm:
        r = await message.reply_text("❌ Only admins can use /shadowban.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]
    try:
        target_id, target_name, _ = await _resolve_target(client, message, args)
    except ValueError as e:
        r = await message.reply_text(str(e), parse_mode=HTML)
        return asyncio.create_task(_auto_del(r, 20))

    if await _is_admin(client, message.chat.id, target_id):
        r = await message.reply_text("❌ Cannot shadow ban an admin.")
        return asyncio.create_task(_auto_del(r, 15))

    await shadowban_col.update_one(
        {"chat_id": message.chat.id, "user_id": target_id},
        {"$set": {"chat_id": message.chat.id, "user_id": target_id,
                  "banned_by": (message.from_user.id if message.from_user else 0), "banned_at": datetime.utcnow()}},
        upsert=True,
    )
    mention = f'<a href="tg://user?id={target_id}">{target_name}</a>'
    r = await message.reply_text(
        "🕵️ SHADOW BAN ACTIVATED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User : {mention}\n"
        f"🆔 ID   : <code>{target_id}</code>\n\n"
        "Their messages will be silently deleted.\n"
        "They won't know they are banned.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(r, 40))
    print(f"[SHADOWBAN] user={target_id} in chat={message.chat.id}")
    asyncio.create_task(log_event(client,
        f"🕵️ <b>Shadow Ban Applied</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User  : {mention}\n"
        f"🆔 ID    : <code>{target_id}</code>\n"
        f"💬 Group : <code>{message.chat.id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


@app.on_message(filters.command("unshadowban") & filters.group)
async def unshadowban_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /unshadowban.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]
    try:
        target_id, target_name, _ = await _resolve_target(client, message, args)
    except ValueError as e:
        r = await message.reply_text(str(e), parse_mode=HTML)
        return asyncio.create_task(_auto_del(r, 20))

    result = await shadowban_col.delete_one(
        {"chat_id": message.chat.id, "user_id": target_id}
    )
    mention = f'<a href="tg://user?id={target_id}">{target_name}</a>'
    if result.deleted_count:
        r = await message.reply_text(
            f"✅ Shadow ban removed for {mention}.\n"
            "They can now post normally.",
            parse_mode=HTML,
        )
        asyncio.create_task(log_event(client,
            f"🔓 <b>Shadow Ban Removed</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 User  : {mention}\n"
            f"🆔 ID    : <code>{target_id}</code>\n"
            f"💬 Group : <code>{message.chat.id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
    else:
        r = await message.reply_text(f"⚠️ {mention} was not shadow banned.", parse_mode=HTML)
    asyncio.create_task(_auto_del(r, 30))


@app.on_message(filters.command("shadowbans") & filters.group)
async def shadowbans_list_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return
    docs = await shadowban_col.find({"chat_id": message.chat.id}).to_list(length=50)
    if not docs:
        r = await message.reply_text("📋 No shadow banned users in this group.")
        return asyncio.create_task(_auto_del(r, 20))
    lines = [f"🕵️ <b>Shadow Banned Users</b> ({len(docs)}):\n━━━━━━━━━━━━━━━━━━━━━━"]
    for d in docs:
        lines.append(f"• <code>{d['user_id']}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n🤖 DESI MLH SYSTEM")
    r = await message.reply_text("\n".join(lines), parse_mode=HTML)
    asyncio.create_task(_auto_del(r, 60))


# ── Shadow ban enforcement handler ────────────────────────────────────────────

@app.on_message(filters.group & filters.incoming & ~filters.service, group=1)
async def shadowban_enforcer(client: Client, message: Message):
    print(f"[GROUP_MSG] chat={message.chat.id} user={getattr(message.from_user,'id','?')} text={str(message.text or '')[:30]}")
    if not message.from_user:
        return
    doc = await shadowban_col.find_one({
        "chat_id": message.chat.id,
        "user_id": message.from_user.id,
    })
    if doc:
        try:
            await message.delete()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  CUSTOM FILTER SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("addfilter") & filters.group)
async def addfilter_cmd(client: Client, message: Message):
    sender_id = message.from_user.id if message.from_user else "anon"
    print(f"[ADDFILTER] cmd from user={sender_id} chat={message.chat.id}")
    is_adm = await _is_admin_msg(client, message)
    print(f"[ADDFILTER] is_admin={is_adm}")
    if not is_adm:
        r = await message.reply_text("❌ Only admins can use /addfilter.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]
    if not args:
        r = await message.reply_text(
            "⚙️ <b>Usage:</b>\n"
            "<code>/addfilter pattern [action]</code>\n\n"
            "<b>Actions:</b>\n"
            "• <code>delete</code> — delete message (default)\n"
            "• <code>warn</code>   — delete + warn user\n"
            "• <code>mute</code>   — delete + mute 1 hour\n"
            "• <code>ban</code>    — delete + ban user\n\n"
            "<b>Examples:</b>\n"
            "<code>/addfilter badword</code>\n"
            "<code>/addfilter spam.*.link warn</code>\n"
            "<code>/addfilter http://evil.com ban</code>",
            parse_mode=HTML,
        )
        return asyncio.create_task(_auto_del(r, 60))

    pattern = args[0]
    action  = args[1].lower() if len(args) > 1 else "delete"
    if action not in ("delete", "warn", "mute", "ban"):
        action = "delete"

    # Validate regex
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error:
        r = await message.reply_text("❌ Invalid regex pattern.")
        return asyncio.create_task(_auto_del(r, 20))

    await filters_col.update_one(
        {"chat_id": message.chat.id, "pattern": pattern},
        {"$set": {"chat_id": message.chat.id, "pattern": pattern,
                  "action": action, "added_by": (message.from_user.id if message.from_user else 0),
                  "added_at": datetime.utcnow()}},
        upsert=True,
    )
    r = await message.reply_text(
        "✅ <b>Filter Added</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Pattern : <code>{pattern}</code>\n"
        f"⚡ Action  : <b>{action}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(r, 30))
    asyncio.create_task(log_event(client,
        f"⚙️ <b>Filter Added</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 Pattern : <code>{pattern}</code>\n"
        f"⚡ Action  : <b>{action}</b>\n"
        f"💬 Group   : <code>{message.chat.id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))




# ── Filter enforcement handler ─────────────────────────────────────────────────

@app.on_message(filters.group & filters.incoming & ~filters.service, group=2)
async def filter_enforcer(client: Client, message: Message):
    if not message.from_user:
        return
    if await _is_admin_msg(client, message):
        return

    chat_docs = await filters_col.find({"chat_id": message.chat.id}).to_list(length=100)
    if not chat_docs:
        return

    text = (message.text or message.caption or "").lower()
    user_id   = message.from_user.id
    user_name = message.from_user.first_name or str(user_id)

    for doc in chat_docs:
        try:
            if re.search(doc["pattern"], text, re.IGNORECASE):
                action = doc.get("action", "delete")
                # Always delete the message
                try:
                    await message.delete()
                except Exception:
                    pass

                if action == "warn":
                    # Reuse warn logic
                    old_doc   = await users_col.find_one({"user_id": user_id})
                    old_warns = (old_doc or {}).get("warn_count", 0)
                    new_warns = old_warns + 1
                    await users_col.update_one(
                        {"user_id": user_id},
                        {"$set": {"warn_count": new_warns}},
                        upsert=True,
                    )
                    mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'
                    r = await client.send_message(
                        message.chat.id,
                        f"⚠️ {mention} got a warning for triggering filter "
                        f"<code>{doc['pattern']}</code>\n"
                        f"Warns: {new_warns}/{MAX_WARNS}",
                        parse_mode=HTML,
                    )
                    asyncio.create_task(_auto_del(r, 30))

                elif action == "mute":
                    until = datetime.utcnow() + timedelta(hours=1)
                    try:
                        await client.restrict_chat_member(
                            message.chat.id, user_id,
                            ChatPermissions(can_send_messages=False),
                            until_date=until,
                        )
                    except Exception:
                        pass
                    mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'
                    r = await client.send_message(
                        message.chat.id,
                        f"🔇 {mention} muted 1 hour — triggered filter "
                        f"<code>{doc['pattern']}</code>",
                        parse_mode=HTML,
                    )
                    asyncio.create_task(_auto_del(r, 30))

                elif action == "ban":
                    try:
                        await client.ban_chat_member(message.chat.id, user_id)
                    except Exception:
                        pass
                    mention = f'<a href="tg://user?id={user_id}">{user_name}</a>'
                    r = await client.send_message(
                        message.chat.id,
                        f"🚫 {mention} banned — triggered filter "
                        f"<code>{doc['pattern']}</code>",
                        parse_mode=HTML,
                    )
                    asyncio.create_task(_auto_del(r, 30))

                break   # Only apply first matching filter
        except re.error:
            continue


# ═════════════════════════════════════════════════════════════════════════════
#  NIGHT MODE SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

_NIGHT_RESTRICTED = ChatPermissions(
    can_send_messages        = False,
    can_send_media_messages  = False,
    can_send_polls           = False,
    can_add_web_page_previews= False,
    can_change_info          = False,
    can_invite_users         = False,
    can_pin_messages         = False,
)

_NIGHT_OPEN = ChatPermissions(
    can_send_messages        = True,
    can_send_media_messages  = True,
    can_send_polls           = True,
    can_add_web_page_previews= True,
    can_change_info          = False,
    can_invite_users         = True,
    can_pin_messages         = False,
)


def _parse_hhmm(s: str):
    """Parse HH:MM → (hour, minute) or raise ValueError."""
    parts = s.strip().split(":")
    if len(parts) != 2:
        raise ValueError
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError
    return h, m


@app.on_message(filters.command("nightmode") & filters.group)
async def nightmode_cmd(client: Client, message: Message):
    sender_id = message.from_user.id if message.from_user else "anon"
    print(f"[NIGHTMODE] cmd from user={sender_id} chat={message.chat.id}")
    is_adm = await _is_admin_msg(client, message)
    print(f"[NIGHTMODE] is_admin={is_adm}")
    if not is_adm:
        r = await message.reply_text("❌ Only admins can use /nightmode.")
        return asyncio.create_task(_auto_del(r, 15))

    args   = message.command[1:]
    sub    = args[0].lower() if args else ""
    chat_id = message.chat.id

    # ── /nightmode status ──────────────────────────────────────────────────
    if sub == "status" or not sub:
        doc = await nightmode_col.find_one({"chat_id": chat_id})
        if not doc or not doc.get("enabled"):
            r = await message.reply_text("🌙 Night Mode is currently <b>OFF</b>.", parse_mode=HTML)
        else:
            sh, sm = doc["start_h"], doc["start_m"]
            eh, em = doc["end_h"],   doc["end_m"]
            r = await message.reply_text(
                "🌙 <b>Night Mode</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Status : <b>ON</b>\n"
                f"Starts : <b>{sh:02d}:{sm:02d} BST</b>\n"
                f"Ends   : <b>{eh:02d}:{em:02d} BST</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "🤖 DESI MLH SYSTEM",
                parse_mode=HTML,
            )
        return asyncio.create_task(_auto_del(r, 40))

    # ── /nightmode off ─────────────────────────────────────────────────────
    if sub == "off":
        await nightmode_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"enabled": False}},
            upsert=True,
        )
        # Restore group permissions
        try:
            await client.set_chat_permissions(chat_id, _NIGHT_OPEN)
        except Exception:
            pass
        r = await message.reply_text("☀️ Night Mode <b>disabled</b>. Group is open.", parse_mode=HTML)
        asyncio.create_task(log_event(client,
            f"☀️ <b>Night Mode Disabled</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💬 Group : <code>{chat_id}</code>\n"
            f"🔓 Group is now open to all members.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
        return asyncio.create_task(_auto_del(r, 30))

    # ── /nightmode on HH:MM HH:MM ─────────────────────────────────────────
    if sub == "on":
        if len(args) < 3:
            r = await message.reply_text(
                "⚙️ Usage:\n"
                "<code>/nightmode on HH:MM HH:MM</code>\n\n"
                "Example (11pm → 6am BST):\n"
                "<code>/nightmode on 23:00 06:00</code>",
                parse_mode=HTML,
            )
            return asyncio.create_task(_auto_del(r, 40))
        try:
            sh, sm = _parse_hhmm(args[1])
            eh, em = _parse_hhmm(args[2])
        except (ValueError, IndexError):
            r = await message.reply_text("❌ Invalid time format. Use HH:MM (e.g. 23:00).")
            return asyncio.create_task(_auto_del(r, 20))

        # Convert BST (UTC+6) → UTC
        sh_utc = (sh - 6) % 24
        eh_utc = (eh - 6) % 24

        await nightmode_col.update_one(
            {"chat_id": chat_id},
            {"$set": {
                "chat_id": chat_id,
                "enabled": True,
                "start_h": sh,    "start_m": sm,
                "end_h":   eh,    "end_m":   em,
                "start_h_utc": sh_utc, "start_m_utc": sm,
                "end_h_utc":   eh_utc, "end_m_utc":   em,
                "is_night": False,       # current state tracker
            }},
            upsert=True,
        )
        r = await message.reply_text(
            "🌙 <b>Night Mode Enabled</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔒 Restricts at : <b>{sh:02d}:{sm:02d} BST</b>\n"
            f"🔓 Opens at     : <b>{eh:02d}:{em:02d} BST</b>\n\n"
            "During night mode all members are restricted.\n"
            "Only admins can post.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        asyncio.create_task(log_event(client,
            f"🌙 <b>Night Mode Enabled</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔒 Starts : <b>{sh:02d}:{sm:02d} BST</b>\n"
            f"🔓 Ends   : <b>{eh:02d}:{em:02d} BST</b>\n"
            f"💬 Group  : <code>{chat_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
        return asyncio.create_task(_auto_del(r, 40))

    r = await message.reply_text(
        "Usage: <code>/nightmode on HH:MM HH:MM</code> | <code>/nightmode off</code> | <code>/nightmode status</code>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(r, 30))


async def nightmode_loop(client: Client):
    """Background task: enforces night mode every 60 seconds."""
    print("[NIGHTMODE] Loop started.")
    while True:
        try:
            await asyncio.sleep(60)
            now_utc = datetime.utcnow()
            now_min = now_utc.hour * 60 + now_utc.minute

            docs = await nightmode_col.find({"enabled": True}).to_list(length=100)
            for doc in docs:
                chat_id   = doc["chat_id"]
                sh = doc["start_h_utc"] * 60 + doc["start_m_utc"]
                eh = doc["end_h_utc"]   * 60 + doc["end_m_utc"]
                currently_night = doc.get("is_night", False)

                # Determine if we're inside night window
                if sh < eh:
                    in_night = sh <= now_min < eh
                else:   # wraps midnight
                    in_night = now_min >= sh or now_min < eh

                if in_night and not currently_night:
                    # Activate night mode
                    try:
                        await client.set_chat_permissions(chat_id, _NIGHT_RESTRICTED)
                        await nightmode_col.update_one(
                            {"chat_id": chat_id}, {"$set": {"is_night": True}}
                        )
                        bst_h = (doc["start_h_utc"] + 6) % 24
                        await client.send_message(
                            chat_id,
                            f"🌙 <b>Night Mode ON</b>\n"
                            f"Group is now restricted until {doc['end_h']:02d}:{doc['end_m']:02d} BST.\n"
                            "Only admins can post. Good night! 😴",
                            parse_mode=HTML,
                        )
                        print(f"[NIGHTMODE] Activated for chat={chat_id}")
                    except Exception as e:
                        print(f"[NIGHTMODE] Error activating {chat_id}: {e}")

                elif not in_night and currently_night:
                    # Deactivate night mode
                    try:
                        await client.set_chat_permissions(chat_id, _NIGHT_OPEN)
                        await nightmode_col.update_one(
                            {"chat_id": chat_id}, {"$set": {"is_night": False}}
                        )
                        await client.send_message(
                            chat_id,
                            f"☀️ <b>Night Mode OFF</b>\n"
                            "Group is now open. Good morning! 🌅",
                            parse_mode=HTML,
                        )
                        print(f"[NIGHTMODE] Deactivated for chat={chat_id}")
                    except Exception as e:
                        print(f"[NIGHTMODE] Error deactivating {chat_id}: {e}")
        except Exception as e:
            print(f"[NIGHTMODE] Loop error: {e}")


# ═════════════════════════════════════════════════════════════════════════════
#  SHADOWBAN — IMPROVED COMMANDS
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("clearshadowbans") & filters.group)
async def clearshadowbans_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /clearshadowbans.")
        return asyncio.create_task(_auto_del(r, 15))
    result = await shadowban_col.delete_many({"chat_id": message.chat.id})
    r = await message.reply_text(
        f"🧹 <b>Shadow Bans Cleared</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Removed <b>{result.deleted_count}</b> shadow ban(s).\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(r, 30))
    asyncio.create_task(log_event(client,
        f"🧹 <b>All Shadow Bans Cleared</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Removed : <b>{result.deleted_count}</b> ban(s)\n"
        f"💬 Group   : <code>{message.chat.id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


# ═════════════════════════════════════════════════════════════════════════════
#  FILTER SYSTEM — IMPROVED
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("filters") & filters.group)
async def list_filters_improved_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return
    docs = await filters_col.find({"chat_id": message.chat.id}).to_list(length=100)
    if not docs:
        r = await message.reply_text(
            "📋 <b>No Filters Set</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Add a filter: <code>/addfilter pattern [action]</code>\n"
            "Actions: <code>delete</code> | <code>warn</code> | <code>mute</code> | <code>ban</code>",
            parse_mode=HTML,
        )
        return asyncio.create_task(_auto_del(r, 30))

    action_icons = {"delete": "🗑", "warn": "⚠️", "mute": "🔇", "ban": "🚫"}
    lines = [f"⚙️ <b>Active Filters</b> ({len(docs)}):\n━━━━━━━━━━━━━━━━━━━━━━"]
    for i, d in enumerate(docs, 1):
        icon = action_icons.get(d["action"], "🗑")
        lines.append(f"<b>{i}.</b> {icon} <code>{d['pattern']}</code> → <b>{d['action']}</b>")
    lines.append(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🗑 Delete: <code>/delfilter 1</code> or <code>/delfilter pattern</code>\n"
        "🧹 Clear all: <code>/clearfilters</code>\n"
        "🤖 DESI MLH SYSTEM"
    )
    r = await message.reply_text("\n".join(lines), parse_mode=HTML)
    asyncio.create_task(_auto_del(r, 90))


@app.on_message(filters.command("delfilter") & filters.group)
async def delfilter_improved_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /delfilter.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]
    if not args:
        r = await message.reply_text(
            "Usage: <code>/delfilter 1</code> (by number) or <code>/delfilter pattern</code>",
            parse_mode=HTML,
        )
        return asyncio.create_task(_auto_del(r, 20))

    query = args[0]
    # Try by number first
    if query.isdigit():
        idx = int(query) - 1
        docs = await filters_col.find({"chat_id": message.chat.id}).to_list(length=100)
        if idx < 0 or idx >= len(docs):
            r = await message.reply_text(f"❌ No filter #{query} found. Use /filters to see the list.")
            return asyncio.create_task(_auto_del(r, 20))
        doc = docs[idx]
        await filters_col.delete_one({"_id": doc["_id"]})
        r = await message.reply_text(
            f"✅ Filter #{query} removed: <code>{doc['pattern']}</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(log_event(client,
            f"🗑 <b>Filter Deleted</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔍 Pattern : <code>{doc['pattern']}</code>\n"
            f"💬 Group   : <code>{message.chat.id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
    else:
        result = await filters_col.delete_one({"chat_id": message.chat.id, "pattern": query})
        if result.deleted_count:
            r = await message.reply_text(f"✅ Filter <code>{query}</code> removed.", parse_mode=HTML)
            asyncio.create_task(log_event(client,
                f"🗑 <b>Filter Deleted</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔍 Pattern : <code>{query}</code>\n"
                f"💬 Group   : <code>{message.chat.id}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 DESI MLH SYSTEM"
            ))
        else:
            r = await message.reply_text(f"❌ No filter found for <code>{query}</code>.", parse_mode=HTML)
    asyncio.create_task(_auto_del(r, 25))


@app.on_message(filters.command("clearfilters") & filters.group)
async def clearfilters_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /clearfilters.")
        return asyncio.create_task(_auto_del(r, 15))
    result = await filters_col.delete_many({"chat_id": message.chat.id})
    r = await message.reply_text(
        f"🧹 <b>All Filters Cleared</b>\n"
        f"✅ Removed <b>{result.deleted_count}</b> filter(s).",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(r, 25))
    asyncio.create_task(log_event(client,
        f"🧹 <b>All Filters Cleared</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Removed : <b>{result.deleted_count}</b> filter(s)\n"
        f"💬 Group   : <code>{message.chat.id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


# ═════════════════════════════════════════════════════════════════════════════
#  ANTI-FLOOD SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("antiflood") & filters.group)
async def antiflood_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /antiflood.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]
    chat_id = message.chat.id
    sub = args[0].lower() if args else ""

    if sub == "off":
        await antiflood_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"enabled": False}},
            upsert=True,
        )
        r = await message.reply_text("✅ Anti-flood <b>disabled</b>.", parse_mode=HTML)
        asyncio.create_task(log_event(client,
            f"🌊 <b>Anti-flood Disabled</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💬 Group : <code>{chat_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
        return asyncio.create_task(_auto_del(r, 25))

    if sub == "status":
        doc = await antiflood_col.find_one({"chat_id": chat_id})
        if not doc or not doc.get("enabled"):
            r = await message.reply_text("🌊 Anti-flood: <b>OFF</b>", parse_mode=HTML)
        else:
            r = await message.reply_text(
                f"🌊 <b>Anti-flood: ON</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📨 Limit : <b>{doc['limit']} messages</b>\n"
                f"⏱ Window: <b>{doc['seconds']} seconds</b>\n"
                f"⚡ Action: <b>{doc.get('action','mute')}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 DESI MLH SYSTEM",
                parse_mode=HTML,
            )
        return asyncio.create_task(_auto_del(r, 30))

    if sub == "on":
        # /antiflood on 5 10 [mute|ban|kick]
        if len(args) < 3:
            r = await message.reply_text(
                "⚙️ <b>Anti-flood Usage:</b>\n"
                "<code>/antiflood on [messages] [seconds] [action]</code>\n\n"
                "<b>Example:</b>\n"
                "<code>/antiflood on 5 10</code> — mute if 5 msg in 10s\n"
                "<code>/antiflood on 8 10 ban</code> — ban if 8 msg in 10s\n\n"
                "<b>Actions:</b> <code>mute</code> | <code>ban</code> | <code>kick</code>",
                parse_mode=HTML,
            )
            return asyncio.create_task(_auto_del(r, 40))
        try:
            limit  = int(args[1])
            secs   = int(args[2])
            action = args[3].lower() if len(args) > 3 else "mute"
            if action not in ("mute", "ban", "kick"):
                action = "mute"
        except (ValueError, IndexError):
            r = await message.reply_text("❌ Invalid numbers. Example: <code>/antiflood on 5 10</code>", parse_mode=HTML)
            return asyncio.create_task(_auto_del(r, 20))

        await antiflood_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"chat_id": chat_id, "enabled": True,
                      "limit": limit, "seconds": secs, "action": action}},
            upsert=True,
        )
        r = await message.reply_text(
            f"🌊 <b>Anti-flood Enabled</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📨 Limit : <b>{limit} messages</b>\n"
            f"⏱ Window: <b>{secs} seconds</b>\n"
            f"⚡ Action: <b>{action}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        asyncio.create_task(log_event(client,
            f"🌊 <b>Anti-flood Enabled</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📨 Limit  : <b>{limit} messages</b>\n"
            f"⏱ Window : <b>{secs} seconds</b>\n"
            f"⚡ Action : <b>{action}</b>\n"
            f"💬 Group  : <code>{chat_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
        return asyncio.create_task(_auto_del(r, 35))

    r = await message.reply_text(
        "Usage: <code>/antiflood on 5 10</code> | <code>/antiflood off</code> | <code>/antiflood status</code>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(r, 25))


@app.on_message(filters.group & filters.incoming & ~filters.service, group=3)
async def antiflood_enforcer(client: Client, message: Message):
    if not message.from_user:
        return
    if await _is_admin_msg(client, message):
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    doc = await antiflood_col.find_one({"chat_id": chat_id, "enabled": True})
    if not doc:
        return

    limit  = doc["limit"]
    secs   = doc["seconds"]
    action = doc.get("action", "mute")

    key = (chat_id, user_id)
    now = datetime.utcnow().timestamp()

    times = flood_tracker.get(key, [])
    times = [t for t in times if now - t < secs]
    times.append(now)
    flood_tracker[key] = times

    if len(times) < limit:
        return

    # Flood detected — clear tracker
    flood_tracker[key] = []
    name    = message.from_user.first_name or "User"
    mention = f'<a href="tg://user?id={user_id}">{name}</a>'

    try:
        await message.delete()
    except Exception:
        pass

    try:
        if action == "ban":
            await client.ban_chat_member(chat_id, user_id)
            verb = "🚫 Banned"
        elif action == "kick":
            await client.ban_chat_member(chat_id, user_id)
            await asyncio.sleep(1)
            await client.unban_chat_member(chat_id, user_id)
            verb = "👟 Kicked"
        else:
            until = datetime.utcnow() + timedelta(minutes=10)
            await client.restrict_chat_member(
                chat_id, user_id,
                ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            verb = "🔇 Muted (10 min)"
    except Exception:
        verb = "⚠️ Action failed"

    warn_msg = await client.send_message(
        chat_id,
        f"🌊 <b>FLOOD DETECTED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User  : {mention}\n"
        f"📨 Sent  : <b>{limit}+ messages</b> in <b>{secs}s</b>\n"
        f"⚡ Action : <b>{verb}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(warn_msg, 30))


# ═════════════════════════════════════════════════════════════════════════════
#  WELCOME MESSAGE SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("welcome") & filters.group)
async def welcome_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /welcome.")
        return asyncio.create_task(_auto_del(r, 15))

    args   = message.command[1:]
    sub    = args[0].lower() if args else ""
    chat_id = message.chat.id

    if sub == "off":
        await welcome_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"enabled": False}},
            upsert=True,
        )
        r = await message.reply_text("✅ Welcome message <b>disabled</b>.", parse_mode=HTML)
        asyncio.create_task(log_event(client,
            f"👋 <b>Welcome Message Disabled</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💬 Group : <code>{chat_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
        return asyncio.create_task(_auto_del(r, 25))

    if sub == "status":
        doc = await welcome_col.find_one({"chat_id": chat_id})
        if not doc or not doc.get("enabled"):
            r = await message.reply_text("👋 Welcome message: <b>OFF</b>", parse_mode=HTML)
        else:
            r = await message.reply_text(
                f"👋 <b>Welcome: ON</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{doc['text']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Variables: {{name}} {{username}} {{group}}</i>",
                parse_mode=HTML,
            )
        return asyncio.create_task(_auto_del(r, 40))

    if sub == "set":
        if len(args) < 2:
            r = await message.reply_text(
                "⚙️ <b>Welcome Usage:</b>\n"
                "<code>/welcome set Welcome {name} to {group}! 🎉</code>\n\n"
                "<b>Variables:</b>\n"
                "• <code>{name}</code> — member's name\n"
                "• <code>{username}</code> — @username\n"
                "• <code>{group}</code> — group name\n"
                "• <code>{count}</code> — member count\n\n"
                "<b>Other commands:</b>\n"
                "<code>/welcome off</code> — disable\n"
                "<code>/welcome status</code> — current message",
                parse_mode=HTML,
            )
            return asyncio.create_task(_auto_del(r, 60))

        text = " ".join(args[1:])
        await welcome_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"chat_id": chat_id, "enabled": True, "text": text}},
            upsert=True,
        )
        preview = text.format(
            name="সদস্য",
            username="@username",
            group="এই গ্রুপে",
            count="100",
        )
        r = await message.reply_text(
            f"✅ <b>Welcome Message Set!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Preview:</b>\n{preview}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        asyncio.create_task(log_event(client,
            f"👋 <b>Welcome Message Set</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💬 Group : <code>{chat_id}</code>\n"
            f"📝 Text  : {text[:100]}{'…' if len(text) > 100 else ''}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
        return asyncio.create_task(_auto_del(r, 40))

    r = await message.reply_text(
        "Usage: <code>/welcome set text</code> | <code>/welcome off</code> | <code>/welcome status</code>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(r, 25))


@app.on_message(filters.new_chat_members & filters.group, group=4)
async def welcome_new_member(client: Client, message: Message):
    doc = await welcome_col.find_one({"chat_id": message.chat.id, "enabled": True})
    if not doc:
        return

    try:
        chat    = await client.get_chat(message.chat.id)
        count   = chat.members_count or "?"
    except Exception:
        count = "?"

    for member in message.new_chat_members:
        if member.is_bot:
            continue
        name     = member.first_name or "Member"
        username = f"@{member.username}" if member.username else name
        group    = message.chat.title or "this group"

        try:
            text = doc["text"].format(
                name=name,
                username=username,
                group=group,
                count=count,
            )
        except Exception:
            text = doc["text"]

        try:
            w = await message.reply_text(
                text,
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(w, 120))
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  GROUP RULES SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("setrules") & filters.group)
async def setrules_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /setrules.")
        return asyncio.create_task(_auto_del(r, 15))

    args = message.command[1:]
    if not args:
        r = await message.reply_text(
            "⚙️ <b>Usage:</b>\n"
            "<code>/setrules 1. No spam\n2. Respect everyone\n3. No links</code>\n\n"
            "Or: <code>/clearrules</code> to remove rules.",
            parse_mode=HTML,
        )
        return asyncio.create_task(_auto_del(r, 40))

    rules_text = " ".join(args)
    await rules_col.update_one(
        {"chat_id": message.chat.id},
        {"$set": {"chat_id": message.chat.id, "rules": rules_text}},
        upsert=True,
    )
    r = await message.reply_text(
        "✅ <b>Group rules saved!</b>\n"
        "Members can view with: <code>/rules</code>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(r, 25))
    asyncio.create_task(log_event(client,
        f"📜 <b>Group Rules Updated</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 Group : <code>{message.chat.id}</code>\n"
        f"📝 Rules : {rules_text[:150]}{'…' if len(rules_text) > 150 else ''}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


@app.on_message(filters.command("rules") & filters.group)
async def rules_cmd(client: Client, message: Message):
    doc = await rules_col.find_one({"chat_id": message.chat.id})
    if not doc or not doc.get("rules"):
        r = await message.reply_text(
            "📜 No rules set for this group yet.\n"
            "Admins can set rules with: <code>/setrules</code>",
            parse_mode=HTML,
        )
        return asyncio.create_task(_auto_del(r, 25))

    group_name = message.chat.title or "this group"
    r = await message.reply_text(
        f"📜 <b>Rules of {group_name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{doc['rules']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(r, 90))


@app.on_message(filters.command("clearrules") & filters.group)
async def clearrules_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        r = await message.reply_text("❌ Only admins can use /clearrules.")
        return asyncio.create_task(_auto_del(r, 15))
    await rules_col.delete_one({"chat_id": message.chat.id})
    r = await message.reply_text("🧹 Group rules cleared.", parse_mode=HTML)
    asyncio.create_task(_auto_del(r, 20))
    asyncio.create_task(log_event(client,
        f"🧹 <b>Group Rules Cleared</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 Group : <code>{message.chat.id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


# ═════════════════════════════════════════════════════════════════════════════
#  Scheduled Broadcast Loop
# ═════════════════════════════════════════════════════════════════════════════

async def schedule_loop(client: Client):
    """Background task: checks every 60s for scheduled broadcasts that are due."""
    print("[SCHEDULE] Loop started.")
    while True:
        try:
            now  = datetime.utcnow()
            due  = await scheduled_col.find({"send_at": {"$lte": now}}).to_list(length=50)
            for doc in due:
                doc_id  = doc["_id"]
                session = doc.get("session", {})
                label   = doc.get("label", "?")
                print(f"[SCHEDULE] Firing scheduled broadcast id={doc_id} label={label}")

                # Send to admin as status message
                status_msg = await client.send_message(
                    ADMIN_ID,
                    f"📡 <b>Scheduled Broadcast Starting</b>\n"
                    f"⏰ Scheduled: {label}\n"
                    f"👥 Sending now...",
                    parse_mode=HTML,
                )

                # Run the broadcast using existing do_broadcast logic
                asyncio.create_task(_run_scheduled(client, session, status_msg, doc_id, label))

        except Exception as e:
            print(f"[SCHEDULE] Loop error: {e}")

        await asyncio.sleep(60)


async def _run_scheduled(client: Client, session: dict, status_msg, doc_id, label: str):
    """Execute a scheduled broadcast and clean it up."""
    try:
        # Reconstruct entities as empty list (formatting stored in text HTML)
        session["entities"] = []
        await do_broadcast(client, session, status_msg)
        await log_event(client,
            f"⏰ <b>Scheduled Broadcast Fired</b>\n"
            f"📅 Label: <b>{label}</b>\n"
            f"🆔 ID: <code>{doc_id}</code>"
        )
    except Exception as e:
        print(f"[SCHEDULE] Fire error id={doc_id}: {e}")
    finally:
        await scheduled_col.delete_one({"_id": doc_id})
        print(f"[SCHEDULE] Cleaned up doc id={doc_id}")


# ── /schedule commands ─────────────────────────────────────────────────────────

@app.on_message(filters.command("schedule") & filters.user(ADMIN_ID) & filters.private)
async def schedule_cmd(client: Client, message: Message):
    args = message.command[1:]
    sub  = args[0].lower() if args else "list"

    if sub == "list":
        docs = await scheduled_col.find().sort("send_at", 1).to_list(length=20)
        if not docs:
            return await message.reply_text(
                "📭 No scheduled broadcasts.\n\n"
                "Use /broadcast → ⏰ Schedule to create one."
            )
        lines = []
        for d in docs:
            bst = d["send_at"] + timedelta(hours=6)
            lines.append(
                f"📅 <b>{bst.strftime('%d.%m.%Y %H:%M')} BST</b>\n"
                f"   🆔 <code>{d['_id']}</code>"
            )
        text = "\n\n".join(lines)
        await message.reply_text(
            "⏰ <b>SCHEDULED BROADCASTS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{text}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Cancel: /schedule cancel &lt;ID&gt;",
            parse_mode=HTML,
        )
        return

    if sub == "cancel" and len(args) >= 2:
        from bson import ObjectId
        try:
            oid = ObjectId(args[1])
        except Exception:
            return await message.reply_text("❌ Invalid ID format.")
        doc = await scheduled_col.find_one({"_id": oid})
        if not doc:
            return await message.reply_text("❌ Scheduled broadcast not found.")
        await scheduled_col.delete_one({"_id": oid})
        await message.reply_text(
            f"🗑️ <b>Cancelled!</b>\n"
            f"Broadcast scheduled for <b>{doc.get('label', '?')}</b> has been removed.",
            parse_mode=HTML,
        )
        return

    await message.reply_text(
        "⏰ <b>SCHEDULE COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "/schedule list              — View all\n"
        "/schedule cancel &lt;ID&gt; — Cancel one\n\n"
        "To schedule: /broadcast → ⏰ Schedule",
        parse_mode=HTML,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  Log Channel Commands
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("logchannel") & filters.user(ADMIN_ID) & filters.private)
async def logchannel_cmd(client: Client, message: Message):
    args = message.command[1:]
    sub  = args[0].lower() if args else ""

    if sub == "set" and len(args) >= 2:
        raw = args[1]
        try:
            cid = int(raw)
        except ValueError:
            return await message.reply_text("❌ Please provide a numeric channel ID.\nExample: /logchannel set -1001234567890")
        await settings_col.update_one(
            {"key": "log_channel"}, {"$set": {"key": "log_channel", "chat_id": cid}}, upsert=True
        )
        # Test send
        try:
            await client.send_message(cid,
                "✅ <b>DESI MLH Log Channel Connected!</b>\n\n"
                "All important bot events will appear here.",
                parse_mode=HTML,
            )
            await message.reply_text(f"✅ Log channel set to <code>{cid}</code>\nTest message sent successfully!", parse_mode=HTML)
        except Exception as e:
            await message.reply_text(f"⚠️ Channel saved but test failed: {e}\nMake sure the bot is admin in that channel.", parse_mode=HTML)
        return

    if sub == "off":
        await settings_col.delete_one({"key": "log_channel"})
        await message.reply_text("❌ Log channel disabled.")
        return

    if sub == "test":
        await log_event(client, "🔔 <b>Test log event!</b>\nLog channel is working correctly.")
        await message.reply_text("✅ Test event sent to log channel.")
        return

    # Status
    cid = await get_log_channel()
    status = f"✅ Active — <code>{cid}</code>" if cid else "❌ Not set"
    await message.reply_text(
        "📋 <b>LOG CHANNEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Status: {status}\n\n"
        "Commands:\n"
        "/logchannel set -100xxx  — Set channel\n"
        "/logchannel off          — Disable\n"
        "/logchannel test         — Send test log\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ Bot must be <b>Admin</b> in the log channel.",
        parse_mode=HTML,
    )


# ═════════════════════════════════════════════════════════════════════════════
#  Regular user handler
# ═════════════════════════════════════════════════════════════════════════════

@app.on_message(
    filters.incoming & filters.text & filters.private
    & ~filters.user(ADMIN_ID)
    & ~filters.command(["start", "help", "video", "daily"])
)
async def text_handler(client: Client, message: Message):
    reply = REPLIES.get(message.text.lower().strip())
    if reply:
        await message.reply_text(reply)
    else:
        await message.reply_text(
            "I'm not sure how to respond to that. Try /help to see what I can do!"
        )


async def main():
    print("Bot is starting...")
    await app.start()
    loop = asyncio.get_event_loop()
    loop.create_task(schedule_loop(app))
    loop.create_task(nightmode_loop(app))
    print("[SCHEDULE] Background loop scheduled.")
    from pyrogram import idle as pyrogram_idle
    await pyrogram_idle()
    await app.stop()


app.run(main())
