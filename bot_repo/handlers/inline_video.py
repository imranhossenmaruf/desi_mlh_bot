"""
inline_video.py — Smart Inline Random Video System

Trigger: @BotUsername video
Logic:
  - Pick 10 random videos from MongoDB not seen by the user in last 7 days
  - Display as '🎬 Video #[message_id]'
  - cache_time=0 so every query gives a fresh set
  - On click: send video and update seen_videos in DB
"""

import asyncio
import random
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import (
    InlineQuery,
    InlineQueryResultCachedVideo,
    InlineQueryResultArticle,
    InputTextMessageContent,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from config import (
    HTML, VIDEO_CHANNEL, VIDEO_REPEAT_DAYS,
    videos_col, vid_hist_col, users_col, premium_col,
    DAILY_VIDEO_LIMIT, app,
)
from helpers import bot_api, BOT_TOKEN, _bot_token_ctx
from strings import get_string


_PLACEHOLDER_THUMB = "https://telegra.ph/file/6b02e984e1f7d45f5e6ab.jpg"


async def _get_unseen_videos(user_id: int, limit: int = 10) -> list[dict]:
    """Return up to `limit` random videos the user has NOT seen in the last 7 days."""
    cutoff = datetime.utcnow() - timedelta(days=VIDEO_REPEAT_DAYS)

    # Gather seen message IDs
    seen_ids: set[int] = set()
    async for d in vid_hist_col.find(
        {"user_id": user_id, "sent_at": {"$gte": cutoff}},
        {"message_id": 1},
    ):
        seen_ids.add(d["message_id"])

    # All videos in the channel
    all_docs = await videos_col.find(
        {"channel_id": VIDEO_CHANNEL}
    ).to_list(length=None)
    if not all_docs:
        all_docs = await videos_col.find({}).to_list(length=None)

    pool = [d for d in all_docs if d["message_id"] not in seen_ids]
    if not pool:
        pool = all_docs  # If all seen, show everything (fresh set)

    random.shuffle(pool)
    return pool[:limit]


async def _check_daily_limit(user_id: int) -> tuple[bool, int, int, int]:
    """
    Returns (limit_reached, effective_limit, hrs_remaining, mins_remaining).
    """
    from datetime import timezone
    today = datetime.utcnow().strftime("%Y-%m-%d")

    doc      = await users_col.find_one({"user_id": user_id})
    prem_doc = await premium_col.find_one({"user_id": user_id})

    raw_limit = None
    if prem_doc:
        expires_at = prem_doc.get("expires_at")
        if expires_at and expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
            raw_limit = prem_doc.get("video_limit", DAILY_VIDEO_LIMIT)

    if raw_limit is None:
        raw_limit = (doc or {}).get("video_limit")

    is_unlimited = raw_limit == -1 or (isinstance(raw_limit, int) and raw_limit >= 999)
    if is_unlimited:
        return False, 0, 0, 0

    effective_limit = raw_limit if isinstance(raw_limit, int) and raw_limit > 0 else DAILY_VIDEO_LIMIT
    vid_date  = (doc or {}).get("video_date", "")
    vid_count = (doc or {}).get("video_count", 0) if vid_date == today else 0

    if vid_count >= effective_limit:
        midnight  = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        remaining = midnight + timedelta(days=1) - datetime.utcnow()
        hrs, rem  = divmod(int(remaining.total_seconds()), 3600)
        mins      = rem // 60
        return True, effective_limit, hrs, mins

    return False, effective_limit, 0, 0


# ── Inline query handler ──────────────────────────────────────────────────────

@app.on_inline_query()
async def inline_video_query(client: Client, query: InlineQuery):
    """
    Handles inline queries.  Only responds when query text is exactly 'video'
    (case-insensitive).  Returns 10 random unseen videos.
    """
    raw   = (query.query or "").strip().lower()
    uid   = query.from_user.id

    # Only respond to "video" query
    if raw != "video":
        await query.answer(
            results=[],
            cache_time=0,
            switch_pm_text="Type 'video' to search",
            switch_pm_parameter="inline_help",
        )
        return

    # Check daily limit
    limit_reached, eff_limit, hrs, mins = await _check_daily_limit(uid)
    if limit_reached:
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    title="⚠️ Daily Limit Reached",
                    description=f"Resets in {hrs}h {mins}m — Upgrade via @IH_Maruf",
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            f"⚠️ <b>Daily Limit Reached</b>\n"
                            f"📹 Used all {eff_limit} videos for today.\n"
                            f"🔄 Resets in <b>{hrs}h {mins}m</b>\n\n"
                            "💎 Upgrade to Premium → @IH_Maruf"
                        ),
                        parse_mode=HTML,
                    ),
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("💎 Upgrade Premium", url="https://t.me/IH_Maruf")
                    ]]),
                )
            ],
            cache_time=0,
        )
        return

    videos = await _get_unseen_videos(uid, limit=10)

    if not videos:
        await query.answer(
            results=[
                InlineQueryResultArticle(
                    title="📭 No Videos Found",
                    description="No new videos available right now.",
                    input_message_content=InputTextMessageContent(
                        message_text="📭 No new videos available right now. Please try again later."
                    ),
                )
            ],
            cache_time=0,
        )
        return

    results = []
    for doc in videos:
        msg_id  = doc["message_id"]
        file_id = doc.get("file_id", "")
        vid_id  = msg_id  # Use message_id as display ID

        title       = f"🎬 Video #{vid_id}"
        description = "Click to receive this video in your chat"

        # Use cached file_id if available
        if file_id:
            results.append(
                InlineQueryResultCachedVideo(
                    video_file_id=file_id,
                    title=title,
                    caption=title,
                    description=description,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "📥 Get Video",
                            callback_data=f"inline_vid:{msg_id}",
                        )
                    ]]),
                )
            )
        else:
            # No file_id: show article placeholder
            results.append(
                InlineQueryResultArticle(
                    id=str(msg_id),
                    title=title,
                    description=description,
                    input_message_content=InputTextMessageContent(
                        message_text=(
                            f"🎬 <b>Video #{vid_id}</b>\n\n"
                            f"Send /video in private chat to receive this video."
                        ),
                        parse_mode=HTML,
                    ),
                )
            )

    await query.answer(
        results=results,
        cache_time=0,
        is_personal=True,
    )


# ── Callback: send video when user clicks inline result ───────────────────────

@app.on_callback_query(filters.regex(r"^inline_vid:(\d+)$"))
async def inline_vid_callback(client: Client, cq: CallbackQuery):
    """When user taps 'Get Video' in inline result, send the video privately."""
    msg_id = int(cq.data.split(":")[1])
    uid    = cq.from_user.id

    await cq.answer("🎬 Sending your video…")

    # Check limit again at click time
    limit_reached, eff_limit, hrs, mins = await _check_daily_limit(uid)
    if limit_reached:
        try:
            await client.send_message(
                uid,
                f"⚠️ <b>Daily Limit Reached</b>\n"
                f"📹 Used all {eff_limit} videos today.\n"
                f"🔄 Resets in <b>{hrs}h {mins}m</b>\n\n"
                "💎 Upgrade to Premium → @IH_Maruf",
                parse_mode=HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💎 Upgrade Premium", url="https://t.me/IH_Maruf")
                ]]),
            )
        except Exception:
            pass
        return

    # Fetch video doc
    doc = await videos_col.find_one({"message_id": msg_id})
    if not doc:
        try:
            await client.send_message(uid, "❌ Video not found. Please try again.")
        except Exception:
            pass
        return

    file_id = doc.get("file_id")
    today   = datetime.utcnow().strftime("%Y-%m-%d")
    user_doc = await users_col.find_one({"user_id": uid}) or {}
    vid_count = user_doc.get("video_count", 0) if user_doc.get("video_date", "") == today else 0

    try:
        if file_id:
            token = _bot_token_ctx.get() or BOT_TOKEN
            url   = f"https://api.telegram.org/bot{token}/sendVideo"
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, json={
                    "chat_id":            uid,
                    "video":              file_id,
                    "caption":            f"🎬 Video #{msg_id}",
                    "has_spoiler":        True,
                    "protect_content":    True,
                    "supports_streaming": True,
                }) as resp:
                    result = await resp.json()
                    if not result.get("ok"):
                        raise Exception(result.get("description", "sendVideo failed"))
        else:
            # Fallback: copy from channel
            token = _bot_token_ctx.get() or BOT_TOKEN
            url   = f"https://api.telegram.org/bot{token}/copyMessage"
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.post(url, json={
                    "chat_id":         uid,
                    "from_chat_id":    VIDEO_CHANNEL,
                    "message_id":      msg_id,
                    "caption":         f"🎬 Video #{msg_id}",
                    "has_spoiler":     True,
                    "protect_content": True,
                }) as resp:
                    result = await resp.json()
                    if not result.get("ok"):
                        raise Exception(result.get("description", "copyMessage failed"))

        # Update video count and history
        used = vid_count + 1
        await users_col.update_one(
            {"user_id": uid},
            {"$set": {"video_date": today, "video_count": used}},
            upsert=True,
        )
        await vid_hist_col.insert_one({
            "user_id":    uid,
            "message_id": msg_id,
            "sent_at":    datetime.utcnow(),
            "source":     "inline",
        })

    except Exception as e:
        print(f"[INLINE_VID] Send failed for msg={msg_id} uid={uid}: {e}")
        try:
            await client.send_message(uid, "❌ Could not send video. Please try /video instead.")
        except Exception:
            pass
