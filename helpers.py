import re
import asyncio
import aiohttp
import contextvars
from datetime import datetime, timedelta

from pyrogram import Client, enums, filters as _pf
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions

from config import (
    HTML, BOT_TOKEN, ADMIN_ID, ADMIN_IDS,
    users_col, settings_col, scheduled_col, groups_col,
    broadcast_sessions,
    STATE_CUSTOMIZE,
    admins_col,
)

# ── Per-update context: active bot token + clone config ───────────────────────
_bot_token_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bot_token", default=BOT_TOKEN
)
_clone_config_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "clone_config", default=None
)

BOT_USERNAME_CACHE: str = ""


def get_cfg(key: str, fallback=None, client=None):
    """Read from active clone config first; fall back to global value.

    Priority: client._clone_config > ContextVar > fallback
    """
    # Method 1: client attribute (most reliable for handlers)
    if client is not None:
        cfg = getattr(client, "_clone_config", None)
        if cfg and cfg.get(key) is not None:
            return cfg[key]
    # Method 2: ContextVar (set by injector per-update)
    cfg = _clone_config_ctx.get()
    if cfg and cfg.get(key) is not None:
        return cfg[key]
    return fallback


# ── Dynamic admin checks ───────────────────────────────────────────────────────

async def is_any_admin(user_id: int) -> bool:
    """Main-bot admin check: super admin OR DB sub-admin."""
    if user_id in ADMIN_IDS:
        return True
    doc = await admins_col.find_one({"user_id": user_id, "active": True})
    return doc is not None


def is_clone_context() -> bool:
    return _clone_config_ctx.get() is not None


async def _admin_filter_func(flt, client, update) -> bool:
    user = getattr(update, "from_user", None)
    if not user:
        return False
    uid = user.id
    # client attribute is the most reliable (set at clone build time)
    cfg = getattr(client, "_clone_config", None) or _clone_config_ctx.get()
    if cfg:
        return uid in ADMIN_IDS or uid == cfg.get("admin_id")
    return await is_any_admin(uid)


async def _clone_admin_only_func(flt, client, update) -> bool:
    """True only when in clone context AND user is that clone's admin (or ADMIN_IDS)."""
    user = getattr(update, "from_user", None)
    if not user:
        return False
    cfg = getattr(client, "_clone_config", None) or _clone_config_ctx.get()
    if not cfg:
        return False
    return user.id in ADMIN_IDS or user.id == cfg.get("admin_id")


admin_filter       = _pf.create(_admin_filter_func,      name="AdminFilter")
clone_admin_filter = _pf.create(_clone_admin_only_func,  name="CloneAdminFilter")


async def get_bot_username(client: Client) -> str:
    import config
    if not config.BOT_USERNAME:
        me = await client.get_me()
        config.BOT_USERNAME = me.username or ""
    return config.BOT_USERNAME


async def get_log_channel(client=None) -> int | None:
    # Priority 1: clone's log_group from client attribute
    if client is not None:
        cfg = getattr(client, "_clone_config", None) or _clone_config_ctx.get()
        if cfg and cfg.get("log_group"):
            return cfg["log_group"]
    # Priority 2: ContextVar
    clone_lg = get_cfg("log_group")
    if clone_lg:
        return clone_lg
    # Priority 3: global settings_col
    doc = await settings_col.find_one({"key": "log_channel"})
    if doc and doc.get("chat_id"):
        return doc["chat_id"]
    # Priority 4: LOG_CHANNEL from .env / config
    from config import LOG_CHANNEL as _LC
    return _LC if _LC else None


async def log_event(client: Client, text: str):
    try:
        cid = await get_log_channel(client=client)
        if cid:
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M") + " UTC"
            # Use client's own bot token for sending logs
            bot_token = getattr(client, "_bot_token", None) or _bot_token_ctx.get() or BOT_TOKEN
            import aiohttp as _aiohttp
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            async with _aiohttp.ClientSession() as _sess:
                await _sess.post(url, json={
                    "chat_id":    cid,
                    "text":       f"🗒 <b>LOG</b> | {now}\n\n{text}",
                    "parse_mode": "HTML",
                })
    except Exception as e:
        print(f"[LOG_EVENT] Failed: {e}")


async def send_to_monitor(client: Client, text: str, parse_mode: str = "HTML"):
    """Send an activity event to the Monitor Group."""
    try:
        from handlers.control_group import get_monitor_group
        mon = await get_monitor_group()
        if not mon:
            return
        bot_token = getattr(client, "_bot_token", None) or _bot_token_ctx.get() or BOT_TOKEN
        import aiohttp as _aiohttp
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        async with _aiohttp.ClientSession() as _sess:
            await _sess.post(url, json={
                "chat_id":    mon,
                "text":       text,
                "parse_mode": parse_mode,
            })
    except Exception as e:
        print(f"[MONITOR] Failed: {e}")


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


async def get_custom_buttons(chat_id: int) -> InlineKeyboardMarkup | None:
    """Get custom buttons configured for a group."""
    from config import group_settings_col
    doc = await group_settings_col.find_one({"chat_id": chat_id})
    if not doc or not doc.get("custom_buttons"):
        return None

    buttons = doc["custom_buttons"]
    if not buttons:
        return None

    # Create inline keyboard
    keyboard = []
    for btn in buttons:
        keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])

    return InlineKeyboardMarkup(keyboard)


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


def _kb_to_json(reply_markup) -> dict | None:
    """Convert Pyrogram InlineKeyboardMarkup → Bot API JSON dict."""
    if reply_markup is None:
        return None
    rows = []
    for row in reply_markup.inline_keyboard:
        btn_row = []
        for btn in row:
            b = {"text": btn.text}
            if btn.url:
                b["url"] = btn.url
            elif btn.callback_data:
                b["callback_data"] = btn.callback_data
            btn_row.append(b)
        rows.append(btn_row)
    return {"inline_keyboard": rows}


class _FakeMsg:
    """Minimal message-like object returned by bot_api calls."""
    __slots__ = ("id",)
    def __init__(self, msg_id):
        self.id = msg_id


async def _send_media(client: Client, chat_id: int, session: dict,
                      caption=None, caption_entities=None, reply_markup=None):
    """
    Send media via Bot API directly — avoids ALL Pyrogram version quirks.

    Strategy (most-to-least reliable):
      1. copyMessage  — when from_chat_id + message_id are known (immediate broadcast)
      2. sendPhoto / sendVideo / … — using stored file_id (scheduled broadcast)
    """
    from_chat_id = session.get("media_chat_id")
    msg_id       = session.get("media_msg_id")
    file_id      = session.get("file_id")
    media_kind   = session.get("media_kind", "document")

    print(f"[_send_media] chat={chat_id} kind={media_kind} "
          f"fid={bool(file_id)} copy={bool(from_chat_id and msg_id)} cap={bool(caption)}")

    kb_json = _kb_to_json(reply_markup)

    # ── PATH 1: copyMessage (most reliable, works for ALL media types) ────────
    if from_chat_id and msg_id:
        params: dict = {
            "chat_id":      chat_id,
            "from_chat_id": from_chat_id,
            "message_id":   msg_id,
        }
        # Only override caption if we have something to say
        if caption:
            params["caption"] = caption
        if kb_json:
            params["reply_markup"] = kb_json

        resp = await bot_api("copyMessage", params)
        if resp.get("ok"):
            result = resp.get("result", {})
            return _FakeMsg(result.get("message_id"))
        else:
            print(f"[_send_media] copyMessage failed ({resp.get('description')}), trying sendFile...")
            # fall through to PATH 2

    # ── PATH 2: sendPhoto / sendVideo / … using file_id ──────────────────────
    if not file_id:
        raise RuntimeError("No media source: media_chat_id/msg_id unavailable and file_id missing")

    method_map = {
        "photo":      ("sendPhoto",      "photo"),
        "video":      ("sendVideo",      "video"),
        "animation":  ("sendAnimation",  "animation"),
        "document":   ("sendDocument",   "document"),
        "audio":      ("sendAudio",      "audio"),
        "voice":      ("sendVoice",      "voice"),
        "sticker":    ("sendSticker",    "sticker"),
        "video_note": ("sendVideoNote",  "video_note"),
    }
    if media_kind not in method_map:
        media_kind = "document"

    api_method, field_name = method_map[media_kind]
    params = {"chat_id": chat_id, field_name: file_id}
    if media_kind not in ("sticker", "video_note") and caption:
        params["caption"] = caption
    if kb_json:
        params["reply_markup"] = kb_json

    resp = await bot_api(api_method, params)
    if resp.get("ok"):
        result = resp.get("result", {})
        return _FakeMsg(result.get("message_id"))
    else:
        raise RuntimeError(f"Bot API {api_method} failed: {resp.get('description', 'unknown')}")


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
            # Use Bot API to avoid Pyrogram PEER_ID_INVALID
            kb_json = _kb_to_json(kb)
            params: dict = {
                "chat_id":    chat_id,
                "text":       text or "(empty)",
                "parse_mode": "HTML",
            }
            if kb_json:
                params["reply_markup"] = kb_json
            resp = await bot_api("sendMessage", params)
            if resp.get("ok"):
                sent = _FakeMsg(resp["result"]["message_id"])
            else:
                raise RuntimeError(resp.get("description", "sendMessage failed"))
        elif msg_type == "media":
            # Use file_id directly for preview (more reliable than copyMessage)
            file_id    = session.get("file_id")
            media_kind = session.get("media_kind", "photo")
            _preview_method = {
                "photo":      ("sendPhoto",      "photo"),
                "video":      ("sendVideo",      "video"),
                "animation":  ("sendAnimation",  "animation"),
                "document":   ("sendDocument",   "document"),
                "audio":      ("sendAudio",      "audio"),
                "voice":      ("sendVoice",      "voice"),
                "sticker":    ("sendSticker",    "sticker"),
                "video_note": ("sendVideoNote",  "video_note"),
            }
            if file_id and media_kind in _preview_method:
                api_method, field_name = _preview_method[media_kind]
                _p: dict = {"chat_id": chat_id, field_name: file_id}
                if text and media_kind not in ("sticker", "video_note"):
                    _p["caption"]    = text
                    _p["parse_mode"] = "HTML"
                _kb = _kb_to_json(kb)
                if _kb:
                    _p["reply_markup"] = _kb
                _r = await bot_api(api_method, _p)
                if _r.get("ok"):
                    sent = _FakeMsg(_r["result"]["message_id"])
                    print(f"[refresh_preview] media preview sent via {api_method}")
                else:
                    raise RuntimeError(_r.get("description", f"{api_method} failed"))
            else:
                sent = await _send_media(client, chat_id, session,
                                         caption=text, caption_entities=entities, reply_markup=kb)
    except Exception as e:
        print(f"[refresh_preview] error: {e}")
        try:
            kb_json = _kb_to_json(kb)
            params_err: dict = {
                "chat_id":    chat_id,
                "text":       f"⚠️ Preview failed: <code>{e}</code>\n\nMidia আবার পাঠান।",
                "parse_mode": "HTML",
            }
            if kb_json:
                params_err["reply_markup"] = kb_json
            r = await bot_api("sendMessage", params_err)
            if r.get("ok"):
                sent = _FakeMsg(r["result"]["message_id"])
        except Exception as e2:
            print(f"[refresh_preview] error fallback also failed: {e2}")

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

    if msg_type == "text":
        # Use Bot API (HTTP) to avoid Pyrogram PEER_ID_INVALID for uncached users
        kb_json = _kb_to_json(reply_markup)
        params: dict = {"chat_id": uid, "text": text or "(empty)", "parse_mode": "HTML"}
        if kb_json:
            params["reply_markup"] = kb_json
        resp = await bot_api("sendMessage", params)
        if resp.get("ok"):
            return _FakeMsg(resp["result"]["message_id"])
        raise RuntimeError(f"sendMessage failed: {resp.get('description', 'unknown')}")
    elif msg_type == "media":
        return await _send_media(client, uid, session,
                                 caption=text, reply_markup=reply_markup)
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
        except Exception as _err:
            print(f"[BROADCAST] uid={uid} FAILED: {_err}")
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

    # ── Also broadcast to all groups where bot is a member ────────────────────
    group_sent = group_failed = 0
    try:
        group_docs = await groups_col.find({}).to_list(length=None)
        for gdoc in group_docs:
            gid = gdoc.get("chat_id")
            if not gid:
                continue
            try:
                await send_to_user(client, gid, session, reply_markup=extra_kb)
                group_sent += 1
            except Exception as _gerr:
                print(f"[BROADCAST] group={gid} FAILED: {_gerr}")
                group_failed += 1
            await asyncio.sleep(0.1)
    except Exception as _ge:
        print(f"[BROADCAST] Group loop error: {_ge}")

    aud = audience_label(session)
    group_line = f"📣 Groups: <b>{group_sent:,}</b> sent, <b>{group_failed:,}</b> failed\n" if (group_sent + group_failed) > 0 else ""
    await status_msg.edit_text(
        "✅ <b>Broadcast Sent Successfully!</b>\n\n"
        f"📨 Users: <b>{sent:,}</b> delivered, <b>{failed:,}</b> failed\n"
        f"{group_line}"
        "\nUse /broadcast to start a new broadcast anytime.",
        parse_mode=HTML,
    )
    broadcast_sessions.pop(session.get("chat_id", ADMIN_ID), None)
    await log_event(client,
        f"📢 <b>Broadcast Completed</b>\n"
        f"👥 Filter: {aud}\n"
        f"✅ Users: <b>{sent:,}</b>  ❌ Failed: <b>{failed:,}</b>\n"
        f"📣 Groups: <b>{group_sent:,}</b> sent, <b>{group_failed:,}</b> failed"
    )


async def bot_api(method: str, params: dict, token: str = None) -> dict:
    t   = token or _bot_token_ctx.get()
    url = f"https://api.telegram.org/bot{t}/{method}"
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
