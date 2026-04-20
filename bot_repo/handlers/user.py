import io
import csv
import asyncio
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    HTML, ADMIN_ID, DAILY_VIDEO_LIMIT, PACKAGES, PACKAGE_ORDER,
    users_col, videos_col, vid_hist_col, premium_col,
    groups_col, conversations_col,
    app,
)
from helpers import get_bot_username, get_rank, get_status, log_event, bot_api, admin_filter


@app.on_message(filters.command("stats") & admin_filter & filters.private)
async def stats_handler(client: Client, message: Message):
    wait = await message.reply_text("⏳ Gathering stats...")
    now       = datetime.utcnow()
    t0_today  = now.replace(hour=0, minute=0, second=0, microsecond=0)
    t0_7d     = t0_today - timedelta(days=7)
    t0_30d    = t0_today - timedelta(days=30)
    today_str = now.strftime("%Y-%m-%d")

    # ── Users ──────────────────────────────────────────────────────────
    total_users = await users_col.count_documents({})
    new_today   = await users_col.count_documents({"joined_at": {"$gte": t0_today}})
    new_7d      = await users_col.count_documents({"joined_at": {"$gte": t0_7d}})
    new_30d     = await users_col.count_documents({"joined_at": {"$gte": t0_30d}})

    # ── Videos ─────────────────────────────────────────────────────────
    total_vids      = await videos_col.count_documents({})
    vids_sent_today = await vid_hist_col.count_documents({"sent_at": {"$gte": t0_today}})
    vids_sent_7d    = await vid_hist_col.count_documents({"sent_at": {"$gte": t0_7d}})
    vid_users_today = await users_col.count_documents({"video_date": today_str})
    daily_today     = await users_col.count_documents({"last_daily": {"$gte": t0_today}})

    # ── Premium ────────────────────────────────────────────────────────
    active_prem = await premium_col.count_documents({"expires_at": {"$gt": now}})
    pkg_lines   = []
    for pk in PACKAGE_ORDER:
        cnt = await premium_col.count_documents({"package": pk, "expires_at": {"$gt": now}})
        if cnt:
            label = PACKAGES[pk]["label"]
            pkg_lines.append(f"   {label}: {cnt}")

    stars_agg = await premium_col.aggregate([
        {"$group": {"_id": None,
                    "active": {"$sum": {"$cond": [{"$gt": ["$expires_at", now]}, "$stars_paid", 0]}},
                    "alltime": {"$sum": "$stars_paid"}}}
    ]).to_list(length=1)
    stars_active  = stars_agg[0]["active"]  if stars_agg else 0
    stars_alltime = stars_agg[0]["alltime"] if stars_agg else 0

    # Stars revenue today
    stars_today_agg = await premium_col.aggregate([
        {"$match": {"started_at": {"$gte": t0_today}}},
        {"$group": {"_id": None, "total": {"$sum": "$stars_paid"}}},
    ]).to_list(length=1)
    stars_today = stars_today_agg[0]["total"] if stars_today_agg else 0

    # ── Points ─────────────────────────────────────────────────────────
    pts_agg    = await users_col.aggregate([
        {"$group": {"_id": None, "total": {"$sum": "$points"}}}
    ]).to_list(length=1)
    total_pts  = pts_agg[0]["total"] if pts_agg else 0

    top_ref    = await users_col.find_one({}, sort=[("ref_count", -1)])
    top_ref_name = (top_ref.get("first_name") or str(top_ref.get("user_id", "?"))) if top_ref else "—"
    top_ref_cnt  = top_ref.get("ref_count", 0) if top_ref else 0

    # ── Inbox ──────────────────────────────────────────────────────────
    inbox_today = await conversations_col.count_documents({"timestamp": {"$gte": t0_today}})
    inbox_total = await conversations_col.count_documents({})

    # ── Groups ────────────────────────────────────────────────────────
    total_groups = await groups_col.count_documents({})

    pkg_block = "\n".join(pkg_lines) if pkg_lines else "   None active"

    await wait.delete()
    await message.reply_text(
        "📊 BOT REPORT — 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👥 USERS:\n"
        f"📌 Total        : {total_users:,}\n"
        f"🆕 Today        : {new_today:,}\n"
        f"📅 Last 7 Days  : {new_7d:,}\n"
        f"📆 Last 30 Days : {new_30d:,}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💎 PREMIUM:\n"
        f"✅ Active Subs  : {active_prem:,}\n"
        f"{pkg_block}\n"
        f"⭐ Stars Today  : {stars_today:,}\n"
        f"⭐ Active Stars : {stars_active:,}\n"
        f"⭐ All-Time Stars: {stars_alltime:,}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📹 VIDEOS:\n"
        f"📦 Library      : {total_vids:,}\n"
        f"▶️  Sent Today   : {vids_sent_today:,}\n"
        f"▶️  Sent 7 Days  : {vids_sent_7d:,}\n"
        f"👤 Users Today  : {vid_users_today:,}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎯 ENGAGEMENT:\n"
        f"🎁 Daily Claims : {daily_today:,}\n"
        f"💰 Total Points : {total_pts:,}\n"
        f"🏆 Top Referrer : {top_ref_name} ({top_ref_cnt} refs)\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📩 INBOX:\n"
        f"📨 Today        : {inbox_today:,} messages\n"
        f"📨 All-Time     : {inbox_total:,} messages\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏠 Groups       : {total_groups:,}\n"
        f"🕐 {now.strftime('%d %b %Y  %H:%M')} UTC\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )


@app.on_message(filters.command("user") & admin_filter & filters.private)
async def user_lookup_handler(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text("Usage:\n/user 123456789\n/user @username")
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

    last_daily   = doc.get("last_daily")
    now          = datetime.utcnow()
    daily_status = (
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


@app.on_message(filters.command("addpoints") & admin_filter & filters.private)
async def addpoints_handler(client: Client, message: Message):
    await _change_points(message, positive=True)


@app.on_message(filters.command("removepoints") & admin_filter & filters.private)
async def removepoints_handler(client: Client, message: Message):
    await _change_points(message, positive=False)


async def _change_points(message: Message, positive: bool):
    from config import app as _app
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
    new_points = max(0, old_points + amount)
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
    asyncio.create_task(log_event(_app,
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


@app.on_message(filters.command("setlimit") & admin_filter & filters.private)
async def setlimit_handler(client: Client, message: Message):
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
    await message.reply_text(
        "✅ VIDEO LIMIT UPDATED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User    : {mention}\n"
        f"🆔 ID      : {target_id}\n"
        f"📹 New Limit: {limit_label} videos/day\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )
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


@app.on_message(filters.command("export") & admin_filter & filters.private)
async def export_handler(client: Client, message: Message):
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
        csv_bytes = buf.getvalue().encode("utf-8-sig")
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


@app.on_message(filters.command("clearhistory") & admin_filter & filters.private)
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

    result  = await vid_hist_col.delete_many({"user_id": target_id})
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


@app.on_message(filters.command("profile") & filters.private)
async def profile_cmd(client: Client, message: Message):
    from datetime import timezone
    user_id    = message.from_user.id
    doc        = await users_col.find_one({"user_id": user_id})
    if not doc:
        await message.reply_text("❌ Profile not found. Please /start first.")
        return

    ref_count  = doc.get("ref_count", 0)
    points     = doc.get("points", 0)
    joined_at  = doc.get("joined_at")
    joined_str = joined_at.strftime("%d %b %Y") if joined_at else "—"
    bot_uname  = await get_bot_username(client)

    today     = datetime.utcnow().strftime("%Y-%m-%d")
    vid_date  = doc.get("video_date", "")
    vid_count = doc.get("video_count", 0) if vid_date == today else 0

    last_daily = doc.get("last_daily")
    now        = datetime.utcnow()
    if last_daily and (now - last_daily).total_seconds() < 86400:
        rem_secs   = 86400 - int((now - last_daily).total_seconds())
        hrs, r     = divmod(rem_secs, 3600)
        daily_line = f"📅 Daily Bonus: claimed (next in {hrs}h {r//60}m)"
    else:
        daily_line = "📅 Daily Bonus: available ✅  →  /daily"

    rank     = get_rank(ref_count)
    status   = get_status(points)
    ref_link = f"https://t.me/{bot_uname}?start={user_id}"

    prem_doc = await premium_col.find_one({"user_id": user_id})
    raw_lim  = doc.get("video_limit")
    if prem_doc:
        exp = prem_doc.get("expires_at")
        if exp and exp.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            pkg_info  = PACKAGES.get(prem_doc["package"], {})
            prem_lim  = prem_doc.get("video_limit", 0)
            lim_str   = "∞" if prem_lim >= 999 else str(prem_lim)
            rem_days  = (exp.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days
            prem_line = (
                "\n💎 PREMIUM MEMBERSHIP:\n"
                f"   Package  : {pkg_info.get('label', prem_doc['package'])}\n"
                f"   Expires  : {exp.strftime('%d %b %Y')}  ({rem_days}d left)\n"
                f"   Vid/day  : {lim_str}\n"
            )
        else:
            await premium_col.delete_one({"user_id": user_id})
            prem_line = ""
            if raw_lim == -1:
                lim_str = "∞ (Admin)"
            elif isinstance(raw_lim, int) and raw_lim > 0:
                lim_str = str(raw_lim)
            else:
                lim_str = str(DAILY_VIDEO_LIMIT)
    else:
        prem_line = ""
        if raw_lim == -1:
            lim_str = "∞ (Admin)"
        elif isinstance(raw_lim, int) and raw_lim > 0:
            lim_str = str(raw_lim)
        else:
            lim_str = str(DAILY_VIDEO_LIMIT)

    await message.reply_text(
        "━━━━━━━━━━━━━━━━━━━\n"
        "👤 MY PROFILE — 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 ID       : {user_id}\n"
        f"📅 Joined   : {joined_str}\n"
        f"{prem_line}"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📊 STATISTICS:\n"
        f"💰 Points   : {points}\n"
        f"👥 Referrals: {ref_count}\n"
        f"🏅 Rank     : {rank}\n"
        f"✨ Status   : {status}\n\n"
        f"📹 Videos Today : {vid_count}/{lim_str}\n"
        f"{daily_line}\n\n"
        f"🔗 Referral Link:\n{ref_link}\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💎 Buy Premium", callback_data="open_buypremium"),
        ]]),
    )


@app.on_message(filters.command("resetcount") & admin_filter & filters.private)
async def resetcount_handler(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage:\n"
            "/resetcount @username\n"
            "/resetcount 123456789"
        )
        return
    raw = args[0].lstrip("@")
    doc = (
        await users_col.find_one({"user_id": int(raw)})
        if raw.isdigit()
        else await users_col.find_one({"username": raw})
    )
    if not doc:
        await message.reply_text("❌ User not found.")
        return
    target_id = doc["user_id"]
    fname     = doc.get("first_name") or str(target_id)
    await users_col.update_one(
        {"user_id": target_id},
        {"$set": {"video_count": 0, "video_date": ""}},
    )
    await message.reply_text(
        "✅ VIDEO COUNT RESET\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User : {fname}\n"
        f"🆔 ID   : <code>{target_id}</code>\n"
        "📹 Today's count set to 0.\n"
        "User can now watch videos again.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    print(f"[ADMIN] resetcount user={target_id}")
