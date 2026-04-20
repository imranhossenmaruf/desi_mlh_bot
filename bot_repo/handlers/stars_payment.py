"""
Telegram Stars Payment Handler
===============================
Polls Bot API for two update types:
  1. pre_checkout_query  — must be answered within 10 seconds
  2. message.successful_payment — grant premium automatically

Why Bot API polling (not Pyrogram):
  Pyrogram 2.0.106 does not expose pre_checkout_query events natively.
  Bot API getUpdates runs on a separate transport from MTProto, so
  there is no conflict with Pyrogram's internal update handling.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp

from config import BOT_TOKEN, PACKAGES, app, premium_col, users_col
from helpers import bot_api, log_event


# ─────────────────────────────────────────────────────────────────────────────
#  Grant Stars premium to a user (fully automatic, no admin needed)
# ─────────────────────────────────────────────────────────────────────────────

async def _grant_stars_premium(user_id: int, pkg_key: str, stars_paid: int):
    pkg = PACKAGES.get(pkg_key)
    if not pkg:
        print(f"[STARS] Unknown package key: {pkg_key}")
        return

    now     = datetime.now(timezone.utc)
    expires = now + timedelta(days=pkg["days"])
    lim_str = "Unlimited" if pkg["video_limit"] >= 999 else f"{pkg['video_limit']}/day"

    await premium_col.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id":      user_id,
            "package":      pkg_key,
            "video_limit":  pkg["video_limit"],
            "started_at":   now,
            "expires_at":   expires,
            "payment_via":  "telegram_stars",
            "stars_paid":   stars_paid,
        }},
        upsert=True,
    )

    today_str = now.strftime("%Y-%m-%d")
    await users_col.update_one(
        {"user_id": user_id},
        {"$set": {"video_count": 0, "video_date": today_str}},
    )

    await bot_api("sendMessage", {
        "chat_id":    user_id,
        "parse_mode": "HTML",
        "text": (
            "⭐━━━━━━━━━━━━━━━━━━━━━━⭐\n"
            "  🎉  PREMIUM ACTIVATED!  🎉\n"
            "⭐━━━━━━━━━━━━━━━━━━━━━━⭐\n\n"
            f"📦 Package    : <b>{pkg['label']}</b>\n"
            f"💫 Paid       : <b>{stars_paid} Telegram Stars</b>\n"
            f"🎬 Video Limit: <b>{lim_str}</b>\n"
            f"📅 Duration   : <b>{pkg['days']} days</b>\n"
            f"📅 Expires    : <b>{expires.strftime('%d %b %Y')}</b>\n\n"
            "✅ Your account has been instantly upgraded!\n"
            "Use /mypremium to check your status.\n\n"
            "⭐━━━━━━━━━━━━━━━━━━━━━━⭐\n"
            "🤖 DESI MLH SYSTEM"
        ),
    })

    print(f"[STARS] ✅ Premium granted: user={user_id} pkg={pkg_key} stars={stars_paid}")

    asyncio.create_task(log_event(
        app,
        f"⭐ <b>Stars Payment — Premium Granted</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User   : <code>{user_id}</code>\n"
        f"📦 Package: <b>{pkg['label']}</b>\n"
        f"💫 Stars  : <b>{stars_paid}</b>\n"
        f"📅 Expires: <b>{expires.strftime('%d %b %Y')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 DESI MLH SYSTEM"
    ))


# ─────────────────────────────────────────────────────────────────────────────
#  Process a single update from Bot API
# ─────────────────────────────────────────────────────────────────────────────

async def _process_update(update: dict):
    # ── Pre-checkout query — MUST be answered within 10 seconds ──────────────
    if "pre_checkout_query" in update:
        pcq    = update["pre_checkout_query"]
        pcq_id = pcq["id"]
        payload = pcq.get("invoice_payload", "")

        if payload.startswith("stars_"):
            await bot_api("answerPreCheckoutQuery", {
                "pre_checkout_query_id": pcq_id,
                "ok": True,
            })
            print(f"[STARS] ✅ Answered pre_checkout_query: {pcq_id}")
        else:
            await bot_api("answerPreCheckoutQuery", {
                "pre_checkout_query_id": pcq_id,
                "ok":            False,
                "error_message": "Invalid payment payload.",
            })
            print(f"[STARS] ❌ Rejected unknown pre_checkout_query payload: {payload}")
        return

    # ── Successful payment message ────────────────────────────────────────────
    if "message" in update:
        msg = update["message"]
        if "successful_payment" not in msg:
            return

        payment = msg["successful_payment"]
        payload = payment.get("invoice_payload", "")
        user_id = msg["from"]["id"]
        stars_paid = payment.get("total_amount", 0)

        if not payload.startswith("stars_"):
            print(f"[STARS] Unknown payment payload: {payload}")
            return

        pkg_key = payload[len("stars_"):]
        print(f"[STARS] Payment received: user={user_id} pkg={pkg_key} stars={stars_paid}")
        await _grant_stars_premium(user_id, pkg_key, stars_paid)


# ─────────────────────────────────────────────────────────────────────────────
#  Background polling loop
# ─────────────────────────────────────────────────────────────────────────────

async def stars_payment_loop():
    """
    Poll Bot API getUpdates for pre_checkout_query and successful_payment.
    This runs independently from Pyrogram's MTProto transport.
    """
    print("[STARS] Payment polling loop started.")
    offset = 0

    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {
                "offset":          offset,
                "timeout":         25,
                "allowed_updates": ["pre_checkout_query", "message"],
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=params,
                    timeout=aiohttp.ClientTimeout(total=35),
                ) as resp:
                    data = await resp.json()

            if not data.get("ok"):
                print(f"[STARS] getUpdates error: {data.get('description')}")
                await asyncio.sleep(5)
                continue

            results = data.get("result", [])
            for update in results:
                offset = update["update_id"] + 1
                try:
                    await _process_update(update)
                except Exception as e:
                    print(f"[STARS] Error processing update {update.get('update_id')}: {e}")

        except asyncio.CancelledError:
            print("[STARS] Polling loop cancelled.")
            break
        except Exception as e:
            print(f"[STARS] Loop error: {e}")
            await asyncio.sleep(5)
