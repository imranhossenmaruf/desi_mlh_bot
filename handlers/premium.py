from datetime import datetime, timedelta, timezone

from pyrogram import filters
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import (
    HTML, ADMIN_ID, PACKAGES, PAYMENT_METHODS,
    app, users_col, premium_col, proof_sessions,
)
from helpers import get_bot_username, log_event


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def get_premium(user_id: int) -> dict | None:
    doc = await premium_col.find_one({"user_id": user_id})
    if not doc:
        return None
    if doc["expires_at"] < datetime.now(timezone.utc):
        await premium_col.delete_one({"user_id": user_id})
        return None
    return doc


async def get_user_video_limit(user_id: int) -> int:
    from config import DAILY_VIDEO_LIMIT
    prem = await get_premium(user_id)
    if prem:
        return prem["video_limit"]
    user = await users_col.find_one({"user_id": user_id})
    if user and user.get("custom_limit"):
        return user["custom_limit"]
    return DAILY_VIDEO_LIMIT


def _packages_keyboard():
    rows = []
    for key, pkg in PACKAGES.items():
        rows.append([
            InlineKeyboardButton(
                f"{pkg['label']}  •  {pkg['price']}",
                callback_data=f"pkg_{key}",
            )
        ])
    rows.append([InlineKeyboardButton("❌ বাতিল", callback_data="pkg_cancel")])
    return InlineKeyboardMarkup(rows)


def _payment_keyboard(pkg_key: str):
    rows = []
    for method_key, method in PAYMENT_METHODS.items():
        rows.append([
            InlineKeyboardButton(
                method["label"],
                callback_data=f"pay_{method_key}_{pkg_key}",
            )
        ])
    rows.append([InlineKeyboardButton("🔙 ফিরে যান", callback_data="pkg_back")])
    return InlineKeyboardMarkup(rows)


def _proof_keyboard(pkg_key: str, method_key: str):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "📸 Payment Proof পাঠান",
            callback_data=f"proof_{pkg_key}_{method_key}",
        ),
        InlineKeyboardButton("❌ বাতিল", callback_data="pkg_cancel"),
    ]])


# ─────────────────────────────────────────────────────────────────────────────
#  /buypremium  — Package list
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("buypremium"))
async def buypremium_cmd(_, message: Message):
    prem = await get_premium(message.from_user.id)
    if prem:
        remaining = (prem["expires_at"] - datetime.now(timezone.utc)).days
        pkg = PACKAGES.get(prem["package"], {})
        await message.reply_text(
            f"<b>💎 আপনি ইতিমধ্যে Premium সদস্য!</b>\n\n"
            f"📦 প্যাকেজ: <b>{pkg.get('label', prem['package'])}</b>\n"
            f"📅 মেয়াদ শেষ: <b>{prem['expires_at'].strftime('%d %b %Y')}</b>\n"
            f"⏳ বাকি: <b>{remaining} দিন</b>\n"
            f"🎬 ভিডিও লিমিট: <b>{prem['video_limit']}/দিন</b>",
            parse_mode=HTML,
        )
        return

    text = (
        "<b>💎 Premium প্যাকেজ বেছে নিন</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
    )
    for pkg in PACKAGES.values():
        text += (
            f"{pkg['label']}  •  <b>{pkg['price']}</b>\n"
            f"   └ {pkg['desc']}\n\n"
        )
    text += "━━━━━━━━━━━━━━━━━━━\nএকটি প্যাকেজ বেছে নিন 👇"

    await message.reply_text(text, parse_mode=HTML, reply_markup=_packages_keyboard())


# ─────────────────────────────────────────────────────────────────────────────
#  /mypremium — Check premium status
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("mypremium"))
async def mypremium_cmd(_, message: Message):
    prem = await get_premium(message.from_user.id)
    if not prem:
        bot_username = await get_bot_username()
        await message.reply_text(
            "❌ আপনি এখনো Premium সদস্য নন।\n\n"
            "Premium নিতে /buypremium লিখুন।",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💎 Buy Premium ✨", callback_data="open_buypremium"),
            ]]),
        )
        return

    remaining = (prem["expires_at"] - datetime.now(timezone.utc)).days
    pkg = PACKAGES.get(prem["package"], {})
    await message.reply_text(
        f"<b>✅ Premium সদস্যতার তথ্য</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 প্যাকেজ: <b>{pkg.get('label', prem['package'])}</b>\n"
        f"📅 শুরু হয়েছে: <b>{prem['started_at'].strftime('%d %b %Y')}</b>\n"
        f"📅 শেষ হবে: <b>{prem['expires_at'].strftime('%d %b %Y')}</b>\n"
        f"⏳ বাকি আছে: <b>{remaining} দিন</b>\n"
        f"🎬 ভিডিও লিমিট: <b>{prem['video_limit']}/দিন</b>",
        parse_mode=HTML,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Package selection callback
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^pkg_(bronze|silver|gold)$"))
async def pkg_selected(_, cq: CallbackQuery):
    pkg_key = cq.data.split("_", 1)[1]
    pkg = PACKAGES[pkg_key]

    text = (
        f"<b>{pkg['label']} — {pkg['price']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📅 মেয়াদ: <b>{pkg['days']} দিন</b>\n"
        f"🎬 ভিডিও: <b>{pkg['video_limit'] if pkg['video_limit'] < 999 else 'Unlimited'}/দিন</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n\n"
        f"💳 পেমেন্ট পদ্ধতি বেছে নিন 👇"
    )
    await cq.message.edit_text(text, parse_mode=HTML, reply_markup=_payment_keyboard(pkg_key))


@app.on_callback_query(filters.regex("^pkg_cancel$"))
async def pkg_cancel(_, cq: CallbackQuery):
    proof_sessions.pop(cq.from_user.id, None)
    await cq.message.delete()
    await cq.answer("বাতিল করা হয়েছে।")


@app.on_callback_query(filters.regex("^pkg_back$"))
async def pkg_back(_, cq: CallbackQuery):
    text = (
        "<b>💎 Premium প্যাকেজ বেছে নিন</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n\n"
    )
    for pkg in PACKAGES.values():
        text += (
            f"{pkg['label']}  •  <b>{pkg['price']}</b>\n"
            f"   └ {pkg['desc']}\n\n"
        )
    text += "━━━━━━━━━━━━━━━━━━━\nএকটি প্যাকেজ বেছে নিন 👇"
    await cq.message.edit_text(text, parse_mode=HTML, reply_markup=_packages_keyboard())


@app.on_callback_query(filters.regex("^open_buypremium$"))
async def open_buypremium(_, cq: CallbackQuery):
    await cq.message.delete()
    await cq.answer()
    fake = cq.message
    fake.from_user = cq.from_user
    await buypremium_cmd(_, cq.message)


# ─────────────────────────────────────────────────────────────────────────────
#  Payment method callback — send QR code
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^pay_(binance|redotpay)_(bronze|silver|gold)$"))
async def pay_method_selected(_, cq: CallbackQuery):
    parts       = cq.data.split("_")
    method_key  = parts[1]
    pkg_key     = parts[2]
    method      = PAYMENT_METHODS[method_key]
    pkg         = PACKAGES[pkg_key]

    if method_key == "binance":
        caption = (
            f"<b>💛 Binance Pay দিয়ে পেমেন্ট করুন</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📦 প্যাকেজ: <b>{pkg['label']}</b>\n"
            f"💵 পরিমাণ: <b>{pkg['price']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Binance নাম: <code>{method['name']}</code>\n\n"
            f"📱 QR কোড স্ক্যান করুন অথবা নামটি Binance অ্যাপে সার্চ করুন।\n\n"
            f"✅ পেমেন্টের পর নিচের বাটনে ক্লিক করে screenshot পাঠান।"
        )
    else:
        caption = (
            f"<b>🔴 RedotPay দিয়ে পেমেন্ট করুন</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📦 প্যাকেজ: <b>{pkg['label']}</b>\n"
            f"💵 পরিমাণ: <b>{pkg['price']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"🆔 RedotPay ID: <code>{method['id']}</code>\n\n"
            f"📱 QR কোড স্ক্যান করুন অথবা RedotPay অ্যাপে ID দিয়ে পাঠান।\n\n"
            f"✅ পেমেন্টের পর নিচের বাটনে ক্লিক করে screenshot পাঠান।"
        )

    await cq.message.delete()
    await app.send_photo(
        chat_id=cq.from_user.id,
        photo=method["qr"],
        caption=caption,
        parse_mode=HTML,
        reply_markup=_proof_keyboard(pkg_key, method_key),
    )
    await cq.answer()


# ─────────────────────────────────────────────────────────────────────────────
#  "Send Proof" button — ask user for screenshot
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^proof_(bronze|silver|gold)_(binance|redotpay)$"))
async def proof_btn(_, cq: CallbackQuery):
    parts     = cq.data.split("_")
    pkg_key   = parts[1]
    method_key = parts[2]
    user_id   = cq.from_user.id

    proof_sessions[user_id] = {"pkg": pkg_key, "method": method_key}

    await cq.answer("এখন screenshot পাঠান।")
    await app.send_message(
        chat_id=user_id,
        text=(
            "📸 <b>Payment screenshot পাঠান</b>\n\n"
            "এখনই পেমেন্টের screenshot টি এই চ্যাটে পাঠান।\n"
            "Admin যাচাই করার পর আপনার Premium সক্রিয় হবে।\n\n"
            "বাতিল করতে /cancel লিখুন।"
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

    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    username_str = f"@{user.username}" if user.username else "নেই"

    caption = (
        f"<b>💳 নতুন পেমেন্ট প্রুফ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"👤 নাম: <b>{name}</b>\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"📛 Username: {username_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 প্যাকেজ: <b>{pkg['label']}</b> ({pkg['price']})\n"
        f"📅 মেয়াদ: <b>{pkg['days']} দিন</b>\n"
        f"💳 পেমেন্ট: <b>{method['label']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

    approve_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Approve",
            callback_data=f"prem_approve_{user_id}_{pkg_key}",
        ),
        InlineKeyboardButton(
            "❌ Reject",
            callback_data=f"prem_reject_{user_id}",
        ),
    ]])

    await app.send_photo(
        chat_id=ADMIN_ID,
        photo=message.photo.file_id,
        caption=caption,
        parse_mode=HTML,
        reply_markup=approve_kb,
    )

    await message.reply_text(
        "✅ <b>প্রুফ পাঠানো হয়েছে!</b>\n\n"
        "Admin যাচাই করার পর আপনাকে notify করা হবে।\n"
        "সাধারণত ১-২৪ ঘণ্টার মধ্যে Activate হয়।",
        parse_mode=HTML,
    )
    await log_event(f"💳 Payment proof from {name} ({user_id}) — {pkg['label']} via {method['label']}")


# ─────────────────────────────────────────────────────────────────────────────
#  /cancel during proof session
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("cancel"))
async def cancel_proof(_, message: Message):
    if proof_sessions.pop(message.from_user.id, None):
        await message.reply_text("❌ বাতিল করা হয়েছে।")


# ─────────────────────────────────────────────────────────────────────────────
#  Admin: Approve Premium
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^prem_approve_(\d+)_(bronze|silver|gold)$"))
async def prem_approve(_, cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer("⛔ শুধু Admin পারবেন।", show_alert=True)
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
            "user_id":    uid,
            "package":    pkg_key,
            "video_limit": pkg["video_limit"],
            "started_at": now,
            "expires_at": expires,
        }},
        upsert=True,
    )

    try:
        await app.send_message(
            chat_id=uid,
            text=(
                f"🎉 <b>অভিনন্দন! Premium Activated!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"📦 প্যাকেজ: <b>{pkg['label']}</b>\n"
                f"🎬 ভিডিও লিমিট: <b>{pkg['video_limit'] if pkg['video_limit'] < 999 else 'Unlimited'}/দিন</b>\n"
                f"📅 মেয়াদ: <b>{pkg['days']} দিন</b>\n"
                f"📅 শেষ হবে: <b>{expires.strftime('%d %b %Y')}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"ধন্যবাদ আমাদের সাথে থাকার জন্য! 💖"
            ),
            parse_mode=HTML,
        )
    except Exception:
        pass

    await cq.message.edit_caption(
        cq.message.caption + f"\n\n✅ <b>Approved by Admin</b> — {now.strftime('%d %b %Y %H:%M')} UTC",
        parse_mode=HTML,
    )
    await cq.answer("✅ Premium Activate করা হয়েছে!")
    await log_event(f"✅ Premium approved: uid={uid} pkg={pkg_key}")


# ─────────────────────────────────────────────────────────────────────────────
#  Admin: Reject Premium
# ─────────────────────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^prem_reject_(\d+)$"))
async def prem_reject(_, cq: CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        await cq.answer("⛔ শুধু Admin পারবেন।", show_alert=True)
        return

    uid = int(cq.data.split("_")[2])

    try:
        await app.send_message(
            chat_id=uid,
            text=(
                "❌ <b>Payment Proof গ্রহণ করা হয়নি।</b>\n\n"
                "পেমেন্টের সঠিক screenshot পুনরায় পাঠান অথবা\n"
                "Admin-এর সাথে যোগাযোগ করুন: @IH_Maruf"
            ),
            parse_mode=HTML,
        )
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    await cq.message.edit_caption(
        cq.message.caption + f"\n\n❌ <b>Rejected by Admin</b> — {now.strftime('%d %b %Y %H:%M')} UTC",
        parse_mode=HTML,
    )
    await cq.answer("❌ Reject করা হয়েছে।")
    await log_event(f"❌ Premium rejected: uid={uid}")


# ─────────────────────────────────────────────────────────────────────────────
#  Admin: /premiumlist  — list all active premium users
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("premiumlist"))
async def premiumlist_cmd(_, message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    now  = datetime.now(timezone.utc)
    docs = await premium_col.find({"expires_at": {"$gt": now}}).to_list(length=200)

    if not docs:
        await message.reply_text("📭 কোনো Active Premium সদস্য নেই।")
        return

    lines = [f"<b>💎 Active Premium সদস্য ({len(docs)} জন)</b>\n━━━━━━━━━━━━━━━━━━━\n"]
    for i, doc in enumerate(docs, 1):
        pkg     = PACKAGES.get(doc["package"], {})
        rem     = (doc["expires_at"] - now).days
        lines.append(
            f"{i}. <code>{doc['user_id']}</code> — {pkg.get('label', doc['package'])} "
            f"(বাকি {rem} দিন)"
        )

    await message.reply_text("\n".join(lines), parse_mode=HTML)


# ─────────────────────────────────────────────────────────────────────────────
#  Admin: /revokepremium <user_id>
# ─────────────────────────────────────────────────────────────────────────────

@app.on_message(filters.private & filters.command("revokepremium"))
async def revokepremium_cmd(_, message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()
    if len(parts) < 2:
        await message.reply_text("⚠️ ব্যবহার: /revokepremium &lt;user_id&gt;", parse_mode=HTML)
        return

    try:
        uid = int(parts[1])
    except ValueError:
        await message.reply_text("❌ সঠিক User ID দিন।")
        return

    res = await premium_col.delete_one({"user_id": uid})
    if res.deleted_count:
        await message.reply_text(f"✅ <code>{uid}</code>-এর Premium বাতিল করা হয়েছে।", parse_mode=HTML)
        try:
            await app.send_message(uid, "⚠️ আপনার Premium সদস্যতা বাতিল করা হয়েছে।")
        except Exception:
            pass
    else:
        await message.reply_text(f"❌ <code>{uid}</code> Premium সদস্য নন।", parse_mode=HTML)
