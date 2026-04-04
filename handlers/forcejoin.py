import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    HTML, ADMIN_ID, settings_col, fj_sessions, app,
)
from helpers import log_event, admin_filter


async def _fj_doc() -> dict:
    doc = await settings_col.find_one({"key": "force_join"})
    return doc or {"key": "force_join", "enabled": False, "channels": []}


async def get_force_join() -> dict:
    return await _fj_doc()


async def get_fj_channels() -> list:
    doc = await _fj_doc()
    return doc.get("channels", [])


async def get_not_joined(client: Client, user_id: int, channels: list) -> list:
    not_joined = []
    for ch in channels:
        raw_cid = ch.get("chat_id", "")
        try:
            cid = int(raw_cid) if str(raw_cid).lstrip("-").isdigit() else str(raw_cid)
        except Exception:
            cid = str(raw_cid)
        try:
            member = await client.get_chat_member(cid, user_id)
            from pyrogram import enums
            if member.status in (
                enums.ChatMemberStatus.LEFT,
                enums.ChatMemberStatus.BANNED,
            ):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined


async def _check_force_join(user_id: int) -> list:
    from config import app as _app
    doc = await _fj_doc()
    if not doc.get("enabled"):
        return []
    channels = doc.get("channels", [])
    if not channels:
        return []
    return await get_not_joined(_app, user_id, channels)


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
        "📢 <b>Force Join — Add Channel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Channels added so far: <b>{len(channels)}</b>\n"
        f"{ch_list}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Click <b>Set Button</b> to add a new channel.\n\n"
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
        f"Status   : {enabled}",
        f"Channels : {len(channels)}",
    ]
    for i, ch in enumerate(channels, 1):
        cid = ch.get("chat_id", "?")
        lines.append(f"  {i}. {ch['name']}")
        lines.append(f"     🔗 {ch['link']}")
        lines.append(f"     🆔 {cid}")
    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━",
        "Commands:",
        "/forcejoin on          — Enable",
        "/forcejoin off         — Disable",
        "/forcejoin add         — Add a channel (wizard)",
        "/forcejoin remove <n>  — Remove channel #n",
        "/forcejoin list        — Show this list",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "🤖 DESI MLH SYSTEM",
    ]
    return "\n".join(lines)


@app.on_message(filters.command("forcejoin") & admin_filter & filters.private)
async def forcejoin_cmd(client: Client, message: Message):
    args = message.command[1:]
    doc  = await _fj_doc()

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
        await settings_col.update_one(
            {"key": "force_join"}, {"$set": {"enabled": True}}, upsert=True
        )
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
        await settings_col.update_one(
            {"key": "force_join"}, {"$set": {"enabled": False}}, upsert=True
        )
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
        if len(args) < 2 or not args[1].isdigit():
            await message.reply_text(
                f"Usage: /forcejoin remove <number>\n\n"
                f"Current channels:\n" +
                "\n".join(f"  {i}. {c['name']}" for i, c in enumerate(channels, 1))
                or "  (none)"
            )
            return
        idx = int(args[1]) - 1
        if idx < 0 or idx >= len(channels):
            await message.reply_text(f"❌ Invalid number. Choose 1–{len(channels)}.")
            return
        removed = channels.pop(idx)
        await settings_col.update_one(
            {"key": "force_join"}, {"$set": {"channels": channels}}, upsert=True
        )
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
        await _fj_show_add_card(message.reply_text, doc)
        return

    if sub == "clean":
        channels = doc.get("channels", [])
        broken   = [ch for ch in channels if str(ch.get("chat_id", "")).startswith("http")]
        if not broken:
            await message.reply_text("✅ No broken entries found. All channels have valid IDs.")
            return
        fixed = [ch for ch in channels if not str(ch.get("chat_id", "")).startswith("http")]
        await settings_col.update_one(
            {"key": "force_join"}, {"$set": {"channels": fixed}}, upsert=True
        )
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
            await settings_col.update_one(
                {"key": "force_join"}, {"$set": {"channels": channels}}, upsert=True
            )
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


@app.on_message(filters.command("forcejoinadd") & admin_filter & filters.private)
async def forcejoinadd_cmd(client: Client, message: Message):
    fj_sessions.pop(ADMIN_ID, None)
    doc = await _fj_doc()
    await _fj_show_add_card(message.reply_text, doc)


@app.on_message(filters.command("forcebuttondel") & admin_filter & filters.private)
async def forcebuttondel_cmd(client: Client, message: Message):
    doc      = await _fj_doc()
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


@app.on_callback_query(filters.regex("^fj_set_button$") & admin_filter)
async def fj_set_button_cb(client: Client, cq: CallbackQuery):
    fj_sessions[cq.from_user.id] = {
        "state":               "fj_wait_btn",
        "wizard_msg_id":       cq.message.id,
        "pending_channels":    [],
        "unresolved_channels": [],
        "fwd_index":           0,
    }
    await cq.edit_message_text(
        "📢 <b>Step 1 — Send Channel Info</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Format: <code>Channel Name | Join Link</code>\n\n"
        "🌐 <b>Public channel:</b>\n"
        "<code>DESI MLH | https://t.me/desimlh</code>\n\n"
        "🔒 <b>Private channel (invite link):</b>\n"
        "<code>VIP Group | https://t.me/+dV5BmONTLmcxZDU1</code>\n"
        "↳ Bot will ask you to <b>forward a message</b> from it next.\n\n"
        "📦 <b>Multiple at once (use &&):</b>\n"
        "<code>DESI MLH | https://t.me/desimlh && VIP | https://t.me/+xxx</code>\n\n"
        "Type /cancel to cancel.",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="fj_cancel")
        ]]),
    )
    await cq.answer()


@app.on_callback_query(filters.regex("^fj_confirm$") & admin_filter)
async def fj_confirm_cb(client: Client, cq: CallbackQuery):
    fj = fj_sessions.pop(ADMIN_ID, None)
    if not fj or fj.get("state") != "fj_add_confirm":
        return await cq.answer("No active session.", show_alert=True)

    pending  = fj.get("pending_channels", [])
    doc      = await _fj_doc()
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

    await settings_col.update_one(
        {"key": "force_join"},
        {"$set": {"channels": channels}},
        upsert=True,
    )

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
            from config import app as _app
            await _app.get_chat(cid)
            status_lines.append(f"  ✅ {ch['name']}")
        except (ChannelPrivate, PeerIdInvalid, ChannelInvalid,
                UsernameInvalid, UsernameNotOccupied, ValueError) as e:
            status_lines.append(f"  ⚠️ {ch['name']} — {e}")
        except Exception as e:
            status_lines.append(f"  ⚠️ {ch['name']} — {e}")

    has_warning  = any("⚠️" in l for l in status_lines)
    status_text  = "\n".join(status_lines)
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


@app.on_callback_query(filters.regex("^fj_cancel$") & admin_filter)
async def fj_cancel_cb(client: Client, cq: CallbackQuery):
    fj_sessions.pop(ADMIN_ID, None)
    await cq.edit_message_text(
        "🚫 Cancelled.\n"
        "No channel was added.\n\n"
        "Use /forcejoinadd to try again."
    )
    await cq.answer("Cancelled.")


@app.on_callback_query(filters.regex(r"^fj_del_(\d+|done)$") & admin_filter)
async def fj_del_cb(client: Client, cq: CallbackQuery):
    data = cq.data

    if data == "fj_del_done":
        await cq.edit_message_text("✅ Done. No more changes.")
        return await cq.answer()

    idx      = int(data.split("_")[-1])
    doc      = await _fj_doc()
    channels = doc.get("channels", [])

    if idx >= len(channels):
        await cq.answer("Channel not found — list may have changed.", show_alert=True)
        return

    removed = channels.pop(idx)
    await settings_col.update_one(
        {"key": "force_join"},
        {"$set": {"channels": channels}},
        upsert=True,
    )

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
    not_joined = await _check_force_join(user_id)

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
