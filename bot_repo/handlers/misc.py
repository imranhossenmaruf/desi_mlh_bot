import asyncio
import random
import urllib.parse
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery

from config import (
    HTML, ADMIN_ID, REPLIES,
    broadcast_sessions, fj_sessions,
    scheduled_col, settings_col, users_col, group_settings_col,
    STATE_CONTENT, STATE_BUTTONS, STATE_JOIN_DATE, STATE_CUSTOMIZE, STATE_SCHEDULE,
    app,
)
from helpers import (
    parse_date, parse_buttons, has_media, refresh_preview,
    log_event, send_to_user, bot_api, delete_msg_safe, get_bot_username,
    admin_filter, send_to_monitor,
)


@app.on_chat_join_request()
async def join_request_handler(client: Client, request):
    user       = request.from_user
    chat       = request.chat
    user_id    = user.id
    chat_id    = chat.id
    first_name = user.first_name or "User"
    group_name = chat.title or "the group"
    bot_uname  = await get_bot_username(client)

    # Check if auto approve is enabled for this group
    doc = await group_settings_col.find_one({"chat_id": chat_id})
    auto_approve = doc.get("auto_approve", True) if doc else True  # Default to True for backward compatibility

    if not auto_approve:
        print(f"[JOIN] Auto approve disabled for {chat_id}, skipping")
        return

    print(f"[JOIN] Request from user_id={user_id} ({first_name}) in chat_id={chat_id} ({group_name})")

    # Auto-approve এ random delay যোগ করা হয়েছে flood limit এড়াতে
    await asyncio.sleep(random.randint(3, 8))

    approve_result = await bot_api("approveChatJoinRequest", {
        "chat_id": chat_id,
        "user_id": user_id,
    })
    print(f"[JOIN] HTTP approve → {approve_result}")

    if not approve_result.get("ok"):
        try:
            await client.approve_chat_join_request(chat_id, user_id)
            print(f"[JOIN] Pyrogram fallback approve OK for {user_id}")
        except Exception as e:
            print(f"[JOIN] All approve methods failed: {e}")
            return

    # Send confirmation to log chat if configured
    if doc and doc.get("approve_log_chat"):
        log_chat = doc["approve_log_chat"]
        try:
            await client.send_message(
                log_chat,
                f"✅ <b>Join Request Approved</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 User: <a href='tg://user?id={user_id}'>{first_name}</a>\n"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"📌 Group: {group_name}\n"
                f"🕛 Time: {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}",
                parse_mode=HTML
            )
        except Exception as e:
            print(f"[JOIN] Failed to send log: {e}")

    # ── Always send a rich Welcome DM ────────────────────────────────────────
    try:
        dm_markup = {
            "inline_keyboard": [
                [
                    {"text": "🎬 ভিডিও পান",   "url": f"https://t.me/{bot_uname}?start=video"},
                    {"text": "💰 পয়েন্ট চেক", "url": f"https://t.me/{bot_uname}?start=points"},
                ],
                [
                    {"text": "👥 রেফার করুন",  "url": f"https://t.me/{bot_uname}?start=refer"},
                    {"text": "📋 সাহায্য",      "url": f"https://t.me/{bot_uname}?start=help"},
                ],
            ]
        }
        dm_text = (
            f"🎉 <b>স্বাগতম! আপনার Join Request অনুমোদিত হয়েছে।</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 গ্রুপ: <b>{group_name}</b>\n\n"
            f"🤖 <b>বট ব্যবহার করুন:</b>\n"
            f"  🎬 /video  — র‌্যান্ডম ভিডিও পান\n"
            f"  💰 /points — আপনার পয়েন্ট দেখুন\n"
            f"  📋 /help   — সব কমান্ড\n\n"
            f"🔗 <b>আপনার Referral Link:</b>\n"
            f"<code>https://t.me/{bot_uname}?start={user_id}</code>\n\n"
            f"💡 বন্ধুদের Invite করুন — প্রতি Invite-এ <b>+১০ পয়েন্ট!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        await bot_api("sendMessage", {
            "chat_id":      user_id,
            "text":         dm_text,
            "parse_mode":   "HTML",
            "reply_markup": dm_markup,
        })
        print(f"[JOIN] Welcome DM sent to {user_id}")
    except Exception as e:
        print(f"[JOIN] Welcome DM failed: {e}")

    # ── Monitor Group: log join approval ─────────────────────────────────────
    try:
        await send_to_monitor(
            client,
            f"✅ <b>Join Approved</b>\n"
            f"👤 <a href='tg://user?id={user_id}'>{first_name}</a> (<code>{user_id}</code>)\n"
            f"📌 গ্রুপ: <b>{group_name}</b> (<code>{chat_id}</code>)\n"
            f"🕛 {datetime.utcnow().strftime('%d %b %H:%M UTC')}",
        )
    except Exception as e:
        print(f"[JOIN] Monitor send failed: {e}")

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

    full_name = f"{first_name} {user.last_name or ''}".strip()
    uname_tag = f"@{user.username}" if user.username else "No username"
    ref_link  = f"https://t.me/{bot_uname}?start={user_id}"

    if ref_count >= 10:
        status = "VIP ⭐"
    elif ref_count >= 3:
        status = "Active 🔥"
    else:
        status = "Member 👤"

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
        from config import pending_welcome_msgs
        pending_welcome_msgs[user_id] = (chat_id, msg_id)

        async def _auto_delete(uid: int, cid: int, mid: int):
            await asyncio.sleep(300)
            await bot_api("deleteMessage", {"chat_id": cid, "message_id": mid})
            pending_welcome_msgs.pop(uid, None)

        asyncio.create_task(_auto_delete(user_id, chat_id, msg_id))


@app.on_callback_query(filters.regex(r"^grp_(pts|earn)_(\d+)$"))
async def grp_btn_callback(client: Client, cq: CallbackQuery):
    import re
    m         = re.match(r"^grp_(pts|earn)_(\d+)$", cq.data)
    action    = m.group(1)
    target_id = int(m.group(2))
    caller_id = cq.from_user.id

    if caller_id != target_id:
        await cq.answer("❌ These buttons are only for the welcomed member.", show_alert=True)
        return

    bot_uname = await get_bot_username(client)
    doc       = await users_col.find_one({"user_id": caller_id})
    points    = doc.get("points",    0) if doc else 0
    ref_count = doc.get("ref_count", 0) if doc else 0
    ref_link  = f"https://t.me/{bot_uname}?start={caller_id}"

    from config import pending_welcome_msgs
    from helpers import get_rank, get_status
    if caller_id in pending_welcome_msgs:
        grp_chat_id, grp_msg_id = pending_welcome_msgs.pop(caller_id)
        asyncio.create_task(bot_api("deleteMessage", {
            "chat_id": grp_chat_id, "message_id": grp_msg_id
        }))

    rank     = get_rank(ref_count)
    status_s = get_status(points)
    doc2     = await users_col.find_one({"user_id": caller_id})
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
            f"✨ STATUS: {status_s}\n"
            "(Collect more points to upgrade your status!)\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Invite friends to grow your balance!\n"
            f"🔗 {ref_link}\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        alert_text = f"💰 Points: {points}  |  🏅 Rank: {rank}  |  👥 Refs: {ref_count}"
    else:
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

    share_text = urllib.parse.quote(
        "🎬 Join DESI MLH Video Community using my referral link and earn bonus points!"
    )
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={share_text}"
    dm_markup = {
        "inline_keyboard": [[
            {"text": "📤 Share Your Referral Link", "url": share_url}
        ]]
    }

    dm_ok = await bot_api("sendMessage", {
        "chat_id":      caller_id,
        "text":         dm_text,
        "reply_markup": dm_markup,
    })
    if dm_ok.get("ok"):
        await cq.answer("✅ Check your DM from the bot!", show_alert=False)
    else:
        await cq.answer(alert_text, show_alert=True)


@app.on_message(
    admin_filter & filters.private
    & ~filters.command([
        "start", "help", "stats", "user", "addpoints", "removepoints",
        "setlimit", "export", "broadcast", "sbc", "cancel", "blockuser",
        "unblockuser", "daily", "video", "listvideos", "delvideo", "clearvideos",
        "forcejoin", "forcejoinadd", "forcebuttondel", "clearhistory",
        "logchannel", "schedule", "setinboxgroup", "setcontrolgroup", "syncvideos", "nightmode",
        "shadowban", "unshadowban", "shadowbans", "clearshadowbans",
        "addfilter", "delfilter", "filters", "clearfilters",
        "antiflood", "welcome", "setrules", "rules", "clearrules",
        "mute", "unmute", "ban", "unban", "warn", "clearwarn", "warns",
        "kick", "del", "ro", "pin", "unpin", "report",
        "chat", "inbox", "groups", "resetcount",
        "addadmin", "removeadmin", "admins",
        "addclone", "removeclone", "clones",
        "cloneconfig", "setvideochannel", "setcloneinbox", "setclonelog", "setupclone",
        "setmonitorgroup", "monitorstatus", "trackchats", "groupstats", "overview", "checkgroups",
        "packages", "setprice", "groupdm",
    ])
)
async def admin_message_handler(client: Client, message: Message):
    uid     = message.from_user.id
    session = broadcast_sessions.get(uid)
    fj      = fj_sessions.get(uid)
    text_in = (message.text or "").strip()
    print(f"[ADMIN_MSG] photo={bool(message.photo)} video={bool(message.video)} "
          f"doc={bool(message.document)} text={bool(message.text)} "
          f"caption={bool(message.caption)} "
          f"sess_state={session.get('state') if session else 'NONE'} fj={bool(fj)}")

    if fj:
        state = fj.get("state")

        if state == "fj_wait_btn":
            if text_in.lower() == "/cancel":
                fj_sessions.pop(uid, None)
                await message.reply_text("🚫 Cancelled.")
                return

            raw_entries = [e.strip() for e in text_in.split("&&")]
            parsed = []
            from handlers.forcejoin import _fj_parse_entry, _fj_extract_chat_id
            for entry in raw_entries:
                r = _fj_parse_entry(entry)
                if r:
                    parsed.append(r)

            if not parsed:
                await message.reply_text(
                    "❌ Could not parse. Use this format:\n\n"
                    "<code>Name | https://t.me/link</code>\n\n"
                    "To skip the ID step, add it as 3rd part:\n"
                    "<code>Name | https://t.me/+xxx | -1001234567890</code>\n\n"
                    "Multiple at once:\n"
                    "<code>Ch1 | link1 && Ch2 | link2</code>",
                    parse_mode=HTML,
                )
                return

            # ── Separate: already has a numeric/username ID vs still needs ID ──
            def _is_resolved(ch):
                cid = str(ch.get("chat_id", ""))
                # Numeric ID (e.g. -1001234567890)
                if cid.lstrip("-").isdigit():
                    return True
                # @username
                if cid.startswith("@") and len(cid) > 1:
                    return True
                return False

            resolved   = [c for c in parsed if _is_resolved(c)]
            unresolved = [c for c in parsed if not _is_resolved(c)]

            for ch in resolved:
                fj["pending_channels"].append(ch)

            if unresolved:
                fj["unresolved_channels"] = unresolved
                fj["fwd_index"] = 0
                fj["state"]     = "fj_wait_fwd"
                ch0 = unresolved[0]
                await message.reply_text(
                    f"🔒 <b>Step 2 — Set Chat ID</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📢 Name : <b>{ch0['name']}</b>\n"
                    f"🔗 Link : {ch0['link']}\n\n"
                    "Please provide the <b>chat ID</b> using <b>one of these methods</b>:\n\n"
                    "1️⃣ <b>Forward</b> any message from that channel/group\n"
                    "2️⃣ <b>Type</b> the numeric ID directly:\n"
                    "   <code>-1001234567890</code>\n\n"
                    "💡 To find the ID: add @userinfobot to the channel/group\n"
                    "   and it will show the numeric ID.\n\n"
                    "Type /cancel to abort.",
                    parse_mode=HTML,
                )
                return

            from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            fj["state"] = "fj_add_confirm"
            pending = fj["pending_channels"]
            lines   = "\n".join(f"  {i+1}. {c['name']}  🆔 <code>{c.get('chat_id','?')}</code>"
                                for i, c in enumerate(pending))
            await message.reply_text(
                f"✅ <b>Confirm Adding {len(pending)} Entry(s)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{lines}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Tap <b>Confirm</b> to save or <b>Cancel</b> to abort.",
                parse_mode=HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Confirm", callback_data="fj_confirm"),
                    InlineKeyboardButton("❌ Cancel",  callback_data="fj_cancel"),
                ]]),
            )
            return

        if state == "fj_wait_fwd":
            from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            if text_in.lower() == "/cancel":
                fj_sessions.pop(uid, None)
                await message.reply_text("🚫 Cancelled.")
                return

            unresolved = fj.get("unresolved_channels", [])
            idx        = fj.get("fwd_index", 0)
            if idx >= len(unresolved):
                fj["state"] = "fj_add_confirm"
                return

            ch = unresolved[idx]

            # ── Method 1: forwarded message ─────────────────────────────────
            fwd_chat = getattr(message, "forward_from_chat", None)
            if fwd_chat:
                resolved_id = str(fwd_chat.id)

            # ── Method 2: typed numeric ID or @username ──────────────────────
            elif text_in and (text_in.lstrip("-").isdigit() or text_in.startswith("@")):
                resolved_id = text_in.strip()

            else:
                # Still no valid ID — re-prompt
                await message.reply_text(
                    f"⚠️ Could not detect the chat ID for <b>{ch['name']}</b>.\n\n"
                    "Please use <b>one of these methods</b>:\n"
                    "1️⃣ <b>Forward</b> any message from that channel/group\n"
                    "2️⃣ <b>Type</b> the numeric ID:  <code>-1001234567890</code>\n\n"
                    "Type /cancel to abort.",
                    parse_mode=HTML,
                )
                return

            # ── Got the ID — save and advance ───────────────────────────────
            ch["chat_id"] = resolved_id
            fj["pending_channels"].append(ch)
            fj["fwd_index"] = idx + 1

            if fj["fwd_index"] < len(unresolved):
                next_ch = unresolved[fj["fwd_index"]]
                await message.reply_text(
                    f"✅ Got it! <b>{ch['name']}</b> → ID: <code>{resolved_id}</code>\n\n"
                    f"📢 Next: <b>{next_ch['name']}</b>\n"
                    f"🔗 {next_ch['link']}\n\n"
                    "Send the chat ID:\n"
                    "1️⃣ <b>Forward</b> a message from that channel/group\n"
                    "2️⃣ <b>Type</b> the ID:  <code>-1001234567890</code>",
                    parse_mode=HTML,
                )
                return

            fj["state"] = "fj_add_confirm"
            pending     = fj["pending_channels"]
            lines       = "\n".join(f"  {i+1}. {c['name']}  🆔 <code>{c.get('chat_id','?')}</code>"
                                    for i, c in enumerate(pending))
            await message.reply_text(
                f"✅ <b>Confirm Adding {len(pending)} Entry(s)</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{lines}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Tap <b>Confirm</b> to save, or <b>Cancel</b> to abort.",
                parse_mode=HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Confirm", callback_data="fj_confirm"),
                    InlineKeyboardButton("❌ Cancel",  callback_data="fj_cancel"),
                ]]),
            )
            return

    if not session:
        return

    state = session.get("state")

    if state == STATE_JOIN_DATE:
        dt = parse_date(text_in)
        if not dt:
            await message.reply_text(
                "❌ Invalid date. Please use:\n"
                "<code>DD.MM.YYYY HH:MM</code>  or  <code>MM/DD/YYYY HH:MM</code>",
                parse_mode=HTML,
            )
            return

        from helpers import kb_customize
        session["audience"]   = "join_after"
        session["join_after"] = dt
        session["state"]      = STATE_CONTENT
        await message.reply_text(
            f"✅ Filter set: joined after <b>{dt.strftime('%d %b %Y  %H:%M')}</b>\n\n"
            "Now send the message you want to broadcast.\n"
            "Type /cancel to cancel.",
            parse_mode=HTML,
        )
        return

    if state == STATE_CONTENT:
        print(f"[STATE_CONTENT] has_media={has_media(message)}")
        try:
            if has_media(message):
                session["msg_type"]      = "media"
                session["media_chat_id"] = message.chat.id
                session["media_msg_id"]  = message.id
                if message.caption:
                    session["text"]     = message.caption
                    session["entities"] = message.caption_entities or []
                # Extract file_id and media_kind for reliable delivery
                if message.photo:
                    session["file_id"]    = message.photo.file_id
                    session["media_kind"] = "photo"
                elif message.video:
                    session["file_id"]    = message.video.file_id
                    session["media_kind"] = "video"
                elif message.animation:
                    session["file_id"]    = message.animation.file_id
                    session["media_kind"] = "animation"
                elif message.document:
                    session["file_id"]    = message.document.file_id
                    session["media_kind"] = "document"
                elif message.audio:
                    session["file_id"]    = message.audio.file_id
                    session["media_kind"] = "audio"
                elif message.voice:
                    session["file_id"]    = message.voice.file_id
                    session["media_kind"] = "voice"
                elif message.sticker:
                    session["file_id"]    = message.sticker.file_id
                    session["media_kind"] = "sticker"
                elif message.video_note:
                    session["file_id"]    = message.video_note.file_id
                    session["media_kind"] = "video_note"
                print(f"[STATE_CONTENT] fid={session.get('file_id','NONE')[:20] if session.get('file_id') else 'NONE'} kind={session.get('media_kind')}")
                session["state"] = STATE_CUSTOMIZE
            else:
                session["msg_type"] = "text"
                session["text"]     = message.text or ""
                session["entities"] = message.entities or []
                session["state"]    = STATE_CUSTOMIZE

            from helpers import kb_customize
            print("[STATE_CONTENT] calling refresh_preview...")
            await refresh_preview(client, session)
            print("[STATE_CONTENT] refresh_preview done")
        except Exception as exc:
            import traceback
            print(f"[STATE_CONTENT] EXCEPTION: {exc}")
            traceback.print_exc()
            try:
                await message.reply_text(f"⚠️ Error: <code>{exc}</code>", parse_mode="html")
            except Exception:
                pass
        return

    if state == STATE_BUTTONS:
        buttons = parse_buttons(text_in)
        if not buttons:
            await message.reply_text(
                "❌ Couldn't parse buttons.\n"
                "Format: <code>Name | https://link.com</code>",
                parse_mode=HTML,
            )
            return
        # Auto-pair: flatten all buttons then group 2 per row
        flat = [b for row in buttons for b in row]
        buttons = [flat[i:i+2] for i in range(0, len(flat), 2)]
        session["extra_buttons"] = buttons
        session["state"]         = STATE_CUSTOMIZE
        await refresh_preview(client, session)
        return

    if state == STATE_SCHEDULE:
        from datetime import timezone
        BST_OFFSET  = timedelta(hours=6)
        dt_bst      = parse_date(text_in)
        if not dt_bst:
            await message.reply_text(
                "❌ Invalid date. Use:\n<code>DD.MM.YYYY HH:MM</code>",
                parse_mode=HTML,
            )
            return
        dt_utc = dt_bst - BST_OFFSET

        if dt_utc <= datetime.utcnow():
            await message.reply_text("❌ Scheduled time must be in the future.")
            return

        await scheduled_col.insert_one({
            "scheduled_at":  dt_utc,
            "audience":      session.get("audience", "all"),
            "join_after":    session.get("join_after"),
            "msg_type":      session.get("msg_type"),
            "text":          session.get("text"),
            "entities_raw":  [e.__class__.__name__ for e in (session.get("entities") or [])],
            "media_chat_id": session.get("media_chat_id"),
            "media_msg_id":  session.get("media_msg_id"),
            "file_id":       session.get("file_id"),
            "media_kind":    session.get("media_kind"),
            "extra_buttons": session.get("extra_buttons"),
        })
        broadcast_sessions.pop(uid, None)
        await delete_msg_safe(client, session["chat_id"], session.get("preview_msg_id"))
        await message.reply_text(
            "⏰ <b>Broadcast Scheduled</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🗓 Date: <b>{dt_bst.strftime('%d %b %Y  %H:%M')} BST</b>\n"
            f"👥 Audience: {session.get('audience', 'all')}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        return


@app.on_message(filters.incoming & filters.private & ~admin_filter)
async def text_handler(client: Client, message: Message):
    if not message.text:
        return
    text = message.text.strip().lower()
    if text in REPLIES:
        await message.reply_text(REPLIES[text])


async def schedule_loop(client: Client):
    print("[SCHEDULE] Loop started.")
    while True:
        try:
            await _run_scheduled(client)
        except Exception as e:
            print(f"[SCHEDULE] Loop error: {e}")
        await asyncio.sleep(30)


async def _run_scheduled(client: Client):
    now  = datetime.utcnow()
    docs = await scheduled_col.find({"scheduled_at": {"$lte": now}}).to_list(length=None)
    for doc in docs:
        try:
            await scheduled_col.delete_one({"_id": doc["_id"]})
            from config import app as _app
            admin_chat = ADMIN_ID
            status_msg = await _app.send_message(
                admin_chat,
                "📡 <b>Running scheduled broadcast...</b>",
                parse_mode=HTML,
            )
            session = {
                "audience":      doc.get("audience", "all"),
                "join_after":    doc.get("join_after"),
                "msg_type":      doc.get("msg_type"),
                "text":          doc.get("text"),
                "entities":      [],
                "media_chat_id": doc.get("media_chat_id"),
                "media_msg_id":  doc.get("media_msg_id"),
                "file_id":       doc.get("file_id"),
                "media_kind":    doc.get("media_kind"),
                "extra_buttons": doc.get("extra_buttons"),
                "chat_id":       admin_chat,
            }
            from helpers import do_broadcast
            asyncio.create_task(do_broadcast(client, session, status_msg))
            print(f"[SCHEDULE] Fired broadcast doc={doc['_id']}")
        except Exception as e:
            print(f"[SCHEDULE] Failed to run doc={doc.get('_id')}: {e}")


@app.on_message(filters.command("schedule") & admin_filter & filters.private)
async def schedule_cmd(client: Client, message: Message):
    docs = await scheduled_col.find({}).to_list(length=None)
    if not docs:
        await message.reply_text("📭 No scheduled broadcasts.")
        return
    lines = ["⏰ <b>Scheduled Broadcasts</b>"]
    for i, d in enumerate(docs, 1):
        at = d.get("scheduled_at")
        from datetime import timezone, timedelta
        if at:
            bst_time = (at + timedelta(hours=6)).strftime("%d %b %Y  %H:%M")
            lines.append(f"  {i}. {bst_time} BST — audience: {d.get('audience', 'all')}")
        else:
            lines.append(f"  {i}. (no time set)")
    await message.reply_text("\n".join(lines), parse_mode=HTML)


@app.on_message(filters.command("logchannel") & admin_filter & filters.private)
async def logchannel_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        doc = await settings_col.find_one({"key": "log_channel"})
        if doc:
            await message.reply_text(
                f"📝 Log channel: <code>{doc.get('chat_id')}</code>",
                parse_mode=HTML,
            )
        else:
            await message.reply_text("📝 No log channel set.")
        return

    sub = args[0].lower()

    if sub == "off":
        await settings_col.delete_one({"key": "log_channel"})
        await message.reply_text("❌ Log channel removed.")
        return

    if sub == "status":
        doc = await settings_col.find_one({"key": "log_channel"})
        if doc:
            await message.reply_text(
                f"📝 Log channel: <code>{doc.get('chat_id')}</code>",
                parse_mode=HTML,
            )
        else:
            await message.reply_text("📝 No log channel set.")
        return

    if sub == "set" and len(args) >= 2:
        raw = args[1]
        cid = int(raw) if raw.lstrip("-").isdigit() else raw
        await settings_col.update_one(
            {"key": "log_channel"},
            {"$set": {"key": "log_channel", "chat_id": cid}},
            upsert=True,
        )
        await message.reply_text(
            f"✅ Log channel set to <code>{cid}</code>.",
            parse_mode=HTML,
        )
        await log_event(client, "🔧 Log channel configured.")
        return

    await message.reply_text(
        "Usage:\n"
        "/logchannel set -100123456789\n"
        "/logchannel off\n"
        "/logchannel status"
    )
