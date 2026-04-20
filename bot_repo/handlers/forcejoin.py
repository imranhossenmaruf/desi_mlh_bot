import asyncio

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    HTML, ADMIN_ID, settings_col, fj_sessions, app,
)
from helpers import log_event, admin_filter, _clone_config_ctx, BOT_TOKEN


# ══════════════════════════════════════════════════════════════════════════════
# Low-level Bot API helper (for membership checks when Pyrogram can't access)
# ══════════════════════════════════════════════════════════════════════════════

def _fj_token(client) -> str:
    cfg = getattr(client, "_clone_config", None) or _clone_config_ctx.get()
    if cfg and cfg.get("token"):
        return cfg["token"]
    return BOT_TOKEN


async def _bot_api_check_member(client: Client, chat_id, user_id: int):
    """Check membership via Bot API getChatMember.

    Returns:
        True  — user is a member / admin / owner / restricted
        False — user is NOT in the chat (left or kicked)
        None  — bot cannot determine (not admin, chat inaccessible, etc.)
    """
    token = _fj_token(client)
    url   = f"https://api.telegram.org/bot{token}/getChatMember"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"chat_id": chat_id, "user_id": user_id}) as resp:
                data = await resp.json()
        if not data.get("ok"):
            desc = data.get("description", "")
            print(f"[FJ] getChatMember API error for {chat_id}: {desc}")
            return None
        status = data.get("result", {}).get("status", "")
        return status in ("member", "administrator", "creator", "restricted")
    except Exception as e:
        print(f"[FJ] getChatMember API exception for {chat_id}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers — per-bot namespacing
# ══════════════════════════════════════════════════════════════════════════════

def _fj_key(client=None) -> str:
    """Return the settings_col key for this bot's force_join config.
    Main bot → 'force_join'
    Clone bot → 'force_join_<token>'
    """
    if client is not None:
        cfg = getattr(client, "_clone_config", None) or _clone_config_ctx.get()
        if cfg:
            tok = cfg.get("token", "")
            if tok:
                return f"force_join_{tok}"
    return "force_join"


async def _fj_doc(client=None) -> dict:
    key = _fj_key(client)
    doc = await settings_col.find_one({"key": key})
    return doc or {"key": key, "enabled": False, "channels": []}


async def _fj_save(data: dict, client=None):
    """Upsert the force_join doc for this bot."""
    key = _fj_key(client)
    await settings_col.update_one(
        {"key": key},
        {"$set": {**data, "key": key}},
        upsert=True,
    )


async def get_force_join(client=None) -> dict:
    return await _fj_doc(client)


async def get_fj_channels(client=None) -> list:
    doc = await _fj_doc(client)
    return doc.get("channels", [])


async def get_not_joined(client: Client, user_id: int, channels: list) -> list:
    """Return channels the user has NOT fully joined.

    Check order:
      1. Pyrogram get_chat_member  (fast; needs bot to be admin in the chat)
      2. Bot API getChatMember     (fallback when Pyrogram raises non-participant errors)

    Invite links stored as chat_id are resolved to numeric IDs first.

    Only UserNotParticipant / left / kicked → "not joined".
    If neither method can determine membership (bot not admin, chat inaccessible),
    the channel is skipped to avoid permanently locking out legitimate users.
    Admins must ensure the bot is an admin in every force-join target.
    """
    from pyrogram import enums
    from pyrogram.errors import (
        UserNotParticipant, ChannelPrivate, PeerIdInvalid,
        ChannelInvalid, UsernameInvalid, UsernameNotOccupied,
        ChatAdminRequired,
    )

    _JOINED = {
        enums.ChatMemberStatus.OWNER,
        enums.ChatMemberStatus.ADMINISTRATOR,
        enums.ChatMemberStatus.MEMBER,
        enums.ChatMemberStatus.RESTRICTED,
    }
    # Bot API statuses that count as "in the chat"
    _API_JOINED = {"member", "administrator", "creator", "restricted"}

    not_joined = []
    for ch in channels:
        raw_cid = ch.get("chat_id", "")
        name    = ch.get("name", str(raw_cid))

        # ── Step 0: resolve invite links → numeric ID ──────────────────────
        # Pyrogram cannot use t.me/+ invite links in get_chat_member.
        if isinstance(raw_cid, str) and ("t.me/+" in raw_cid or
                (raw_cid.startswith("http") and "+" in raw_cid)):
            try:
                chat_obj = await client.get_chat(raw_cid)
                cid      = chat_obj.id
                print(f"[FJ] Resolved invite link → {cid} for '{name}'")
            except Exception as e:
                print(f"[FJ] Cannot resolve invite link for '{name}': {e} — using Bot API")
                # Bot API also accepts invite links in some cases
                api_ok = await _bot_api_check_member(client, raw_cid, user_id)
                if api_ok is False:
                    not_joined.append(ch)
                elif api_ok is None:
                    # Can't verify at all — warn admin, don't block user
                    print(f"[FJ] ⚠️ SETUP ISSUE: '{name}' — bot cannot check membership. "
                          "Add bot as admin OR provide a numeric group ID.")
                continue
        else:
            try:
                cid = int(raw_cid) if str(raw_cid).lstrip("-").isdigit() else str(raw_cid)
            except Exception:
                cid = str(raw_cid)

        # ── Step 1: Pyrogram get_chat_member ───────────────────────────────
        try:
            member = await client.get_chat_member(cid, user_id)
            if member.status not in _JOINED:
                not_joined.append(ch)
            # else: user IS joined → do nothing
            continue
        except UserNotParticipant:
            # User is definitively NOT in the chat
            not_joined.append(ch)
            continue
        except (ChannelPrivate, PeerIdInvalid, ChannelInvalid,
                UsernameInvalid, UsernameNotOccupied) as e:
            print(f"[FJ] Chat unreachable for '{name}' ({cid}): {e} — trying Bot API")
        except ChatAdminRequired as e:
            print(f"[FJ] Bot not admin in '{name}' ({cid}): {e} — trying Bot API")
        except Exception as e:
            print(f"[FJ] get_chat_member error for '{name}' ({cid}): {e} — trying Bot API")

        # ── Step 2: Bot API getChatMember fallback ─────────────────────────
        api_result = await _bot_api_check_member(client, cid, user_id)
        if api_result is False:
            # Bot API confirmed user is NOT in the chat
            not_joined.append(ch)
        elif api_result is True:
            # Bot API confirmed user IS in the chat
            pass
        else:
            # None — bot cannot verify (not admin anywhere)
            print(f"[FJ] ⚠️ SETUP ISSUE: '{name}' ({cid}) — bot cannot check membership. "
                  "Ensure the bot is an ADMIN in this group/channel!")
            # Don't add to not_joined — skip this check to avoid locking out users

    return not_joined


async def _check_force_join(user_id: int, client=None) -> list:
    """Return list of channels the user hasn't joined.
    Uses the bot's own client so clone bots check correctly.
    """
    from config import app as _main_app
    check_client = client if client is not None else _main_app
    doc = await _fj_doc(client)
    if not doc.get("enabled"):
        return []
    channels = doc.get("channels", [])
    if not channels:
        return []
    return await get_not_joined(check_client, user_id, channels)


def _fj_extract_chat_id(link: str) -> str:
    if "t.me/" in link and "+" not in link:
        username = link.rstrip("/").split("t.me/")[-1].lstrip("@")
        return f"@{username}" if username else link
    return link


def _fj_parse_entry(part: str):
    part = part.strip()
    if "|" in part:
        bits = [b.strip() for b in part.split("|")]
    elif " - " in part:
        bits = [b.strip() for b in part.split(" - ")]
    else:
        return None

    if len(bits) < 2:
        return None

    name = bits[0]
    link = bits[1]
    if not name or not link:
        return None

    if len(bits) >= 3 and bits[2]:
        chat_id = bits[2]
    else:
        chat_id = _fj_extract_chat_id(link)

    return {"name": name, "link": link, "chat_id": chat_id}


def _fj_join_buttons(not_joined: list) -> list:
    btns = []
    for ch in not_joined:
        link = ch.get("link", "")
        if link.startswith("https://") or link.startswith("http://"):
            btns.append(InlineKeyboardButton(f"📢 {ch['name']}", url=link))
        else:
            btns.append(InlineKeyboardButton(
                f"⚠️ {ch['name']}",
                callback_data="fj_no_link",
            ))

    rows = [btns[i:i+2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton("✅ I've Joined All Channels!", callback_data="fj_check")])
    return rows


async def _fj_show_add_card(reply_fn, doc: dict):
    channels = doc.get("channels", [])
    lines    = [f"  {i}. {c['name']}" for i, c in enumerate(channels, 1)]
    ch_list  = "\n".join(lines) if lines else "  (none yet)"
    await reply_fn(
        "📢 <b>Force Join — Add Channel / Group</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Added so far: <b>{len(channels)}</b>\n"
        f"{ch_list}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Click <b>Set Button</b> to add a channel or group.\n\n"
        "Type /cancel to cancel.",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📢 Set Button", callback_data="fj_set_button"),
            InlineKeyboardButton("❌ Cancel",     callback_data="fj_cancel"),
        ]]),
    )


def _fj_status_text(doc: dict) -> str:
    enabled  = "✅ ON" if doc.get("enabled") else "❌ OFF"
    channels = doc.get("channels", [])
    lines    = [
        "📢 FORCE JOIN STATUS",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"Status          : {enabled}",
        f"Channels/Groups : {len(channels)}",
    ]
    for i, ch in enumerate(channels, 1):
        cid  = ch.get("chat_id", "?")
        kind = "👥 Group" if str(cid).lstrip("-").isdigit() and not str(cid).startswith("-100") else "📢 Channel/Group"
        lines.append(f"  {i}. {ch['name']}")
        lines.append(f"     🔗 {ch['link']}")
        lines.append(f"     🆔 {cid}")
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Commands:",
        "/forcejoin on          — Enable",
        "/forcejoin off         — Disable",
        "/forcejoin add         — Add channel/group (wizard)",
        "/forcejoin remove <n>  — Remove entry #n",
        "/forcejoin list        — Show this list",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🤖 DESI MLH SYSTEM",
    ]
    return "\n".join(lines)


def _fj_session_key(client, uid: int) -> int:
    """Session key: user id (unique per admin).
    We keep uid as the key — different bots have different admins,
    and ADMIN_ID is always the super admin so no collision risk.
    """
    return uid


# ══════════════════════════════════════════════════════════════════════════════
# /forcejoin command
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("forcejoin") & admin_filter & filters.private)
async def forcejoin_cmd(client: Client, message: Message):
    args = message.command[1:]
    doc  = await _fj_doc(client)
    uid  = message.from_user.id

    if not args or args[0].lower() == "list":
        await message.reply_text(_fj_status_text(doc))
        return

    sub = args[0].lower()

    if sub == "on":
        channels = doc.get("channels", [])
        if not channels:
            await message.reply_text(
                "⚠️ No channels added yet!\n\n"
                "Add at least one channel first:\n"
                "/forcejoin add"
            )
            return
        await _fj_save({"enabled": True, "channels": channels}, client)
        await message.reply_text(
            "✅ FORCE JOIN ENABLED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 {len(channels)} channel(s) configured.\n"
            "Users must join ALL channels\n"
            "before receiving any video.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        return

    if sub == "off":
        channels = doc.get("channels", [])
        await _fj_save({"enabled": False, "channels": channels}, client)
        await message.reply_text(
            "❌ FORCE JOIN DISABLED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Users can now get videos without\n"
            "joining any channel.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        return

    if sub == "remove":
        channels = doc.get("channels", [])
        if not channels:
            await message.reply_text("❌ No channels configured. Use /forcejoin add first.")
            return
        if len(args) < 2 or not args[1].isdigit():
            ch_list = "\n".join(f"  {i}. {c['name']}" for i, c in enumerate(channels, 1))
            await message.reply_text(
                f"Usage: /forcejoin remove <number>\n\n"
                f"Current channels:\n{ch_list}"
            )
            return
        idx = int(args[1]) - 1
        if idx < 0 or idx >= len(channels):
            await message.reply_text(f"❌ Invalid number. Choose 1–{len(channels)}.")
            return
        removed = channels.pop(idx)
        await _fj_save({"channels": channels, "enabled": doc.get("enabled", False)}, client)
        await message.reply_text(
            "🗑️ CHANNEL REMOVED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 {removed['name']}\n"
            f"🔗 {removed['link']}\n\n"
            f"Remaining: {len(channels)} channel(s)\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        return

    if sub in ("add", "link"):
        fj_sessions.pop(uid, None)
        await _fj_show_add_card(message.reply_text, doc)
        return

    if sub == "clean":
        channels = doc.get("channels", [])
        broken   = [ch for ch in channels if str(ch.get("chat_id", "")).startswith("http")]
        if not broken:
            await message.reply_text("✅ No broken entries found. All channels have valid IDs.")
            return
        fixed = [ch for ch in channels if not str(ch.get("chat_id", "")).startswith("http")]
        await _fj_save({"channels": fixed, "enabled": doc.get("enabled", False)}, client)
        names = "\n".join(f"  🗑️ {ch['name']}" for ch in broken)
        await message.reply_text(
            "🧹 <b>CLEANED UP BROKEN ENTRIES</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Removed {len(broken)} broken channel(s):\n{names}\n\n"
            f"✅ Remaining valid channels: {len(fixed)}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM",
            parse_mode=HTML,
        )
        return

    if sub == "fix":
        channels = doc.get("channels", [])
        if not channels:
            await message.reply_text("❌ No channels configured.")
            return
        msg = await message.reply_text("🔄 Resolving all invite links...")
        results = []
        changed = False
        for ch in channels:
            cid = ch.get("chat_id", "")
            if isinstance(cid, str) and cid.startswith("http"):
                try:
                    resolved = await client.get_chat(cid)
                    old_cid  = cid
                    ch["chat_id"] = str(resolved.id)
                    results.append(f"✅ {ch['name']}: {old_cid[:30]}… → {ch['chat_id']}")
                    changed = True
                except Exception as e:
                    results.append(f"⚠️ {ch['name']}: failed — {e}")
            else:
                results.append(f"ℹ️ {ch['name']}: already numeric ({cid})")
        if changed:
            await _fj_save({"channels": channels, "enabled": doc.get("enabled", False)}, client)
        result_text = "\n".join(results)
        await msg.edit_text(
            "🔧 FORCE JOIN FIX RESULT\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{result_text}\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{'✅ Saved!' if changed else 'ℹ️ No changes needed.'}"
        )
        return

    await message.reply_text(_fj_status_text(doc))


# ══════════════════════════════════════════════════════════════════════════════
# /forcejoinadd — shortcut for wizard
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("forcejoinadd") & admin_filter & filters.private)
async def forcejoinadd_cmd(client: Client, message: Message):
    uid = message.from_user.id
    fj_sessions.pop(uid, None)
    doc = await _fj_doc(client)
    await _fj_show_add_card(message.reply_text, doc)


# ══════════════════════════════════════════════════════════════════════════════
# /forcebuttondel — button-based removal UI
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("forcebuttondel") & admin_filter & filters.private)
async def forcebuttondel_cmd(client: Client, message: Message):
    doc      = await _fj_doc(client)
    channels = doc.get("channels", [])
    if not channels:
        return await message.reply_text(
            "❌ No channels configured.\n\n"
            "Use /forcejoinadd to add channels first."
        )
    buttons = []
    for i, ch in enumerate(channels):
        buttons.append([InlineKeyboardButton(f"🗑️ {ch['name']}", callback_data=f"fj_del_{i}")])
    buttons.append([InlineKeyboardButton("✅ Done", callback_data="fj_del_done")])
    await message.reply_text(
        "🗑️ <b>REMOVE FORCE-JOIN CHANNEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Total channels: <b>{len(channels)}</b>\n\n"
        "Tap a channel below to <b>remove</b> it immediately:\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks — fj_set_button (start wizard)
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex("^fj_set_button$") & admin_filter)
async def fj_set_button_cb(client: Client, cq: CallbackQuery):
    uid = cq.from_user.id
    fj_sessions[uid] = {
        "state":               "fj_wait_btn",
        "wizard_msg_id":       cq.message.id,
        "pending_channels":    [],
        "unresolved_channels": [],
        "fwd_index":           0,
        "fj_key":              _fj_key(client),   # store which bot this belongs to
    }
    await cq.edit_message_text(
        "📢 <b>Step 1 — Add Channel or Group</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Format:</b> <code>Name | Join Link</code>\n\n"
        "🌐 <b>Public channel/group:</b>\n"
        "<code>DESI MLH | https://t.me/desimlh</code>\n\n"
        "🔒 <b>Private (invite link):</b>\n"
        "<code>VIP Group | https://t.me/+dV5BmONTLmcxZDU1</code>\n"
        "↳ Bot will ask for the <b>chat ID</b> next step.\n\n"
        "🆔 <b>With ID (skip the ID step):</b>\n"
        "<code>My Group | https://t.me/+xxx | -1001234567890</code>\n\n"
        "📦 <b>Multiple at once:</b>\n"
        "<code>Channel 1 | link1 && Channel 2 | link2</code>\n\n"
        "Type /cancel to cancel.",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="fj_cancel")
        ]]),
    )
    await cq.answer()


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks — fj_confirm (save channels)
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex("^fj_confirm$") & admin_filter)
async def fj_confirm_cb(client: Client, cq: CallbackQuery):
    uid = cq.from_user.id                          # ← FIXED: was ADMIN_ID
    fj  = fj_sessions.pop(uid, None)
    if not fj or fj.get("state") != "fj_add_confirm":
        return await cq.answer("No active session.", show_alert=True)

    pending  = fj.get("pending_channels", [])
    doc      = await _fj_doc(client)
    existing = doc.get("channels", [])

    new_names = {ch["name"] for ch in pending}
    new_links = {ch["link"] for ch in pending}

    def _is_broken(ch):
        cid = ch.get("chat_id", "")
        return isinstance(cid, str) and cid.startswith("http")

    kept     = [
        ch for ch in existing
        if ch["name"] not in new_names
        and ch["link"] not in new_links
        and not _is_broken(ch)
    ]
    channels = kept + pending

    await _fj_save({"channels": channels, "enabled": doc.get("enabled", False)}, client)

    removed_count = len(existing) - len(kept)
    if removed_count:
        print(f"[FORCEJOIN] Cleaned up {removed_count} old/duplicate/broken entry(s)")

    from pyrogram.errors import (
        ChannelPrivate, PeerIdInvalid, ChannelInvalid,
        UsernameInvalid, UsernameNotOccupied,
    )
    status_lines = []
    for ch in pending:
        raw_cid = ch["chat_id"]
        try:
            cid = int(raw_cid) if str(raw_cid).lstrip("-").isdigit() else str(raw_cid)
        except Exception:
            cid = str(raw_cid)
        try:
            if isinstance(cid, str) and cid.startswith("http"):
                raise ValueError("invite link — provide numeric ID instead")
            await client.get_chat(cid)           # ← use this bot's client
            status_lines.append(f"  ✅ {ch['name']}")
        except (ChannelPrivate, PeerIdInvalid, ChannelInvalid,
                UsernameInvalid, UsernameNotOccupied, ValueError) as e:
            status_lines.append(f"  ⚠️ {ch['name']} — {e}")
        except Exception as e:
            status_lines.append(f"  ⚠️ {ch['name']} — {e}")

    has_warning   = any("⚠️" in l for l in status_lines)
    status_text   = "\n".join(status_lines)
    warning_block = (
        "\n\n⚠️ <b>ACTION REQUIRED</b>\n"
        "Bot cannot verify membership for ⚠️ channels above.\n"
        "👉 Add bot as <b>Admin</b> to those channels,\n"
        "   then users will be properly checked.\n"
        "   Until then, those channels will BLOCK everyone."
    ) if has_warning else ""

    await cq.edit_message_text(
        f"✅ {len(pending)} CHANNEL(S) ADDED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{status_text}\n\n"
        f"📋 Total channels now: {len(channels)}"
        f"{warning_block}\n\n"
        "Use /forcejoin on to enable the check.\n"
        "Use /forcejoinadd to add more channels.\n"
        "Use /forcebuttondel to remove a channel.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )
    await cq.answer(f"✅ {len(pending)} channel(s) added!")
    print(f"[FORCEJOIN] Added {len(pending)} channels: {[c['name'] for c in pending]}")


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks — fj_cancel
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex("^fj_cancel$") & admin_filter)
async def fj_cancel_cb(client: Client, cq: CallbackQuery):
    uid = cq.from_user.id                          # ← FIXED: was ADMIN_ID
    fj_sessions.pop(uid, None)
    await cq.edit_message_text(
        "🚫 Cancelled.\n"
        "No channel was added.\n\n"
        "Use /forcejoinadd to try again."
    )
    await cq.answer("Cancelled.")


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks — fj_del_<n> / fj_del_done (button removal)
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^fj_del_(\d+|done)$") & admin_filter)
async def fj_del_cb(client: Client, cq: CallbackQuery):
    data = cq.data

    if data == "fj_del_done":
        await cq.edit_message_text("✅ Done. No more changes.")
        return await cq.answer()

    idx      = int(data.split("_")[-1])
    doc      = await _fj_doc(client)
    channels = doc.get("channels", [])

    if idx >= len(channels):
        await cq.answer("Channel not found — list may have changed.", show_alert=True)
        return

    removed = channels.pop(idx)
    await _fj_save({"channels": channels, "enabled": doc.get("enabled", False)}, client)

    if not channels:
        await cq.edit_message_text(
            f"🗑️ <b>{removed['name']}</b> removed.\n\n"
            "No channels left.\n"
            "Use /forcejoinadd to add new ones.",
            parse_mode=HTML,
        )
    else:
        buttons = []
        for i, ch in enumerate(channels):
            buttons.append([InlineKeyboardButton(f"🗑️ {ch['name']}", callback_data=f"fj_del_{i}")])
        buttons.append([InlineKeyboardButton("✅ Done", callback_data="fj_del_done")])
        await cq.edit_message_text(
            "🗑️ <b>REMOVE FORCE-JOIN CHANNEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ Removed: <b>{removed['name']}</b>\n\n"
            f"📋 Remaining: <b>{len(channels)}</b> channel(s)\n\n"
            "Tap another to remove, or Done to finish:\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            parse_mode=HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    await cq.answer(f"🗑️ {removed['name']} removed!")
    print(f"[FORCEJOIN] Removed channel: {removed}")


# ══════════════════════════════════════════════════════════════════════════════
# Callbacks — fj_no_link / fj_check (user-facing)
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex("^fj_no_link$"))
async def fj_no_link_cb(client: Client, cq: CallbackQuery):
    await cq.answer(
        "⚠️ No join link configured for this channel.\n"
        "Please contact the admin.",
        show_alert=True,
    )


@app.on_callback_query(filters.regex("^fj_check$"))
async def fj_check_cb(client: Client, cq: CallbackQuery):
    user_id    = cq.from_user.id
    not_joined = await _check_force_join(user_id, client)   # ← pass client

    if not_joined:
        try:
            await cq.edit_message_text(
                "⚠️ NOT ALL CHANNELS JOINED\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"You still need to join {len(not_joined)} more channel(s).\n\n"
                "👇 Join them below, then tap the button.",
                reply_markup=InlineKeyboardMarkup(_fj_join_buttons(not_joined)),
            )
        except Exception:
            pass
        await cq.answer("❌ Please join all channels first!", show_alert=True)
        return

    await cq.answer("✅ All channels joined! Sending your video...")
    try:
        await cq.edit_message_text(
            "✅ ALL CHANNELS JOINED!\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🎉 Thank you for joining!\n"
            "📹 Your video is being sent...\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
    except Exception:
        pass

    from handlers.video import _send_video_to_user
    err = await _send_video_to_user(client, user_id)
    if err:
        await cq.message.reply_text(err)
