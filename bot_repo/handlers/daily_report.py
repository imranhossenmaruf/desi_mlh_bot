"""
Daily Auto-Report System
─────────────────────────────────────────────────────────────
প্রতিদিন রাত ১২:০০ টায় (Bangladesh Standard Time = UTC+6)
Control Group-এ গতকালের summary report পাঠায়।

Commands (Control Group):
  /dailyreport           — এখনই গতকালের report পাঠান
  /dailyreporton         — Auto daily report চালু
  /dailyreportoff        — Auto daily report বন্ধ
  /reporttime HH:MM      — Report পাঠানোর সময় সেট করুন (BDT)
"""

import asyncio
from datetime import datetime, timedelta, timezone

from pyrogram import Client, filters
from pyrogram.types import Message

from config import (
    HTML, ADMIN_IDS,
    groups_col, users_col, conversations_col, db, app,
)
from helpers import _auto_del, admin_filter

# ── Constants ─────────────────────────────────────────────────────────────────
BDT = timezone(timedelta(hours=6))          # Bangladesh Standard Time

_report_col = db["daily_report_settings"]  # stores on/off + send_time

# ── Default send time: 00:00 BDT every night ─────────────────────────────────
DEFAULT_HOUR   = 0
DEFAULT_MINUTE = 0


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_settings() -> dict:
    doc = await _report_col.find_one({"key": "daily_report"})
    return doc or {
        "enabled":     True,
        "send_hour":   DEFAULT_HOUR,
        "send_minute": DEFAULT_MINUTE,
    }


async def _save_settings(data: dict):
    await _report_col.update_one(
        {"key": "daily_report"},
        {"$set": {**data, "key": "daily_report"}},
        upsert=True,
    )


# ── Control Group helpers ─────────────────────────────────────────────────────

async def _get_control_group() -> int | None:
    from handlers.control_group import get_control_group
    return await get_control_group()


async def _is_ctrl_admin(client: Client, message: Message) -> bool:
    if message.from_user and message.from_user.id in ADMIN_IDS:
        return True
    try:
        from pyrogram.enums import ChatMemberStatus
        member = await client.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False


# ── Report Builder ────────────────────────────────────────────────────────────

async def _build_daily_report(target_date: datetime | None = None) -> str:
    """Build the full daily report for a given UTC date (defaults to yesterday)."""
    now_bdt      = datetime.now(BDT)
    report_bdt   = target_date or (now_bdt - timedelta(days=1))

    # Day window in UTC
    day_start_bdt = report_bdt.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_bdt   = day_start_bdt + timedelta(days=1)
    day_start_utc = day_start_bdt.astimezone(timezone.utc).replace(tzinfo=None)
    day_end_utc   = day_end_bdt.astimezone(timezone.utc).replace(tzinfo=None)

    day_label = report_bdt.strftime("%d %B %Y")   # e.g. "09 April 2026"

    vid_hist = db["user_video_history"]

    # ── User stats ────────────────────────────────────────────────────────────
    total_users = await users_col.count_documents({})
    new_users   = await users_col.count_documents(
        {"joined_at": {"$gte": day_start_utc, "$lt": day_end_utc}}
    )

    # ── Inbox stats ───────────────────────────────────────────────────────────
    msgs_in  = await conversations_col.count_documents(
        {"direction": "in",  "timestamp": {"$gte": day_start_utc, "$lt": day_end_utc}}
    )
    msgs_out = await conversations_col.count_documents(
        {"direction": "out", "timestamp": {"$gte": day_start_utc, "$lt": day_end_utc}}
    )

    # ── Video stats ───────────────────────────────────────────────────────────
    videos_sent = await vid_hist.count_documents(
        {"sent_at": {"$gte": day_start_utc, "$lt": day_end_utc}}
    )

    # ── Group stats ───────────────────────────────────────────────────────────
    all_groups    = await groups_col.find({}).to_list(length=None)
    total_groups  = len(all_groups)
    admin_groups  = sum(1 for g in all_groups if g.get("bot_is_admin"))
    total_members = sum(g.get("member_count") or 0 for g in all_groups)

    # Top 5 groups by member count
    top5 = sorted(
        [g for g in all_groups if g.get("member_count")],
        key=lambda g: g["member_count"],
        reverse=True,
    )[:5]

    # ── Protection status ─────────────────────────────────────────────────────
    prot_col  = db["group_protections"]
    prot_docs = await prot_col.find({}).to_list(length=None)
    af_on     = sum(1 for p in prot_docs if p.get("anti_forward"))
    lk_on     = sum(1 for p in prot_docs if p.get("anti_link"))
    sp_on     = sum(1 for p in prot_docs if p.get("anti_spam"))

    # ── Premium stats ─────────────────────────────────────────────────────────
    premium_col  = db["premium_users"]
    total_prem   = await premium_col.count_documents({})
    active_prem  = await premium_col.count_documents(
        {"expires_at": {"$gt": now_bdt.replace(tzinfo=None)}}
    )

    # ── Build text ────────────────────────────────────────────────────────────
    top5_lines = ""
    for i, g in enumerate(top5, 1):
        title   = (g.get("title") or str(g.get("chat_id", "?")))[:22]
        members = g.get("member_count", 0)
        top5_lines += f"  {i}. <b>{title}</b> — {members:,}\n"

    if not top5_lines:
        top5_lines = "  ডেটা নেই\n"

    response_rate = (
        f"{(msgs_out / msgs_in * 100):.0f}%" if msgs_in > 0 else "—"
    )

    report = (
        f"📋 <b>দৈনিক রিপোর্ট</b>\n"
        f"🗓 <b>{day_label}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"👤 <b>ব্যবহারকারী</b>\n"
        f"  মোট: <b>{total_users:,}</b>   নতুন: <b>+{new_users:,}</b>\n"
        f"  প্রিমিয়াম: <b>{active_prem}</b> / {total_prem} সক্রিয়\n"
        f"\n"
        f"📬 <b>ইনবক্স বার্তা</b>\n"
        f"  আসা: <b>{msgs_in:,}</b>   উত্তর: <b>{msgs_out:,}</b>\n"
        f"  Response Rate: <b>{response_rate}</b>\n"
        f"\n"
        f"🎬 <b>ভিডিও পাঠানো</b>\n"
        f"  মোট: <b>{videos_sent:,}</b>\n"
        f"\n"
        f"🏘️ <b>গ্রুপ পরিসংখ্যান</b>\n"
        f"  মোট: <b>{total_groups}</b>   Admin: <b>{admin_groups}</b>\n"
        f"  সদস্য: <b>{total_members:,}</b>\n"
        f"\n"
        f"🔝 <b>শীর্ষ ৫ গ্রুপ</b>\n"
        f"{top5_lines}"
        f"\n"
        f"🛡️ <b>সুরক্ষা সক্রিয়</b>\n"
        f"  Forward: <b>{af_on}</b> | Link: <b>{lk_on}</b> | Spam: <b>{sp_on}</b> গ্রুপে\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 <i>রিপোর্ট তৈরি: {now_bdt.strftime('%d %b %Y %H:%M')} BDT</i>"
    )
    return report


# ── Background Loop ───────────────────────────────────────────────────────────

async def daily_report_loop(client: Client):
    """Runs indefinitely. Sends daily report to control group at configured time."""
    print("[DAILY_REPORT] Loop started.")
    _sent_date: datetime | None = None   # track last sent date (BDT)

    while True:
        try:
            settings = await _get_settings()

            if not settings.get("enabled", True):
                await asyncio.sleep(60)
                continue

            now_bdt       = datetime.now(BDT)
            target_hour   = settings.get("send_hour",   DEFAULT_HOUR)
            target_minute = settings.get("send_minute", DEFAULT_MINUTE)

            # Calculate seconds until next send time
            target_today = now_bdt.replace(
                hour=target_hour, minute=target_minute,
                second=0, microsecond=0,
            )
            if now_bdt >= target_today:
                target_today += timedelta(days=1)

            wait_secs = (target_today - now_bdt).total_seconds()

            # Check if we already sent today
            today_date = now_bdt.date()
            if _sent_date == today_date and now_bdt < target_today:
                await asyncio.sleep(min(wait_secs, 60))
                continue

            # Wait until send time
            if wait_secs > 0:
                await asyncio.sleep(min(wait_secs, 60))
                continue

            # ── Send time reached ─────────────────────────────────────────────
            if _sent_date == today_date:
                await asyncio.sleep(60)
                continue

            ctrl_gid = await _get_control_group()
            if ctrl_gid:
                try:
                    report_text = await _build_daily_report()
                    await client.send_message(ctrl_gid, report_text, parse_mode=HTML)
                    print(f"[DAILY_REPORT] Sent to control group {ctrl_gid}")
                except Exception as e:
                    print(f"[DAILY_REPORT] Send failed: {e}")
            else:
                print("[DAILY_REPORT] No control group set — skipping.")

            _sent_date = today_date

        except Exception as e:
            print(f"[DAILY_REPORT] Loop error: {e}")

        await asyncio.sleep(60)


# ── Commands ──────────────────────────────────────────────────────────────────

@app.on_message(filters.command("dailyreport") & filters.group, group=1)
async def dailyreport_now_cmd(client: Client, message: Message):
    """Control group: manually trigger daily report."""
    from handlers.control_group import is_control_group
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    m = await message.reply_text("⏳ রিপোর্ট তৈরি হচ্ছে…", parse_mode=HTML)
    try:
        text = await _build_daily_report()
        await m.edit_text(text, parse_mode=HTML)
    except Exception as e:
        await m.edit_text(f"❌ Error: <code>{e}</code>", parse_mode=HTML)


@app.on_message(filters.command(["dailyreporton", "dailyreportoff"]) & filters.group, group=1)
async def dailyreport_toggle_cmd(client: Client, message: Message):
    """Control group: enable/disable auto daily report."""
    from handlers.control_group import is_control_group
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    enabled = message.command[0].lower() == "dailyreporton"
    settings = await _get_settings()
    settings["enabled"] = enabled
    await _save_settings(settings)

    icon = "✅" if enabled else "❌"
    hour = settings.get("send_hour", DEFAULT_HOUR)
    minn = settings.get("send_minute", DEFAULT_MINUTE)
    status_text = (
        f"{icon} <b>Daily Auto-Report {'চালু' if enabled else 'বন্ধ'}</b>\n"
        + (f"⏰ প্রতিদিন <b>{hour:02d}:{minn:02d} BDT</b>-এ পাঠানো হবে।" if enabled else "")
    )
    m = await message.reply_text(status_text, parse_mode=HTML)
    asyncio.create_task(_auto_del(m, 20))


@app.on_message(filters.command("reporttime") & filters.group, group=1)
async def reporttime_cmd(client: Client, message: Message):
    """Control group: set daily report time. Usage: /reporttime HH:MM"""
    from handlers.control_group import is_control_group
    if not await is_control_group(message.chat.id):
        return
    if not await _is_ctrl_admin(client, message):
        return

    args = message.command[1:]
    if not args:
        settings = await _get_settings()
        h = settings.get("send_hour", DEFAULT_HOUR)
        mn = settings.get("send_minute", DEFAULT_MINUTE)
        m = await message.reply_text(
            f"⏰ বর্তমান report time: <b>{h:02d}:{mn:02d} BDT</b>\n"
            f"পরিবর্তন করতে: <code>/reporttime HH:MM</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 20))
        return

    time_str = args[0].strip()
    try:
        h, mn = map(int, time_str.split(":"))
        assert 0 <= h <= 23 and 0 <= mn <= 59
    except Exception:
        m = await message.reply_text(
            "⚠️ Format: <code>/reporttime HH:MM</code>\nউদাহরণ: <code>/reporttime 00:00</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 15))
        return

    settings = await _get_settings()
    settings["send_hour"]   = h
    settings["send_minute"] = mn
    await _save_settings(settings)

    m = await message.reply_text(
        f"✅ Report time সেট হয়েছে: <b>{h:02d}:{mn:02d} BDT</b>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 20))


@app.on_message(filters.command("dailyreport") & filters.private & admin_filter, group=1)
async def dailyreport_private_cmd(client: Client, message: Message):
    """Private DM: admin can pull today's report anytime."""
    m = await message.reply_text("⏳ রিপোর্ট তৈরি হচ্ছে…", parse_mode=HTML)
    try:
        text = await _build_daily_report()
        await m.edit_text(text, parse_mode=HTML)
    except Exception as e:
        await m.edit_text(f"❌ Error: <code>{e}</code>", parse_mode=HTML)
