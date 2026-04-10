"""
Keyword Auto-Reply System
──────────────────────────
Control Group থেকে keywords সেট করা যাবে।
গ্রুপে কেউ keyword সহ মেসেজ পাঠালে বট auto-reply দেবে।

Control Group Commands:
  /kw add [word] [reply]  — keyword যোগ
  /kw del [word]          — keyword সরানো
  /kw list                — সব keyword দেখুন
  /kw clear               — সব মুছুন

উদাহরণ:
  /kw add hello হ্যালো! স্বাগতম!
  /kw add video ভিডিও পেতে বটে /video কমান্ড দিন।
"""

import asyncio
import re

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, db, app
from helpers import _auto_del, is_any_admin


# ── DB ─────────────────────────────────────────────────────────────────────────
_kw_col = db["keyword_triggers"]

# ── In-memory cache ────────────────────────────────────────────────────────────
_kw_cache: list | None = None


async def _load_kw() -> list:
    global _kw_cache
    if _kw_cache is not None:
        return _kw_cache
    docs = await _kw_col.find({}).to_list(length=500)
    _kw_cache = docs
    return docs


def _drop_cache():
    global _kw_cache
    _kw_cache = None


# ── /kw command ────────────────────────────────────────────────────────────────

@app.on_message(filters.command("kw") & filters.group, group=1)
async def kw_cmd(client: Client, message: Message):
    from handlers.control_group import is_control_group

    uid = message.from_user.id if message.from_user else 0
    if not await is_any_admin(uid):
        return
    if not await is_control_group(message.chat.id):
        return

    args = message.command[1:]
    if not args:
        await _kw_usage(message)
        return

    sub = args[0].lower()

    # ── add ───────────────────────────────────────────────────────────────────
    if sub == "add":
        if len(args) < 3:
            m = await message.reply_text(
                "⚠️ <b>ব্যবহার:</b>\n"
                "<code>/kw add [keyword] [reply মেসেজ]</code>\n\n"
                "<b>উদাহরণ:</b>\n"
                "<code>/kw add hello হ্যালো! কেমন আছেন?</code>",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 25))
            return

        kw    = args[1].strip().lower()
        reply = " ".join(args[2:]).strip()

        await _kw_col.update_one(
            {"keyword": kw},
            {"$set": {"keyword": kw, "reply": reply}},
            upsert=True,
        )
        _drop_cache()

        m = await message.reply_text(
            f"✅ <b>Keyword যোগ হয়েছে!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔑 Keyword : <code>{kw}</code>\n"
            f"💬 Reply   : {reply}",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 25))

    # ── del ───────────────────────────────────────────────────────────────────
    elif sub == "del":
        if len(args) < 2:
            m = await message.reply_text(
                "⚠️ <b>ব্যবহার:</b> <code>/kw del [keyword]</code>", parse_mode=HTML
            )
            asyncio.create_task(_auto_del(m, 20))
            return

        kw  = args[1].strip().lower()
        res = await _kw_col.delete_one({"keyword": kw})
        _drop_cache()

        if res.deleted_count:
            m = await message.reply_text(
                f"🗑️ Keyword মুছে গেছে: <code>{kw}</code>", parse_mode=HTML
            )
        else:
            m = await message.reply_text(
                f"❌ Keyword পাওয়া যায়নি: <code>{kw}</code>", parse_mode=HTML
            )
        asyncio.create_task(_auto_del(m, 20))

    # ── list ──────────────────────────────────────────────────────────────────
    elif sub == "list":
        docs = await _kw_col.find({}).to_list(length=300)
        if not docs:
            m = await message.reply_text("📭 কোনো keyword সেট নেই।", parse_mode=HTML)
            asyncio.create_task(_auto_del(m, 20))
            return

        lines = ["🔑 <b>Keyword তালিকা</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
        for i, d in enumerate(docs, 1):
            kw  = d.get("keyword", "?")
            rep = (d.get("reply") or "")[:50]
            if len(d.get("reply", "")) > 50:
                rep += "…"
            lines.append(f"{i}. <code>{kw}</code> → {rep}")
        lines.append(f"\n📊 মোট: <b>{len(docs)}</b> টি")

        m = await message.reply_text("\n".join(lines), parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 90))

    # ── clear ─────────────────────────────────────────────────────────────────
    elif sub == "clear":
        cnt = (await _kw_col.delete_many({})).deleted_count
        _drop_cache()
        m = await message.reply_text(
            f"🗑️ সব keyword মুছে গেছে। মোট: <b>{cnt}</b> টি।", parse_mode=HTML
        )
        asyncio.create_task(_auto_del(m, 20))

    else:
        await _kw_usage(message)

    try:
        await message.delete()
    except Exception:
        pass


async def _kw_usage(message: Message):
    m = await message.reply_text(
        "🔑 <b>Keyword System</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "/kw add [word] [reply]  — যোগ করুন\n"
        "/kw del [word]          — সরানো\n"
        "/kw list                — সব দেখুন\n"
        "/kw clear               — সব মুছুন",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))


# ── Trigger in groups ──────────────────────────────────────────────────────────

@app.on_message(
    filters.group & ~filters.service & (filters.text | filters.caption),
    group=6,
)
async def _kw_trigger(client: Client, message: Message):
    if not message.from_user or message.from_user.is_bot:
        return

    txt = (message.text or message.caption or "").strip()
    if not txt or txt.startswith("/"):
        return

    kws = await _load_kw()
    if not kws:
        return

    tl = txt.lower()
    for d in kws:
        kw  = (d.get("keyword") or "").lower()
        rep = d.get("reply") or ""
        if not kw or not rep:
            continue
        if re.search(rf"\b{re.escape(kw)}\b", tl):
            try:
                await message.reply_text(rep, parse_mode=HTML)
            except Exception as e:
                print(f"[KW] reply error: {e}")
            break
