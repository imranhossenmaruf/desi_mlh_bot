"""
strings.py — Complete Multilingual Message System (BN / EN / AR)

Usage:
    from strings import get_string, get_user_lang, set_user_lang, resolve_lang

    lang = await get_user_lang(user_id)
    text = get_string("welcome_start", lang=lang, name="Rahim")
"""

from __future__ import annotations
import asyncio
from typing import Any

# ── Supported languages ───────────────────────────────────────────────────────
SUPPORTED_LANGS: tuple[str, ...] = ("en", "bn", "ar")
DEFAULT_LANG = "en"

# ── Telegram language_code → our lang code ────────────────────────────────────
LANG_MAP: dict[str, str] = {
    "en": "en", "en-us": "en", "en-gb": "en", "en-in": "en", "en-au": "en",
    "bn": "bn", "bn-bd": "bn", "bn-in": "bn",
    "ar": "ar", "ar-sa": "ar", "ar-eg": "ar", "ar-ae": "ar",
    "ar-kw": "ar", "ar-iq": "ar", "ar-jo": "ar",
}

LANG_NAMES = {
    "en": "English 🇬🇧",
    "bn": "বাংলা 🇧🇩",
    "ar": "العربية 🇸🇦",
}

# ── In-memory cache for user languages ────────────────────────────────────────
# { user_id: "en" | "bn" | "ar" }
_user_lang_cache: dict[int, str] = {}


def resolve_lang(code: str | None) -> str:
    """Convert a Telegram language_code to our supported lang code."""
    if not code:
        return DEFAULT_LANG
    normalized = code.lower().replace("_", "-")
    direct = LANG_MAP.get(normalized)
    if direct:
        return direct
    prefix = normalized.split("-")[0]
    return LANG_MAP.get(prefix, DEFAULT_LANG)


async def get_user_lang(user_id: int) -> str:
    """
    Return the saved language for a user.
    Checks in-memory cache first, then MongoDB.
    Falls back to DEFAULT_LANG ('en').
    """
    if user_id in _user_lang_cache:
        return _user_lang_cache[user_id]
    try:
        from config import users_col
        doc = await users_col.find_one({"user_id": user_id}, {"lang": 1})
        lang = (doc or {}).get("lang", DEFAULT_LANG)
        lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
        _user_lang_cache[user_id] = lang
        return lang
    except Exception:
        return DEFAULT_LANG


async def set_user_lang(user_id: int, lang: str) -> bool:
    """Persist a user's language choice to MongoDB and update cache."""
    lang = lang.lower()
    if lang not in SUPPORTED_LANGS:
        return False
    _user_lang_cache[user_id] = lang
    try:
        from config import users_col
        await users_col.update_one(
            {"user_id": user_id},
            {"$set": {"lang": lang}},
            upsert=True,
        )
        return True
    except Exception:
        return False


def get_string(key: str, lang: str = DEFAULT_LANG, **kwargs: Any) -> str:
    """
    Return the translated string for `key` in `lang`.
    Falls back to 'en' then to the key itself.
    Supports f-string style kwargs.
    """
    entry = STRINGS.get(key)
    if not entry:
        return key
    lang = lang if lang in SUPPORTED_LANGS else DEFAULT_LANG
    text = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return text


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE DICTIONARY
# Each key maps to {"en": ..., "bn": ..., "ar": ...}
# ═══════════════════════════════════════════════════════════════════════════════
STRINGS: dict[str, dict[str, str]] = {

    # ── Privacy Notice (one-time) ─────────────────────────────────────────────
    "privacy_notice": {
        "en": (
            "ℹ️ <b>Privacy Notice</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "By using this bot you agree that:\n\n"
            "• Your Telegram ID and usage data are stored.\n"
            "• Messages may be relayed to admins for support.\n"
            "• Data is used solely to provide bot services.\n\n"
            "<i>This notice is shown only once.</i>"
        ),
        "bn": (
            "ℹ️ <b>গোপনীয়তা নোটিশ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "এই বট ব্যবহার করে আপনি সম্মত হচ্ছেন যে:\n\n"
            "• আপনার Telegram ID ও ব্যবহারের তথ্য সংরক্ষিত হবে।\n"
            "• সহায়তার জন্য বার্তা admin-দের কাছে পাঠানো হতে পারে।\n"
            "• তথ্য শুধুমাত্র বট সেবা প্রদানের জন্য ব্যবহৃত হয়।\n\n"
            "<i>এই নোটিশটি শুধুমাত্র একবার দেখানো হবে।</i>"
        ),
        "ar": (
            "ℹ️ <b>إشعار الخصوصية</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "باستخدام هذا البوت، فإنك توافق على:\n\n"
            "• تخزين معرّف Telegram وبيانات الاستخدام الخاصة بك.\n"
            "• قد تُرسَل الرسائل إلى المسؤولين للدعم.\n"
            "• تُستخدم البيانات فقط لتقديم خدمات البوت.\n\n"
            "<i>يُعرض هذا الإشعار مرة واحدة فقط.</i>"
        ),
    },

    # ── Welcome — after force-join ─────────────────────────────────────────────
    "welcome_joined": {
        "en": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "✨🎬  𝗪𝗘𝗟𝗖𝗢𝗠𝗘 🎬✨\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🎉 Congrats <b>{name}</b>! You're officially in! 🎊\n\n"
            "You are now a verified member of our\n"
            "Video Community 🎥\n\n"
            "🔥 To watch videos use:\n"
            "👉 /video\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📜 GROUP RULES\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "✅ Be respectful to all members\n"
            "✅ No spam or self-promotion\n"
            "✅ No illegal content\n"
            "✅ Follow admin instructions\n"
            "⚠️ Rule violation = Instant remove\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        "bn": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "✨🎬  স্বাগতম 🎬✨\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🎉 অভিনন্দন <b>{name}</b>! আপনি যোগ দিয়েছেন! 🎊\n\n"
            "আপনি এখন আমাদের ভিডিও কমিউনিটির\n"
            "যাচাইকৃত সদস্য 🎥\n\n"
            "🔥 ভিডিও দেখতে ব্যবহার করুন:\n"
            "👉 /video\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📜 গ্রুপের নিয়ম\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "✅ সবার সাথে সম্মানের সাথে কথা বলুন\n"
            "✅ স্প্যাম বা প্রচার করবেন না\n"
            "✅ অবৈধ কন্টেন্ট শেয়ার করবেন না\n"
            "✅ Admin-এর নির্দেশ মানুন\n"
            "⚠️ নিয়ম ভাঙলে তাৎক্ষণিক বের করা হবে\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        "ar": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "✨🎬  أهلاً وسهلاً 🎬✨\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🎉 تهانينا <b>{name}</b>! أنت الآن عضو رسمي! 🎊\n\n"
            "أنت الآن عضو معتمد في مجتمع\n"
            "الفيديو لدينا 🎥\n\n"
            "🔥 لمشاهدة الفيديوهات استخدم:\n"
            "👉 /video\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📜 قواعد المجموعة\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "✅ احترم جميع الأعضاء\n"
            "✅ ممنوع الإزعاج والترويج الذاتي\n"
            "✅ ممنوع المحتوى غير القانوني\n"
            "✅ اتبع تعليمات المسؤول\n"
            "⚠️ مخالفة القواعد = إزالة فورية\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
    },

    # ── Welcome — direct /start ───────────────────────────────────────────────
    "welcome_start": {
        "en": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "✨🎬  𝗪𝗘𝗟𝗖𝗢𝗠𝗘 🎬✨\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "👑 Welcome <b>{name}</b>! 👑\n"
            "You are now a member of our Video Community 🎥\n\n"
            "🔥 To watch videos use:\n"
            "👉 /video\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📜 RULES\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "✅ Be respectful\n"
            "✅ No spam\n"
            "✅ No illegal content\n"
            "✅ Follow admin rules\n"
            "⚠️ Rule violation = Instant remove\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        "bn": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "✨🎬  স্বাগতম 🎬✨\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "👑 স্বাগতম <b>{name}</b>! 👑\n"
            "আপনি এখন আমাদের ভিডিও কমিউনিটির সদস্য 🎥\n\n"
            "🔥 ভিডিও দেখতে ব্যবহার করুন:\n"
            "👉 /video\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📜 নিয়মাবলী\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "✅ সম্মানের সাথে কথা বলুন\n"
            "✅ স্প্যাম করবেন না\n"
            "✅ অবৈধ কন্টেন্ট শেয়ার করবেন না\n"
            "✅ Admin-এর নিয়ম মানুন\n"
            "⚠️ নিয়ম ভাঙলে তাৎক্ষণিক বের করা হবে\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        "ar": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "✨🎬  أهلاً وسهلاً 🎬✨\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "👑 أهلاً <b>{name}</b>! 👑\n"
            "أنت الآن عضو في مجتمع الفيديو لدينا 🎥\n\n"
            "🔥 لمشاهدة الفيديوهات استخدم:\n"
            "👉 /video\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📜 القواعد\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "✅ كن محترماً\n"
            "✅ ممنوع الإزعاج\n"
            "✅ ممنوع المحتوى غير القانوني\n"
            "✅ اتبع قواعد المسؤول\n"
            "⚠️ مخالفة القواعد = إزالة فورية\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
    },

    # ── Force-join required ────────────────────────────────────────────────────
    "force_join_required": {
        "en": (
            "📢 <b>JOIN REQUIRED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "You must join all <b>{count}</b> channel(s) below\n"
            "before you can receive videos.\n\n"
            "1️⃣ Join each channel using the buttons\n"
            "2️⃣ Tap ✅ to verify and get your video\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ),
        "bn": (
            "📢 <b>যোগ দেওয়া আবশ্যক</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "ভিডিও পাওয়ার আগে নিচের <b>{count}</b>টি চ্যানেলে যোগ দিতে হবে।\n\n"
            "1️⃣ বাটন দিয়ে প্রতিটি চ্যানেলে যোগ দিন\n"
            "2️⃣ ✅ বাটন চাপ দিয়ে যাচাই করুন\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ),
        "ar": (
            "📢 <b>يجب الانضمام</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "يجب عليك الانضمام إلى <b>{count}</b> قناة أدناه\n"
            "قبل أن تتمكن من استلام الفيديوهات.\n\n"
            "1️⃣ انضم لكل قناة بالأزرار\n"
            "2️⃣ اضغط ✅ للتحقق والحصول على الفيديو\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ),
    },

    # ── Help — user commands ──────────────────────────────────────────────────
    "help_user": {
        "en": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "📋 <b>COMMANDS</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "👤 YOUR COMMANDS:\n"
            "/start  — Register & get started\n"
            "/video  — 🎬 Get a random video\n"
            "/daily  — 📅 Claim daily +5 points\n"
            "/help   — 📋 Show this message\n"
            "/lang   — 🌍 Change language\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "💡 TIP: Use /video every day for new content!\n"
            "Invite friends with /start to earn points.\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        "bn": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "📋 <b>কমান্ড তালিকা</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "👤 আপনার কমান্ড:\n"
            "/start  — নিবন্ধন করুন\n"
            "/video  — 🎬 র‌্যান্ডম ভিডিও পান\n"
            "/daily  — 📅 দৈনিক +৫ পয়েন্ট নিন\n"
            "/help   — 📋 সাহায্য দেখুন\n"
            "/lang   — 🌍 ভাষা পরিবর্তন করুন\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "💡 টিপস: প্রতিদিন /video ব্যবহার করুন!\n"
            "বন্ধুদের invite করে পয়েন্ট অর্জন করুন।\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        "ar": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "📋 <b>قائمة الأوامر</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "👤 أوامرك:\n"
            "/start  — التسجيل والبدء\n"
            "/video  — 🎬 احصل على فيديو عشوائي\n"
            "/daily  — 📅 احصل على +٥ نقاط يومية\n"
            "/help   — 📋 عرض هذه الرسالة\n"
            "/lang   — 🌍 تغيير اللغة\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "💡 نصيحة: استخدم /video كل يوم!\n"
            "ادعُ أصدقاءك لكسب النقاط.\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
    },

    # ── Status / Profile ──────────────────────────────────────────────────────
    "status_profile": {
        "en": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "👤 <b>MY PROFILE</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🆔 ID       : {user_id}\n"
            "📅 Joined   : {joined}\n\n"
            "📊 STATISTICS:\n"
            "💰 Points   : {points}\n"
            "👥 Referrals: {refs}\n"
            "🏅 Rank     : {rank}\n"
            "✨ Status   : {status}\n\n"
            "📹 Videos Today: {vid_today}/{vid_limit}\n"
            "{daily_line}\n\n"
            "🔗 Referral Link:\n{ref_link}\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        "bn": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "👤 <b>আমার প্রোফাইল</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🆔 আইডি      : {user_id}\n"
            "📅 যোগের তারিখ: {joined}\n\n"
            "📊 পরিসংখ্যান:\n"
            "💰 পয়েন্ট   : {points}\n"
            "👥 রেফারেল  : {refs}\n"
            "🏅 র‌্যাংক   : {rank}\n"
            "✨ মর্যাদা  : {status}\n\n"
            "📹 আজকের ভিডিও: {vid_today}/{vid_limit}\n"
            "{daily_line}\n\n"
            "🔗 রেফারেল লিংক:\n{ref_link}\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
        "ar": (
            "━━━━━━━━━━━━━━━━━━━\n"
            "👤 <b>ملفي الشخصي</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🆔 المعرّف    : {user_id}\n"
            "📅 تاريخ الانضمام: {joined}\n\n"
            "📊 الإحصائيات:\n"
            "💰 النقاط    : {points}\n"
            "👥 الإحالات  : {refs}\n"
            "🏅 الرتبة    : {rank}\n"
            "✨ الحالة    : {status}\n\n"
            "📹 فيديوهات اليوم: {vid_today}/{vid_limit}\n"
            "{daily_line}\n\n"
            "🔗 رابط الإحالة:\n{ref_link}\n"
            "━━━━━━━━━━━━━━━━━━━"
        ),
    },

    # ── Daily bonus lines (embedded in status) ────────────────────────────────
    "daily_available": {
        "en": "📅 Daily Bonus: available ✅  →  /daily",
        "bn": "📅 দৈনিক বোনাস: পাওয়া যাবে ✅  →  /daily",
        "ar": "📅 المكافأة اليومية: متاحة ✅  →  /daily",
    },
    "daily_claimed": {
        "en": "📅 Daily Bonus: claimed (next in {hrs}h {mins}m)",
        "bn": "📅 দৈনিক বোনাস: গ্রহণ করা হয়েছে (পরেরটি {hrs}h {mins}m পরে)",
        "ar": "📅 المكافأة اليومية: مستلمة (التالية بعد {hrs}h {mins}m)",
    },

    # ── Video: limit reached ──────────────────────────────────────────────────
    "video_limit_reached": {
        "en": (
            "⚠️ <b>Daily Limit Reached</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📹 You have used all <b>{limit}</b> video requests for today.\n\n"
            "🔄 Resets in: <b>{hrs}h {mins}m</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💎 <b>Upgrade to Premium</b> for more videos every day!\n"
            "Contact 👉 @IH_Maruf"
        ),
        "bn": (
            "⚠️ <b>দৈনিক সীমা শেষ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📹 আজকের জন্য <b>{limit}</b>টি ভিডিও শেষ হয়েছে।\n\n"
            "🔄 রিসেট হবে: <b>{hrs}h {mins}m</b> পরে\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💎 <b>প্রিমিয়ামে আপগ্রেড করুন</b> — আরও বেশি ভিডিও পান!\n"
            "যোগাযোগ করুন 👉 @IH_Maruf"
        ),
        "ar": (
            "⚠️ <b>تم الوصول للحد اليومي</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📹 لقد استخدمت جميع <b>{limit}</b> طلبات فيديو لهذا اليوم.\n\n"
            "🔄 يُعاد ضبطه خلال: <b>{hrs}h {mins}m</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "💎 <b>ترقية إلى Premium</b> للحصول على المزيد!\n"
            "تواصل مع 👉 @IH_Maruf"
        ),
    },

    # ── Video: no videos in library ───────────────────────────────────────────
    "video_no_videos": {
        "en": (
            "📭 <b>No Videos Available</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "There are no videos in the library yet.\n\n"
            "📩 Please contact the admin to add videos."
        ),
        "bn": (
            "📭 <b>কোনো ভিডিও নেই</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "এখনো লাইব্রেরিতে কোনো ভিডিও নেই।\n\n"
            "📩 Admin-কে ভিডিও যোগ করতে বলুন।"
        ),
        "ar": (
            "📭 <b>لا توجد فيديوهات</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "لا توجد فيديوهات في المكتبة حتى الآن.\n\n"
            "📩 يرجى التواصل مع المسؤول لإضافة الفيديوهات."
        ),
    },

    # ── Video: all watched ────────────────────────────────────────────────────
    "video_all_watched": {
        "en": (
            "🎬 <b>You've Watched Everything!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "You have already seen all available videos\n"
            "within the last 7 days. 🙌\n\n"
            "🔄 New videos will be available soon.\n"
            "Try again later or contact the admin."
        ),
        "bn": (
            "🎬 <b>সব ভিডিও দেখা হয়ে গেছে!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "গত ৭ দিনে পাওয়া সব ভিডিও আপনি দেখে ফেলেছেন। 🙌\n\n"
            "🔄 নতুন ভিডিও শীঘ্রই আসবে।\n"
            "পরে আবার চেষ্টা করুন বা admin-এর সাথে যোগাযোগ করুন।"
        ),
        "ar": (
            "🎬 <b>لقد شاهدت كل شيء!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "لقد شاهدت جميع الفيديوهات المتاحة\n"
            "خلال الأيام السبعة الماضية. 🙌\n\n"
            "🔄 ستتوفر فيديوهات جديدة قريباً.\n"
            "حاول مرة أخرى لاحقاً أو تواصل مع المسؤول."
        ),
    },

    # ── Video: user banned ────────────────────────────────────────────────────
    "video_banned": {
        "en": (
            "🚫 <b>Access Restricted</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your access to this bot has been\n"
            "suspended by the admin."
        ),
        "bn": (
            "🚫 <b>প্রবেশাধিকার সীমিত</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Admin কর্তৃক এই বটে আপনার প্রবেশাধিকার\n"
            "স্থগিত করা হয়েছে।"
        ),
        "ar": (
            "🚫 <b>الوصول مقيّد</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "تم تعليق وصولك إلى هذا البوت\n"
            "من قِبَل المسؤول."
        ),
    },

    # ── /lang command ──────────────────────────────────────────────────────────
    "lang_current": {
        "en": "🌍 Your current language: <b>{lang_name}</b>\n\nChoose a new language:",
        "bn": "🌍 আপনার বর্তমান ভাষা: <b>{lang_name}</b>\n\nনতুন ভাষা বেছে নিন:",
        "ar": "🌍 لغتك الحالية: <b>{lang_name}</b>\n\nاختر لغة جديدة:",
    },
    "lang_set_success": {
        "en": "✅ Language changed to <b>English 🇬🇧</b>",
        "bn": "✅ ভাষা পরিবর্তন হয়েছে: <b>বাংলা 🇧🇩</b>",
        "ar": "✅ تم تغيير اللغة إلى <b>العربية 🇸🇦</b>",
    },

    # ── Referral notification ─────────────────────────────────────────────────
    "referral_notif": {
        "en": (
            "🎉 <b>New Referral Joined!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Congratulations! Someone just joined using your link.\n\n"
            "💰 You earned: <b>+{pts} Points</b>\n"
            "⭐ Current Balance: <b>{total}</b>\n\n"
            "Keep sharing to earn more! 🚀"
        ),
        "bn": (
            "🎉 <b>নতুন রেফারেল যোগ দিয়েছে!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "অভিনন্দন! কেউ আপনার লিংক দিয়ে যোগ দিয়েছে।\n\n"
            "💰 আপনি পেলেন: <b>+{pts} পয়েন্ট</b>\n"
            "⭐ বর্তমান ব্যালেন্স: <b>{total}</b>\n\n"
            "আরও শেয়ার করে আরও উপার্জন করুন! 🚀"
        ),
        "ar": (
            "🎉 <b>انضم إحالة جديدة!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "تهانينا! انضم شخص ما باستخدام رابطك.\n\n"
            "💰 حصلت على: <b>+{pts} نقطة</b>\n"
            "⭐ الرصيد الحالي: <b>{total}</b>\n\n"
            "واصل المشاركة لكسب المزيد! 🚀"
        ),
    },

    # ── Command disabled ───────────────────────────────────────────────────────
    "cmd_disabled": {
        "en": "🚫 This command is currently disabled.",
        "bn": "🚫 এই কমান্ডটি এই মুহূর্তে বন্ধ আছে।",
        "ar": "🚫 هذا الأمر معطل حالياً.",
    },

    # ── Broadcast translation header ──────────────────────────────────────────
    "broadcast_header": {
        "en": "📢 <b>Announcement</b>\n━━━━━━━━━━━━━━━━━━━━━━\n{text}",
        "bn": "📢 <b>বিজ্ঞপ্তি</b>\n━━━━━━━━━━━━━━━━━━━━━━\n{text}",
        "ar": "📢 <b>إعلان</b>\n━━━━━━━━━━━━━━━━━━━━━━\n{text}",
    },

    # ── Daily bonus ───────────────────────────────────────────────────────────
    "daily_already_claimed": {
        "en": (
            "⏳ <b>Already Claimed Today</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📅 Daily bonus already collected.\n\n"
            "🕐 Next claim in: <b>{hrs}h {mins}m</b>"
        ),
        "bn": (
            "⏳ <b>আজকের বোনাস নেওয়া হয়েছে</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📅 দৈনিক বোনাস ইতিমধ্যে সংগ্রহ করা হয়েছে।\n\n"
            "🕐 পরের বার: <b>{hrs}h {mins}m</b> পরে"
        ),
        "ar": (
            "⏳ <b>تم الاستلام اليوم بالفعل</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📅 تم استلام المكافأة اليومية بالفعل.\n\n"
            "🕐 الاستلام التالي بعد: <b>{hrs}h {mins}m</b>"
        ),
    },
    "daily_success": {
        "en": (
            "🎉 <b>Daily Bonus Claimed!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📅 Check-in Reward:  <b>+5 Points</b>\n"
            "💰 New Balance:  <b>{total} Points</b>\n"
            "🏅 Rank:  <b>{rank}</b>\n"
            "✨ Status:  <b>{status}</b>\n\n"
            "🔄 Come back in 24 hours!"
        ),
        "bn": (
            "🎉 <b>দৈনিক বোনাস পেয়েছেন!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📅 চেক-ইন পুরস্কার:  <b>+৫ পয়েন্ট</b>\n"
            "💰 নতুন ব্যালেন্স:  <b>{total} পয়েন্ট</b>\n"
            "🏅 র‌্যাংক:  <b>{rank}</b>\n"
            "✨ মর্যাদা:  <b>{status}</b>\n\n"
            "🔄 ২৪ ঘণ্টা পরে আবার আসুন!"
        ),
        "ar": (
            "🎉 <b>تم استلام المكافأة اليومية!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📅 مكافأة تسجيل الحضور:  <b>+٥ نقاط</b>\n"
            "💰 الرصيد الجديد:  <b>{total} نقطة</b>\n"
            "🏅 الرتبة:  <b>{rank}</b>\n"
            "✨ الحالة:  <b>{status}</b>\n\n"
            "🔄 عُد بعد ٢٤ ساعة!"
        ),
    },

    # ── Bot added to group ────────────────────────────────────────────────────
    "bot_added_group": {
        "en": (
            "🤖 <b>Bot has been added!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🙏 Thank you {adder}, for adding the bot!\n\n"
            "📋 <b>User Commands:</b>\n"
            "/start — Start the bot\n"
            "/video — Get a video\n"
            "/daily — Daily reward\n"
            "/help  — Get help\n"
            "/lang  — 🌍 Change language"
        ),
        "bn": (
            "🤖 <b>বট যুক্ত হয়েছে!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🙏 ধন্যবাদ {adder}, বটটি যুক্ত করার জন্য!\n\n"
            "📋 <b>ব্যবহারকারীদের কমান্ড:</b>\n"
            "/start — বট শুরু করুন\n"
            "/video — ভিডিও পান\n"
            "/daily — দৈনিক পুরস্কার পান\n"
            "/help  — সাহায্য পান\n"
            "/lang  — 🌍 ভাষা পরিবর্তন করুন"
        ),
        "ar": (
            "🤖 <b>تمت إضافة البوت!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🙏 شكراً {adder} على إضافة البوت!\n\n"
            "📋 <b>أوامر المستخدمين:</b>\n"
            "/start — ابدأ البوت\n"
            "/video — احصل على فيديو\n"
            "/daily — مكافأة يومية\n"
            "/help  — احصل على المساعدة\n"
            "/lang  — 🌍 تغيير اللغة"
        ),
    },

    # ── Inline video ──────────────────────────────────────────────────────────
    "inline_video_title": {
        "en": "🎬 Video #{vid_id}",
        "bn": "🎬 ভিডিও #{vid_id}",
        "ar": "🎬 فيديو #{vid_id}",
    },

    # ── Group video — direct send ──────────────────────────────────────────────
    "video_group_limit_reached": {
        "en": (
            "⚠️ {mention}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📹 Daily limit reached.\n"
            "🔄 Resets in <b>{hrs}h {mins}m</b>.\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ),
        "bn": (
            "⚠️ {mention}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📹 আজকের <b>daily limit</b> শেষ।\n"
            "🔄 <b>{hrs}h {mins}m</b> পরে reset হবে।\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ),
        "ar": (
            "⚠️ {mention}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📹 تم استنفاد الحد اليومي للفيديو.\n"
            "🔄 يُعاد تعيينه خلال <b>{hrs}h {mins}m</b>.\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ),
    },
    "video_group_caption": {
        "en": (
            "🎬 Video for {mention}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "{usage_line}\n"
            "⏳ Deletes in 25 minutes.\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ),
        "bn": (
            "🎬 {mention}-এর জন্য ভিডিও\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "{usage_line}\n"
            "⏳ ভিডিওটি ২৫ মিনিটে মুছে যাবে।\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ),
        "ar": (
            "🎬 فيديو لـ {mention}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "{usage_line}\n"
            "⏳ يُحذف خلال ٢٥ دقيقة.\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        ),
    },
}
