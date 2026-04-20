"""
Group Protection System — TOS Compliant
=========================================
1. Anti-Forward    — Delete forwarded messages from non-admins + warning
2. Link Protection — Delete links from non-admins + rolling warning
3. Anti-Spam       — Configurable threshold, mute 5 minutes

EXEMPTIONS (always bypassed, no exceptions):
  - All group admins / creators
  - Bot owner (@IH_Maruf / BOT_OWNER_USERNAME)
  - Global ADMIN_IDS

NOTE: Invisible tag features have been fully removed.
      Only visible, real moderator actions are performed.
"""

import asyncio
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import Message, ChatPermissions

from config import HTML, ADMIN_ID, ADMIN_IDS, BOT_OWNER_USERNAME, db, app
from helpers import _auto_del, is_any_admin, _is_admin_msg, send_to_monitor


_prot_col = db["group_protections"]

_URL_RE = re.compile(
    r"(https?://|t\.me/|www\.|bit\.ly|tinyurl\.com|telegram\.me|telegram\.org|"
    r"youtu\.be|@\w{5,})",
    re.IGNORECASE,
)

_prot_cache: dict[int, dict]    = {}
_last_warn:  dict[tuple, int]   = {}
_spam_track: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=60))
_muted_at:   dict[tuple, float] = {}


async def _get_prot(chat_id: int) -> dict:
    if chat_id in _prot_cache:
        return _prot_cache[chat_id]
    doc = await _prot_col.find_one({"chat_id": chat_id})
    cfg = doc or {}
    _prot_cache[chat_id] = cfg
    return cfg


async def _save_prot(chat_id: int, key: str, value):
    _prot_cache.pop(chat_id, None)
    await _prot_col.update_one(
        {"chat_id": chat_id},
        {"$set": {key: value, "chat_id": chat_id}},
        upsert=True,
    )


async def _is_exempt(client: Client, message: Message) -> bool:
    """
    Returns True if user is exempt from all protections:
    - In global ADMIN_IDS
    - Is the bot owner (@IH_Maruf)
    - Is a group admin or creator
    """
    if not message.from_user:
        return False

    uid      = message.from_user.id
    username = (message.from_user.username or "").lower().lstrip("@")

    # Global admin / bot owner always exempt
    if uid in ADMIN_IDS:
        return True
    if username == BOT_OWNER_USERNAME.lower().lstrip("@"):
        return True

    # Check Telegram group admin status
    try:
        member = await client.get_chat_member(message.chat.id, uid)
        if member.status.name in ("ADMINISTRATOR", "OWNER"):
            return True
    except Exception:
        pass

    return False


# ── /protect command ──────────────────────────────────────────────────────────

@app.on_message(filters.command("protect") & filters.group, group=1)
async def protect_cmd(client: Client, message: Message):
    from handlers.control_group import is_control_group

    uid     = message.from_user.id if message.from_user else 0
    is_ctrl = await is_control_group(message.chat.id)

    if not is_ctrl and not await _is_admin_msg(client, message):
        return
    if not await is_any_admin(uid):
        return

    args = message.command[1:]

    KEY_MAP = {
        "forward": "anti_forward",
        "links":   "link_protection",
        "spam":    "anti_spam",
    }

    if not is_ctrl:
        if len(args) < 2:
            m = await message.reply_text(
                "<b>Protection Settings:</b>\n"
                "/protect forward on|off\n"
                "/protect links on|off\n"
                "/protect spam on|off\n"
                "/protect spam_limit [number]",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 20))
            return

        key_raw = args[0].lower()
        val_raw = args[1].lower() if len(args) > 1 else ""

        if key_raw == "spam_limit":
            try:
                limit = int(val_raw)
                await _save_prot(message.chat.id, "spam_limit", limit)
                m = await message.reply_text(f"Spam limit set to {limit} messages.", parse_mode=HTML)
                asyncio.create_task(_auto_del(m, 15))
            except ValueError:
                m = await message.reply_text("Usage: /protect spam_limit [number]", parse_mode=HTML)
                asyncio.create_task(_auto_del(m, 15))
            return

        db_key = KEY_MAP.get(key_raw)
        if not db_key:
            m = await message.reply_text(
                "Unknown setting. Use: forward | links | spam", parse_mode=HTML
            )
            asyncio.create_task(_auto_del(m, 15))
            return

        enabled = val_raw == "on"
        await _save_prot(message.chat.id, db_key, enabled)
        status = "enabled" if enabled else "disabled"
        m = await message.reply_text(f"{key_raw.title()} protection {status}.", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 15))
        return


# ── Anti-Forward enforcement ──────────────────────────────────────────────────

@app.on_message(filters.group & filters.forwarded & filters.incoming, group=10)
async def enforce_anti_forward(client: Client, message: Message):
    try:
        cfg = await _get_prot(message.chat.id)
        if not cfg.get("anti_forward"):
            return

        # Always exempt: group admins, bot owner, global admins
        if await _is_exempt(client, message):
            return

        await message.delete()

        uid = message.from_user.id if message.from_user else 0
        key = (message.chat.id, uid, "fwd")
        old_warn = _last_warn.get(key)
        if old_warn:
            try:
                await client.delete_messages(message.chat.id, old_warn)
            except Exception:
                pass

        warn = await client.send_message(
            message.chat.id,
            "<b>Forwarded messages are not allowed here.</b>\n"
            "<i>Group admins are exempt from this rule.</i>",
            parse_mode=HTML,
        )
        _last_warn[key] = warn.id
        asyncio.create_task(_auto_del(warn, 15))

    except Exception as e:
        print(f"[PROT/FWD] Error: {e}")


# ── Link Protection enforcement ───────────────────────────────────────────────

@app.on_message(filters.group & filters.incoming & ~filters.forwarded, group=11)
async def enforce_link_protection(client: Client, message: Message):
    try:
        cfg = await _get_prot(message.chat.id)
        if not cfg.get("link_protection"):
            return

        text = message.text or message.caption or ""
        if not _URL_RE.search(text):
            return

        # Always exempt: group admins, bot owner, global admins
        if await _is_exempt(client, message):
            return

        await message.delete()

        uid = message.from_user.id if message.from_user else 0
        key = (message.chat.id, uid, "link")
        old_warn = _last_warn.get(key)
        if old_warn:
            try:
                await client.delete_messages(message.chat.id, old_warn)
            except Exception:
                pass

        warn = await client.send_message(
            message.chat.id,
            "<b>Links are not allowed in this group.</b>\n"
            "<i>Group admins are exempt from this rule.</i>",
            parse_mode=HTML,
        )
        _last_warn[key] = warn.id
        asyncio.create_task(_auto_del(warn, 15))

    except Exception as e:
        print(f"[PROT/LINK] Error: {e}")


# ── Anti-Spam enforcement ─────────────────────────────────────────────────────

@app.on_message(filters.group & filters.incoming, group=12)
async def enforce_anti_spam(client: Client, message: Message):
    try:
        cfg = await _get_prot(message.chat.id)
        if not cfg.get("anti_spam"):
            return

        # Always exempt: group admins, bot owner, global admins
        if await _is_exempt(client, message):
            return

        uid = message.from_user.id if message.from_user else 0
        key = (message.chat.id, uid)
        now = time.monotonic()

        track = _spam_track[key]
        while track and now - track[0] > 10:
            track.popleft()
        track.append(now)

        limit = cfg.get("spam_limit", 5)
        if len(track) > limit:
            muted_time = _muted_at.get(key, 0)
            if now - muted_time < 300:
                return
            _muted_at[key] = now
            try:
                until = datetime.utcnow() + timedelta(minutes=5)
                await client.restrict_chat_member(
                    message.chat.id,
                    uid,
                    ChatPermissions(can_send_messages=False),
                    until_date=until,
                )
                warn = await client.send_message(
                    message.chat.id,
                    "<b>Spam detected.</b> User muted for 5 minutes.",
                    parse_mode=HTML,
                )
                asyncio.create_task(_auto_del(warn, 20))
            except Exception as e:
                print(f"[PROT/SPAM] Mute error: {e}")
    except Exception as e:
        print(f"[PROT/SPAM] Error: {e}")
