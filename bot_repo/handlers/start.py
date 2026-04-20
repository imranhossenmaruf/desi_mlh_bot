import asyncio
import urllib.parse
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from config import (
    HTML, ADMIN_ID, ADMIN_IDS, DAILY_VIDEO_LIMIT,
    users_col, broadcast_sessions, pending_welcome_msgs,
)
from helpers import (
    get_bot_username, save_user, get_rank, get_status, log_event, bot_api,
)
from strings import get_string, get_user_lang, set_user_lang, LANG_NAMES, SUPPORTED_LANGS


@filters.create
def _from_video_link(_, __, message: Message) -> bool:
    return False


@filters.create
def _has_start_param(_, __, message: Message) -> bool:
    return True


@filters.create
def _fj_import_guard(_, __, message: Message) -> bool:
    return True


async def _check_force_join_import(user_id: int, client=None):
    from handlers.forcejoin import _check_force_join
    return await _check_force_join(user_id, client)


async def _fj_join_buttons_import(not_joined: list):
    from handlers.forcejoin import _fj_join_buttons
    return _fj_join_buttons(not_joined)


async def _send_video_import(client: Client, user_id: int):
    from handlers.video import _send_video_to_user
    return await _send_video_to_user(client, user_id)


@filters.create
def _app_import(_, __, message: Message) -> bool:
    return True


from config import app


@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user = message.from_user
    is_new = not await users_col.find_one({"user_id": user.id})
    await save_user(user)
    name         = user.first_name or "Guest"
    bot_username = await get_bot_username(client)

    start_param  = message.command[1] if len(message.command) > 1 else ""
    from_join    = start_param == "joined"
    from_video   = start_param == "video"

    if from_video:
        from handlers.forcejoin import _check_force_join, _fj_join_buttons
        not_joined = await _check_force_join(user.id, client)
        if not_joined:
            _ulang = await get_user_lang(user.id)
            await message.reply_text(
                get_string("force_join_required", lang=_ulang, count=len(not_joined)),
                parse_mode=HTML,
                reply_markup=InlineKeyboardMarkup(_fj_join_buttons(not_joined)),
            )
            return
        from handlers.video import _send_video_to_user
        err = await _send_video_to_user(client, user.id)
        if err:
            await message.reply_text(err)
        return

    if start_param == "buypremium":
        from handlers.premium import buypremium_cmd
        await buypremium_cmd(client, message)
        return

    if start_param == "profile":
        from handlers.user import profile_cmd
        await profile_cmd(client, message)
        return

    mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    uname   = f"@{user.username}" if user.username else "no username"

    # ── Get user language (cached from save_user or MongoDB) ──────────────────
    user_lang = await get_user_lang(user.id)

    # ── One-time Privacy Notice ────────────────────────────────────────────────
    user_doc = await users_col.find_one({"user_id": user.id}) or {}
    if not user_doc.get("is_notified"):
        await message.reply_text(
            get_string("privacy_notice", lang=user_lang),
            parse_mode=HTML,
            disable_notification=True,
        )
        await users_col.update_one(
            {"user_id": user.id},
            {"$set": {"is_notified": True}},
            upsert=True,
        )

    if is_new:
        asyncio.create_task(log_event(client,
            f"👤 <b>New User Started Bot</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔔 User    : {mention}\n"
            f"🆔 ID      : <code>{user.id}</code>\n"
            f"📛 Handle  : {uname}\n"
            f"📋 Name    : {user.first_name} {user.last_name or ''}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
    else:
        asyncio.create_task(log_event(client,
            f"🔄 <b>Existing User Restarted Bot</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔔 User    : {mention}\n"
            f"🆔 ID      : <code>{user.id}</code>\n"
            f"📛 Handle  : {uname}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))

    if is_new and start_param.isdigit():
        ref_id = int(start_param)
        if ref_id != user.id:
            ref_doc = await users_col.find_one({"user_id": ref_id})
            if ref_doc:
                new_points = ref_doc.get("points", 0) + 10
                new_rc     = ref_doc.get("ref_count", 0) + 1
                await users_col.update_one(
                    {"user_id": ref_id},
                    {"$set": {"points": new_points, "ref_count": new_rc}},
                )
                ref_lang = await get_user_lang(ref_id)
                notif    = get_string("referral_notif", lang=ref_lang, pts=10, total=new_points)
                asyncio.create_task(bot_api("sendMessage", {
                    "chat_id":    ref_id,
                    "text":       notif,
                    "parse_mode": "HTML",
                }))
                new_mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
                ref_mention = f'<a href="tg://user?id={ref_id}">{ref_id}</a>'
                asyncio.create_task(log_event(client,
                    f"🔗 <b>Referral Credit</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🆕 New User    : {new_mention} (<code>{user.id}</code>)\n"
                    f"📛 Handle      : @{user.username or 'none'}\n"
                    f"🎯 Referred by : {ref_mention}\n"
                    f"💰 Reward      : +10 pts → Total: <b>{new_points}</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🤖 DESI MLH SYSTEM"
                ))

    if from_join:
        if user.id in pending_welcome_msgs:
            grp_chat_id, grp_msg_id = pending_welcome_msgs.pop(user.id)
            asyncio.create_task(bot_api("deleteMessage", {
                "chat_id":    grp_chat_id,
                "message_id": grp_msg_id,
            }))
        welcome_msg = get_string("welcome_joined", lang=user_lang, name=name)
    else:
        welcome_msg = get_string("welcome_start", lang=user_lang, name=name)

    _share_text = (
        "░▒▓█ 🔥 DIAMOND BOT ACCESS 🔥 █▓▒░\n\n"
        "🎬 Premium commands live now\n\n"
        "💌 Click & Enter\n\n"
        "✨ For true enthusiasts only\n\n"
        f"https://t.me/{bot_username}?start=video"
    )
    _share_url = "https://t.me/share/url?text=" + urllib.parse.quote(_share_text, safe="")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Me To Group",
                              url=f"https://t.me/{bot_username}?startgroup=true")],
        [
            InlineKeyboardButton("👑 VIP Channel",  url="https://t.me/+qFuMDi1eB7AxZGU1"),
            InlineKeyboardButton("📊 My Status",    callback_data="status"),
        ],
        [
            InlineKeyboardButton("💎 Buy Premium ✨", callback_data="open_buypremium"),
            InlineKeyboardButton("📤 Share Bot",     url=_share_url),
        ],
    ])
    await message.reply_text(welcome_msg, parse_mode=HTML, reply_markup=keyboard)


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

    last_daily = (doc or {}).get("last_daily")
    now        = datetime.utcnow()
    lang       = await get_user_lang(user_id)

    if last_daily and (now - last_daily).total_seconds() < 86400:
        rem_secs   = 86400 - int((now - last_daily).total_seconds())
        hrs, r     = divmod(rem_secs, 3600)
        daily_line = get_string("daily_claimed", lang=lang, hrs=hrs, mins=r // 60)
    else:
        daily_line = get_string("daily_available", lang=lang)

    rank      = get_rank(ref_count)
    status    = get_status(points)
    ref_link  = f"https://t.me/{bot_uname}?start={user_id}"

    await cq.edit_message_text(
        get_string(
            "status_profile", lang=lang,
            user_id=user_id, joined=joined_str,
            points=points, refs=ref_count,
            rank=rank, status=status,
            vid_today=vid_count, vid_limit=DAILY_VIDEO_LIMIT,
            daily_line=daily_line, ref_link=ref_link,
        ),
        parse_mode=HTML,
    )
    await cq.answer()


@app.on_message(filters.command("help") & filters.private)
async def help_handler(client: Client, message: Message):
    is_admin  = message.from_user.id in ADMIN_IDS
    user_lang = await get_user_lang(message.from_user.id)
    user_text = get_string("help_user", lang=user_lang)

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
        "/unmute              — 🔊 Unmute a user\n"
        "/ro [duration]       — 👁 Read-only mode\n"
        "/ban [reason]        — 🚫 Ban a user\n"
        "/unban               — ✅ Unban a user\n"
        "/kick [reason]       — 👢 Kick (ban+unban)\n"
        "/warn [reason]       — ⚠️ Warn user (3 = auto-ban)\n"
        "/warns               — 📊 Check warn count\n"
        "/clearwarn           — 🗑️ Clear warnings\n"
        "/del                 — 🗑️ Delete replied message\n"
        "/pin [silent]        — 📌 Pin replied message\n"
        "/unpin               — 📌 Unpin message(s)\n"
        "/report [reason]     — 🚨 Report message to admin\n\n"
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
        "/clearvideos confirm         — 🧹 Wipe entire library\n"
        "/syncvideos                  — 🔄 Backfill file_ids (enables spoiler)\n\n"
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
        "/logchannel status           — Show current channel\n\n"
        "👑 SUPER ADMIN ONLY (private chat):\n"
        "/addadmin [id] [label]       — Add bot admin\n"
        "/removeadmin [id]            — Remove bot admin\n"
        "/admins                      — List all admins\n\n"
        "📊 MONITORING & ACTIVITY:\n"
        "/setmonitorgroup             — Set monitor group\n"
        "/monitorstatus               — Show monitor status\n"
        "/trackchats                  — Track chat activity\n"
        "/groupdm                     — Send DM to group\n"
        "/groupstats                  — Show group stats\n\n"
        "📬 INBOX MANAGEMENT:\n"
        "/setinboxgroup               — Set inbox group\n"
        "/chat [id]                   — Start chat with user\n"
        "/inbox                       — Show inbox\n\n"
        "📋 CLONE MANAGEMENT:\n"
        "/addclone [token]            — Add bot clone\n"
        "/removeclone [id]            — Remove bot clone\n"
        "/clones                      — List all clones\n"
        "/cloneconfig [id]            — Configure clone\n"
        "/setcloneinbox [id]          — Set clone inbox\n"
        "/setclonelog [id]            — Set clone log\n"
        "/setupclone [id]             — Setup clone\n\n"
        "💎 PREMIUM MANAGEMENT:\n"
        "/buypremium [user] [pkg]     — Buy premium for user\n"
        "/mypremium                   — Check my premium\n"
        "/packages                    — List premium packages\n"
        "/premiumlist                 — List premium users\n"
        "/profile [user]              — Show user profile\n"
        "/refreshguard                — Refresh guard\n"
        "/resetcount [user]           — Reset user count\n"
        "/revokepremium [user]        — Revoke premium\n"
        "/setprice [pkg] [price]      — Set package price\n"
        "/upgrade [user] [pkg]        — Upgrade user premium\n\n"
        "🏷️ AUTO TAGGING:\n"
        "/tag [text]                  — Tag users\n"
        "/tagall [text]               — Tag all users\n\n"
        "📅 SCHEDULED TASKS:\n"
        "/schedule                    — View scheduled broadcasts\n\n"
        "📋 GROUPS MANAGEMENT:\n"
        "/groups                      — List managed groups\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )

    if is_admin:
        # Admin text is too long for one message — split at the midpoint section
        part1 = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "📋 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 — ALL COMMANDS (1/2)\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "👤 USER COMMANDS:\n"
            "/start  /video  /daily  /help\n\n"
            "🛡️ GROUP MODERATION (reply to user):\n"
            "/mute [2D/3H/30M]  /unmute  /ro [dur]\n"
            "/ban [reason]  /unban  /kick [reason]\n"
            "/warn  /warns  /clearwarn  /del  /pin  /unpin  /report\n\n"
            "🌙 NIGHT MODE:\n"
            "/nightmode on HH:MM HH:MM  |  /nightmode off  |  status\n\n"
            "🕵️ SHADOW BAN:\n"
            "/shadowban  /unshadowban  /shadowbans  /clearshadowbans\n\n"
            "⚙️ FILTERS:\n"
            "/addfilter [word] [delete|warn|mute|ban]\n"
            "/delfilter  /filters  /clearfilters\n\n"
            "🌊 ANTI-FLOOD:\n"
            "/antiflood on [msgs] [secs] [action]  |  off  |  status\n\n"
            "👋 WELCOME:\n"
            "/welcome set [text]  |  off  |  status\n\n"
            "📜 RULES:\n"
            "/setrules [text]  /rules  /clearrules\n\n"
            "👑 ADMIN (private):\n"
            "/stats  /user [id]  /addpoints  /removepoints\n"
            "/setlimit  /blockuser  /unblockuser\n"
            "/clearhistory  /export  /resetcount\n\n"
            "📹 VIDEO LIBRARY:\n"
            "/listvideos  /delvideo  /clearvideos  /syncvideos\n\n"
            "📢 BROADCAST:\n"
            "/broadcast  /sbc  /cancel\n\n"
            "📡 FORCE-JOIN:\n"
            "/forcejoin on|off|list  /forcejoinadd  /forcebuttondel\n"
            "━━━━━━━━━━━━━━━━━━━"
        )
        part2 = (
            "━━━━━━━━━━━━━━━━━━━\n"
            "📋 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 — ALL COMMANDS (2/2)\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "📝 LOG CHANNEL:\n"
            "/logchannel set [id]  |  off  |  status\n\n"
            "👑 SUPER ADMIN:\n"
            "/addadmin [id]  /removeadmin  /admins\n\n"
            "📊 MONITORING:\n"
            "/setmonitorgroup  /monitorstatus  /trackchats\n"
            "/groupdm  /groupstats  /overview [period]\n\n"
            "📬 INBOX:\n"
            "/setinboxgroup  /chat [id]  /inbox\n\n"
            "📋 CLONE MANAGEMENT:\n"
            "/addclone  /removeclone  /clones  /cloneconfig\n"
            "/setcloneinbox  /setclonelog  /setupclone\n\n"
            "💎 PREMIUM:\n"
            "/packages  /setprice  /buypremium  /mypremium\n"
            "/premiumlist  /profile  /revokepremium  /upgrade\n\n"
            "🏷️ AUTO TAG:\n"
            "/tag [text]  /tagall [text]  /taggroup [gid] [msg]\n\n"
            "📅 SCHEDULE:\n"
            "/schedule\n\n"
            "📋 GROUPS:\n"
            "/groups  /groupstats  /ctrlhelp  /syscheck\n"
            "/sendall  /sendto  /protect  /protections\n"
            "/kw add|del|list|clear  — Keyword reply\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        await message.reply_text(part1)
        await message.reply_text(part2)
    else:
        await message.reply_text(user_text)


@app.on_message(filters.command("daily") & filters.private)
async def daily_handler(client: Client, message: Message):
    from datetime import timedelta
    user_id = message.from_user.id
    now     = datetime.utcnow()
    lang    = await get_user_lang(user_id)

    doc = await users_col.find_one({"user_id": user_id})

    if (doc or {}).get("bot_banned"):
        await message.reply_text(get_string("video_banned", lang=lang), parse_mode=HTML)
        return

    last_daily = (doc or {}).get("last_daily")

    if last_daily and (now - last_daily).total_seconds() < 86400:
        remaining = timedelta(seconds=86400) - (now - last_daily)
        hrs, rem  = divmod(int(remaining.total_seconds()), 3600)
        mins      = rem // 60
        await message.reply_text(
            get_string("daily_already_claimed", lang=lang, hrs=hrs, mins=mins),
            parse_mode=HTML,
        )
        return

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
        get_string("daily_success", lang=lang, total=new_points, rank=rank, status=status),
        parse_mode=HTML,
    )
    print(f"[DAILY] user={user_id} claimed +5 pts → total={new_points}")


# ── /lang — User Language Selection ──────────────────────────────────────────

@app.on_message(filters.command("lang") & filters.private)
async def lang_handler(client: Client, message: Message):
    """Show language selection menu."""
    user_id  = message.from_user.id
    cur_lang = await get_user_lang(user_id)
    cur_name = LANG_NAMES.get(cur_lang, cur_lang)

    text = get_string("lang_current", lang=cur_lang, lang_name=cur_name)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("English 🇬🇧",  callback_data="setlang:en"),
            InlineKeyboardButton("বাংলা 🇧🇩",    callback_data="setlang:bn"),
            InlineKeyboardButton("العربية 🇸🇦",  callback_data="setlang:ar"),
        ]
    ])
    await message.reply_text(text, parse_mode=HTML, reply_markup=keyboard)


@app.on_callback_query(filters.regex(r"^setlang:(en|bn|ar)$"))
async def setlang_callback(client: Client, cq: CallbackQuery):
    """Handle language button selection."""
    user_id  = cq.from_user.id
    new_lang = cq.data.split(":")[1]

    success = await set_user_lang(user_id, new_lang)
    if success:
        confirm = get_string("lang_set_success", lang=new_lang)
        await cq.edit_message_text(confirm, parse_mode=HTML)
    else:
        await cq.answer("❌ Invalid language.", show_alert=True)
    await cq.answer()
