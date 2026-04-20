import asyncio
from datetime import datetime, timedelta, timezone

from pyrogram import filters
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import (
    HTML, ADMIN_ID, ADMIN_IDS, PACKAGES, PACKAGE_ORDER, PAYMENT_METHODS,
    app, users_col, premium_col, proof_sessions,
)
from helpers import log_event, bot_api


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def get_premium(user_id: int) -> dict | None:
    doc = await premium_col.find_one({"user_id": user_id})
    if not doc:
        return None
    expires = doc.get("expires_at")
    if expires is None:
        return None
    if expires.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        await premium_col.delete_one({"user_id": user_id})
        return None
    return doc


async def get_user_video_limit(user_id: int) -> int:
    from config import DAILY_VIDEO_LIMIT
    prem = await get_premium(user_id)
    if prem:
        return prem["video_limit"]
    user = await users_col.find_one({"user_id": user_id})
    if user and user.get("video_limit") and user["video_limit"] > 0:
        return user["video_limit"]
    return DAILY_VIDEO_LIMIT


def _packages_text() -> str:
    lines = []
    for pkg in PACKAGES.values():
        vids  = "∞" if pkg["video_limit"] >= 999 else f"{pkg['video_limit']}/day"
        lines.append(f"<b>{pkg['label']}</b>  •  {pkg['price']}  •  {pkg['days']}d  •  {vids}")
    body = "\n".join(lines)
    return (
        f"<b>💎 ᴘʀᴇᴍɪᴜᴍ ᴘʟᴀɴꜱ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"{body}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"✨ Choose your package 👇"
    )


def _packages_keyboard():
    keys = list(PACKAGES.items())
    rows = []
    for i in range(0, len(keys), 2):
        row = []
        for key, pkg in keys[i:i+2]:
            row.append(InlineKeyboardButton(
                f"{pkg['label']}  {pkg['price']}",
                callback_data=f"pkg_{key}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="pkg_cancel")])
    return InlineKeyboardMarkup(rows)


def _payment_keyboard(pkg_key: str):
    # শুধুমাত্র Telegram Stars payment দেখানো হবে।
    # Crypto/USDT payment methods সরিয়ে দেওয়া হয়েছে।
    pkg = PACKAGES.get(pkg_key, {})
    stars = pkg.get("stars", 0)
    rows = [
        [InlineKeyboardButton(
            f"⭐ Pay with Telegram Stars ({stars} Stars)",
            callback_data=f"pay_stars_{pkg_key}",
        )],
        [InlineKeyboardButton("🔙 Back", callback_data="pkg_back")],
    ]
    return InlineKeyboardMarkup(rows)


def _proof_keyboard(pkg_key: str, method_key: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📸 Send Payment Proof",
            callback_data=f"proof_{pkg_key}_{method_key}",
        ),
        InlineKeyboardButton("❌ Cancel", callback_data="pkg_cancel"),
    ]])


# ─────────────────────────────────────────────────────────────────────────────
#  /buypremium
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("buypremium"))
async def buypremium_cmd(_, message: Message):
    prem = await get_premium(message.from_user.id)
    if prem:
        remaining = (prem["expires_at"].replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days
        pkg = PACKAGES.get(prem["package"], {})
        lim = prem.get("video_limit", 0)
        lim_str = "Unlimited" if lim >= 999 else f"{lim}/day"
        await message.reply_text(
            f"<b>💎 You are already a Premium Member!</b>\n\n"
            f"📦 Package: <b>{pkg.get('label', prem['package'])}</b>\n"
            f"📅 Expires: <b>{prem['expires_at'].strftime('%d %b %Y')}</b>\n"
            f"⏳ Remaining: <b>{remaining} days</b>\n"
            f"🎬 Video Limit: <b>{lim_str}</b>\n\n"
            f"⬆️ Want to upgrade? Use /upgrade",
            parse_mode=HTML,
        )
        return
    await message.reply_text(
        _packages_text(), parse_mode=HTML, reply_markup=_packages_keyboard()
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /mypremium
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("mypremium"))
async def mypremium_cmd(_, message: Message):
    prem = await get_premium(message.from_user.id)
    if not prem:
        await message.reply_text(
            "❌ You are not a Premium member yet.\n\nUse /buypremium to get started.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 Buy Premium ✨", callback_data="open_buypremium")
            ]]),
        )
        return
    remaining = (prem["expires_at"].replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days
    pkg     = PACKAGES.get(prem["package"], {})
    lim     = prem.get("video_limit", 0)
    lim_str = "Unlimited" if lim >= 999 else f"{lim}/day"
    await message.reply_text(
        f"<b>✅ Your Premium Membership</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Package: <b>{pkg.get('label', prem['package'])}</b>\n"
        f"📅 Started: <b>{prem.get('started_at', datetime.now(timezone.utc)).strftime('%d %b %Y')}</b>\n"
        f"📅 Expires: <b>{prem['expires_at'].strftime('%d %b %Y')}</b>\n"
        f"⏳ Remaining: <b>{remaining} days</b>\n"
        f"🎬 Video Limit: <b>{lim_str}</b>",
        parse_mode=HTML,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  /premiumlist
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("premiumlist"))
async def premiumlist_cmd(_, message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    now  = datetime.now(timezone.utc)
    docs = await premium_col.find({"expires_at": {"$gt": now}}).to_list(length=200)
    if not docs:
        await message.reply_text("📭 No active Premium members.")
        return
    lines = [f"<b>💎 Active Premium Members ({len(docs)})</b>\n━━━━━━━━━━━━━━━━━━━\n"]
    for i, doc in enumerate(docs, 1):
        pkg = PACKAGES.get(doc["package"], {})
        rem = (doc["expires_at"].replace(tzinfo=timezone.utc) - now).days
        lines.append(
            f"{i}. <code>{doc['user_id']}</code> — {pkg.get('label', doc['package'])} ({rem} days left)"
        )
    await message.reply_text("\n".join(lines), parse_mode=HTML)


# ─────────────────────────────────────────────────────────────────────────────
#  /revokepremium
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("revokepremium"))
async def revokepremium_cmd(_, message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) < 2:
        await message.reply_text("⚠️ Usage: /revokepremium &lt;user_id&gt;", parse_mode=HTML)
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await message.reply_text("❌ Please provide a valid User ID.")
        return
    res = await premium_col.delete_one({"user_id": uid})
    if res.deleted_count:
        await message.reply_text(
            f"✅ Premium revoked for <code>{uid}</code>.", parse_mode=HTML
        )
        try:
            await app.send_message(uid, "⚠️ Your Premium membership has been revoked by the admin.")
        except Exception:
            pass
    else:
        await message.reply_text(
            f"❌ <code>{uid}</code> is not a Premium member.", parse_mode=HTML
        )


# ─────────────────────────────────────────────────────────────────────────────
#  /cancel — during proof submission
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("cancel"))
async def cancel_proof(_, message: Message):
    if proof_sessions.pop(message.from_user.id, None):
        await message.reply_text("❌ Cancelled.")


# ─────────────────────────────────────────────────────────────────────────────
#  Package selection callback
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^pkg_(starter|basic|standard|pro|vip|elite)$"))
async def pkg_selected(_, cq: CallbackQuery):
    pkg_key = cq.data.split("_", 1)[1]
    pkg     = PACKAGES[pkg_key]
    lim_str = "Unlimited" if pkg["video_limit"] >= 999 else f"{pkg['video_limit']}/day"
    text = (
        f"<b>{pkg['label']} — {pkg['price']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Duration: <b>{pkg['days']} days</b>\n"
        f"🎬 Videos: <b>{lim_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"💳 Choose a payment method 👇"
    )
    await cq.message.edit_text(text, parse_mode=HTML, reply_markup=_payment_keyboard(pkg_key))


@app.on_callback_query(filters.regex("^pkg_cancel$"))
async def pkg_cancel(_, cq: CallbackQuery):
    proof_sessions.pop(cq.from_user.id, None)
    await cq.message.delete()
    await cq.answer("Cancelled.")


@app.on_callback_query(filters.regex("^pkg_back$"))
async def pkg_back(_, cq: CallbackQuery):
    await cq.message.edit_text(
        _packages_text(), parse_mode=HTML, reply_markup=_packages_keyboard()
    )


@app.on_callback_query(filters.regex("^open_buypremium$"))
async def open_buypremium(_, cq: CallbackQuery):
    await cq.answer()
    await cq.message.edit_text(
        _packages_text(), parse_mode=HTML, reply_markup=_packages_keyboard()
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Payment method callback
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(
    filters.regex(r"^pay_(binance|redotpay|trc20|bep20)_(starter|basic|standard|pro|vip|elite)$")
)
async def pay_method_selected(_, cq: CallbackQuery):
    parts      = cq.data.split("_", 2)
    method_key = parts[1]
    pkg_key    = parts[2]
    method     = PAYMENT_METHODS[method_key]
    pkg        = PACKAGES[pkg_key]

    if method.get("type") == "qr":
        if method_key == "binance":
            caption = (
                f"<b>💛 Binance Pay — {pkg['label']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Package : <b>{pkg['label']}</b>\n"
                f"💵 Amount  : <b>{pkg['price']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>📋 How to pay:</b>\n"
                f"1️⃣ Open Binance app → Pay → Send\n"
                f"2️⃣ Search by name: <b>{method.get('name', '')}</b>\n"
                f"   or scan the QR code\n"
                f"3️⃣ Send exactly <b>{pkg['price']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📸 Then click below to submit proof."
            )
        else:
            caption = (
                f"<b>🔴 RedotPay — {pkg['label']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Package : <b>{pkg['label']}</b>\n"
                f"💵 Amount  : <b>{pkg['price']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>📋 How to pay:</b>\n"
                f"1️⃣ Open RedotPay app → Transfer → Send\n"
                f"2️⃣ Enter ID: <b>{method.get('id', '')}</b>\n"
                f"   or scan the QR code\n"
                f"3️⃣ Send exactly <b>{pkg['price']}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📸 Then click below to submit proof."
            )
        try:
            await cq.message.delete()
        except Exception:
            pass
        await app.send_photo(
            chat_id=cq.from_user.id,
            photo=method["qr"],
            caption=caption,
            parse_mode=HTML,
            reply_markup=_proof_keyboard(pkg_key, method_key),
        )
    else:
        address = method.get("address", "")
        text = (
            f"<b>{method['label']} — {pkg['label']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Package : <b>{pkg['label']}</b>\n"
            f"💵 Amount  : <b>{pkg['price']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<b>📋 Wallet Address:</b>\n"
            f"<code>{address}</code>\n\n"
            f"⚠️ {method.get('note', '')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📸 After sending, click below to submit proof."
        )
        await cq.message.edit_text(
            text, parse_mode=HTML, reply_markup=_proof_keyboard(pkg_key, method_key)
        )
    await cq.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  Telegram Stars payment callback — sends invoice directly
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(
    filters.regex(r"^pay_stars_(starter|basic|standard|pro|vip|elite)$")
)
async def pay_stars_selected(_, cq: CallbackQuery):
    pkg_key = cq.data.split("_", 2)[2]
    pkg     = PACKAGES[pkg_key]
    uid     = cq.from_user.id
    stars   = pkg["stars"]
    lim_str = "Unlimited" if pkg["video_limit"] >= 999 else f"{pkg['video_limit']}/day"

    await cq.answer("Opening Telegram Stars payment…")

    try:
        await cq.message.delete()
    except Exception:
        pass

    resp = await bot_api("sendInvoice", {
        "chat_id":     uid,
        "title":       f"{pkg['label']} Premium",
        "description": f"{pkg['days']} Days • {lim_str} Videos/Day\nInstant activation — no admin needed!",
        "payload":     f"stars_{pkg_key}",
        "currency":    "XTR",
        "prices":      [{"label": pkg["label"], "amount": stars}],
    })

    if not resp.get("ok"):
        err = resp.get("description", "unknown error")
        await bot_api("sendMessage", {
            "chat_id":    uid,
            "parse_mode": "HTML",
            "text": (
                "❌ <b>Could not open Stars payment.</b>\n\n"
                f"Reason: <code>{err}</code>\n\n"
                "Please try a crypto payment method or contact admin."
            ),
        })


# ─────────────────────────────────────────────────────────────────────────────
#  "Send Proof" button
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(
    filters.regex(r"^proof_(starter|basic|standard|pro|vip|elite)_(binance|redotpay|trc20|bep20)$")
)
async def proof_btn(_, cq: CallbackQuery):
    parts      = cq.data.split("_", 2)
    pkg_key    = parts[1]
    method_key = parts[2]
    user_id    = cq.from_user.id

    proof_sessions[user_id] = {"pkg": pkg_key, "method": method_key}

    await cq.answer("Now send your payment screenshot.")
    await app.send_message(
        chat_id=user_id,
        text=(
            "📸 <b>SEND PAYMENT SCREENSHOT</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "Please send your payment screenshot\n"
            "in this chat right now.\n\n"
            "⏳ Premium will be activated after\n"
            "   admin verification.\n\n"
            "❌ To cancel: /cancel\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🤖 Unity Group Manager"
        ),
        parse_mode=HTML,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Photo handler — receive proof and forward to admin
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.photo)
async def proof_photo_handler(_, message: Message):
    user_id = message.from_user.id
    session = proof_sessions.get(user_id)
    if not session:
        return

    pkg_key    = session["pkg"]
    method_key = session["method"]
    pkg        = PACKAGES[pkg_key]
    method     = PAYMENT_METHODS[method_key]
    user       = message.from_user

    proof_sessions.pop(user_id, None)

    name         = f"{user.first_name or ''} {user.last_name or ''}".strip()
    username_str = f"@{user.username}" if user.username else "N/A"
    upgrade_from = session.get("upgrade_from")
    pay_amount   = session.get("pay_amount")

    if upgrade_from:
        from_pkg = PACKAGES.get(upgrade_from, {})
        header   = f"⬆️ UPGRADE: {from_pkg.get('label', upgrade_from)} → {pkg['label']}"
        pay_str  = f"${pay_amount} USDT (UPGRADE)"
    else:
        header  = "💳 New Payment Proof"
        pay_str = pkg["price"]

    caption = (
        f"<b>{header}</b>\n━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Name: <b>{name}</b>\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"📛 Username: {username_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Package: <b>{pkg['label']}</b>\n"
        f"📅 Duration: <b>{pkg['days']} days</b>\n"
        f"💳 Method: <b>{method['label']}</b>\n"
        f"💰 Amount: <b>{pay_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

    approve_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"prem_approve_{user_id}_{pkg_key}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"prem_reject_{user_id}"),
    ]])

    await app.send_photo(
        chat_id=ADMIN_ID,
        photo=message.photo.file_id,
        caption=caption,
        parse_mode=HTML,
        reply_markup=approve_kb,
    )

    await message.reply_text(
        "✅ <b>PROOF SUBMITTED!</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📩 Your screenshot has been sent\n"
        "   to the admin for review.\n\n"
        "⏰ Estimated activation time:\n"
        "   1–24 hours\n\n"
        "🔔 You'll receive a notification\n"
        "   once it's approved.\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Approve / Reject callbacks
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(
    filters.regex(r"^prem_approve_(\d+)_(starter|basic|standard|pro|vip|elite)$")
)
async def prem_approve(_, cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer("⛔ Admin only.", show_alert=True)
        return

    parts   = cq.data.split("_")
    uid     = int(parts[2])
    pkg_key = parts[3]
    pkg     = PACKAGES[pkg_key]

    now     = datetime.now(timezone.utc)
    expires = now + timedelta(days=pkg["days"])

    await premium_col.update_one(
        {"user_id": uid},
        {"$set": {
            "user_id":     uid,
            "package":     pkg_key,
            "video_limit": pkg["video_limit"],
            "started_at":  now,
            "expires_at":  expires,
        }},
        upsert=True,
    )
    today_str = now.strftime("%Y-%m-%d")
    await users_col.update_one(
        {"user_id": uid},
        {"$set": {"video_count": 0, "video_date": today_str}},
    )

    lim_str = "Unlimited" if pkg["video_limit"] >= 999 else f"{pkg['video_limit']}/day"
    try:
        await app.send_message(
            chat_id=uid,
            text=(
                f"🎉 <b>Congratulations! Premium Activated!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Package: <b>{pkg['label']}</b>\n"
                f"🎬 Video Limit: <b>{lim_str}</b>\n"
                f"📅 Duration: <b>{pkg['days']} days</b>\n"
                f"📅 Expires: <b>{expires.strftime('%d %b %Y')}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Your daily video counter has been reset.\n"
                f"Enjoy your Premium access! 💖"
            ),
            parse_mode=HTML,
        )
    except Exception:
        pass

    try:
        await cq.message.edit_caption(
            cq.message.caption + f"\n\n✅ <b>Approved by Admin</b> — {now.strftime('%d %b %Y %H:%M')} UTC",
            parse_mode=HTML,
        )
    except Exception:
        pass

    await cq.answer("✅ Premium activated!")
    asyncio.create_task(log_event(
        cq._client,
        f"✅ <b>Premium Approved</b>\n"
        f"👤 User: <code>{uid}</code>\n"
        f"📦 Package: {pkg['label']}"
    ))


@app.on_callback_query(filters.regex(r"^prem_reject_(\d+)$"))
async def prem_reject(_, cq: CallbackQuery):
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer("⛔ Admin only.", show_alert=True)
        return

    uid = int(cq.data.split("_")[2])

    try:
        await app.send_message(
            chat_id=uid,
            text=(
                "❌ <b>PAYMENT PROOF REJECTED</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "Your payment screenshot was not\n"
                "accepted by the admin.\n\n"
                "📌 Possible reasons:\n"
                "• Screenshot is unclear or invalid\n"
                "• Wrong payment amount\n"
                "• Unrecognized payment method\n\n"
                "🔄 Try again: /buypremium\n"
                "📩 Need help? Contact: @IH_Maruf\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "🤖 DESI MLH SYSTEM"
            ),
            parse_mode=HTML,
        )
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    try:
        await cq.message.edit_caption(
            cq.message.caption + f"\n\n❌ <b>Rejected by Admin</b> — {now.strftime('%d %b %Y %H:%M')} UTC",
            parse_mode=HTML,
        )
    except Exception:
        pass

    await cq.answer("❌ Rejected.")


# ─────────────────────────────────────────────────────────────────────────────
#  /upgrade — upgrade premium package
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("upgrade"))
async def upgrade_cmd(_, message: Message):
    user_id  = message.from_user.id
    prem_doc = await get_premium(user_id)
    if not prem_doc:
        await message.reply_text(
            "❌ You don't have an active premium plan.\n\nUse /buypremium to get started.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 Buy Premium", callback_data="open_buypremium")
            ]]),
        )
        return

    current_key = prem_doc.get("package", "starter")
    try:
        current_idx = PACKAGE_ORDER.index(current_key)
    except ValueError:
        current_idx = 0

    upgrade_pkgs = PACKAGE_ORDER[current_idx + 1:]
    if not upgrade_pkgs:
        await message.reply_text(
            "✅ You are already on the <b>highest plan</b> (Elite)!\n\n"
            "Nothing to upgrade — enjoy unlimited access. 👑",
            parse_mode=HTML,
        )
        return

    current_price = PACKAGES.get(current_key, {}).get("price_usd", 0)
    rows = []
    for pkg_key in upgrade_pkgs:
        pkg  = PACKAGES[pkg_key]
        diff = pkg["price_usd"] - current_price
        rows.append([InlineKeyboardButton(
            f"{pkg['label']}  •  Pay ${diff} USDT",
            callback_data=f"upg_{pkg_key}",
        )])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="pkg_cancel")])

    exp       = prem_doc.get("expires_at")
    days_left = (exp.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days if exp else 0
    cur_pkg   = PACKAGES.get(current_key, {})

    await message.reply_text(
        f"⬆️ <b>UPGRADE YOUR PLAN</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Current: <b>{cur_pkg.get('label', current_key)}</b> (${current_price})\n"
        f"⏳ Remaining: <b>{days_left} days</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💡 You only pay the <b>difference</b>!\n\n"
        f"Select a plan to upgrade to 👇",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


@app.on_callback_query(filters.regex(r"^upg_(starter|basic|standard|pro|vip|elite)$"))
async def upg_pkg_selected(_, cq: CallbackQuery):
    pkg_key  = cq.data.split("_", 1)[1]
    pkg      = PACKAGES[pkg_key]
    user_id  = cq.from_user.id
    prem_doc = await get_premium(user_id)

    if not prem_doc:
        await cq.answer("No active premium. Use /buypremium.", show_alert=True)
        return

    current_key   = prem_doc.get("package", "starter")
    current_price = PACKAGES.get(current_key, {}).get("price_usd", 0)
    diff          = max(0, pkg["price_usd"] - current_price)
    lim_str       = "Unlimited" if pkg["video_limit"] >= 999 else f"{pkg['video_limit']}/day"

    exp       = prem_doc.get("expires_at")
    days_left = (exp.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).days if exp else 0

    rows = []
    for method_key, method in PAYMENT_METHODS.items():
        rows.append([InlineKeyboardButton(
            method["label"],
            callback_data=f"upgpay_{method_key}_{pkg_key}",
        )])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="open_buypremium")])

    await cq.message.edit_text(
        f"⬆️ <b>UPGRADE PLAN</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 From     : <b>{PACKAGES.get(current_key, {}).get('label', current_key)}</b>\n"
        f"📦 To       : <b>{pkg['label']}</b> (${pkg['price_usd']})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 You Pay: <b>${diff} USDT</b>\n"
        f"🎬 New Limit: <b>{lim_str}</b>\n"
        f"📅 Duration: <b>{pkg['days']} days</b>\n"
        f"⏳ Expiry: <b>{days_left}d remaining (resets)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Select payment method:",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )
    await cq.answer()


@app.on_callback_query(
    filters.regex(r"^upgpay_(binance|redotpay|trc20|bep20)_(starter|basic|standard|pro|vip|elite)$")
)
async def upgpay_method_selected(_, cq: CallbackQuery):
    parts      = cq.data.split("_")
    method_key = parts[1]
    pkg_key    = parts[2]
    method     = PAYMENT_METHODS[method_key]
    pkg        = PACKAGES[pkg_key]
    user_id    = cq.from_user.id

    prem_doc = await get_premium(user_id)
    if not prem_doc:
        await cq.answer("No active premium. Use /buypremium.", show_alert=True)
        return

    current_key   = prem_doc.get("package", "starter")
    current_price = PACKAGES.get(current_key, {}).get("price_usd", 0)
    diff          = max(0, pkg["price_usd"] - current_price)
    price_str     = f"${diff} USDT"

    proof_sessions[user_id] = {
        "pkg":          pkg_key,
        "method":       method_key,
        "upgrade_from": current_key,
        "pay_amount":   diff,
    }

    if method.get("type") == "qr":
        caption = (
            f"<b>⬆️ UPGRADE PAYMENT — {pkg['label']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Amount to Pay: <b>{price_str}</b>\n"
            f"💳 Method: <b>{method['label']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 {method.get('note', '')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📸 After payment, send your screenshot in chat."
        )
        try:
            await cq.message.delete()
        except Exception:
            pass
        await app.send_photo(
            chat_id=user_id,
            photo=method["qr"],
            caption=caption,
            parse_mode=HTML,
        )
    else:
        text = (
            f"<b>⬆️ UPGRADE — {pkg['label']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Amount   : <b>{price_str}</b>\n"
            f"💳 Method   : <b>{method['label']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Wallet Address:</b>\n<code>{method.get('address', '')}</code>\n\n"
            f"⚠️ {method.get('note', '')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📸 Send screenshot after payment."
        )
        try:
            await cq.message.edit_text(text, parse_mode=HTML)
        except Exception:
            await app.send_message(user_id, text, parse_mode=HTML)

    await app.send_message(
        chat_id=user_id,
        text=(
            "📸 <b>SEND UPGRADE PAYMENT SCREENSHOT</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "Please send your payment screenshot now.\n\n"
            "❌ To cancel: /cancel\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🤖 Unity Group Manager"
        ),
        parse_mode=HTML,
    )
    await cq.answer()
