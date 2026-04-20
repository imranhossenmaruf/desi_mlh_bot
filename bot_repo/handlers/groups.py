import asyncio
from datetime import datetime

from pyrogram import Client, filters, StopPropagation
from pyrogram.enums import MessageEntityType, ChatMemberStatus
from pyrogram.types import (
    Message, ChatMemberUpdated, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from config import HTML, ADMIN_ID, ADMIN_IDS, groups_col, group_settings_col, welcome_col, nightmode_col, antiflood_col, filters_col, app
from helpers import log_event, _auto_del, get_bot_username, admin_filter, bot_api, get_custom_buttons

_URL_ENTITY_TYPES = {MessageEntityType.URL, MessageEntityType.TEXT_LINK}

# ── Bot-added dedup: prevents welcome msg firing twice (new_chat_members + ChatMemberUpdated) ──
_bot_added_dedup: dict[int, float] = {}   # chat_id → monotonic timestamp


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _is_privileged(client: Client, chat_id: int, user_id: int) -> bool:
    """True if user is the bot's admin OR a group admin/owner."""
    if user_id in ADMIN_IDS:
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False


def _msg_has_link(message: Message) -> bool:
    entities = (message.entities or []) + (message.caption_entities or [])
    return any(e.type in _URL_ENTITY_TYPES for e in entities)


def _msg_is_forwarded(message: Message) -> bool:
    return bool(
        message.forward_from
        or message.forward_from_chat
        or message.forward_sender_name
        or message.forward_date
    )


# ── Anti-forward / Anti-link (highest priority, group=-5) ────────────────────

@app.on_message(filters.group, group=-5)
async def anti_forward_link(client: Client, message: Message):
    if not message.from_user:
        return
    if not (_msg_is_forwarded(message) or _msg_has_link(message)):
        return

    user_id = message.from_user.id
    if await _is_privileged(client, message.chat.id, user_id):
        return

    try:
        await message.delete()
    except Exception as e:
        print(f"[ANTI-FL] Delete failed in {message.chat.id}: {e}")
    # Message deleted silently — no warning sent.


# ── Group command guard (group=-4) ────────────────────────────────────────────

@app.on_message(filters.group, group=-4)
async def group_command_guard(client: Client, message: Message):
    """Block all commands in groups for non-privileged users.
    /video → redirect message (auto-del 3 min).
    All other commands → silently deleted.
    """
    text = message.text or message.caption or ""
    if not text.startswith("/"):
        return

    user_id = message.from_user.id if message.from_user else None
    if user_id is None:
        return

    cmd_name = text.split()[0].lstrip("/").split("@")[0].lower()
    privileged = await _is_privileged(client, message.chat.id, user_id)
    print(f"[GUARD] chat={message.chat.id} user={user_id} cmd=/{cmd_name} privileged={privileged}", flush=True)

    if privileged:
        return

    cmd = text.split()[0].lstrip("/").split("@")[0].lower()

    if cmd == "video":
        # ── check group video settings ────────────────────────────────────
        grp_cfg   = await group_settings_col.find_one({"chat_id": message.chat.id}) or {}
        features  = grp_cfg.get("features", {})
        video_on  = features.get("video", True)      # True = allow video
        video_msg = features.get("video_msg", True)  # True = show redirect msg

        if video_on:
            # video চালু → guard ছেড়ে দাও, video.py handler নেবে
            return

        # video বন্ধ
        try:
            await message.delete()
        except Exception:
            pass

        if not video_msg:
            # video_msg বন্ধ → চুপচাপ ডিলিট করো
            raise StopPropagation

        # video_msg চালু → redirect দেখাও
        bot_username = await get_bot_username(client)
        user    = message.from_user
        u_name  = (user.first_name or "User") if user else "User"
        u_id    = user.id if user else 0
        mention = f"<a href='tg://user?id={u_id}'>{u_name}</a>"

        keyboard = [
            [
                InlineKeyboardButton(
                    "🎬 Get Video Now",
                    url=f"https://t.me/{bot_username}?start=video",
                ),
                InlineKeyboardButton(
                    "🔵⃝𝐂𝐎𝐔𝐏𝐋𝐄⃝🔵",
                    url="https://t.me/+PnUkO8waIEcyNDY1",
                ),
            ]
        ]

        custom_kb = await get_custom_buttons(message.chat.id)
        if custom_kb:
            keyboard.extend(custom_kb.inline_keyboard)

        m = await client.send_message(
            message.chat.id,
            f"╔══════════════════════╗\n"
            f"        🎬 𝑫𝑬𝑺𝑰 𝑴𝑳𝑯 𝑽𝑰𝑫𝑬𝑶\n"
            f"╚══════════════════════╝\n\n"
            f"👋 Hey {mention}!\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🚫 <b>Videos are only available</b>\n"
            f"    in <b>Private Chat</b> with the bot!\n\n"
            f"🎬 <b>What you get in private:</b>\n"
            f"   ✅ HD Videos with Spoiler Protection\n"
            f"   ✅ Daily Limits Based on Your Plan\n"
            f"   ✅ Premium = More Videos Per Day\n"
            f"   ✅ Exclusive Premium-Only Content\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💎 <b>Upgrade for more daily videos!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 <b>Tap a button below to proceed:</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            reply_to_message_id=message.id,
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 180))

    else:
        try:
            await message.delete()
        except Exception:
            pass

    raise StopPropagation

_BOT_ID: int = 0


async def _get_bot_id(client: Client) -> int:
    global _BOT_ID
    if not _BOT_ID:
        me = await client.get_me()
        _BOT_ID = me.id
    return _BOT_ID


async def _upsert_group(chat, added_by=None, bot_is_admin: bool = False, can_invite: bool = False):
    added_by_id   = getattr(added_by, "id", None)
    added_by_name = getattr(added_by, "first_name", None) or str(added_by_id)
    # member_count: available on Chat objects for supergroups/channels
    member_count  = getattr(chat, "members_count", None)
    set_data = {
        "chat_id":          chat.id,
        "title":            chat.title or str(chat.id),
        "type":             str(chat.type),
        "bot_is_admin":     bot_is_admin,
        "can_invite_users": can_invite,
        "added_by_id":      added_by_id,
        "added_by_name":    added_by_name,
        "updated_at":       datetime.utcnow(),
    }
    if member_count is not None:
        set_data["member_count"] = member_count
    await groups_col.update_one(
        {"chat_id": chat.id},
        {
            "$set": set_data,
            "$setOnInsert": {"added_at": datetime.utcnow()},
        },
        upsert=True,
    )


async def _remove_group(chat_id: int):
    await groups_col.delete_one({"chat_id": chat_id})


async def _try_add_admin(client: Client, chat_id: int):
    """If bot has can_invite_users, invite the bot's admin to the group."""
    try:
        await client.add_chat_members(chat_id, ADMIN_ID)
        print(f"[GROUPS] Admin {ADMIN_ID} added to {chat_id}")
        await log_event(client,
            f"✅ <b>Admin Added to Group</b>\n"
            f"🆔 Chat: <code>{chat_id}</code>"
        )
    except Exception as e:
        print(f"[GROUPS] Could not add admin to {chat_id}: {e}")


async def _detect_system_group(chat_id: int) -> str | None:
    """Return system group type string if chat_id matches a stored system group."""
    from config import db, settings_col
    try:
        ctrl_doc = await db["control_group_settings"].find_one({"key": "control_group"})
        if ctrl_doc and int(ctrl_doc.get("chat_id", 0)) == chat_id:
            return "control"
        mon_doc = await db["monitor_group_settings"].find_one({"key": "monitor_group"})
        if mon_doc and int(mon_doc.get("chat_id", 0)) == chat_id:
            return "monitor"
        inbox_doc = await settings_col.find_one({"key": "inbox_group"})
        if inbox_doc and int(inbox_doc.get("chat_id", 0)) == chat_id:
            return "inbox"
        log_doc = await settings_col.find_one({"key": "log_channel"})
        if log_doc and int(log_doc.get("chat_id", 0)) == chat_id:
            return "log"
    except Exception as e:
        print(f"[JOIN] _detect_system_group error: {e}")
    return None


_SYSTEM_GROUP_INFO = {
    "control": {
        "label": "🎛️ Control Group",
        "desc": (
            "এই গ্রুপ থেকে সব বট-গ্রুপ পরিচালনা করা হয়।\n"
            "শুধুমাত্র Admin-দের জন্য।"
        ),
        "commands": (
            "/ctrlhelp — এই গ্রুপের সব কমান্ড\n"
            "/groups — পরিচালিত গ্রুপের তালিকা\n"
            "/groupstats — সব গ্রুপের পরিসংখ্যান\n"
            "/sendall [msg] — সব গ্রুপে broadcast\n"
            "/sendto [chat_id] [msg] — নির্দিষ্ট গ্রুপে\n"
            "/protect [gid] forward|links|spam on|off\n"
            "/protections — সব গ্রুপের protection status\n"
            "/kw add|del|list|clear — Keyword auto-reply\n"
            "/taggroup [gid] [msg] — সবাইকে tag\n"
            "/overview [period] — পরিসংখ্যান overview\n"
            "/syscheck — সিস্টেম স্বাস্থ্য পরীক্ষা\n"
            "/broadcast — সব user-এ broadcast\n"
            "/nightmode on|off|status — রাতের মোড"
        ),
        "required_perms": ["delete_messages", "restrict_members", "pin_messages", "send_messages"],
    },
    "monitor": {
        "label": "🕵️ Monitor Group",
        "desc": (
            "এই গ্রুপে সব গ্রুপের activity log আসে।\n"
            "নতুন join, exit, warn, ban সব এখানে দেখাবে।"
        ),
        "commands": (
            "এই গ্রুপে সরাসরি কমান্ড নেই।\n"
            "বট স্বয়ংক্রিয়ভাবে এখানে activity report পাঠাবে:\n"
            "• নতুন user join/leave\n"
            "• Warn / Ban / Mute events\n"
            "• Spam detection alerts\n"
            "• Night mode reports"
        ),
        "required_perms": ["send_messages"],
    },
    "inbox": {
        "label": "📬 Inbox Group",
        "desc": (
            "User-দের DM এখানে forward হয়।\n"
            "এখান থেকে reply করলে সরাসরি user-এর DM-এ যাবে।"
        ),
        "commands": (
            "/chat [user_id] — নির্দিষ্ট user-এর সাথে কথোপকথন শুরু\n"
            "reply করুন — user-এর DM-এ message যাবে\n\n"
            "• User-এর message স্বয়ংক্রিয়ভাবে এখানে আসবে\n"
            "• Admin reply করলে user পাবে\n"
            "• Media (photo/video/file) সব support করে"
        ),
        "required_perms": ["send_messages"],
    },
    "log": {
        "label": "📋 Log Channel",
        "desc": (
            "সব bot event এখানে log হয়।\n"
            "User join, broadcast, ban, system events সব।"
        ),
        "commands": (
            "এই channel-এ সরাসরি কমান্ড নেই।\n"
            "বট স্বয়ংক্রিয়ভাবে এখানে log পাঠাবে:\n"
            "• Bot start/stop events\n"
            "• User join/ban/block events\n"
            "• Broadcast results\n"
            "• System errors & warnings"
        ),
        "required_perms": ["send_messages"],
    },
}


async def _check_bot_permissions(client: Client, chat_id: int, bot_id: int, required: list[str]) -> dict:
    """Check bot permissions. Returns dict with status and missing perms list."""
    result = {"is_admin": False, "missing": [], "privileges": {}}
    try:
        member = await client.get_chat_member(chat_id, bot_id)
        priv   = getattr(member, "privileges", None)
        if not priv:
            result["missing"] = required
            return result
        result["is_admin"] = True
        perm_map = {
            "delete_messages":  getattr(priv, "can_delete_messages", False),
            "restrict_members": getattr(priv, "can_restrict_members", False),
            "pin_messages":     getattr(priv, "can_pin_messages", False),
            "send_messages":    getattr(priv, "can_send_messages", True),
            "invite_users":     getattr(priv, "can_invite_users", False),
        }
        result["privileges"] = perm_map
        for perm in required:
            if not perm_map.get(perm, False):
                result["missing"].append(perm)
    except Exception as e:
        result["error"] = str(e)
    return result


async def _handle_bot_added(client: Client, chat, added_by=None):
    """Bot যোগ হলে simple welcome message পাঠাও + DB-তে group save করো।"""
    import time as _time
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    # ── Dedup: on_new_members AND on_chat_member_updated both fire for supergroups ──
    now = _time.monotonic()
    last = _bot_added_dedup.get(chat.id, 0)
    if now - last < 10:   # same chat within 10 s → skip
        return
    _bot_added_dedup[chat.id] = now

    bot_id = await _get_bot_id(client)

    # Check permissions
    perm_result  = await _check_bot_permissions(
        client, chat.id, bot_id,
        ["delete_messages", "restrict_members", "pin_messages", "send_messages", "invite_users"]
    )
    bot_is_admin = perm_result["is_admin"]
    can_invite   = perm_result["privileges"].get("invite_users", False)

    await _upsert_group(chat, added_by, bot_is_admin, can_invite)

    # ── Auto-detect and store group language ─────────────────────────────────
    try:
        from strings import resolve_lang
        from handlers.cmd_control import set_group_lang
        adder_lang_code = getattr(added_by, "language_code", None)
        detected_lang   = resolve_lang(adder_lang_code)
        await set_group_lang(chat.id, detected_lang)
        print(f"[LANG] Group {chat.id} language auto-set to '{detected_lang}' "
              f"(adder lang_code={adder_lang_code!r})")
    except Exception as _le:
        print(f"[LANG] Could not auto-set group language: {_le}")

    adder_name    = getattr(added_by, "first_name", None) or "Unknown"
    adder_id      = getattr(added_by, "id", None) or 0
    adder_mention = f"<a href='tg://user?id={adder_id}'>{adder_name}</a>"

    # ── Simple welcome message (all groups) — language-aware ─────────────
    try:
        from handlers.cmd_control import get_group_lang as _ggl
        from strings import get_string as _gs
        _grp_lang  = await _ggl(chat.id)
        welcome_text = _gs("bot_added_group", lang=_grp_lang, adder=adder_mention)
    except Exception:
        welcome_text = (
            f"🤖 <b>Bot has been added!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🙏 Thank you {adder_mention} for adding the bot!\n\n"
            f"/start /video /daily /help /lang"
        )

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✓⃝𝐂𝐎𝐔𝐏𝐋𝐄✓⃝",
                url="https://t.me/+EGED6B-57ykzMmY9"
            ),
            InlineKeyboardButton(
                "ADD ME TO GROUP",
                url="https://t.me/desi_mlh_bot?startgroup=true&admin=change_info+delete_messages+restrict_members+invite_users+pin_messages+promote_members+manage_video_chats+manage_chat+post_messages+edit_messages"
            ),
        ]
    ])

    try:
        await client.send_message(chat.id, welcome_text, parse_mode=HTML, reply_markup=buttons)
    except Exception as e:
        print(f"[JOIN] Welcome msg failed for {chat.id}: {e}")

    # Log to log channel (internal)
    missing = [p for p in ["delete_messages", "restrict_members", "pin_messages", "send_messages"]
               if not perm_result["privileges"].get(p, False)]
    await log_event(client,
        f"🤖 <b>Bot Added to Group</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Group:</b> {chat.title or chat.id}\n"
        f"🆔 <b>Chat ID:</b> <code>{chat.id}</code>\n"
        f"👤 <b>Added by:</b> {adder_mention}\n"
        f"👑 <b>Bot is Admin:</b> {'Yes ✅' if bot_is_admin else 'No ❌'}\n"
        f"📨 <b>Missing Perms:</b> {', '.join(missing) if missing else 'None ✅'}"
    )
    print(f"[GROUPS] Added to '{chat.title}' ({chat.id}) admin={bot_is_admin} missing={missing}")


# ── Handler: new_chat_members (works for regular groups & some supergroups) ──

@app.on_message(filters.new_chat_members, group=50)
async def on_new_members(client: Client, message: Message):
    bot_id = await _get_bot_id(client)
    human_count = 0
    for user in message.new_chat_members:
        if user.id == bot_id:
            asyncio.create_task(_handle_bot_added(client, message.chat, message.from_user))
        elif not user.is_bot:
            human_count += 1
    if human_count:
        await groups_col.update_one(
            {"chat_id": message.chat.id},
            {"$inc": {"member_count": human_count}},
        )


# ── Handler: ChatMemberUpdated (supergroups / channels) ──────────────────────

@app.on_chat_member_updated(group=50)
async def on_chat_member_updated(client: Client, update: ChatMemberUpdated):
    bot_id = await _get_bot_id(client)
    if not update.new_chat_member or update.new_chat_member.user.id != bot_id:
        return

    new_status = str(update.new_chat_member.status)
    old_status = str(update.old_chat_member.status) if update.old_chat_member else ""

    # Bot was added / promoted
    if new_status in ("ChatMemberStatus.MEMBER", "ChatMemberStatus.ADMINISTRATOR"):
        if old_status in ("ChatMemberStatus.LEFT", "ChatMemberStatus.BANNED", ""):
            asyncio.create_task(
                _handle_bot_added(client, update.chat, update.from_user)
            )

    # Bot was removed / banned
    elif new_status in ("ChatMemberStatus.LEFT", "ChatMemberStatus.BANNED", "ChatMemberStatus.KICKED"):
        await _remove_group(update.chat.id)
        await log_event(client,
            f"🚪 <b>Bot Removed from Group</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <b>Group:</b> {update.chat.title or update.chat.id}\n"
            f"🆔 <b>Chat ID:</b> <code>{update.chat.id}</code>"
        )
        print(f"[GROUPS] Removed from '{update.chat.title}' ({update.chat.id})")


# ── Handler: left_chat_member (for regular groups) ───────────────────────────

@app.on_message(filters.left_chat_member, group=50)
async def on_left_member(client: Client, message: Message):
    bot_id = await _get_bot_id(client)
    user   = message.left_chat_member
    if not user:
        return
    if user.id == bot_id:
        # Bot was removed from the group
        await _remove_group(message.chat.id)
        await log_event(client,
            f"🚪 <b>Bot Removed from Group</b>\n"
            f"📌 <b>Group:</b> {message.chat.title or message.chat.id}\n"
            f"🆔 <b>Chat ID:</b> <code>{message.chat.id}</code>"
        )
        print(f"[GROUPS] Removed from '{message.chat.title}' ({message.chat.id})")
    elif not user.is_bot:
        # Human member left — decrement stored member count
        await groups_col.update_one(
            {"chat_id": message.chat.id},
            {"$inc": {"member_count": -1}},
        )


# ── Admin command: /groups ────────────────────────────────────────────────────

@app.on_message(filters.command("groups") & admin_filter & filters.private)
async def groups_cmd(client: Client, message: Message):
    from config import db as _db
    docs = await groups_col.find({}).sort("added_at", -1).to_list(length=None)
    using_fallback = False
    if not docs:
        docs = await _db["known_groups"].find({"main_bot": True}).to_list(length=None)
        using_fallback = True

    if not docs:
        await message.reply_text("📭 Bot is not a member of any group yet.", parse_mode=HTML)
        return

    note = "\n<i>(known_groups cache — /syncgroups দিয়ে sync করুন)</i>" if using_fallback else ""
    lines = [f"🤖 <b>Bot Groups ({len(docs)})</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    for d in docs:
        title  = d.get("title", "Unknown")
        cid    = d.get("chat_id", "?")
        status = "👑 Admin" if d.get("bot_is_admin") else "👤 Member"
        lines.append(f"• <b>{title}</b>\n  <code>{cid}</code>  —  {status}")

    if note:
        lines.append(note)
    await message.reply_text("\n".join(lines), parse_mode=HTML)


@app.on_message(filters.command("syncgroups") & admin_filter & filters.private)
async def syncgroups_cmd(client: Client, message: Message):
    """known_groups থেকে bot_groups (groups_col) sync করুন।"""
    from config import db as _db
    wait_msg = await message.reply_text("⏳ গ্রুপ sync হচ্ছে...", parse_mode=HTML)

    synced = 0
    failed = 0
    async for doc in _db["known_groups"].find({"main_bot": True}):
        cid = doc.get("chat_id")
        if not cid:
            continue
        try:
            chat = await client.get_chat(cid)
            await groups_col.update_one(
                {"chat_id": cid},
                {"$set": {
                    "chat_id": cid,
                    "title":   chat.title or str(cid),
                    "type":    str(chat.type),
                    "updated_at": __import__("datetime").datetime.utcnow(),
                },
                 "$setOnInsert": {"added_at": __import__("datetime").datetime.utcnow()}},
                upsert=True,
            )
            synced += 1
        except Exception:
            failed += 1

    try:
        await wait_msg.delete()
    except Exception:
        pass

    await message.reply_text(
        f"✅ <b>Sync সম্পন্ন!</b>\n"
        f"📍 Synced: <b>{synced}</b> গ্রুপ\n"
        f"❌ Failed: <b>{failed}</b> গ্রুপ",
        parse_mode=HTML,
    )


# ── Group settings command: /group ────────────────────────────────────────────

@app.on_message(filters.command("group") & filters.group)
async def group_settings_cmd(client: Client, message: Message):
    if not await _is_privileged(client, message.chat.id, message.from_user.id):
        return

    args = message.command[1:]
    chat_id = message.chat.id

    # Get current settings
    doc = await group_settings_col.find_one({"chat_id": chat_id}) or {}

    if not args:
        # Show current settings and menu
        # Check actual status from respective collections
        welcome_doc = await welcome_col.find_one({"chat_id": chat_id})
        welcome_enabled = welcome_doc.get("enabled", False) if welcome_doc else False

        nightmode_doc = await nightmode_col.find_one({"chat_id": chat_id})
        nightmode_enabled = nightmode_doc.get("enabled", False) if nightmode_doc else False

        antiflood_doc = await antiflood_col.find_one({"chat_id": chat_id})
        antiflood_enabled = antiflood_doc.get("enabled", False) if antiflood_doc else False

        filters_doc = await filters_col.find_one({"chat_id": chat_id})
        filters_enabled = filters_doc.get("enabled", False) if filters_doc else False

        # Group settings features
        video_enabled = doc.get("video", True)  # Default to True
        auto_reaction_enabled = doc.get("auto_reaction", False)
        auto_reply_enabled = doc.get("auto_reply", False)
        auto_approve_enabled = doc.get("auto_approve", True)  # Default to True

        features = {
            "video": ("🎬 Video Command", video_enabled),
            "welcome": ("👋 Welcome Messages", welcome_enabled), 
            "nightmode": ("🌙 Night Mode", nightmode_enabled),
            "antiflood": ("🚫 Anti-Flood", antiflood_enabled),
            "filters": ("🔍 Filters", filters_enabled),
            "auto_reaction": ("😀 Auto Reactions", auto_reaction_enabled),
            "auto_reply": ("💬 Auto Reply", auto_reply_enabled),
            "auto_approve": ("✅ Auto Approve Joins", auto_approve_enabled)
        }

        status_lines = []
        for key, (name, enabled) in features.items():
            status = "✅ ON" if enabled else "❌ OFF"
            status_lines.append(f"{name}: {status}")

        text = (
            f"⚙️ <b>Group Settings for {message.chat.title}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n" +
            "\n".join(status_lines) + "\n\n" +
            "Use buttons below to toggle features or configure:\n\n"
            "<b>Quick Toggle:</b>\n"
            "/group video on/off\n"
            "/group welcome on/off\n"
            "/group nightmode on/off\n"
            "/group antiflood on/off\n"
            "/group filters on/off\n\n"
            "<b>Advanced Setup:</b>\n"
            "/group reaction [emojis] - Set auto reactions\n"
            "/group reply add \"keyword\" \"response\"\n"
            "/group reply remove \"keyword\"\n"
            "/group buttons add \"text\" \"url\"\n"
            "/group approve on/off [log_chat_id]"
        )

        keyboard = [
            [
                InlineKeyboardButton("🎬 Video", callback_data=f"group_toggle_video_{chat_id}"),
                InlineKeyboardButton("👋 Welcome", callback_data=f"group_toggle_welcome_{chat_id}"),
            ],
            [
                InlineKeyboardButton("🌙 Night Mode", callback_data=f"group_toggle_nightmode_{chat_id}"),
                InlineKeyboardButton("🚫 Anti-Flood", callback_data=f"group_toggle_antiflood_{chat_id}"),
            ],
            [
                InlineKeyboardButton("🔍 Filters", callback_data=f"group_toggle_filters_{chat_id}"),
                InlineKeyboardButton("😀 Reactions", callback_data=f"group_toggle_auto_reaction_{chat_id}"),
            ],
            [
                InlineKeyboardButton("💬 Auto Reply", callback_data=f"group_setup_auto_reply_{chat_id}"),
                InlineKeyboardButton("✅ Auto Approve", callback_data=f"group_toggle_auto_approve_{chat_id}"),
            ],
            [
                InlineKeyboardButton("🔘 Custom Buttons", callback_data=f"group_setup_buttons_{chat_id}"),
            ]
        ]

        await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=HTML)
        return

    sub_cmd = args[0].lower()

    if sub_cmd in ["video", "auto_reaction", "auto_reply", "auto_approve"]:
        # These are stored in group_settings_col
        if len(args) < 2:
            current = "ON" if doc.get(sub_cmd, False) else "OFF"
            await message.reply_text(f"Current {sub_cmd} status: {current}\n\nUsage: /group {sub_cmd} on/off", parse_mode=HTML)
            return

        state = args[1].lower()
        if state not in ["on", "off"]:
            await message.reply_text("Use 'on' or 'off'", parse_mode=HTML)
            return

        enabled = state == "on"
        await group_settings_col.update_one(
            {"chat_id": chat_id},
            {"$set": {sub_cmd: enabled}},
            upsert=True
        )

        status = "ENABLED" if enabled else "DISABLED"
        await message.reply_text(f"✅ {sub_cmd.replace('_', ' ').title()} {status}", parse_mode=HTML)

    elif sub_cmd == "welcome":
        if len(args) < 2:
            welcome_doc = await welcome_col.find_one({"chat_id": chat_id})
            current = "ON" if (welcome_doc and welcome_doc.get("enabled", False)) else "OFF"
            await message.reply_text(f"Current welcome status: {current}\n\nUsage: /group welcome on/off", parse_mode=HTML)
            return

        state = args[1].lower()
        if state not in ["on", "off"]:
            await message.reply_text("Use 'on' or 'off'", parse_mode=HTML)
            return

        enabled = state == "on"
        await welcome_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"enabled": enabled}},
            upsert=True
        )

        status = "ENABLED" if enabled else "DISABLED"
        await message.reply_text(f"✅ Welcome Messages {status}", parse_mode=HTML)

    elif sub_cmd == "nightmode":
        if len(args) < 2:
            nightmode_doc = await nightmode_col.find_one({"chat_id": chat_id})
            current = "ON" if (nightmode_doc and nightmode_doc.get("enabled", False)) else "OFF"
            await message.reply_text(f"Current nightmode status: {current}\n\nUsage: /group nightmode on/off", parse_mode=HTML)
            return

        state = args[1].lower()
        if state not in ["on", "off"]:
            await message.reply_text("Use 'on' or 'off'", parse_mode=HTML)
            return

        enabled = state == "on"
        if enabled:
            # Enable with default times if not set
            await nightmode_col.update_one(
                {"chat_id": chat_id},
                {"$set": {"enabled": True, "start_h": 23, "start_m": 0, "end_h": 6, "end_m": 0}},
                upsert=True
            )
        else:
            await nightmode_col.update_one(
                {"chat_id": chat_id},
                {"$set": {"enabled": False}},
                upsert=True
            )

        status = "ENABLED" if enabled else "DISABLED"
        await message.reply_text(f"✅ Night Mode {status}", parse_mode=HTML)

    elif sub_cmd == "antiflood":
        if len(args) < 2:
            antiflood_doc = await antiflood_col.find_one({"chat_id": chat_id})
            current = "ON" if (antiflood_doc and antiflood_doc.get("enabled", False)) else "OFF"
            await message.reply_text(f"Current antiflood status: {current}\n\nUsage: /group antiflood on/off", parse_mode=HTML)
            return

        state = args[1].lower()
        if state not in ["on", "off"]:
            await message.reply_text("Use 'on' or 'off'", parse_mode=HTML)
            return

        enabled = state == "on"
        await antiflood_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"enabled": enabled}},
            upsert=True
        )

        status = "ENABLED" if enabled else "DISABLED"
        await message.reply_text(f"✅ Anti-Flood {status}", parse_mode=HTML)

    elif sub_cmd == "filters":
        if len(args) < 2:
            filters_doc = await filters_col.find_one({"chat_id": chat_id})
            current = "ON" if (filters_doc and filters_doc.get("enabled", False)) else "OFF"
            await message.reply_text(f"Current filters status: {current}\n\nUsage: /group filters on/off", parse_mode=HTML)
            return

        state = args[1].lower()
        if state not in ["on", "off"]:
            await message.reply_text("Use 'on' or 'off'", parse_mode=HTML)
            return

        enabled = state == "on"
        await filters_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"enabled": enabled}},
            upsert=True
        )

        status = "ENABLED" if enabled else "DISABLED"
        await message.reply_text(f"✅ Filters {status}", parse_mode=HTML)

    elif sub_cmd == "reaction":
        if len(args) < 2:
            current = doc.get("reaction_emojis", [])
            await message.reply_text(f"Current reactions: {', '.join(current) if current else 'None'}\n\nUsage: /group reaction emoji1 emoji2 ...", parse_mode=HTML)
            return

        emojis = args[1:]
        # Validate emojis (basic check)
        valid_emojis = [e for e in emojis if len(e.strip()) > 0]
        await group_settings_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"reaction_emojis": valid_emojis, "auto_reaction": True}},
            upsert=True
        )
        await message.reply_text(f"✅ Auto reactions set to: {', '.join(valid_emojis)}", parse_mode=HTML)

    elif sub_cmd == "reply":
        if len(args) < 2:
            await message.reply_text("Usage:\n/group reply add \"keyword\" \"response\"\n/group reply remove \"keyword\"", parse_mode=HTML)
            return

        action = args[1].lower()
        if action == "add":
            if len(args) < 4:
                await message.reply_text("Usage: /group reply add \"keyword\" \"response\"", parse_mode=HTML)
                return
            keyword = args[2].strip('"')
            response = args[3].strip('"')
            replies = doc.get("auto_replies", {})
            replies[keyword] = response
            await group_settings_col.update_one(
                {"chat_id": chat_id},
                {"$set": {"auto_replies": replies, "auto_reply": True}},
                upsert=True
            )
            await message.reply_text(f"✅ Added auto reply for '{keyword}'", parse_mode=HTML)

        elif action == "remove":
            if len(args) < 3:
                await message.reply_text("Usage: /group reply remove \"keyword\"", parse_mode=HTML)
                return
            keyword = args[2].strip('"')
            replies = doc.get("auto_replies", {})
            if keyword in replies:
                del replies[keyword]
                await group_settings_col.update_one(
                    {"chat_id": chat_id},
                    {"$set": {"auto_replies": replies}},
                    upsert=True
                )
                await message.reply_text(f"✅ Removed auto reply for '{keyword}'", parse_mode=HTML)
            else:
                await message.reply_text(f"❌ Keyword '{keyword}' not found", parse_mode=HTML)

    elif sub_cmd == "buttons":
        if len(args) < 2:
            current = doc.get("custom_buttons", [])
            btn_text = "\n".join([f"• {btn['text']} → {btn['url']}" for btn in current]) if current else "None"
            await message.reply_text(f"Current buttons:\n{btn_text}\n\nUsage:\n/group buttons add \"text\" \"url\"\n/group buttons remove \"text\"", parse_mode=HTML)
            return

        action = args[1].lower()
        if action == "add":
            if len(args) < 4:
                await message.reply_text("Usage: /group buttons add \"text\" \"url\"", parse_mode=HTML)
                return
            text = args[2].strip('"')
            url = args[3].strip('"')
            buttons = doc.get("custom_buttons", [])
            buttons.append({"text": text, "url": url})
            await group_settings_col.update_one(
                {"chat_id": chat_id},
                {"$set": {"custom_buttons": buttons}},
                upsert=True
            )
            await message.reply_text(f"✅ Added button '{text}' → {url}", parse_mode=HTML)

        elif action == "remove":
            if len(args) < 3:
                await message.reply_text("Usage: /group buttons remove \"text\"", parse_mode=HTML)
                return
            text = args[2].strip('"')
            buttons = doc.get("custom_buttons", [])
            new_buttons = [btn for btn in buttons if btn['text'] != text]
            if len(new_buttons) < len(buttons):
                await group_settings_col.update_one(
                    {"chat_id": chat_id},
                    {"$set": {"custom_buttons": new_buttons}},
                    upsert=True
                )
                await message.reply_text(f"✅ Removed button '{text}'", parse_mode=HTML)
            else:
                await message.reply_text(f"❌ Button '{text}' not found", parse_mode=HTML)

    elif sub_cmd == "approve":
        if len(args) < 2:
            current = "ON" if doc.get("auto_approve", False) else "OFF"
            log_chat = doc.get("approve_log_chat", "")
            await message.reply_text(f"Auto approve: {current}\nLog chat: {log_chat}\n\nUsage: /group approve on/off [log_chat_id]", parse_mode=HTML)
            return

        state = args[1].lower()
        if state not in ["on", "off"]:
            await message.reply_text("Use 'on' or 'off'", parse_mode=HTML)
            return

        enabled = state == "on"
        update_data = {"auto_approve": enabled}
        if len(args) > 2:
            try:
                log_chat = int(args[2])
                update_data["approve_log_chat"] = log_chat
            except ValueError:
                await message.reply_text("Invalid log chat ID", parse_mode=HTML)
                return

        await group_settings_col.update_one(
            {"chat_id": chat_id},
            {"$set": update_data},
            upsert=True
        )
        status = "ENABLED" if enabled else "DISABLED"
        await message.reply_text(f"✅ Auto approve {status}", parse_mode=HTML)

    else:
        await message.reply_text("Unknown subcommand. Use /group for help.", parse_mode=HTML)


# ── Callback handlers for group settings ──────────────────────────────────────

@app.on_callback_query(filters.regex(r"^group_toggle_(\w+)_(-?\d+)$"))
async def group_toggle_callback(client: Client, cq: CallbackQuery):
    import re
    m = re.match(r"^group_toggle_(\w+)_(-?\d+)$", cq.data)
    feature = m.group(1)
    chat_id = int(m.group(2))

    # Check if user is admin in that group
    if not await _is_privileged(client, chat_id, cq.from_user.id):
        await cq.answer("❌ Only group admins can change settings", show_alert=True)
        return

    # Handle different collections
    if feature in ["video", "auto_reaction", "auto_reply", "auto_approve"]:
        # These are in group_settings_col
        doc = await group_settings_col.find_one({"chat_id": chat_id}) or {}
        current = doc.get(feature, False)
        new_state = not current
        await group_settings_col.update_one(
            {"chat_id": chat_id},
            {"$set": {feature: new_state}},
            upsert=True
        )

    elif feature == "welcome":
        welcome_doc = await welcome_col.find_one({"chat_id": chat_id})
        current = welcome_doc.get("enabled", False) if welcome_doc else False
        new_state = not current
        await welcome_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"enabled": new_state}},
            upsert=True
        )

    elif feature == "nightmode":
        nightmode_doc = await nightmode_col.find_one({"chat_id": chat_id})
        current = nightmode_doc.get("enabled", False) if nightmode_doc else False
        new_state = not current
        if new_state:
            # Enable with default times if not set
            await nightmode_col.update_one(
                {"chat_id": chat_id},
                {"$set": {"enabled": True, "start_h": 23, "start_m": 0, "end_h": 6, "end_m": 0}},
                upsert=True
            )
        else:
            await nightmode_col.update_one(
                {"chat_id": chat_id},
                {"$set": {"enabled": False}},
                upsert=True
            )

    elif feature == "antiflood":
        antiflood_doc = await antiflood_col.find_one({"chat_id": chat_id})
        current = antiflood_doc.get("enabled", False) if antiflood_doc else False
        new_state = not current
        await antiflood_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"enabled": new_state}},
            upsert=True
        )

    elif feature == "filters":
        filters_doc = await filters_col.find_one({"chat_id": chat_id})
        current = filters_doc.get("enabled", False) if filters_doc else False
        new_state = not current
        await filters_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"enabled": new_state}},
            upsert=True
        )

    status = "ENABLED" if new_state else "DISABLED"
    feature_name = feature.replace("_", " ").title()
    await cq.answer(f"✅ {feature_name} {status}")

    # Update the message
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
        await client.send_message(chat_id, f"✅ {feature_name} {status}")
    except:
        pass


@app.on_callback_query(filters.regex(r"^group_setup_(\w+)_(-?\d+)$"))
async def group_setup_callback(client: Client, cq: CallbackQuery):
    import re
    m = re.match(r"^group_setup_(\w+)_(-?\d+)$", cq.data)
    feature = m.group(1)
    chat_id = int(m.group(2))

    if not await _is_privileged(client, chat_id, cq.from_user.id):
        await cq.answer("❌ Only group admins can change settings", show_alert=True)
        return

    if feature == "auto_reply":
        text = (
            "💬 <b>Auto Reply Setup</b>\n\n"
            "Add keyword-response pairs:\n"
            "<code>/group reply add \"hello\" \"Hi there!\"</code>\n\n"
            "Remove keywords:\n"
            "<code>/group reply remove \"hello\"</code>\n\n"
            "Current replies will be shown in /group"
        )
        await cq.message.edit_text(text, parse_mode=HTML)

    elif feature == "buttons":
        text = (
            "🔘 <b>Custom Buttons Setup</b>\n\n"
            "Add buttons to bot messages:\n"
            "<code>/group buttons add \"Visit Site\" \"https://example.com\"</code>\n\n"
            "Remove buttons:\n"
            "<code>/group buttons remove \"Visit Site\"</code>\n\n"
            "Current buttons will be shown in /group"
        )
        await cq.message.edit_text(text, parse_mode=HTML)


# ── Auto reaction handler ─────────────────────────────────────────────────────

@app.on_message(filters.group & filters.text, group=10)
async def auto_reaction_handler(client: Client, message: Message):
    if not message.from_user or message.from_user.is_bot:
        return

    chat_id = message.chat.id
    doc = await group_settings_col.find_one({"chat_id": chat_id})
    if not doc or not doc.get("auto_reaction", False):
        return

    emojis = doc.get("reaction_emojis", [])
    if not emojis:
        return

    # React with all configured emojis
    try:
        from pyrogram.types import ReactionTypeEmoji
        reactions = [ReactionTypeEmoji(emoji=emoji) for emoji in emojis]
        await client.set_reaction(chat_id, message.id, reactions)
    except Exception as e:
        print(f"[AUTO_REACTION] Failed in {chat_id}: {e}")


# ── Auto reply handler ────────────────────────────────────────────────────────

@app.on_message(filters.group & filters.text, group=11)
async def auto_reply_handler(client: Client, message: Message):
    if not message.from_user or message.from_user.is_bot:
        return

    chat_id = message.chat.id
    text = message.text.lower().strip()

    doc = await group_settings_col.find_one({"chat_id": chat_id})
    if not doc or not doc.get("auto_reply", False):
        return

    replies = doc.get("auto_replies", {})
    for keyword, response in replies.items():
        if keyword.lower() in text:
            try:
                await message.reply_text(response, parse_mode=HTML)
            except Exception as e:
                print(f"[AUTO_REPLY] Failed in {chat_id}: {e}")
            break  # Only reply to first matching keyword


# ── Enhanced join request handler ─────────────────────────────────────────────

# (Moved to misc.py to avoid duplication)


# ── /checkgroups — Admin can manually check all system group permissions ──────

@app.on_message(
    (filters.command("checkgroups") & admin_filter & filters.private) |
    (filters.command("checkgroups") & filters.group)
)
async def checkgroups_cmd(client: Client, message: Message):
    print(f"[CHECKGROUPS] triggered by {message.from_user.id} in {message.chat.id}")
    try:
        from config import db, settings_col
        bot_id = await _get_bot_id(client)

        perm_names = {
            "delete_messages":  "Delete Msgs",
            "restrict_members": "Restrict Members",
            "pin_messages":     "Pin Msgs",
            "send_messages":    "Send Msgs",
            "invite_users":     "Invite Users",
        }

        # Gather all system group IDs from MongoDB
        system_groups = {}
        try:
            ctrl_doc = await db["control_group_settings"].find_one({"key": "control_group"})
            if ctrl_doc and ctrl_doc.get("chat_id"):
                system_groups["control"] = int(ctrl_doc["chat_id"])
        except Exception as e:
            print(f"[CHECKGROUPS] ctrl read error: {e}")

        try:
            mon_doc = await db["monitor_group_settings"].find_one({"key": "monitor_group"})
            if mon_doc and mon_doc.get("chat_id"):
                system_groups["monitor"] = int(mon_doc["chat_id"])
        except Exception as e:
            print(f"[CHECKGROUPS] mon read error: {e}")

        try:
            inbox_doc = await settings_col.find_one({"key": "inbox_group"})
            if inbox_doc and inbox_doc.get("chat_id"):
                system_groups["inbox"] = int(inbox_doc["chat_id"])
        except Exception as e:
            print(f"[CHECKGROUPS] inbox read error: {e}")

        try:
            log_doc = await settings_col.find_one({"key": "log_channel"})
            if log_doc and log_doc.get("chat_id"):
                system_groups["log"] = int(log_doc["chat_id"])
        except Exception as e:
            print(f"[CHECKGROUPS] log read error: {e}")

        print(f"[CHECKGROUPS] system_groups found: {system_groups}")

        if not system_groups:
            await message.reply_text("⚠️ কোনো system group MongoDB-তে সেট নেই!\n\n/setcontrolgroup, /setmonitorgroup, /setinboxgroup দিয়ে সেট করুন।")
            return

        lines = ["🔍 <b>System Group Permission Check</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
        all_ok = True

        for gtype, gid in system_groups.items():
            info  = _SYSTEM_GROUP_INFO.get(gtype, {})
            label = info.get("label", gtype.upper())
            req   = info.get("required_perms", ["send_messages"])

            try:
                chat_obj = await client.get_chat(gid)
                title = chat_obj.title or str(gid)
            except Exception as e:
                title = str(gid)
                print(f"[CHECKGROUPS] get_chat({gid}) error: {e}")

            perm_res = await _check_bot_permissions(client, gid, bot_id, req)
            print(f"[CHECKGROUPS] {gtype} perm_res: {perm_res}")

            if not perm_res["is_admin"]:
                status = "❌ Bot is NOT Admin"
                all_ok = False
            elif perm_res["missing"]:
                missing_str = ", ".join(perm_names.get(p, p) for p in perm_res["missing"])
                status  = f"⚠️ Missing: {missing_str}"
                all_ok  = False
            else:
                status = "✅ সব Permission আছে"

            lines.append(
                f"\n{label}\n"
                f"📌 {title}\n"
                f"🆔 <code>{gid}</code>\n"
                f"📊 {status}"
            )

        lines.append("\n━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("✅ সব OK!" if all_ok else "⚠️ সমস্যাযুক্ত group-এ বটকে Admin করুন।")

        await message.reply_text("\n".join(lines), parse_mode=HTML)
        print("[CHECKGROUPS] reply sent OK")

    except Exception as e:
        import traceback
        print(f"[CHECKGROUPS] FATAL ERROR: {e}\n{traceback.format_exc()}")
        try:
            await message.reply_text(f"❌ Error: {e}")
        except Exception:
            pass
