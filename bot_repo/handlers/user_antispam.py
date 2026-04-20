import asyncio
from collections import defaultdict
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, ADMIN_ID, users_col, app
from helpers import _auto_del, admin_filter

# ── Thresholds ─────────────────────────────────────────────────────────────────
_WINDOW_SECS   = 15       # rolling window in seconds
_WARN_AT       = 8        # messages before warning
_BLOCK_AT      = 15       # messages before soft-block
_BLOCK_MINUTES = 10       # block duration
_MAX_WARNINGS  = 3        # warnings before block

# ── In-memory tracking ─────────────────────────────────────────────────────────
_msg_times:  dict[int, list[datetime]] = defaultdict(list)
_warn_count: dict[int, int]            = defaultdict(int)
_blocked_until: dict[int, datetime]    = {}


def _count_recent(user_id: int) -> int:
    now    = datetime.utcnow()
    cutoff = now - timedelta(seconds=_WINDOW_SECS)
    times  = [t for t in _msg_times[user_id] if t >= cutoff]
    _msg_times[user_id] = times
    return len(times)


def _is_blocked(user_id: int) -> bool:
    until = _blocked_until.get(user_id)
    if until and datetime.utcnow() < until:
        return True
    if user_id in _blocked_until:
        del _blocked_until[user_id]
    return False


def _block_user(user_id: int):
    _blocked_until[user_id] = datetime.utcnow() + timedelta(minutes=_BLOCK_MINUTES)
    _warn_count[user_id]    = 0
    _msg_times[user_id]     = []


# ─── Anti-spam handler: private messages, group=3 (before other handlers) ─────

@app.on_message(filters.private & filters.incoming & ~admin_filter, group=3)
async def user_antispam_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return

    uid = user.id

    # ── Already blocked? ──────────────────────────────────────────────────────
    if _is_blocked(uid):
        until  = _blocked_until[uid]
        mins_left = max(0, int((until - datetime.utcnow()).total_seconds() / 60))
        try:
            await message.delete()
        except Exception:
            pass
        m = await message.reply_text(
            f"🚫 <b>Slow down!</b>\n\n"
            f"You're sending messages too fast.\n"
            f"⏳ Please wait <b>{mins_left} more minute(s)</b>.",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 10))
        return

    # ── Track this message ────────────────────────────────────────────────────
    _msg_times[uid].append(datetime.utcnow())
    count = _count_recent(uid)

    # ── Block threshold reached ───────────────────────────────────────────────
    if count >= _BLOCK_AT:
        _block_user(uid)
        print(f"[ANTI-SPAM] Blocked user={uid} for {_BLOCK_MINUTES}min (count={count})")
        try:
            await message.delete()
        except Exception:
            pass
        m = await message.reply_text(
            f"🚫 <b>You've been temporarily restricted!</b>\n\n"
            f"Too many messages in a short time.\n"
            f"⏳ You can message again in <b>{_BLOCK_MINUTES} minutes</b>.\n\n"
            f"<i>Repeated violations may result in a permanent ban.</i>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 30))
        await users_col.update_one(
            {"user_id": uid},
            {"$inc": {"spam_blocks": 1}},
        )
        return

    # ── Warning threshold reached ─────────────────────────────────────────────
    if count >= _WARN_AT:
        _warn_count[uid] += 1
        wc         = _warn_count[uid]
        remaining  = _BLOCK_AT - count
        m = await message.reply_text(
            f"⚠️ <b>Warning {wc}/{_MAX_WARNINGS}</b>  🐌 Slow down!\n\n"
            f"You're sending messages too fast.\n"
            f"📨 {remaining} more message(s) = temporary block.",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 8))
