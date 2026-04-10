"""
Group Protection System — Control Group থেকে পরিচালনা
───────────────────────────────────────────────────────
১. Anti-Forward    — Forward মেসেজ DELETE + warning (warning-ও delete হবে)
২. Link Protection — লিঙ্ক মেসেজ DELETE + rolling warning
                     পরের warning আসলে আগেরটা auto-delete
৩. Anti-Spam       — configurable threshold, mute 5 মিনিট

Control Group Commands:
  /protect [gid] forward on|off
  /protect [gid] links   on|off
  /protect [gid] spam    on|off
  /protect [gid] spam_limit 5
  /protections            — সব গ্রুপ দেখুন
"""

import asyncio
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import Message, ChatPermissions

from config import HTML, ADMIN_ID, db, app
from helpers import _auto_del, is_any_admin, _is_admin_msg, send_to_monitor


# ── DB ─────────────────────────────────────────────────────────────────────────
_prot_col = db["group_protections"]

# ── URL Regex ──────────────────────────────────────────────────────────────────
_URL_RE = re.compile(
    r"(https?://|t\.me/|www\.|bit\.ly|tinyurl\.com|telegram\.me|telegram\.org|"
    r"youtu\.be|@\w{5,})",
    re.IGNORECASE,
)

# ── Caches ─────────────────────────────────────────────────────────────────────
_prot_cache: dict[int, dict]    = {}
_last_warn:  dict[tuple, int]   = {}          # (chat_id, uid, kind) → msg_id
_spam_track: dict[tuple, deque] = defaultdict(lambda: deque(maxlen=60))
_muted_at:   dict[tuple, float] = {}


# ── DB helpers ─────────────────────────────────────────────────────────────────

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


# ── /protect ───────────────────────────────────────────────────────────────────

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

    # ── Group-internal: /protect forward on ───────────────────────────────────
    if not is_ctrl:
        if len(args) < 2:
            m = await message.reply_text(
                "📋 <b>Protection সেট করুন:</b>\n"
                "<code>/protect forward on|off</code>\n"
                "<code>/protect links on|off</code>\n"
                "<code>/protect spam on|off</code>\n"
                "<code>/protect spam_limit 5</code>",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 20))
            return

        ptype = args[0].lower()
        val   = args[1].lower()
        cid   = message.chat.id

        if ptype == "spam_limit":
            try:
                n = int(val)
                await _save_prot(cid, "spam_limit", n)
                m = await message.reply_text(
                    f"✅ Spam limit: <b>{n} মেসেজ/১০ সেকেন্ড</b>", parse_mode=HTML
                )
            except ValueError:
                m = await message.reply_text("❌ সংখ্যা দিন।", parse_mode=HTML)
            asyncio.create_task(_auto_del(m, 15))
            return

        if ptype not in KEY_MAP:
            m = await message.reply_text(
                "❌ ধরন: <code>forward</code> / <code>links</code> / <code>spam</code>",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 15))
            return

        enabled = val in ("on", "true", "1")
        await _save_prot(cid, KEY_MAP[ptype], enabled)
        icon = "✅" if enabled else "❌"
        m = await message.reply_text(
            f"{icon} <b>{ptype.title()} Protection</b> {'চালু' if enabled else 'বন্ধ'} করা হয়েছে।",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 15))
        try:
            await message.delete()
        except Exception:
            pass
        return

    # ── Control Group: /protect [gid] forward on ──────────────────────────────
    if len(args) < 3:
        m = await message.reply_text(
            "📋 <b>Control Group থেকে:</b>\n"
            "<code>/protect [group_id] forward on|off</code>\n"
            "<code>/protect [group_id] links on|off</code>\n"
            "<code>/protect [group_id] spam on|off</code>\n"
            "<code>/protect [group_id] spam_limit 5</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 25))
        return

    raw_gid = args[0]
    if not raw_gid.lstrip("-").isdigit():
        m = await message.reply_text("❌ সঠিক group ID দিন।", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 15))
        return

    cid   = int(raw_gid)
    ptype = args[1].lower()
    val   = args[2].lower()

    try:
        chat  = await client.get_chat(cid)
        title = chat.title or str(cid)
    except Exception:
        title = str(cid)

    if ptype == "spam_limit":
        try:
            n = int(val)
            await _save_prot(cid, "spam_limit", n)
            m = await message.reply_text(
                f"✅ <b>{title}</b>\nSpam limit: <b>{n} msgs/10s</b>", parse_mode=HTML
            )
        except ValueError:
            m = await message.reply_text("❌ সংখ্যা দিন।", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 15))
        return

    if ptype not in KEY_MAP:
        m = await message.reply_text(
            "❌ ধরন: <code>forward</code> / <code>links</code> / <code>spam</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 15))
        return

    enabled = val in ("on", "true", "1")
    await _save_prot(cid, KEY_MAP[ptype], enabled)
    icon = "✅" if enabled else "❌"
    m = await message.reply_text(
        f"{icon} <b>{ptype.title()} Protection</b>\n"
        f"📍 <b>{title}</b> — {'চালু' if enabled else 'বন্ধ'}",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 20))
    try:
        await message.delete()
    except Exception:
        pass


# ── /protections ───────────────────────────────────────────────────────────────

@app.on_message(filters.command("protections") & filters.group, group=1)
async def protections_cmd(client: Client, message: Message):
    from handlers.control_group import is_control_group

    uid = message.from_user.id if message.from_user else 0
    if not await is_any_admin(uid):
        return

    is_ctrl = await is_control_group(message.chat.id)

    if is_ctrl:
        docs = await _prot_col.find({}).to_list(length=200)
        if not docs:
            m = await message.reply_text("📭 কোনো গ্রুপে protection সেট নেই।", parse_mode=HTML)
            asyncio.create_task(_auto_del(m, 15))
            return

        lines = ["🛡️ <b>সব গ্রুপের Protection</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
        for d in docs:
            cid   = d.get("chat_id", "?")
            fwd   = "✅" if d.get("anti_forward")    else "❌"
            lnk   = "✅" if d.get("link_protection")  else "❌"
            sp    = "✅" if d.get("anti_spam")         else "❌"
            lim   = d.get("spam_limit", 5)
            lines.append(f"📍 <code>{cid}</code>  Fwd:{fwd} Link:{lnk} Spam:{sp}({lim}/10s)")

        m = await message.reply_text("\n".join(lines), parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 90))
    else:
        cid = message.chat.id
        cfg = await _get_prot(cid)
        m   = await message.reply_text(
            f"🛡️ <b>এই গ্রুপের Protection</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔄 Anti-Forward   : {'✅ চালু' if cfg.get('anti_forward')   else '❌ বন্ধ'}\n"
            f"🔗 Link Protection: {'✅ চালু' if cfg.get('link_protection') else '❌ বন্ধ'}\n"
            f"⚡ Anti-Spam      : {'✅ চালু' if cfg.get('anti_spam')        else '❌ বন্ধ'} "
            f"({cfg.get('spam_limit', 5)} msgs/10s)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"সেট: <code>/protect forward|links|spam on|off</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 30))

    try:
        await message.delete()
    except Exception:
        pass


# ── Main Protection Handler (group=3, fires before most handlers) ──────────────

@app.on_message(filters.group & ~filters.service, group=3)
async def _protection_handler(client: Client, message: Message):
    if not message.from_user or message.from_user.is_bot:
        return
    if await _is_admin_msg(client, message):
        return

    chat_id = message.chat.id
    uid     = message.from_user.id
    fname   = message.from_user.first_name or "User"
    mention = f'<a href="tg://user?id={uid}">{fname}</a>'
    cfg     = await _get_prot(chat_id)

    # ── 1. Anti-Forward ───────────────────────────────────────────────────────
    if cfg.get("anti_forward"):
        is_fwd = (
            message.forward_date is not None
            or message.forward_from is not None
            or message.forward_from_chat is not None
            or message.forward_sender_name is not None
        )
        if is_fwd:
            try:
                await message.delete()
            except Exception:
                pass

            if cfg.get("show_warning", True):
                wkey = (chat_id, uid, "fwd")
                old  = _last_warn.pop(wkey, None)
                if old:
                    try:
                        await client.delete_messages(chat_id, old)
                    except Exception:
                        pass

                w = await client.send_message(
                    chat_id,
                    f"⚠️ {mention}\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔄 <b>Forward করা মেসেজ এই গ্রুপে নিষিদ্ধ।</b>\n"
                    f"মেসেজটি মুছে ফেলা হয়েছে।\n━━━━━━━━━━━━━━━━━━━━━━",
                    parse_mode=HTML,
                )
                _last_warn[wkey] = w.id
                asyncio.create_task(_auto_del(w, 30))

            asyncio.create_task(send_to_monitor(
                client,
                f"🔄 <b>Anti-Forward</b>\n"
                f"👤 {mention} (<code>{uid}</code>)\n"
                f"📌 গ্রুপ: <b>{message.chat.title}</b> (<code>{chat_id}</code>)\n"
                f"🕛 {datetime.utcnow().strftime('%d %b %H:%M UTC')}",
            ))
            return

    # ── 2. Link Protection ────────────────────────────────────────────────────
    if cfg.get("link_protection"):
        txt = message.text or message.caption or ""
        if _URL_RE.search(txt):
            try:
                await message.delete()
            except Exception:
                pass

            if cfg.get("show_warning", True):
                wkey = (chat_id, uid, "link")
                old  = _last_warn.pop(wkey, None)
                if old:
                    try:
                        await client.delete_messages(chat_id, old)
                    except Exception:
                        pass

                w = await client.send_message(
                    chat_id,
                    f"🔗 {mention}\n━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>এই গ্রুপে লিঙ্ক শেয়ার নিষিদ্ধ।</b>\n"
                    f"মেসেজ ডিলিট হয়েছে।\n━━━━━━━━━━━━━━━━━━━━━━",
                    parse_mode=HTML,
                )
                _last_warn[wkey] = w.id
                asyncio.create_task(_auto_del(w, 45))

            asyncio.create_task(send_to_monitor(
                client,
                f"🔗 <b>Link Protection</b>\n"
                f"👤 {mention} (<code>{uid}</code>)\n"
                f"📌 গ্রুপ: <b>{message.chat.title}</b> (<code>{chat_id}</code>)\n"
                f"🕛 {datetime.utcnow().strftime('%d %b %H:%M UTC')}",
            ))
            return

    # ── 3. Anti-Spam ──────────────────────────────────────────────────────────
    if cfg.get("anti_spam"):
        limit  = cfg.get("spam_limit", 5)
        skey   = (chat_id, uid)
        now_ts = time.monotonic()
        dq     = _spam_track[skey]

        while dq and now_ts - dq[0] > 10:
            dq.popleft()
        dq.append(now_ts)

        if len(dq) >= limit:
            last = _muted_at.get(skey, 0)
            if now_ts - last < 60:
                return
            _muted_at[skey] = now_ts
            dq.clear()

            try:
                await client.restrict_chat_member(
                    chat_id, uid,
                    ChatPermissions(can_send_messages=False),
                    until_date=datetime.utcnow() + timedelta(minutes=5),
                )
            except Exception:
                pass

            wkey = (chat_id, uid, "spam")
            old  = _last_warn.pop(wkey, None)
            if old:
                try:
                    await client.delete_messages(chat_id, old)
                except Exception:
                    pass

            w = await client.send_message(
                chat_id,
                f"⚡ {mention}\n━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Spam সনাক্ত!</b> {limit}+ মেসেজ ১০ সেকেন্ডে।\n"
                f"⏱ <b>৫ মিনিটের Mute</b>\n━━━━━━━━━━━━━━━━━━━━━━",
                parse_mode=HTML,
            )
            _last_warn[wkey] = w.id
            asyncio.create_task(_auto_del(w, 60))
            asyncio.create_task(send_to_monitor(
                client,
                f"⚡ <b>Anti-Spam — Muted</b>\n"
                f"👤 {mention} (<code>{uid}</code>)\n"
                f"📌 গ্রুপ: <b>{message.chat.title}</b> (<code>{chat_id}</code>)\n"
                f"📊 Limit: {limit} msgs/10s → 5 মিনিট Mute\n"
                f"🕛 {datetime.utcnow().strftime('%d %b %H:%M UTC')}",
            ))
