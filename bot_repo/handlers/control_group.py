"""
Control Group System
──────────────────────────────────────────────────────────
একটি প্রাইভেট Telegram গ্রুপ থেকে সব বট-গ্রুপ পরিচালনা।

Setup:
  ১. একটি প্রাইভেট গ্রুপ তৈরি করুন (Control Center)
  ২. বটকে Admin করুন
  ৩. সেই গ্রুপে /setcontrolgroup পাঠান

Control Group কমান্ড:
  /groups                         — সব পরিচালিত গ্রুপের তালিকা
  /groupstats                     — বট পরিসংখ্যান
  /sendall [msg]                  — সব গ্রুপে broadcast
  /sendto                         — নির্দিষ্ট গ্রুপে মেসেজ
  /taggroup [chat_id] [msg]       — tag all group members (visible, batch=4, 3s delay)
  /protect [gid] forward|links|spam on|off — Protection সেট
  /protections                    — সব গ্রুপের protection
  /kw add|del|list|clear          — Keyword auto-reply
  /ctrlhelp                       — এই সাহায্য মেনু
"""

import asyncio
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from config import HTML, ADMIN_ID, groups_col, settings_col, db, app
from helpers import _auto_del, _is_admin_msg, bot_api, log_event, is_any_admin, admin_filter, get_bot_username


# ── DB ──────────────────────────────────────────────────────────────────────────
_ctrl_col    = db["control_group_settings"]
_monitor_col = db["monitor_group_settings"]

# ── In-memory sessions ──────────────────────────────────────────────────────────
_ctrl_sessions: dict[int, dict] = {}




# ── Core helpers ────────────────────────────────────────────────────────────────

async def get_control_group() -> int | None:
    doc = await _ctrl_col.find_one({"key": "control_group"})
    return int(doc["chat_id"]) if doc and doc.get("chat_id") else None


async def set_control_group_db(chat_id: int):
    await _ctrl_col.update_one(
        {"key": "control_group"},
        {"$set": {"key": "control_group", "chat_id": chat_id}},
        upsert=True,
    )


async def is_control_group(chat_id: int) -> bool:
    cg = await get_control_group()
    return cg == chat_id


# ── Monitor Group helpers ────────────────────────────────────────────────────────

async def get_monitor_group() -> int | None:
    doc = await _monitor_col.find_one({"key": "monitor_group"})
    return int(doc["chat_id"]) if doc and doc.get("chat_id") else None


async def set_monitor_group_db(chat_id: int):
    await _monitor_col.update_one(
        {"key": "monitor_group"},
        {"$set": {"key": "monitor_group", "chat_id": chat_id}},
        upsert=True,
    )


async def is_monitor_group(chat_id: int) -> bool:
    mg = await get_monitor_group()
    return mg == chat_id


# ── Syscheck helper ──────────────────────────────────────────────────────────────

async def _run_syscheck(client: Client) -> str:
    """Run a full system health check and return an HTML report string."""
    from config import ADMIN_IDS, VIDEO_CHANNEL, LOG_CHANNEL
    from helpers import bot_api
    lines = ["🔍 <b>System Health Check</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    fixes = []

    # ── Helper: check a chat via Bot API (no Pyrogram peer cache needed) ──────
    async def _check_chat_api(chat_id) -> str | None:
        r = await bot_api("getChat", {"chat_id": chat_id})
        if r.get("ok"):
            return r["result"].get("title") or r["result"].get("first_name") or str(chat_id)
        return None

    # 1. MongoDB
    try:
        from config import db as _db
        await _db.list_collection_names()
        lines.append("✅ MongoDB — সংযুক্ত")
    except Exception as e:
        lines.append(f"❌ MongoDB — <code>{e}</code>")
        fixes.append("❌ MongoDB: URI ঠিক আছে কিনা .env ফাইলে চেক করুন।")

    # 2. Bot identity
    try:
        me = await client.get_me()
        lines.append(f"✅ Bot — @{me.username} (<code>{me.id}</code>)")
    except Exception as e:
        lines.append(f"❌ Bot identity — <code>{e}</code>")

    # 3. Control Group
    try:
        cg = await get_control_group()
        if cg:
            title = await _check_chat_api(cg) or str(cg)
            lines.append(f"✅ Control Group — {title} (<code>{cg}</code>)")
        else:
            lines.append("⚠️ Control Group — সেট করা হয়নি")
            fixes.append("⚠️ Control Group: যে গ্রুপ থেকে বট পরিচালনা করতে চান সেখানে /setcontrolgroup পাঠান।")
    except Exception as e:
        lines.append(f"⚠️ Control Group — <code>{e}</code>")

    # 4. Monitor Group
    try:
        mg = await get_monitor_group()
        if mg:
            title = await _check_chat_api(mg) or str(mg)
            lines.append(f"✅ Monitor Group — {title} (<code>{mg}</code>)")
        else:
            lines.append("⚠️ Monitor Group — সেট করা হয়নি")
            fixes.append("⚠️ Monitor Group: যে গ্রুপে activity feed চান সেখানে /setmonitorgroup পাঠান।")
    except Exception as e:
        lines.append(f"⚠️ Monitor Group — <code>{e}</code>")

    # 5. Log Channel
    try:
        from helpers import get_log_channel
        lc = await get_log_channel(client=client)
        if lc:
            title = await _check_chat_api(lc)
            if title:
                lines.append(f"✅ Log Channel — {title} (<code>{lc}</code>)")
            else:
                lines.append(f"⚠️ Log Channel — বট add নেই (<code>{lc}</code>)")
                fixes.append(f"⚠️ Log Channel (<code>{lc}</code>): চ্যানেলে বটকে Admin করুন, তারপর /logchannel দিয়ে সেট করুন।")
        else:
            lines.append("⚠️ Log Channel — সেট করা হয়নি")
            fixes.append("⚠️ Log Channel: /logchannel [channel_id] দিয়ে সেট করুন, আগে চ্যানেলে বটকে Admin করুন।")
    except Exception as e:
        lines.append(f"⚠️ Log Channel — <code>{e}</code>")

    # 6. Video Channel
    try:
        title = await _check_chat_api(VIDEO_CHANNEL)
        if title:
            lines.append(f"✅ Video Channel — {title} (<code>{VIDEO_CHANNEL}</code>)")
        else:
            lines.append(f"❌ Video Channel — বট add নেই (<code>{VIDEO_CHANNEL}</code>)")
            fixes.append(f"❌ Video Channel (<code>{VIDEO_CHANNEL}</code>): চ্যানেলে বটকে Admin করুন। Bot পোস্ট করতে পারবে।")
    except Exception as e:
        lines.append(f"❌ Video Channel — <code>{e}</code>")

    # 7. Managed groups count
    try:
        from config import groups_col as _gcol
        cnt = await _gcol.count_documents({})
        lines.append(f"📊 পরিচালিত গ্রুপ — <b>{cnt}টি</b>")
    except Exception as e:
        lines.append(f"⚠️ Groups count — <code>{e}</code>")

    lines.append(f"\n🕛 {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}")

    # ── Fix Instructions (only if there are issues) ───────────────────────────
    if fixes:
        lines.append("\n🛠️ <b>সমস্যা সমাধান:</b>")
        lines.extend(fixes)

    return "\n".join(lines)


async def _is_ctrl_admin(client: Client, message: Message) -> bool:
    uid = message.from_user.id if message.from_user else 0
    if await is_any_admin(uid):
        return True
    try:
        member = await client.get_chat_member(message.chat.id, uid)
        from pyrogram.enums import ChatMemberStatus
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER
        )
    except Exception:
        return False


# ── /setcontrolgroup ─────────────────────────────────────────────────────────────

@app.on_message(filters.command("setcontrolgroup") & filters.group, group=1)
async def set_control_group_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    if not await is_any_admin(uid):
        return

    chat_id = message.chat.id
    title   = message.chat.title or str(chat_id)

    await set_control_group_db(chat_id)

    await message.reply_text(
        f"✅ <b>Control Group সেট হয়েছে!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 গ্রুপ: <b>{title}</b>\n"
        f"🆔 ID: <code>{chat_id}</code>\n\n"
        f"এখন এই গ্রুপ থেকে সব বট-গ্রুপ পরিচালনা করা যাবে।\n"
        f"কমান্ডের তালিকা দেখতে: /ctrlhelp",
        parse_mode=HTML,
    )


# ── /setmonitorgroup ──────────────────────────────────────────────────────────────

@app.on_message(filters.command("setmonitorgroup") & filters.group, group=1)
async def set_monitor_group_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    if not await is_any_admin(uid):
        return

    chat_id = message.chat.id
    title   = message.chat.title or str(chat_id)

    await set_monitor_group_db(chat_id)

    await message.reply_text(
        f"📡 <b>Monitor Group সেট হয়েছে!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 গ্রুপ: <b>{title}</b>\n"
        f"🆔 ID: <code>{chat_id}</code>\n\n"
        f"এখন protection violations, join approvals এবং\n"
        f"startup alerts এখানে আসবে।",
        parse_mode=HTML,
    )


# ── /setcontrolgroup from DM (with chat_id arg) ───────────────────────────────────

@app.on_message(filters.command("setcontrolgroup") & admin_filter & filters.private)
async def set_control_group_dm(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        cg = await get_control_group()
        if cg:
            await message.reply_text(
                f"🎛️ <b>Control Group</b>\n"
                f"✅ Currently set: <code>{cg}</code>\n\n"
                f"To change: <code>/setcontrolgroup -100xxxxxxxxxx</code>",
                parse_mode=HTML,
            )
        else:
            await message.reply_text(
                "🎛️ <b>Control Group — সেট নেই</b>\n"
                "Use: <code>/setcontrolgroup -100xxxxxxxxxx</code>",
                parse_mode=HTML,
            )
        return
    try:
        chat_id = int(args[0])
    except ValueError:
        await message.reply_text("❌ Invalid ID. Example: <code>-1001234567890</code>", parse_mode=HTML)
        return

    await set_control_group_db(chat_id)

    confirm_ok = False
    try:
        await client.send_message(
            chat_id,
            "✅ <b>এই গ্রুপটি Control Group হিসেবে সেট হয়েছে!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "এখন এই গ্রুপ থেকে সব বট-গ্রুপ পরিচালনা করা যাবে।\n"
            "কমান্ড দেখতে: /ctrlhelp",
            parse_mode=HTML,
        )
        confirm_ok = True
    except Exception as e:
        pass

    status = "✅ Confirmation message sent to group." if confirm_ok else "⚠️ Could not send to group (bot not member or not admin?)"
    await message.reply_text(
        f"✅ <b>Control Group সেট হয়েছে!</b>\n"
        f"🆔 <code>{chat_id}</code>\n\n"
        f"{status}",
        parse_mode=HTML,
    )


# ── /setmonitorgroup from DM (with chat_id arg) ───────────────────────────────────

@app.on_message(filters.command("setmonitorgroup") & admin_filter & filters.private)
async def set_monitor_group_dm(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        mg = await get_monitor_group()
        if mg:
            await message.reply_text(
                f"🕵️ <b>Monitor Group</b>\n"
                f"✅ Currently set: <code>{mg}</code>\n\n"
                f"To change: <code>/setmonitorgroup -100xxxxxxxxxx</code>",
                parse_mode=HTML,
            )
        else:
            await message.reply_text(
                "🕵️ <b>Monitor Group — সেট নেই</b>\n"
                "Use: <code>/setmonitorgroup -100xxxxxxxxxx</code>",
                parse_mode=HTML,
            )
        return
    try:
        chat_id = int(args[0])
    except ValueError:
        await message.reply_text("❌ Invalid ID. Example: <code>-1001234567890</code>", parse_mode=HTML)
        return

    await set_monitor_group_db(chat_id)

    confirm_ok = False
    try:
        await client.send_message(
            chat_id,
            "✅ <b>এই গ্রুপটি Monitor Group হিসেবে সেট হয়েছে!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "এখানে সব গ্রুপের activity log আসবে:\n"
            "• নতুন join/leave\n"
            "• Warn / Ban / Mute events\n"
            "• Spam detection alerts",
            parse_mode=HTML,
        )
        confirm_ok = True
    except Exception as e:
        pass

    status = "✅ Confirmation message sent to group." if confirm_ok else "⚠️ Could not send to group (bot not member or not admin?)"
    await message.reply_text(
        f"✅ <b>Monitor Group সেট হয়েছে!</b>\n"
        f"🆔 <code>{chat_id}</code>\n\n"
        f"{status}",
        parse_mode=HTML,
    )


# ── /syscheck ─────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("syscheck") & filters.group, group=1)
async def syscheck_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        if not await _is_admin_msg(client, message):
            return
    if not await _is_ctrl_admin(client, message):
        return

    wait = await message.reply_text("⏳ System check চলছে...", parse_mode=HTML)
    try:
        report = await _run_syscheck(client)
    except Exception as e:
        report = f"❌ syscheck failed: {e}"

    try:
        await wait.delete()
    except Exception:
        pass

    await message.reply_text(report, parse_mode=HTML)


# ── /ctrlhelp & /help ────────────────────────────────────────────────────────────

CTRL_HELP_TEXT = (
    "🎛️ <b>Control Group — সম্পূর্ণ কমান্ড গাইড</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "📋 <b>গ্রুপ তথ্য:</b>\n"
    "  /groups                              — সব গ্রুপের তালিকা\n"
    "  /groupstats                          — বট পরিসংখ্যান\n\n"
    "📤 <b>মেসেজ পাঠানো:</b>\n"
    "  /sendall [msg]                       — সব গ্রুপে broadcast\n"
    "  /sendall (reply করুন)                — reply করা মেসেজ সব গ্রুপে\n"
    "  /sendto                              — নির্দিষ্ট গ্রুপ বেছে নিন\n"
    "  /sendto [chat_id] [msg]              — সরাসরি ID দিয়ে পাঠান\n\n"
    "🏷️ <b>Tag Commands:</b>\n"
    "  /taggroup [gid] [msg]                — গ্রুপের সবাইকে visible mention + button\n"
    "  /tagall [gid] [msg]                  — গ্রুপের সবাইকে visible mention + button\n"
    "  /cancel                              — active tag session বাতিল করুন\n"
    "  বাটন format: <code>Text | https://url</code> (একাধিক লাইনে)\n\n"
    "🎬 <b>Video কমান্ড:</b>\n"
    "  /videoon [chat_id]                   — নির্দিষ্ট গ্রুপে video চালু (সরাসরি)\n"
    "  /videooff [chat_id]                  — নির্দিষ্ট গ্রুপে video বন্ধ\n"
    "  /videomsgon [chat_id]                — videooff থাকলে redirect msg দেখাও\n"
    "  /videomsgoff [chat_id]               — videooff থাকলে কোনো msg দেখাবে না\n\n"
    "👋 <b>Welcome Message:</b>\n"
    "  /welcomeon [chat_id]                 — নির্দিষ্ট গ্রুপে welcome msg চালু\n"
    "  /welcomeoff [chat_id]                — নির্দিষ্ট গ্রুপে welcome msg বন্ধ\n\n"
    "⚠️ <b>Anti-Flood (Warning):</b>\n"
    "  /antifloodon [chat_id]               — নির্দিষ্ট গ্রুপে antiflood চালু\n"
    "  /antifloodoff [chat_id]              — নির্দিষ্ট গ্রুপে antiflood বন্ধ\n\n"
    "😂 <b>Auto Reaction:</b>\n"
    "  /autoreactionon [chat_id]            — নির্দিষ্ট গ্রুপে auto reaction চালু\n"
    "  /autoreactionoff [chat_id]           — নির্দিষ্ট গ্রুপে auto reaction বন্ধ\n\n"
    "🛡️ <b>Protection System:</b>\n"
    "  /forwardon [gid]                     — Forward সুরক্ষা চালু\n"
    "  /forwardoff [gid]                    — Forward সুরক্ষা বন্ধ\n"
    "  /linkon [gid]                        — Link সুরক্ষা চালু\n"
    "  /linkoff [gid]                       — Link সুরক্ষা বন্ধ\n"
    "  /warnon [gid]                        — ডিলিটের পর Warning পাঠাবে ✅\n"
    "  /warnoff [gid]                       — চুপচাপ ডিলিট করবে, Warning নেই 🔇\n"
    "  /spamon [gid]                         — Spam protection চালু\n"
    "  /spamoff [gid]                       — Spam protection বন্ধ\n"
    "  /protect [gid] spam_limit 5          — Spam limit (msgs/10s)\n"
    "  /protections                         — সব গ্রুপের সেটিং দেখুন\n\n"
    "🔑 <b>Keyword Auto-Reply:</b>\n"
    "  /kw add [word] [reply]               — keyword যোগ করুন\n"
    "  /kw del [word]                       — keyword সরানো\n"
    "  /kw list                             — সব keyword দেখুন\n"
    "  /kw clear                            — সব keyword মুছুন\n\n"
    "📥 <b>Inbox Group:</b>\n"
    "  /setinboxgroup [chat_id]             — Inbox Group সেট করুন\n\n"
    "📡 <b>Monitor & System:</b>\n"
    "  /setmonitorgroup [chat_id]           — Monitor Group সেট করুন\n"
    "  /syscheck                            — সম্পূর্ণ system health check\n\n"
    "📊 <b>Statistics:</b>\n"
    "  /overview                            — শেষ ৭ দিনের summary\n"
    "  /overview 30                         — শেষ ৩০ দিন\n"
    "  /overview 2026-04-01                 — নির্দিষ্ট দিন\n\n"
    "📋 <b>Daily Auto-Report:</b>\n"
    "  /dailyreport                         — এখনই গতকালের report দেখুন\n"
    "  /dailyreporton                       — Auto daily report চালু\n"
    "  /dailyreportoff                      — Auto daily report বন্ধ\n"
    "  /reporttime HH:MM                   — Report time সেট করুন (BDT)\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "💡 সব কমান্ড শুধু Control Group-এই কাজ করবে।"
)


@app.on_message(filters.command(["ctrlhelp", "help"]) & filters.group, group=1)
async def ctrl_help_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    is_ctrl = await is_control_group(message.chat.id)
    if not is_ctrl and not await is_any_admin(uid):
        return
    if not await _is_ctrl_admin(client, message):
        return
    await message.reply_text(CTRL_HELP_TEXT, parse_mode=HTML)


# ── /groups ──────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("groups") & filters.group, group=1)
async def ctrl_groups_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    is_ctrl = await is_control_group(message.chat.id)
    if not is_ctrl and not await is_any_admin(uid):
        return
    if not await _is_ctrl_admin(client, message):
        return

    docs = await groups_col.find({}).sort("title", 1).to_list(length=200)

    # যদি bot_groups খালি হয়, known_groups থেকে নাও
    if not docs:
        docs = await db["known_groups"].find({"main_bot": True}).to_list(length=200)
        using_fallback = True
    else:
        using_fallback = False

    if not docs:
        m = await message.reply_text("📭 এখনো কোনো গ্রুপ নেই।", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 15))
        return

    note = "\n<i>(known_groups cache থেকে — /syncgroups দিয়ে sync করুন)</i>" if using_fallback else ""
    lines = [f"📋 <b>পরিচালিত গ্রুপসমূহ</b> (মোট {len(docs)}টি)\n━━━━━━━━━━━━━━━━━━━━━━"]
    for i, d in enumerate(docs, 1):
        cid       = d.get("chat_id", "?")
        raw_title = d.get("title") or ""

        # ── title যদি numeric হয় (গ্রুপ আইডি স্টোর হয়ে গেছে), API থেকে নাও ──
        if not raw_title or str(raw_title).lstrip("-").isdigit():
            try:
                chat_info = await client.get_chat(cid)
                raw_title = chat_info.title or str(cid)
                # DB আপডেট করো পরবর্তীতে যাতে ঠিক থাকে
                await groups_col.update_one(
                    {"chat_id": cid},
                    {"$set": {"title": raw_title,
                              "username": getattr(chat_info, "username", None) or d.get("username"),
                              "invite_link": getattr(chat_info, "invite_link", None) or d.get("invite_link")}},
                )
            except Exception:
                raw_title = str(cid)

        title = raw_title[:35]

        # ── গ্রুপের লিঙ্ক নির্ধারণ ──────────────────────────────────────
        invite_link = d.get("invite_link") or ""
        username    = d.get("username") or ""
        if username:
            group_url = f"https://t.me/{username.lstrip('@')}"
        elif invite_link:
            group_url = invite_link
        else:
            group_url = None

        if group_url:
            lines.append(f"{i}. <a href='{group_url}'><b>{title}</b></a>\n   🆔 <code>{cid}</code>")
        else:
            lines.append(f"{i}. <b>{title}</b>\n   🆔 <code>{cid}</code>")

    if note:
        lines.append(note)

    await message.reply_text("\n".join(lines), parse_mode=HTML)


# ── /groupstats ──────────────────────────────────────────────────────────────────

@app.on_message(filters.command("groupstats") & filters.group, group=1)
async def ctrl_groupstats_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    is_ctrl = await is_control_group(message.chat.id)
    if not is_ctrl and not await is_any_admin(uid):
        return
    if not await _is_ctrl_admin(client, message):
        return

    from config import users_col, videos_col, clones_col

    total_users  = await users_col.count_documents({})
    total_groups = await groups_col.count_documents({})
    # known_groups fallback
    if total_groups == 0:
        total_groups = await db["known_groups"].count_documents({"main_bot": True})
    total_videos = await videos_col.count_documents({})
    total_clones = await clones_col.count_documents({})

    await message.reply_text(
        f"📊 <b>বট পরিসংখ্যান</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 মোট ব্যবহারকারী : <b>{total_users:,}</b>\n"
        f"📍 গ্রুপ সংখ্যা    : <b>{total_groups:,}</b>\n"
        f"🎬 ভিডিও সংখ্যা   : <b>{total_videos:,}</b>\n"
        f"🤖 Clone বট        : <b>{total_clones:,}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}",
        parse_mode=HTML,
    )


# ── /sendall ─────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("sendall") & filters.group, group=1)
async def ctrl_sendall_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    uid = message.from_user.id

    # Collect content
    target_msg = message.reply_to_message
    text_args  = " ".join(message.command[1:]).strip()

    if not target_msg and not text_args:
        m = await message.reply_text(
            "⚠️ <b>ব্যবহার:</b>\n"
            "<code>/sendall [মেসেজ]</code>\n"
            "অথবা কোনো মেসেজ reply করে <code>/sendall</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 20))
        return

    _ctrl_sessions[uid] = {
        "step":         "send_all_confirm",
        "content_msg":  target_msg,
        "text":         text_args if not target_msg else "",
        "extra_buttons": [],
    }
    await _show_sendall_confirm(client, message, uid)


async def _show_sendall_confirm(client: Client, message: Message, uid: int):
    session = _ctrl_sessions.get(uid, {})
    content = session.get("content_msg")
    txt     = session.get("text", "")
    btns    = session.get("extra_buttons", [])
    gcount  = await groups_col.count_documents({})

    preview = ""
    if content:
        preview = f"\n📎 <i>Reply করা মেসেজ ({content.media and 'মিডিয়া' or 'টেক্সট'})</i>"
    elif txt:
        preview = f"\n📝 <i>{txt[:100]}</i>"

    btn_text = ""
    for row in btns:
        for b in row:
            btn_text += f"\n  🔗 {b['text']} → {b['url']}"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 সব গ্রুপে পাঠান", callback_data=f"csa_yes:{uid}"),
            InlineKeyboardButton("❌ বাতিল",          callback_data=f"csa_no:{uid}"),
        ],
        [
            InlineKeyboardButton("➕ বাটন যোগ করুন", callback_data=f"csa_addbtn:{uid}"),
        ],
    ])

    m = await message.reply_text(
        f"📤 <b>সব গ্রুপে Broadcast</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 গ্রুপ সংখ্যা: <b>{gcount}</b>{preview}"
        f"{btn_text if btn_text else ''}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"নিশ্চিত করুন?",
        parse_mode=HTML,
        reply_markup=kb,
    )
    asyncio.create_task(_auto_del(m, 120))


@app.on_callback_query(filters.regex(r"^csa_(yes|no|addbtn):(\d+)$"))
async def ctrl_sendall_cb(client: Client, cq: CallbackQuery):
    import re as _re
    m2    = _re.match(r"^csa_(yes|no|addbtn):(\d+)$", cq.data)
    action = m2.group(1)
    uid    = int(m2.group(2))

    if cq.from_user.id != uid:
        await cq.answer("❌ শুধু কমান্ড-দাতাই ব্যবহার করতে পারবেন।", show_alert=True)
        return

    session = _ctrl_sessions.get(uid)
    if not session:
        await cq.answer("⏰ Session শেষ হয়ে গেছে।", show_alert=True)
        return

    if action == "no":
        _ctrl_sessions.pop(uid, None)
        await cq.edit_message_text("❌ Broadcast বাতিল করা হয়েছে।")
        return

    if action == "addbtn":
        session["step"] = "sendall_wait_button"
        _ctrl_sessions[uid] = session
        await cq.edit_message_text(
            "🔗 <b>বাটন যোগ করুন</b>\n\n"
            "Format: <code>Button Text | https://example.com</code>",
            parse_mode=HTML,
        )
        return

    # ── action == "yes" — broadcast ─────────────────────────────────────────
    _ctrl_sessions.pop(uid, None)
    await cq.answer("📡 Broadcast শুরু হচ্ছে…")
    await cq.edit_message_text("📡 <b>Broadcast চলছে…</b>", parse_mode=HTML)

    content_msg  = session.get("content_msg")
    text         = session.get("text", "")
    extra_buttons= session.get("extra_buttons", [])

    kb_json = None
    if extra_buttons:
        kb_json = {"inline_keyboard": extra_buttons}

    # ── Translation support ──────────────────────────────────────────────────
    from helpers import translate_text
    from handlers.cmd_control import get_group_lang

    docs = await groups_col.find({}).to_list(length=1000)
    ok = fail = 0
    for d in docs:
        gid = d.get("chat_id")
        if not gid:
            continue
        try:
            if content_msg:
                await client.copy_message(
                    chat_id=gid,
                    from_chat_id=content_msg.chat.id,
                    message_id=content_msg.id,
                )
            else:
                # Auto-translate text to group's language (source: bn)
                grp_lang    = await get_group_lang(gid)
                send_text   = await translate_text(text, target_lang=grp_lang, source_lang="bn") if grp_lang != "bn" else text
                params = {"chat_id": gid, "text": send_text, "parse_mode": "HTML"}
                if kb_json:
                    params["reply_markup"] = kb_json
                await bot_api("sendMessage", params)
            ok += 1
        except FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
        except Exception as e:
            print(f"[SENDALL] {gid} failed: {e}")
            fail += 1
        await asyncio.sleep(0.05)

    try:
        await cq.message.edit_text(
            f"✅ <b>Broadcast সম্পন্ন!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ সফল : <b>{ok}</b>\n"
            f"❌ ব্যর্থ: <b>{fail}</b>",
            parse_mode=HTML,
        )
    except Exception:
        pass


# ── /sendto ──────────────────────────────────────────────────────────────────────

@app.on_message(filters.command("sendto") & filters.group, group=1)
async def ctrl_sendto_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    uid  = message.from_user.id
    args = message.command[1:]

    # Direct: /sendto -1001234 [message]
    if args and args[0].lstrip("-").isdigit():
        gid    = int(args[0])
        text   = " ".join(args[1:]).strip()
        replied = message.reply_to_message

        try:
            chat  = await client.get_chat(gid)
            title = chat.title or str(gid)
        except Exception:
            title = str(gid)

        try:
            if replied:
                await client.copy_message(gid, replied.chat.id, replied.id)
            elif text:
                await bot_api("sendMessage", {
                    "chat_id": gid, "text": text, "parse_mode": "HTML"
                })
            else:
                m = await message.reply_text(
                    "⚠️ মেসেজ বা reply করা মেসেজ দিন।", parse_mode=HTML
                )
                asyncio.create_task(_auto_del(m, 15))
                return

            m = await message.reply_text(
                f"✅ পাঠানো হয়েছে → <b>{title}</b>", parse_mode=HTML
            )
        except Exception as e:
            m = await message.reply_text(f"❌ ব্যর্থ: <code>{e}</code>", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 15))
        return

    # Interactive: show group picker
    docs = await groups_col.find({}).sort("title", 1).to_list(length=100)
    if not docs:
        m = await message.reply_text("📭 কোনো গ্রুপ নেই।", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 15))
        return

    rows = []
    for d in docs:
        cid   = d.get("chat_id", "?")
        title = (d.get("title") or str(cid))[:20]
        rows.append([InlineKeyboardButton(title, callback_data=f"cst_pick:{uid}:{cid}")])

    rows.append([InlineKeyboardButton("❌ বাতিল", callback_data=f"cst_cancel:{uid}")])

    _ctrl_sessions[uid] = {"step": "sendto_pick"}
    m = await message.reply_text(
        "📍 <b>কোন গ্রুপে পাঠাবেন?</b>",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )
    asyncio.create_task(_auto_del(m, 60))


@app.on_callback_query(filters.regex(r"^cst_(pick|cancel)"))
async def ctrl_sendto_pick_cb(client: Client, cq: CallbackQuery):
    parts = cq.data.split(":", 2)
    action = parts[0].replace("cst_", "")
    uid    = int(parts[1])

    if cq.from_user.id != uid:
        await cq.answer("❌ শুধু কমান্ড-দাতাই ব্যবহার করতে পারবেন।", show_alert=True)
        return

    if action == "cancel":
        _ctrl_sessions.pop(uid, None)
        await cq.edit_message_text("❌ বাতিল।")
        return

    gid = int(parts[2])
    try:
        chat  = await client.get_chat(gid)
        title = chat.title or str(gid)
    except Exception:
        title = str(gid)

    _ctrl_sessions[uid] = {"step": "sendto_wait_content", "gid": gid, "title": title}
    await cq.edit_message_text(
        f"📝 এখন <b>{title}</b> গ্রুপে পাঠানোর মেসেজ লিখুন বা forward করুন।\n"
        f"/cancel লিখুন বাতিল করতে।",
        parse_mode=HTML,
    )


# ── /cancel — Control Group-এ সব active session বাতিল করুন ──────────────────────

@app.on_message(filters.command("cancel") & filters.group, group=1)
async def ctrl_cancel_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    uid     = message.from_user.id
    cleared = []

    if uid in _taggroup_sessions:
        _taggroup_sessions.pop(uid, None)
        cleared.append("Taggroup")
    if uid in _tagall_ctrl_sessions:
        _tagall_ctrl_sessions.pop(uid, None)
        cleared.append("Tagall")
    if uid in _ctrl_sessions:
        _ctrl_sessions.pop(uid, None)
        cleared.append("Session")

    if cleared:
        m = await message.reply_text(
            f"❌ বাতিল করা হয়েছে: <b>{', '.join(cleared)}</b>",
            parse_mode=HTML,
        )
    else:
        m = await message.reply_text(
            "ℹ️ কোনো active session নেই।",
            parse_mode=HTML,
        )
    asyncio.create_task(_auto_del(m, 10))


# ── /addbtn — Add buttons to active taggroup / tagall session ────────────────

@app.on_message(filters.command("addbtn") & filters.group, group=1)
async def ctrl_addbtn_cmd(client: Client, message: Message):
    """Add button(s) to active taggroup or tagall session via command."""
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    uid = message.from_user.id

    # Parse raw text after /addbtn command
    raw = message.text or ""
    # Remove command part (/addbtn or /addbtn@botname)
    parts = raw.split(None, 1)
    btn_text = parts[1].strip() if len(parts) > 1 else ""

    if not btn_text:
        m = await message.reply_text(
            "⚠️ Format: <code>/addbtn Button Text | https://url</code>\n\n"
            "দুই বাটন পাশাপাশি:\n"
            "<code>/addbtn Btn1 | url1 && Btn2 | url2</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 20))
        return

    def _parse(raw: str) -> list:
        added = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            segments = line.split("&&") if "&&" in line else [line]
            for seg in segments:
                seg = seg.strip()
                if "|" in seg:
                    bits  = seg.split("|", 1)
                    btext = bits[0].strip()
                    burl  = bits[1].strip()
                    if btext and burl:
                        added.append({"text": btext, "url": burl})
        return added

    parsed = _parse(btn_text)
    print(f"[ADDBTN] uid={uid} raw={btn_text!r:.80} parsed={parsed}", flush=True)

    if not parsed:
        m = await message.reply_text(
            "⚠️ Format ঠিক নেই। উদাহরণ:\n"
            "<code>/addbtn Join | https://t.me/mygroup</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 20))
        return

    # ── Try taggroup session ──────────────────────────────────────────────────
    if uid in _taggroup_sessions and _taggroup_sessions[uid].get("step") == "wait_button":
        sess = _taggroup_sessions[uid]
        sess.setdefault("buttons", []).extend(parsed)
        sess["step"] = "confirm"
        _taggroup_sessions[uid] = sess
        total = len(sess["buttons"])
        import html as _html
        btn_list = "\n".join(f"  {i+1}. {_html.escape(b['text'])}" for i, b in enumerate(sess["buttons"]))
        kb = await _tag_quick_kb("tgrp", uid, True)
        m = await message.reply_text(
            f"✅ <b>{len(parsed)}</b> টি বাটন যোগ হয়েছে! মোট: <b>{total}</b> টি\n\n{btn_list}",
            parse_mode=HTML,
            reply_markup=kb,
        )
        asyncio.create_task(_auto_del(m, 90))
        return

    # ── Try tagall session ────────────────────────────────────────────────────
    if uid in _tagall_ctrl_sessions and _tagall_ctrl_sessions[uid].get("step") == "wait_button":
        sess = _tagall_ctrl_sessions[uid]
        sess.setdefault("extra_buttons", []).extend(parsed)
        sess["step"] = "confirm"
        _tagall_ctrl_sessions[uid] = sess
        total = len(sess["extra_buttons"])
        import html as _html
        btn_list = "\n".join(f"  {i+1}. {_html.escape(b['text'])}" for i, b in enumerate(sess["extra_buttons"]))
        kb = await _tag_quick_kb("ctag", uid, True)
        m = await message.reply_text(
            f"✅ <b>{len(parsed)}</b> টি বাটন যোগ হয়েছে! মোট: <b>{total}</b> টি\n\n{btn_list}",
            parse_mode=HTML,
            reply_markup=kb,
        )
        asyncio.create_task(_auto_del(m, 90))
        return

    # ── No active session ─────────────────────────────────────────────────────
    m = await message.reply_text(
        "⚠️ কোনো active /taggroup বা /tagall session নেই।\n"
        "আগে /taggroup বা /tagall দিন, তারপর ➕ বাটন লিখুন ক্লিক করুন।",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 15))


# ── /taggroup — Session-based visible mention tag with button support ───────────────────

_taggroup_sessions: dict[int, dict] = {}   # uid → session


@app.on_message(filters.command("taggroup") & filters.group, group=1)
async def ctrl_taggroup_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    # split(None, 2) preserves newlines inside the message text
    raw   = (message.text or message.caption or "").strip()
    parts = raw.split(None, 2)   # ["/taggroup", "gid", "rest with newlines"]

    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        m = await message.reply_text(
            "⚠️ <b>ব্যবহার:</b>\n"
            "<code>/taggroup [group_id] [মেসেজ]</code>\n\n"
            "উদাহরণ:\n"
            "<code>/taggroup -100123456789 সবাইকে নোটিশ!</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 20))
        return

    gid     = int(parts[1])
    msg_txt = parts[2] if len(parts) >= 3 else ""

    try:
        chat      = await client.get_chat(gid)
        grp_title = chat.title or str(gid)
    except Exception as e:
        m = await message.reply_text(f"❌ গ্রুপ খুঁজে পাওয়া যায়নি: <code>{e}</code>", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 15))
        return

    uid = message.from_user.id
    _taggroup_sessions[uid] = {
        "gid":       gid,
        "grp_title": grp_title,
        "msg_txt":   msg_txt,
        "buttons":   [],
        "step":      "confirm",
    }
    await _show_taggroup_preview(message, uid)


def _smart_button_rows(flat_buttons: list) -> list:
    """Smart button layout:
    - Even count  → 2 per row
    - Odd count   → first row 1 button, then 2 per row
    """
    n = len(flat_buttons)
    if n == 0:
        return []
    rows  = []
    start = 0
    if n % 2 == 1:
        rows.append([flat_buttons[0]])
        start = 1
    for i in range(start, n, 2):
        rows.append(flat_buttons[i:i + 2])
    return rows


async def _tag_quick_kb(prefix: str, uid: int, has_buttons: bool) -> InlineKeyboardMarkup:
    """Quick-button keyboard for taggroup / tagall preview."""
    rows = [
        [
            InlineKeyboardButton("🏷️ Tag করুন", callback_data=f"{prefix}_yes:{uid}"),
            InlineKeyboardButton("❌ বাতিল",     callback_data=f"{prefix}_no:{uid}"),
        ],
        [
            InlineKeyboardButton("➕ বাটন লিখুন",      callback_data=f"{prefix}_addbtn:{uid}"),
            InlineKeyboardButton("💎 Buy Premium",      callback_data=f"{prefix}_qb_premium:{uid}"),
        ],
        [
            InlineKeyboardButton("👤 My Profile",       callback_data=f"{prefix}_qb_profile:{uid}"),
            InlineKeyboardButton("🔵 Couple Group",     callback_data=f"{prefix}_qb_couple:{uid}"),
        ],
    ]
    if has_buttons:
        rows.append([
            InlineKeyboardButton("🗑️ বাটন মুছুন", callback_data=f"{prefix}_clrbtn:{uid}"),
        ])
    return InlineKeyboardMarkup(rows)


async def _show_taggroup_preview(message: Message, uid: int):
    import html as _html
    sess    = _taggroup_sessions.get(uid, {})
    gid     = sess.get("gid")
    title   = sess.get("grp_title", str(gid))
    msg_txt = sess.get("msg_txt", "")
    btns    = sess.get("buttons", [])   # flat list of {"text":…, "url":…}

    # Safe HTML-escaped preview (max 300 chars)
    preview_txt = _html.escape(msg_txt[:300]) if msg_txt else "<i>(কোনো মেসেজ নেই)</i>"
    if msg_txt and len(msg_txt) > 300:
        preview_txt += "…"

    btn_preview = ""
    for i, b in enumerate(btns, 1):
        btn_preview += f"\n  {i}. 🔗 {_html.escape(b['text'])} → {_html.escape(b['url'])}"

    kb = await _tag_quick_kb("tgrp", uid, bool(btns))

    m = await message.reply_text(
        f"🏷️ <b>Tag Group</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Group: <b>{_html.escape(title)}</b>  <code>{gid}</code>\n"
        f"💬 মেসেজ:\n{preview_txt}"
        f"{btn_preview}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"নিশ্চিত করুন?",
        parse_mode=HTML,
        reply_markup=kb,
    )
    asyncio.create_task(_auto_del(m, 180))


@app.on_callback_query(filters.regex(r"^tgrp_(yes|no|addbtn|clrbtn|qb_\w+):(\d+)$"), group=1)
async def ctrl_taggroup_cb(client: Client, cq: CallbackQuery):
    import re as _re
    m2     = _re.match(r"^tgrp_(yes|no|addbtn|clrbtn|qb_\w+):(\d+)$", cq.data)
    action = m2.group(1)
    uid    = int(m2.group(2))

    if cq.from_user.id != uid:
        await cq.answer("❌ শুধু কমান্ড-দাতাই ব্যবহার করতে পারবেন।", show_alert=True)
        return

    sess = _taggroup_sessions.get(uid)
    if not sess:
        await cq.answer("⏰ Session শেষ হয়ে গেছে।", show_alert=True)
        return

    if action == "no":
        _taggroup_sessions.pop(uid, None)
        await cq.edit_message_text("❌ Taggroup বাতিল।")
        return

    if action == "clrbtn":
        sess["buttons"] = []
        sess["step"]    = "confirm"
        _taggroup_sessions[uid] = sess
        await cq.answer("🗑️ সব বাটন মুছে গেছে।")
        kb = await _tag_quick_kb("tgrp", uid, False)
        import html as _html
        try:
            await cq.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            pass
        return

    if action.startswith("qb_"):
        bot_username = await get_bot_username(client)
        quick = {
            "qb_premium": ("💎 Buy Premium", f"https://t.me/{bot_username}?start=buypremium"),
            "qb_profile":  ("👤 My Profile",  f"https://t.me/{bot_username}?start=profile"),
            "qb_couple":   ("🔵 Couple Group", "https://t.me/+PnUkO8waIEcyNDY1"),
        }
        if action not in quick:
            await cq.answer("Unknown button.", show_alert=True)
            return
        label, url = quick[action]
        btns = sess.setdefault("buttons", [])
        if any(b["url"] == url for b in btns):
            await cq.answer("⚠️ বাটনটি আগেই আছে!", show_alert=True)
            return
        btns.append({"text": label, "url": url})
        _taggroup_sessions[uid] = sess
        await cq.answer(f"✅ '{label}' যোগ হয়েছে!")
        kb = await _tag_quick_kb("tgrp", uid, True)
        try:
            await cq.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            pass
        return

    if action == "addbtn":
        sess["step"] = "wait_button"
        _taggroup_sessions[uid] = sess
        await cq.edit_message_text(
            "🔗 <b>বাটন যোগ করুন</b>\n\n"
            "নিচের command দিন:\n\n"
            "এক বাটন:\n"
            "<code>/addbtn বাটন টেক্সট | https://example.com</code>\n\n"
            "দুই বাটন পাশাপাশি:\n"
            "<code>/addbtn Btn1 | https://url1 && Btn2 | https://url2</code>\n\n"
            "একাধিক বাটন (প্রতি লাইনে একটি):\n"
            "<code>/addbtn Join | https://t.me/...\nChannel | https://t.me/...</code>\n\n"
            "⬅️ /cancel দিয়ে বাতিল করুন।",
            parse_mode=HTML,
        )
        return

    # action == "yes" — tag শুরু করো
    _taggroup_sessions.pop(uid, None)
    await cq.answer("🏷️ Tagging শুরু হচ্ছে…")
    await cq.edit_message_text("⏳ <b>Tagging in progress…</b>", parse_mode=HTML)

    gid     = sess["gid"]
    msg_txt = sess.get("msg_txt", "")
    btns    = sess.get("buttons", [])   # flat list of {"text":…, "url":…}
    # Apply smart layout: even→2/row, odd→first row 1 then 2/row
    kb_json = {"inline_keyboard": _smart_button_rows(btns)} if btns else None

    tagged      = 0
    chunk       = []
    chunks_sent = 0
    BATCH_SIZE  = 4      # 4 visible mentions per message (TOS compliant)
    BATCH_DELAY = 3.0    # 3-second mandatory delay between batches

    try:
        async for member in client.get_chat_members(gid):
            u = member.user
            if u.is_bot or u.is_deleted:
                continue
            name = (u.first_name or "").strip() or "Member"
            chunk.append(f'<a href="tg://user?id={u.id}">{name}</a>')
            tagged += 1

            if len(chunk) >= BATCH_SIZE:
                mentions = "  ".join(chunk)
                prefix   = f"{msg_txt}\n\n" if (chunks_sent == 0 and msg_txt) else ""
                payload  = prefix + mentions
                params   = {"chat_id": gid, "text": payload, "parse_mode": "HTML"}
                if kb_json and chunks_sent == 0:
                    params["reply_markup"] = kb_json
                try:
                    r = await bot_api("sendMessage", params)
                    if r.get("ok"):
                        chunks_sent += 1
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except Exception:
                    pass
                chunk = []
                await asyncio.sleep(BATCH_DELAY)

        if chunk:
            mentions = "  ".join(chunk)
            prefix   = f"{msg_txt}\n\n" if (chunks_sent == 0 and msg_txt) else ""
            payload  = prefix + mentions
            params   = {"chat_id": gid, "text": payload, "parse_mode": "HTML"}
            if kb_json and chunks_sent == 0:
                params["reply_markup"] = kb_json
            try:
                await bot_api("sendMessage", params)
            except Exception:
                pass
    except Exception as err:
        await cq.message.edit_text(f"❌ Error: <code>{err}</code>", parse_mode=HTML)
        return

    await cq.message.edit_text(
        f"✅ <b>Tag সম্পন্ন!</b>\n"
        f"👥 Tagged: <b>{tagged}</b> জন\n"
        f"📍 Group: <b>{sess.get('grp_title', gid)}</b>",
        parse_mode=HTML,
    )


# ── Session handler for sendto and sendall button ─────────────────────────────

@app.on_message(filters.group, group=10)
async def _ctrl_session_handler(client: Client, message: Message):
    if not message.from_user:
        return
    if not await is_control_group(message.chat.id):
        return

    uid  = message.from_user.id
    text = (message.text or "").strip()

    def _parse_btn_input(raw: str) -> list:
        """Parse button format — supports:
          Text | URL          (one button per line)
          T1 | U1 && T2 | U2 (two buttons in one line, stored flat)
        Returns flat list of {"text":…, "url":…} dicts.
        """
        added = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("&&") if "&&" in line else [line]
            for part in parts:
                part = part.strip()
                if "|" in part:
                    bits  = part.split("|", 1)
                    btext = bits[0].strip()
                    burl  = bits[1].strip()
                    if btext and burl:
                        added.append({"text": btext, "url": burl})
        return added

    _BTN_FMT_HELP = (
        "⚠️ <b>Format ঠিক নেই।</b>\n\n"
        "এক লাইনে এক বাটন:\n"
        "<code>Button Text | https://example.com</code>\n\n"
        "এক লাইনে দুই বাটন:\n"
        "<code>Btn1 | https://url1 && Btn2 | https://url2</code>\n\n"
        "একাধিক লাইনে:\n"
        "<code>Join | https://t.me/...\nChannel | https://t.me/...</code>\n\n"
        "/cancel দিয়ে বাতিল করুন।"
    )

    # ── /taggroup button session ──────────────────────────────────────────────
    if uid in _taggroup_sessions and _taggroup_sessions[uid].get("step") == "wait_button":
        print(f"[TGRP_BTN] uid={uid} text={text!r:.80}", flush=True)
        try:
            sess  = _taggroup_sessions[uid]
            added = _parse_btn_input(text)
            if added:
                sess.setdefault("buttons", []).extend(added)
                sess["step"] = "confirm"
                _taggroup_sessions[uid] = sess
                total    = len(sess["buttons"])
                import html as _html
                btn_list = "\n".join(
                    f"  {i+1}. {_html.escape(b['text'])}" for i, b in enumerate(sess["buttons"])
                )
                kb = await _tag_quick_kb("tgrp", uid, True)
                m  = await message.reply_text(
                    f"✅ <b>{len(added)}</b> টি বাটন যোগ হয়েছে!\n"
                    f"📊 মোট: <b>{total}</b> টি\n\n{btn_list}",
                    parse_mode=HTML,
                    reply_markup=kb,
                )
            else:
                m = await message.reply_text(_BTN_FMT_HELP, parse_mode=HTML)
            asyncio.create_task(_auto_del(m, 45))
        except Exception as _e:
            print(f"[TGRP_BTN] ERROR: {_e}", flush=True)
            try:
                await message.reply_text(f"⚠️ বাটন যোগে সমস্যা: <code>{_e}</code>", parse_mode=HTML)
            except Exception:
                pass
        return

    # ── /tagall button session ────────────────────────────────────────────────
    if uid in _tagall_ctrl_sessions and _tagall_ctrl_sessions[uid].get("step") == "wait_button":
        print(f"[CTAG_BTN] uid={uid} text={text!r:.80}", flush=True)
        try:
            sess  = _tagall_ctrl_sessions[uid]
            added = _parse_btn_input(text)
            if added:
                sess.setdefault("extra_buttons", []).extend(added)
                sess["step"] = "confirm"
                _tagall_ctrl_sessions[uid] = sess
                total    = len(sess["extra_buttons"])
                import html as _html
                btn_list = "\n".join(
                    f"  {i+1}. {_html.escape(b['text'])}" for i, b in enumerate(sess["extra_buttons"])
                )
                kb = await _tag_quick_kb("ctag", uid, True)
                m  = await message.reply_text(
                    f"✅ <b>{len(added)}</b> টি বাটন যোগ হয়েছে!\n"
                    f"📊 মোট: <b>{total}</b> টি\n\n{btn_list}",
                    parse_mode=HTML,
                    reply_markup=kb,
                )
            else:
                m = await message.reply_text(_BTN_FMT_HELP, parse_mode=HTML)
            asyncio.create_task(_auto_del(m, 45))
        except Exception as _e:
            print(f"[CTAG_BTN] ERROR: {_e}", flush=True)
            try:
                await message.reply_text(f"⚠️ বাটন যোগে সমস্যা: <code>{_e}</code>", parse_mode=HTML)
            except Exception:
                pass
        return

    if uid not in _ctrl_sessions:
        return

    session = _ctrl_sessions[uid]

    if text == "/cancel":
        _ctrl_sessions.pop(uid, None)
        m = await message.reply_text("❌ বাতিল।", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 10))
        return

    step = session.get("step")

    if step == "sendall_wait_content":
        session["msg_type"]    = "content"
        session["content_msg"] = message
        session["step"]        = "send_all_confirm"
        _ctrl_sessions[uid]    = session
        await _show_sendall_confirm(client, message, uid)

    elif step == "sendto_wait_content":
        gid   = session.get("gid")
        title = session.get("title", str(gid))
        _ctrl_sessions.pop(uid, None)
        try:
            await client.copy_message(gid, message.chat.id, message.id)
            m = await message.reply_text(
                f"✅ পাঠানো হয়েছে — <b>{title}</b>", parse_mode=HTML
            )
        except Exception as e:
            m = await message.reply_text(
                f"❌ ব্যর্থ: <code>{e}</code>", parse_mode=HTML
            )
        asyncio.create_task(_auto_del(m, 15))

    elif step == "sendall_wait_button":
        if "|" in text:
            parts    = text.split("|", 1)
            btn_text = parts[0].strip()
            btn_url  = parts[1].strip()
            session.setdefault("extra_buttons", []).append(
                [{"text": btn_text, "url": btn_url}]
            )
            session["step"] = "send_all_confirm"
            _ctrl_sessions[uid] = session
            m = await message.reply_text(
                f"✅ বাটন যোগ হয়েছে: <b>{btn_text}</b>",
                parse_mode=HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🚀 পাঠান",     callback_data=f"csa_yes:{uid}"),
                    InlineKeyboardButton("❌ বাতিল",     callback_data=f"csa_no:{uid}"),
                    InlineKeyboardButton("➕ আরো বাটন", callback_data=f"csa_addbtn:{uid}"),
                ]]),
            )
        else:
            m = await message.reply_text(
                "⚠️ Format: <code>Button Text | https://example.com</code>",
                parse_mode=HTML,
            )
        asyncio.create_task(_auto_del(m, 25))


# ─── /overview — Activity Statistics ──────────────────────────────────────────

def _parse_overview_args(args: list[str]) -> tuple[datetime, datetime, str]:
    """Returns (start, end, label) for the requested period."""
    from datetime import timezone
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def _try_parse(s: str) -> datetime | None:
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
        return None

    if not args:
        # Default: last 7 days
        start = today_start - __import__('datetime').timedelta(days=6)
        return start, now, "শেষ ৭ দিন"

    if len(args) == 1:
        arg = args[0]
        # Number of days?
        if arg.isdigit():
            n = int(arg)
            start = today_start - __import__('datetime').timedelta(days=n - 1)
            return start, now, f"শেষ {n} দিন"
        # Specific date?
        dt = _try_parse(arg)
        if dt:
            return dt, dt.replace(hour=23, minute=59, second=59), dt.strftime("%d %b %Y")
        return today_start - __import__('datetime').timedelta(days=6), now, "শেষ ৭ দিন"

    if len(args) >= 2:
        dt1 = _try_parse(args[0])
        dt2 = _try_parse(args[1])
        if dt1 and dt2:
            end = dt2.replace(hour=23, minute=59, second=59)
            label = f"{dt1.strftime('%d %b')} – {dt2.strftime('%d %b %Y')}"
            return dt1, end, label

    return today_start - __import__('datetime').timedelta(days=6), now, "শেষ ৭ দিন"


async def _build_overview(start: datetime, end: datetime, label: str) -> str:
    from config import users_col, conversations_col, db as _db
    vid_hist = _db["user_video_history"]

    total_users   = await users_col.count_documents({})
    new_users     = await users_col.count_documents({"joined_at": {"$gte": start, "$lte": end}})
    msgs_in       = await conversations_col.count_documents({"direction": "in",  "timestamp": {"$gte": start, "$lte": end}})
    msgs_out      = await conversations_col.count_documents({"direction": "out", "timestamp": {"$gte": start, "$lte": end}})
    videos_sent   = await vid_hist.count_documents({"sent_at": {"$gte": start, "$lte": end}})

    return (
        f"📊 <b>অ্যাক্টিভিটি ওভারভিউ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗓 <b>পিরিয়ড:</b> {label}\n"
        f"   <i>{start.strftime('%d %b %Y')} → {end.strftime('%d %b %Y')}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>মোট ব্যবহারকারী:</b> {total_users:,}\n"
        f"🆕 <b>নতুন যোগদান:</b> {new_users:,}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 <b>ইনবক্স বার্তা:</b> {msgs_in:,}\n"
        f"📤 <b>উত্তর দেওয়া:</b> {msgs_out:,}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎬 <b>ভিডিও পাঠানো:</b> {videos_sent:,}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 <i>UTC {datetime.utcnow().strftime('%d %b %Y %H:%M')}</i>"
    )


@app.on_message(filters.command("overview") & filters.group, group=1)
async def overview_group_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    args = message.command[1:]
    start, end, label = _parse_overview_args(args)
    try:
        text = await _build_overview(start, end, label)
        m = await message.reply_text(text, parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 60))
    except Exception as e:
        m = await message.reply_text(f"❌ Error: <code>{e}</code>", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 15))


@app.on_message(filters.command("overview") & filters.private & admin_filter)
async def overview_dm_cmd(client: Client, message: Message):
    args = message.command[1:]
    start, end, label = _parse_overview_args(args)
    try:
        text = await _build_overview(start, end, label)
        await message.reply_text(text, parse_mode=HTML)
    except Exception as e:
        await message.reply_text(f"❌ Error: <code>{e}</code>", parse_mode=HTML)


# ── /videoon & /videooff ──────────────────────────────────────────────────────────

@app.on_message(filters.command(["videoon", "videooff"]) & filters.group, group=1)
async def ctrl_video_toggle_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    cmd  = message.command[0].lower()
    args = message.command[1:]

    if not args or not args[0].lstrip("-").isdigit():
        m = await message.reply_text(
            f"⚠️ <b>ব্যবহার:</b> <code>/{cmd} -100xxxxxxxxxx</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 15))
        return

    gid     = int(args[0])
    enabled = (cmd == "videoon")

    try:
        from handlers.group_settings import get_group_settings, update_group_settings
        settings = await get_group_settings(gid)
        features = settings.get("features", {})
        features["video"] = enabled
        await update_group_settings(gid, {"features": features})

        status = "✅ চালু করা হয়েছে" if enabled else "❌ বন্ধ করা হয়েছে"
        await message.reply_text(
            f"🎬 <b>Video কমান্ড {status}</b>\n"
            f"🆔 Group: <code>{gid}</code>",
            parse_mode=HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ ত্রুটি: <code>{e}</code>", parse_mode=HTML)


# ── /videomsgon & /videomsgoff ────────────────────────────────────────────────────

@app.on_message(filters.command(["videomsgon", "videomsgoff"]) & filters.group, group=1)
async def ctrl_videomsg_toggle_cmd(client: Client, message: Message):
    """videooff থাকলে redirect message দেখাবে কি না — এটা নিয়ন্ত্রণ করে।"""
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    cmd  = message.command[0].lower()
    args = message.command[1:]

    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text(
            f"⚠️ <b>ব্যবহার:</b> <code>/{cmd} -100xxxxxxxxxx</code>\n\n"
            f"<i>ℹ️ /videooff থাকলে গ্রুপে /video কমান্ডে redirect msg দেখাবে কি না।</i>",
            parse_mode=HTML,
        )
        return

    gid     = int(args[0])
    enabled = (cmd == "videomsgon")

    try:
        from handlers.group_settings import get_group_settings, update_group_settings
        settings = await get_group_settings(gid)
        features = settings.get("features", {})
        features["video_msg"] = enabled
        await update_group_settings(gid, {"features": features})

        status = "✅ চালু (redirect দেখাবে)" if enabled else "❌ বন্ধ (চুপচাপ ডিলিট)"
        await message.reply_text(
            f"💬 <b>Video Msg {status}</b>\n"
            f"🆔 Group: <code>{gid}</code>",
            parse_mode=HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ ত্রুটি: <code>{e}</code>", parse_mode=HTML)


# ── /welcomeon & /welcomeoff — Control Group থেকে welcome toggle ─────────────────

@app.on_message(filters.command(["welcomeon", "welcomeoff"]) & filters.group, group=1)
async def ctrl_welcome_toggle_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    cmd  = message.command[0].lower()
    args = message.command[1:]

    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text(
            f"⚠️ <b>ব্যবহার:</b> <code>/{cmd} -100xxxxxxxxxx</code>",
            parse_mode=HTML,
        )
        return

    gid     = int(args[0])
    enabled = (cmd == "welcomeon")

    try:
        from config import welcome_col as _welcome_col
        await _welcome_col.update_one(
            {"chat_id": gid},
            {"$set": {"chat_id": gid, "enabled": enabled}},
            upsert=True,
        )
        status = "✅ চালু করা হয়েছে" if enabled else "❌ বন্ধ করা হয়েছে"
        await message.reply_text(
            f"👋 <b>Welcome Message {status}</b>\n"
            f"🆔 Group: <code>{gid}</code>",
            parse_mode=HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ ত্রুটি: <code>{e}</code>", parse_mode=HTML)


# ── /antifloodon & /antifloodoff — Control Group থেকে antiflood toggle ───────────

@app.on_message(filters.command(["antifloodon", "antifloodoff"]) & filters.group, group=1)
async def ctrl_antiflood_toggle_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    cmd  = message.command[0].lower()
    args = message.command[1:]

    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text(
            f"⚠️ <b>ব্যবহার:</b> <code>/{cmd} -100xxxxxxxxxx</code>",
            parse_mode=HTML,
        )
        return

    gid     = int(args[0])
    enabled = (cmd == "antifloodon")

    try:
        from config import antiflood_col as _antiflood_col
        await _antiflood_col.update_one(
            {"chat_id": gid},
            {"$set": {"chat_id": gid, "enabled": enabled}},
            upsert=True,
        )
        status = "✅ চালু করা হয়েছে" if enabled else "❌ বন্ধ করা হয়েছে"
        await message.reply_text(
            f"⚠️ <b>Anti-Flood {status}</b>\n"
            f"🆔 Group: <code>{gid}</code>",
            parse_mode=HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ ত্রুটি: <code>{e}</code>", parse_mode=HTML)


# ── /autoreactionon & /autoreactionoff ───────────────────────────────────────────

@app.on_message(filters.command(["autoreactionon", "autoreactionoff"]) & filters.group, group=1)
async def ctrl_autoreaction_toggle_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    cmd  = message.command[0].lower()
    args = message.command[1:]

    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text(
            f"⚠️ <b>ব্যবহার:</b> <code>/{cmd} -100xxxxxxxxxx</code>",
            parse_mode=HTML,
        )
        return

    gid     = int(args[0])
    enabled = (cmd == "autoreactionon")

    try:
        from handlers.group_settings import get_group_settings, update_group_settings
        from config import auto_reactions_col as _ar_col
        settings = await get_group_settings(gid)
        features = settings.get("features", {})
        features["auto_reactions"] = enabled
        await update_group_settings(gid, {"features": features})

        # auto_reactions_col ও আপডেট করো (apply_auto_reactions এটাও চেক করে)
        ar_doc = await _ar_col.find_one({"chat_id": gid})
        if enabled and not ar_doc:
            # প্রথমবার চালু: default emoji দিয়ে তৈরি করো
            await _ar_col.update_one(
                {"chat_id": gid},
                {"$set": {"chat_id": gid, "enabled": True, "reactions": ["😂", "🔥", "❤️"]}},
                upsert=True,
            )
        elif ar_doc:
            await _ar_col.update_one(
                {"chat_id": gid},
                {"$set": {"enabled": enabled}},
            )

        extra = ""
        if enabled and not ar_doc:
            extra = "\n<i>Default reactions: 😂 🔥 ❤️ — /setreactions দিয়ে পরিবর্তন করুন।</i>"

        status = "✅ চালু করা হয়েছে" if enabled else "❌ বন্ধ করা হয়েছে"
        await message.reply_text(
            f"😂 <b>Auto Reaction {status}</b>\n"
            f"🆔 Group: <code>{gid}</code>{extra}",
            parse_mode=HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ ত্রুটি: <code>{e}</code>", parse_mode=HTML)


# ── /forwardon / /forwardoff / /linkon / /linkoff ─────────────────────────────────
# Control group থেকে forward ও link protection দ্রুত চালু/বন্ধ করুন

@app.on_message(
    filters.command(["forwardon", "forwardoff", "linkon", "linkoff"]) & filters.group,
    group=1,
)
async def ctrl_protection_toggle_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    cmd  = message.command[0].lower()
    args = message.command[1:]

    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text(
            f"⚠️ <b>ব্যবহার:</b> <code>/{cmd} -100xxxxxxxxxx</code>",
            parse_mode=HTML,
        )
        return

    gid = int(args[0])

    if cmd in ("forwardon", "forwardoff"):
        prot_key  = "anti_forward"
        prot_name = "Forward Protection"
        enabled   = (cmd == "forwardon")
    else:
        prot_key  = "link_protection"
        prot_name = "Link Protection"
        enabled   = (cmd == "linkon")

    try:
        from handlers.protection import _save_prot
        await _save_prot(gid, prot_key, enabled)
        icon = "✅" if enabled else "❌"
        await message.reply_text(
            f"{icon} <b>{prot_name}</b> {'চালু' if enabled else 'বন্ধ'} করা হয়েছে।\n"
            f"🆔 Group: <code>{gid}</code>",
            parse_mode=HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ ত্রুটি: <code>{e}</code>", parse_mode=HTML)


# ── /spamon / /spamoff — Spam protection toggle ────────────────────────────────

@app.on_message(
    filters.command(["spamon", "spamoff"]) & filters.group,
    group=1,
)
async def ctrl_spam_toggle_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    cmd  = message.command[0].lower()
    args = message.command[1:]

    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text(
            f"⚠️ <b>ব্যবহার:</b> <code>/{cmd} -100xxxxxxxxxx</code>",
            parse_mode=HTML,
        )
        return

    gid     = int(args[0])
    enabled = (cmd == "spamon")

    try:
        from handlers.protection import _save_prot
        await _save_prot(gid, "anti_spam", enabled)
        icon = "✅" if enabled else "❌"
        await message.reply_text(
            f"{icon} <b>Spam Protection</b> {'চালু' if enabled else 'বন্ধ'} করা হয়েছে।\n"
            f"🆔 Group: <code>{gid}</code>",
            parse_mode=HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ ত্রুটি: <code>{e}</code>", parse_mode=HTML)


# ── /warnon / /warnoff — Warning message toggle ────────────────────────────────
# Forward ও link protection-এর warning message চালু/বন্ধ করুন

@app.on_message(
    filters.command(["warnon", "warnoff"]) & filters.group,
    group=1,
)
async def ctrl_warn_toggle_cmd(client: Client, message: Message):
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    cmd  = message.command[0].lower()
    args = message.command[1:]

    if not args or not args[0].lstrip("-").isdigit():
        await message.reply_text(
            f"⚠️ <b>ব্যবহার:</b> <code>/{cmd} -100xxxxxxxxxx</code>\n\n"
            f"<code>/warnon</code>  — ডিলিট হলে Warning পাঠাবে ✅\n"
            f"<code>/warnoff</code> — চুপচাপ ডিলিট করবে, Warning দেবে না ❌",
            parse_mode=HTML,
        )
        return

    gid     = int(args[0])
    enabled = (cmd == "warnon")

    try:
        from handlers.protection import _save_prot
        await _save_prot(gid, "show_warning", enabled)
        icon = "✅" if enabled else "🔇"
        await message.reply_text(
            f"{icon} <b>Warning Message</b> {'চালু' if enabled else 'বন্ধ'} করা হয়েছে।\n"
            f"🆔 Group: <code>{gid}</code>\n\n"
            f"{'🔔 এখন Protection ট্রিগার হলে ইউজারকে Warning দেওয়া হবে।' if enabled else '🔇 এখন Protection ট্রিগার হলে চুপচাপ ডিলিট হবে।'}",
            parse_mode=HTML,
        )
    except Exception as e:
        await message.reply_text(f"❌ ত্রুটি: <code>{e}</code>", parse_mode=HTML)


# ── /tagall [gid] [msg] — Control Group থেকে visible mention tag ──────────────────────
# (ইতিমধ্যে /taggroup আছে। /tagall এখন control group থেকেও কাজ করবে।)

_tagall_ctrl_sessions: dict[int, dict] = {}


@app.on_message(filters.command("tagall") & filters.group, group=1)
async def ctrl_tagall_cmd(client: Client, message: Message):
    """Control Group থেকে /tagall [gid] [msg] — visible mention tag করে, বাটন যোগের সুবিধাসহ।"""
    # শুধু control group-এ কাজ করবে এই ব্লক
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    # split(None, 2) preserves newlines inside the message text
    raw_ta   = (message.text or message.caption or "").strip()
    parts_ta = raw_ta.split(None, 2)   # ["/tagall", "gid", "rest with newlines"]

    if len(parts_ta) < 2 or not parts_ta[1].lstrip("-").isdigit():
        m = await message.reply_text(
            "⚠️ <b>ব্যবহার:</b>\n"
            "<code>/tagall [group_id] [মেসেজ]</code>\n\n"
            "উদাহরণ:\n"
            "<code>/tagall -1001234567890 সবাইকে জরুরি নোটিশ!</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 20))
        return

    gid     = int(parts_ta[1])
    msg_txt = parts_ta[2] if len(parts_ta) >= 3 else ""
    uid     = message.from_user.id

    # গ্রুপ যাচাই করো
    try:
        chat      = await client.get_chat(gid)
        grp_title = chat.title or str(gid)
    except Exception as e:
        m = await message.reply_text(f"❌ গ্রুপ খুঁজে পাওয়া যায়নি: <code>{e}</code>", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 15))
        return

    # Session সংরক্ষণ করো
    _tagall_ctrl_sessions[uid] = {
        "gid":           gid,
        "grp_title":     grp_title,
        "msg_txt":       msg_txt,
        "extra_buttons": [],
        "step":          "confirm",
    }

    await _show_tagall_confirm(message, uid)


async def _show_tagall_confirm(message: Message, uid: int):
    import html as _html
    session = _tagall_ctrl_sessions.get(uid, {})
    gid     = session.get("gid")
    title   = session.get("grp_title", str(gid))
    msg_txt = session.get("msg_txt", "")
    btns    = session.get("extra_buttons", [])   # flat list

    preview_txt = _html.escape(msg_txt[:300]) if msg_txt else "<i>(কোনো মেসেজ নেই)</i>"
    if msg_txt and len(msg_txt) > 300:
        preview_txt += "…"

    btn_preview = ""
    for i, b in enumerate(btns, 1):
        btn_preview += f"\n  {i}. 🔗 {_html.escape(b['text'])} → {_html.escape(b['url'])}"

    kb = await _tag_quick_kb("ctag", uid, bool(btns))

    m = await message.reply_text(
        f"🏷️ <b>Tag All</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Group: <b>{_html.escape(title)}</b>  <code>{gid}</code>\n"
        f"💬 মেসেজ:\n{preview_txt}"
        f"{btn_preview}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"নিশ্চিত করুন?",
        parse_mode=HTML,
        reply_markup=kb,
    )
    asyncio.create_task(_auto_del(m, 180))


@app.on_callback_query(filters.regex(r"^ctag_(yes|no|addbtn|clrbtn|qb_\w+):(\d+)$"))
async def ctrl_tagall_cb(client: Client, cq: CallbackQuery):
    import re as _re
    m2     = _re.match(r"^ctag_(yes|no|addbtn|clrbtn|qb_\w+):(\d+)$", cq.data)
    action = m2.group(1)
    uid    = int(m2.group(2))

    if cq.from_user.id != uid:
        await cq.answer("❌ শুধু কমান্ড-দাতাই ব্যবহার করতে পারবেন।", show_alert=True)
        return

    session = _tagall_ctrl_sessions.get(uid)
    if not session:
        await cq.answer("⏰ Session শেষ হয়ে গেছে।", show_alert=True)
        return

    if action == "no":
        _tagall_ctrl_sessions.pop(uid, None)
        await cq.edit_message_text("❌ Tagall বাতিল।")
        return

    if action == "clrbtn":
        session["extra_buttons"] = []
        session["step"]          = "confirm"
        _tagall_ctrl_sessions[uid] = session
        await cq.answer("🗑️ সব বাটন মুছে গেছে।")
        kb = await _tag_quick_kb("ctag", uid, False)
        try:
            await cq.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            pass
        return

    if action.startswith("qb_"):
        bot_username = await get_bot_username(client)
        quick = {
            "qb_premium": ("💎 Buy Premium", f"https://t.me/{bot_username}?start=buypremium"),
            "qb_profile":  ("👤 My Profile",  f"https://t.me/{bot_username}?start=profile"),
            "qb_couple":   ("🔵 Couple Group", "https://t.me/+PnUkO8waIEcyNDY1"),
        }
        if action not in quick:
            await cq.answer("Unknown button.", show_alert=True)
            return
        label, url = quick[action]
        btns = session.setdefault("extra_buttons", [])
        if any(b["url"] == url for b in btns):
            await cq.answer("⚠️ বাটনটি আগেই আছে!", show_alert=True)
            return
        btns.append({"text": label, "url": url})
        _tagall_ctrl_sessions[uid] = session
        await cq.answer(f"✅ '{label}' যোগ হয়েছে!")
        kb = await _tag_quick_kb("ctag", uid, True)
        try:
            await cq.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            pass
        return

    if action == "addbtn":
        session["step"] = "wait_button"
        _tagall_ctrl_sessions[uid] = session
        await cq.edit_message_text(
            "🔗 <b>বাটন যোগ করুন</b>\n\n"
            "নিচের command দিন:\n\n"
            "এক বাটন:\n"
            "<code>/addbtn বাটন টেক্সট | https://example.com</code>\n\n"
            "দুই বাটন পাশাপাশি:\n"
            "<code>/addbtn Btn1 | https://url1 && Btn2 | https://url2</code>\n\n"
            "একাধিক বাটন (প্রতি লাইনে একটি):\n"
            "<code>/addbtn Join | https://t.me/...\nChannel | https://t.me/...</code>\n\n"
            "⬅️ /cancel দিয়ে বাতিল করুন।",
            parse_mode=HTML,
        )
        return

    # action == "yes" — tagall শুরু করো
    _tagall_ctrl_sessions.pop(uid, None)
    await cq.answer("🏷️ Tagging শুরু হচ্ছে…")
    await cq.edit_message_text("⏳ <b>Tagging in progress…</b>", parse_mode=HTML)

    gid      = session["gid"]
    msg_txt  = session.get("msg_txt", "")
    btns     = session.get("extra_buttons", [])   # flat list
    kb_json  = {"inline_keyboard": _smart_button_rows(btns)} if btns else None

    tagged      = 0
    chunk       = []
    chunks_sent = 0
    BATCH_SIZE  = 4      # 4 visible mentions per message (TOS compliant)
    BATCH_DELAY = 3.0    # 3-second mandatory delay between batches

    try:
        async for member in client.get_chat_members(gid):
            u = member.user
            if u.is_bot or u.is_deleted:
                continue
            name = (u.first_name or "").strip() or "Member"
            chunk.append(f'<a href="tg://user?id={u.id}">{name}</a>')
            tagged += 1

            if len(chunk) >= BATCH_SIZE:
                mentions = "  ".join(chunk)
                prefix   = f"{msg_txt}\n\n" if (chunks_sent == 0 and msg_txt) else ""
                payload  = prefix + mentions
                params   = {"chat_id": gid, "text": payload, "parse_mode": "HTML"}
                if kb_json and chunks_sent == 0:
                    params["reply_markup"] = kb_json
                try:
                    r = await bot_api("sendMessage", params)
                    if r.get("ok"):
                        chunks_sent += 1
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except Exception:
                    pass
                chunk = []
                await asyncio.sleep(BATCH_DELAY)

        if chunk:
            mentions = "  ".join(chunk)
            prefix   = f"{msg_txt}\n\n" if (chunks_sent == 0 and msg_txt) else ""
            payload  = prefix + mentions
            params   = {"chat_id": gid, "text": payload, "parse_mode": "HTML"}
            if kb_json and chunks_sent == 0:
                params["reply_markup"] = kb_json
            try:
                await bot_api("sendMessage", params)
            except Exception:
                pass