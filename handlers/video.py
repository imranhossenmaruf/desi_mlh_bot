import asyncio
import random
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    HTML, ADMIN_ID, DAILY_VIDEO_LIMIT, VIDEO_CHANNEL, VIDEO_REPEAT_DAYS,
    users_col, videos_col, vid_hist_col, premium_col, del_queue_col,
    app,
)
from helpers import get_bot_username, log_event, bot_api, _bot_token_ctx, BOT_TOKEN, get_cfg, admin_filter, _clone_config_ctx


# ── Channel promotional button ────────────────────────────────────────────────
# Bot API format (dict) — used in sendVideo / copyMessage calls
_COUPLE_BTN_API = {
    "inline_keyboard": [[
        {"text": "🔵✓⃝𝐂𝐎𝐔𝐏𝐋𝐄✓⃝🔵", "url": "https://t.me/+Rbj5D5wXoB4yMjE1"}
    ]]
}
# Pyrogram format — used in send_message / reply_text calls
_COUPLE_BTN_PY = InlineKeyboardMarkup([[
    InlineKeyboardButton("🔵✓⃝𝐂𝐎𝐔𝐏𝐋𝐄✓⃝🔵", url="https://t.me/+Rbj5D5wXoB4yMjE1")
]])


def _get_video_channel(client) -> int:
    """Return the video channel for this client (clone or main)."""
    cfg = getattr(client, "_clone_config", None) or _clone_config_ctx.get()
    if cfg and cfg.get("video_channel"):
        return cfg["video_channel"]
    return VIDEO_CHANNEL


def _get_bot_token(client) -> str:
    """Return the bot token for this client (clone or main)."""
    return getattr(client, "_bot_token", None) or _bot_token_ctx.get() or BOT_TOKEN


def _get_admin_id(client) -> int:
    """Return admin ID for this client (clone admin or main super-admin)."""
    cfg = getattr(client, "_clone_config", None) or _clone_config_ctx.get()
    if cfg and cfg.get("admin_id"):
        return int(cfg["admin_id"])
    return ADMIN_ID


async def _bot_api_for(client, method: str, params: dict) -> dict:
    """Call Telegram Bot API using this client's token."""
    import aiohttp
    token = _get_bot_token(client)
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=params) as resp:
            return await resp.json()


async def _get_fresh_file_id(client, video_channel: int, msg_id: int) -> str | None:
    """Forward a video from the channel to the admin to obtain this bot's own
    file_id reference, then immediately delete the temp forward.

    Each Telegram bot has its own opaque file_id namespace. A file_id obtained
    by the main bot cannot be used by a clone bot and vice versa.  This helper
    makes the clone bot forward the video to itself (via the admin chat) so it
    gets a valid file_id it can reuse for sendVideo + has_spoiler.
    """
    admin_id = _get_admin_id(client)
    try:
        fwd = await _bot_api_for(client, "forwardMessage", {
            "chat_id":      admin_id,
            "from_chat_id": video_channel,
            "message_id":   msg_id,
        })
        if not fwd.get("ok"):
            print(f"[VIDEO] fresh_file_id forward failed: {fwd.get('description')}")
            return None
        result  = fwd.get("result", {})
        video   = result.get("video")
        fwd_mid = result.get("message_id")
        fresh   = (video or {}).get("file_id")
        # Clean up the temp forward immediately
        if fwd_mid:
            await _bot_api_for(client, "deleteMessage", {
                "chat_id":    admin_id,
                "message_id": fwd_mid,
            })
        return fresh
    except Exception as e:
        print(f"[VIDEO] _get_fresh_file_id error: {e}")
        return None


async def _send_video_to_user(client: Client, user_id: int) -> str:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    video_channel = _get_video_channel(client)

    doc = await users_col.find_one({"user_id": user_id})
    if (doc or {}).get("bot_banned"):
        return (
            "🚫 ACCESS RESTRICTED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your access to this bot has been\n"
            "suspended by the admin.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )

    vid_date  = (doc or {}).get("video_date", "")
    vid_count = (doc or {}).get("video_count", 0) if vid_date == today else 0

    from datetime import timezone
    prem_doc = await premium_col.find_one({"user_id": user_id})
    if prem_doc:
        expires_at = prem_doc.get("expires_at")
        if expires_at and expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            raw_limit = prem_doc.get("video_limit", DAILY_VIDEO_LIMIT)
        else:
            await premium_col.delete_one({"user_id": user_id})
            raw_limit = (doc or {}).get("video_limit")
    else:
        raw_limit = (doc or {}).get("video_limit")
    is_unlimited   = (raw_limit == -1 or (isinstance(raw_limit, int) and raw_limit >= 999))
    effective_limit = (
        None
        if is_unlimited
        else (raw_limit if isinstance(raw_limit, int) and raw_limit > 0
              else DAILY_VIDEO_LIMIT)
    )

    if effective_limit is not None and vid_count >= effective_limit:
        midnight  = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        remaining = midnight + timedelta(days=1) - datetime.utcnow()
        hrs, rem  = divmod(int(remaining.total_seconds()), 3600)
        mins      = rem // 60
        return (
            "⚠️ DAILY LIMIT REACHED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📹 You have used all {effective_limit} video requests for today.\n\n"
            f"🔄 Resets in: {hrs}h {mins}m\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )

    cutoff    = datetime.utcnow() - timedelta(days=VIDEO_REPEAT_DAYS)
    seen_docs = vid_hist_col.find({"user_id": user_id, "sent_at": {"$gte": cutoff}})
    seen_ids  = {d["message_id"] async for d in seen_docs}

    # Filter by this bot's video channel (clone isolation)
    all_docs  = await videos_col.find({"channel_id": video_channel}).to_list(length=None)
    if not all_docs:
        # Fallback: docs without channel_id (legacy/main bot)
        all_docs  = await videos_col.find({}).to_list(length=None)
    pool      = [d for d in all_docs if d["message_id"] not in seen_ids]

    if not all_docs:
        return (
            "📭 NO VIDEOS AVAILABLE\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "There are no videos in the library yet.\n\n"
            "📩 Please contact the admin to add videos.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )

    if not pool:
        return (
            "🎬 YOU'VE WATCHED EVERYTHING!\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "You have already watched all available\n"
            "videos within the last 7 days. 🙌\n\n"
            "🔄 New videos will be available soon.\n"
            "Try again later or contact the admin.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )

    chosen    = random.choice(pool)
    msg_id    = chosen["message_id"]
    file_id   = chosen.get("file_id")
    used   = vid_count + 1
    if is_unlimited:
        usage_line = f"📹 Today: {used}  |  Limit: ♾️ Unlimited"
    else:
        left       = effective_limit - used
        usage_line = f"📹 Today: {used}/{effective_limit}  |  Remaining: {left}"

    try:
        caption = (
            "🎬 DESI MLH Video\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{usage_line}\n"
            "⏳ This video deletes in 25 minutes.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )

        # ═══════════════════════════════════════════════════════════════════════
        # SPOILER DELIVERY STRATEGY
        # ───────────────────────────────────────────────────────────────────────
        # has_spoiler=True ONLY works reliably with sendVideo when the bot owns
        # the file_id it's sending.  copyMessage ignores has_spoiler in practice.
        #
        # Each Telegram bot has its own file_id namespace.  A file_id stored by
        # the main bot is INVALID for a clone bot (and vice-versa).
        #
        # Flow:
        #  1. sendVideo with stored file_id  → fast path (own file_id → spoiler ✓)
        #  2. If that fails (cross-bot file_id) → forward from channel to admin,
        #     extract this bot's own file_id, delete temp, retry sendVideo  → spoiler ✓
        #  3. If forward fails (bot not in channel) → copyMessage  → no spoiler ⚠️
        #  4. Anything not found in channel → remove from DB
        # ═══════════════════════════════════════════════════════════════════════

        async def _send_with_file_id(fid: str) -> dict:
            return await _bot_api_for(client, "sendVideo", {
                "chat_id":            user_id,
                "video":              fid,
                "caption":            caption,
                "parse_mode":         "HTML",
                "has_spoiler":        True,
                "protect_content":    True,
                "supports_streaming": True,
                "reply_markup":       _COUPLE_BTN_API,
            })

        resp = None

        # ── Step 1: try stored file_id ──────────────────────────────────────
        if file_id:
            resp = await _send_with_file_id(file_id)
            if not resp.get("ok"):
                fid_err = resp.get("description", "")
                print(f"[VIDEO] sendVideo(stored file_id) failed: {fid_err}")
                resp = None   # signal: try next step

        # ── Step 2: get this bot's own file_id (if step 1 failed / no file_id)
        if resp is None:
            print(f"[VIDEO] Getting fresh file_id for msg={msg_id} (clone bot or stale id)")
            fresh = await _get_fresh_file_id(client, video_channel, msg_id)
            if fresh:
                resp = await _send_with_file_id(fresh)
                if resp.get("ok"):
                    # Cache it — next delivery will be fast
                    await videos_col.update_one(
                        {"message_id": msg_id}, {"$set": {"file_id": fresh}}
                    )
                    print(f"[VIDEO] Saved fresh file_id for msg={msg_id}")
                else:
                    fresh_err = resp.get("description", "")
                    print(f"[VIDEO] sendVideo(fresh file_id) failed: {fresh_err}")
                    resp = None

        # ── Step 3: last resort — copyMessage (no guaranteed spoiler) ────────
        if resp is None:
            print(f"[VIDEO] Falling back to copyMessage for msg={msg_id}")
            resp = await _bot_api_for(client, "copyMessage", {
                "chat_id":         user_id,
                "from_chat_id":    video_channel,
                "message_id":      msg_id,
                "caption":         caption,
                "parse_mode":      "HTML",
                "has_spoiler":     True,
                "protect_content": True,
                "reply_markup":    _COUPLE_BTN_API,
            })

        if not resp.get("ok"):
            final_err = resp.get("description", "")
            print(f"[VIDEO] All delivery methods failed for msg={msg_id}: {final_err}")
            if any(kw in final_err.lower() for kw in ("not found", "invalid", "message_id_invalid")):
                await videos_col.delete_one({"message_id": msg_id})
                print(f"[VIDEO] msg={msg_id} not found in channel, removed from DB")
            # Clean up stale file_id
            if file_id and any(kw in final_err.lower() for kw in ("file", "invalid", "wrong")):
                await videos_col.update_one({"message_id": msg_id}, {"$unset": {"file_id": ""}})
            return "❌ Could not send the video. Please try again."
        sent_msg_id = resp.get("result", {}).get("message_id")
        if sent_msg_id:
            delete_at = datetime.utcnow() + timedelta(seconds=1500)
            bot_token = _get_bot_token(client)
            await del_queue_col.insert_one({
                "chat_id":   user_id,
                "msg_id":    sent_msg_id,
                "delete_at": delete_at,
                "token":     bot_token,
            })
            print(f"[VIDEO_DEL] Queued msg={sent_msg_id} user={user_id} at {delete_at.strftime('%H:%M:%S UTC')}")
    except Exception as e:
        print(f"[VIDEO] send_video error: {e}")
        return "❌ Could not send the video. Please try again."

    now = datetime.utcnow()
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"video_date": today, "video_count": used}},
        upsert=True,
    )
    await vid_hist_col.insert_one(
        {"user_id": user_id, "message_id": msg_id, "sent_at": now}
    )
    print(f"[VIDEO] msg={msg_id} → user={user_id}  ({used}/{DAILY_VIDEO_LIMIT})")
    user_doc  = await users_col.find_one({"user_id": user_id})
    fname_str = (user_doc.get("first_name") or "User") if user_doc else "User"
    uname_str = f"@{user_doc.get('username')}" if user_doc and user_doc.get("username") else "no username"
    mention   = f'<a href="tg://user?id={user_id}">{fname_str}</a>'
    asyncio.create_task(log_event(client,
        f"🎬 <b>Video Watched</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔔 User     : {mention}\n"
        f"🆔 ID       : <code>{user_id}</code>\n"
        f"📛 Handle   : {uname_str}\n"
        f"🎞 Video ID : <code>{msg_id}</code>\n"
        f"📊 Today    : <b>{used}</b> video(s)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))
    return ""


@app.on_message(filters.command("video") & filters.private)
async def video_handler_private(client: Client, message: Message):
    from handlers.forcejoin import _check_force_join, _fj_join_buttons
    user_id    = message.from_user.id
    not_joined = await _check_force_join(user_id, client)

    if not_joined:
        await message.reply_text(
            "📢 JOIN REQUIRED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"You must join all {len(not_joined)} channel(s) below\n"
            "before you can receive videos.\n\n"
            "1️⃣ Join each channel using the buttons\n"
            "2️⃣ Tap ✅ to verify and get your video\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
            reply_markup=InlineKeyboardMarkup(_fj_join_buttons(not_joined)),
        )
        return

    err = await _send_video_to_user(client, user_id)
    if err:
        await message.reply_text(err)


@app.on_message(filters.command("video") & filters.group)
async def video_handler_group(client: Client, message: Message):
    user       = message.from_user
    fname      = (user.first_name or "User") if user else "User"
    user_id    = user.id if user else 0
    bot_uname  = await get_bot_username(client)

    doc        = await users_col.find_one({"user_id": user_id}) if user_id else {}
    doc        = doc or {}
    today      = datetime.utcnow().strftime("%Y-%m-%d")
    vid_date   = doc.get("video_date", "")
    vid_count  = doc.get("video_count", 0) if vid_date == today else 0

    from datetime import timezone
    prem_doc   = await premium_col.find_one({"user_id": user_id}) if user_id else None
    if prem_doc:
        exp = prem_doc.get("expires_at")
        if exp and exp.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            raw_limit = prem_doc.get("video_limit", DAILY_VIDEO_LIMIT)
            pkg_key   = prem_doc.get("package", "")
            from config import PACKAGES
            pkg_label = PACKAGES.get(pkg_key, {}).get("label", "Premium")
            badge     = f"💎 {pkg_label}"
        else:
            await premium_col.delete_one({"user_id": user_id})
            raw_limit = doc.get("video_limit") or DAILY_VIDEO_LIMIT
            badge     = "👤 Free"
    else:
        raw_limit = doc.get("video_limit") or DAILY_VIDEO_LIMIT
        badge     = "👤 Free"

    is_unlimited = (raw_limit == -1 or (isinstance(raw_limit, int) and raw_limit >= 999))
    if is_unlimited:
        limit_str = "♾️ Unlimited"
        remaining = "♾️"
    else:
        limit_str = str(raw_limit)
        remaining = str(max(0, raw_limit - vid_count))

    total_vids = await videos_col.count_documents({"channel_id": _get_video_channel(client)})
    mention    = f'<a href="tg://user?id={user_id}">{fname}</a>' if user_id else fname

    btn = InlineKeyboardMarkup([[
        InlineKeyboardButton("🎬 Get My Video Now", url=f"https://t.me/{bot_uname}?start=video"),
        InlineKeyboardButton("💎 Buy Premium",     callback_data="open_buypremium"),
    ]])

    grp_msg = await message.reply_text(
        f"╔══════════════════════╗\n"
        f"      🎬 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 𝑽𝑰𝑫𝑬𝑶\n"
        f"╚══════════════════════╝\n\n"
        f"👋 Hey {mention}!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 YOUR STATUS:\n"
        f"   🏷 Account   : {badge}\n"
        f"   📹 Used Today: {vid_count} / {limit_str}\n"
        f"   🎯 Remaining : {remaining} videos\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎞 VIDEO LIBRARY:\n"
        f"   📦 Total Videos : {total_vids:,}\n"
        f"   🔒 Spoiler Protected\n"
        f"   ⏳ Auto-Deleted in 25 min\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 Tap the button below to\n"
        f"   receive your video in DM!\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM",
        reply_markup=btn,
        parse_mode=HTML,
    )

    async def _del():
        await asyncio.sleep(90)
        try:
            await grp_msg.delete()
            await message.delete()
        except Exception:
            pass
    asyncio.create_task(_del())


@app.on_message(filters.channel)
async def channel_post_handler(client: Client, message: Message):
    video_channel = _get_video_channel(client)
    if message.chat.id != video_channel:
        return
    if not message.video:
        return
    file_id = message.video.file_id if message.video else None
    exists = await videos_col.find_one({"message_id": message.id})
    if not exists:
        await videos_col.insert_one({
            "channel_id": video_channel,
            "message_id": message.id,
            "file_id":    file_id,
            "added_at":   datetime.utcnow(),
        })
    elif file_id and not exists.get("file_id"):
        await videos_col.update_one(
            {"message_id": message.id}, {"$set": {"file_id": file_id}}
        )
    total = await videos_col.count_documents({"channel_id": video_channel})
    print(f"[VIDEO] Auto-saved new video msg={message.id}  total={total}")

    async def _log_with_video():
        from helpers import get_log_channel, bot_api as _bot_api
        log_ch = await get_log_channel(client=client)
        if not log_ch:
            return
        from datetime import datetime as _dt
        now = _dt.utcnow().strftime("%Y-%m-%d %H:%M") + " UTC"
        text = (
            f"🗒 <b>LOG</b> | {now}\n\n"
            f"🎬 <b>New Video Posted in Channel</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎞 Video ID  : <code>{message.id}</code>\n"
            f"📺 Channel   : <code>{video_channel}</code>\n"
            f"📦 Total DB  : <b>{total} video(s)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        )
        try:
            await _bot_api_for(client, "sendMessage", {
                "chat_id":    log_ch,
                "text":       text,
                "parse_mode": "HTML",
            })
        except Exception as e:
            print(f"[VIDEO LOG] Failed: {e}")

    asyncio.create_task(_log_with_video())


@app.on_message(
    filters.incoming & filters.private & admin_filter
    & filters.forwarded & filters.video
)
async def admin_forward_video(client: Client, message: Message):
    video_channel = _get_video_channel(client)
    fwd_chat = getattr(message, "forward_from_chat", None)
    fwd_id   = getattr(message, "forward_from_message_id", None)
    if not fwd_chat or fwd_chat.id != video_channel or not fwd_id:
        return
    file_id = message.video.file_id if message.video else None
    exists = await videos_col.find_one({"message_id": fwd_id})
    if not exists:
        await videos_col.insert_one({
            "channel_id": video_channel,
            "message_id": fwd_id,
            "file_id":    file_id,
            "added_at":   datetime.utcnow(),
        })
    elif file_id and not exists.get("file_id"):
        await videos_col.update_one(
            {"message_id": fwd_id}, {"$set": {"file_id": file_id}}
        )
    total = await videos_col.count_documents({"channel_id": video_channel})
    await message.reply_text(
        f"✅ Video saved!\n📦 Total in library: {total}"
    )
    status_label = "Updated (already existed)" if exists else "New"
    asyncio.create_task(log_event(client,
        f"📤 <b>Video Saved by Admin</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎞 Video ID  : <code>{fwd_id}</code>\n"
        f"📺 Channel   : <code>{video_channel}</code>\n"
        f"🗃 Status    : {status_label}\n"
        f"📦 Total DB  : <b>{total} video(s)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


@app.on_message(filters.command("syncvideos") & admin_filter & filters.private)
async def syncvideos_cmd(client: Client, message: Message):
    """Backfill file_ids for all videos in DB that don't have one yet."""
    video_channel = _get_video_channel(client)
    docs = await videos_col.find({"channel_id": video_channel, "file_id": {"$exists": False}}).to_list(length=None)
    if not docs:
        await message.reply_text("✅ All videos already have file_id stored. No sync needed.")
        return

    total   = len(docs)
    ok_cnt  = 0
    fail_cnt = 0
    status = await message.reply_text(
        f"🔄 <b>Syncing {total} videos...</b>\n"
        f"This may take a minute. Please wait.",
        parse_mode=HTML,
    )

    for i, doc in enumerate(docs, 1):
        msg_id = doc["message_id"]
        try:
            fwd = await _bot_api_for(client, "forwardMessage", {
                "chat_id":      ADMIN_ID,
                "from_chat_id": video_channel,
                "message_id":   msg_id,
            })
            if fwd.get("ok"):
                result   = fwd.get("result", {})
                video    = result.get("video")
                fwd_mid  = result.get("message_id")
                fid      = (video or {}).get("file_id")
                if fid:
                    await videos_col.update_one(
                        {"message_id": msg_id}, {"$set": {"file_id": fid}}
                    )
                    ok_cnt += 1
                else:
                    fail_cnt += 1
                if fwd_mid:
                    await _bot_api_for(client, "deleteMessage", {
                        "chat_id": ADMIN_ID, "message_id": fwd_mid
                    })
            else:
                desc = fwd.get("description", "")
                if "not found" in desc.lower() or "message to forward not found" in desc.lower():
                    await videos_col.delete_one({"message_id": msg_id})
                    print(f"[SYNC] msg={msg_id} missing in channel, removed from DB")
                fail_cnt += 1
        except Exception as e:
            print(f"[SYNC] Error for msg={msg_id}: {e}")
            fail_cnt += 1
        await asyncio.sleep(0.3)  # avoid flood

    await status.edit_text(
        f"✅ <b>Sync Complete</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📹 Total checked : <b>{total}</b>\n"
        f"✅ file_id saved : <b>{ok_cnt}</b>\n"
        f"❌ Failed/missing: <b>{fail_cnt}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )


@app.on_message(filters.command("listvideos") & admin_filter & filters.private)
async def listvideos_cmd(client: Client, message: Message):
    video_channel = _get_video_channel(client)
    docs = await videos_col.find({"channel_id": video_channel}).sort("added_at", 1).to_list(length=None)
    if not docs:
        # Fallback: legacy docs without channel_id
        docs = await videos_col.find({}).sort("added_at", 1).to_list(length=None)
    total = len(docs)
    if not total:
        await message.reply_text("📭 No videos in the database.")
        return
    chunk_size = 50
    for start in range(0, total, chunk_size):
        chunk = docs[start:start + chunk_size]
        lines = [f"🎬 <b>Video Library</b> ({start+1}–{start+len(chunk)} of {total})\n"
                 f"━━━━━━━━━━━━━━━━━━━━━━"]
        for i, d in enumerate(chunk, start + 1):
            added = d.get("added_at", "")
            date_str = added.strftime("%Y-%m-%d") if hasattr(added, "strftime") else "?"
            lines.append(f"<b>{i}.</b> ID: <code>{d['message_id']}</code>  📅 {date_str}")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━\n"
                     "🗑 Delete: <code>/delvideo &lt;id&gt;</code> or <code>/delvideo &lt;#number&gt;</code>\n"
                     "🧹 Clear all: <code>/clearvideos</code>")
        await message.reply_text("\n".join(lines), parse_mode=HTML)


@app.on_message(filters.command("delvideo") & admin_filter & filters.private)
async def delvideo_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "⚙️ <b>Usage:</b>\n"
            "<code>/delvideo 1234567</code>  — by message ID\n"
            "<code>/delvideo #3</code>       — by list number (from /listvideos)\n\n"
            "Use <code>/listvideos</code> to see all IDs.",
            parse_mode=HTML,
        )
        return

    query = args[0]

    video_channel = _get_video_channel(client)
    if query.startswith("#") or query.isdigit():
        num_str = query.lstrip("#")
        if not num_str.isdigit():
            await message.reply_text("❌ Invalid number. Example: <code>/delvideo #3</code>", parse_mode=HTML)
            return
        idx = int(num_str) - 1
        docs = await videos_col.find({"channel_id": video_channel}).sort("added_at", 1).to_list(length=None)
        if not docs:
            docs = await videos_col.find({}).sort("added_at", 1).to_list(length=None)
        if idx < 0 or idx >= len(docs):
            await message.reply_text(f"❌ No video #{num_str} found. Use /listvideos to see the list.")
            return
        doc    = docs[idx]
        msg_id = doc["message_id"]
    else:
        if not query.lstrip("-").isdigit():
            await message.reply_text("❌ Invalid ID. Provide a numeric message ID.", parse_mode=HTML)
            return
        msg_id = int(query)

    result = await videos_col.delete_one({"message_id": msg_id})
    if result.deleted_count:
        remaining = await videos_col.count_documents({"channel_id": video_channel})
        await message.reply_text(
            f"✅ <b>Video Deleted</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 Message ID : <code>{msg_id}</code>\n"
            f"📦 Remaining  : <b>{remaining} video(s)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        asyncio.create_task(log_event(client,
            f"🗑 <b>Video Deleted from DB</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎞 Video ID : <code>{msg_id}</code>\n"
            f"📦 Remaining: <b>{remaining} video(s)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 DESI MLH SYSTEM"
        ))
    else:
        await message.reply_text(
            f"❌ No video with ID <code>{msg_id}</code> found in database.\n"
            f"Use /listvideos to see all IDs.",
            parse_mode=HTML,
        )


@app.on_message(filters.command("clearvideos") & admin_filter & filters.private)
async def clearvideos_cmd(client: Client, message: Message):
    args  = message.command[1:]
    video_channel = _get_video_channel(client)
    total = await videos_col.count_documents({"channel_id": video_channel})

    if total == 0:
        await message.reply_text("📭 Video library is already empty.")
        return

    if not args or args[0].lower() != "confirm":
        await message.reply_text(
            f"⚠️ <b>Confirm Clear All Videos</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"This will permanently delete <b>{total} video(s)</b>.\n\n"
            f"To confirm, send:\n"
            f"<code>/clearvideos confirm</code>",
            parse_mode=HTML,
        )
        return

    result = await videos_col.delete_many({"channel_id": video_channel})
    await message.reply_text(
        f"🧹 <b>Video Library Cleared</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ Deleted <b>{result.deleted_count} video(s)</b>.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    asyncio.create_task(log_event(client,
        f"🧹 <b>Entire Video Library Cleared</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗑 Deleted   : <b>{result.deleted_count} video(s)</b>\n"
        f"📦 Remaining : <b>0</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


def _has_link(_, __, message: Message) -> bool:
    if not message.entities:
        return False
    link_types = {__import__("pyrogram").enums.MessageEntityType.URL,
                  __import__("pyrogram").enums.MessageEntityType.TEXT_LINK}
    return any(e.type in link_types for e in message.entities)

has_link_filter = filters.create(_has_link)


@app.on_message(
    filters.incoming & filters.group
    & (filters.forwarded | has_link_filter)
)
async def anti_spam_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return

    try:
        from pyrogram import enums as _enums
        member = await client.get_chat_member(message.chat.id, user.id)
        if member.status in (
            _enums.ChatMemberStatus.OWNER,
            _enums.ChatMemberStatus.ADMINISTRATOR,
        ):
            return
    except Exception:
        pass

    try:
        await message.delete()
        print(f"[SPAM] Deleted message from {user.id} in {message.chat.id}")
    except Exception as e:
        print(f"[SPAM] Delete failed: {e}")
        return

    if message.forward_date or message.forward_from or message.forward_from_chat:
        violation = "forwarded message"
        vio_icon  = "📨"
    else:
        violation = "link / URL"
        vio_icon  = "🔗"

    name    = user.first_name or "User"
    mention = f'<a href="tg://user?id={user.id}">{name}</a>'

    warn_text = (
        "⚠️ SPAM WARNING ⚠️\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: {mention}\n"
        f"{vio_icon} Violation: Sending a {violation}\n\n"
        "❌ This content has been removed.\n"
        "🔁 Repeated violations may result in a mute or ban.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )

    try:
        warn_msg = await client.send_message(
            message.chat.id, warn_text,
            parse_mode=HTML,
            reply_markup=_COUPLE_BTN_PY,
        )
        async def _del_warn(msg):
            await asyncio.sleep(40)
            try:
                await msg.delete()
            except Exception:
                pass
        asyncio.create_task(_del_warn(warn_msg))
    except Exception as e:
        print(f"[SPAM] Warning send failed: {e}")
