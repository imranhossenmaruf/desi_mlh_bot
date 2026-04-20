import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    HTML, ADMIN_ID,
    broadcast_sessions, fj_sessions,
    STATE_AUDIENCE, STATE_JOIN_DATE, STATE_CONTENT, STATE_CUSTOMIZE,
    STATE_BUTTONS, STATE_CONFIRM, STATE_SCHEDULE,
    scheduled_col,
    app,
)
from helpers import (
    parse_date, parse_buttons, has_media, send_to_user, do_broadcast,
    auto_delete, refresh_preview, delete_msg_safe,
    kb_audience, kb_customize, kb_confirm, count_targets, audience_label,
    log_event, admin_filter,
)


def _new_session(chat_id: int, mode: str = "broadcast") -> dict:
    return {
        "state":          STATE_AUDIENCE,
        "audience":       "all",
        "join_after":     None,
        "msg_type":       None,
        "text":           "",
        "entities":       [],
        "media_chat_id":  None,
        "media_msg_id":   None,
        "extra_buttons":  None,
        "preview_msg_id": None,
        "chat_id":        chat_id,
        "mode":           mode,
    }


@app.on_message(
    filters.command("broadcast") & admin_filter & filters.private
)
async def broadcast_start(client: Client, message: Message):
    session = _new_session(message.chat.id, mode="broadcast")
    await message.reply_text(
        "📢 <b>Broadcast Message</b>\n\n"
        "Choose who you want to send this broadcast to:\n\n"
        "Type /cancel to cancel the operation.",
        parse_mode=HTML,
        reply_markup=kb_audience(),
    )
    broadcast_sessions[message.from_user.id] = session


@app.on_message(
    filters.command("sbc") & admin_filter & filters.private
)
async def sbc_start(client: Client, message: Message):
    session = _new_session(message.chat.id, mode="sbc")
    await message.reply_text(
        "⏰ <b>Scheduled Broadcast</b>\n\n"
        "Choose who you want to send this scheduled broadcast to:\n\n"
        "Type /cancel to cancel the operation.",
        parse_mode=HTML,
        reply_markup=kb_audience(),
    )
    broadcast_sessions[message.from_user.id] = session


@app.on_callback_query(filters.regex("^bc_all$") & admin_filter)
async def bc_select_all(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    session["audience"]   = "all"
    session["join_after"] = None
    session["state"]      = STATE_CONTENT

    await cq.edit_message_text(
        "✍️ <b>Create Your Broadcast Post</b>\n\n"
        "🎯 Audience: <b>ALL</b> (Private Users + Groups)\n\n"
        "Send the message you want to broadcast.\n"
        "It can be text, photo, video, file, sticker, or a forwarded channel post.\n\n"
        "Type /cancel to cancel the operation.",
        parse_mode=HTML,
    )
    await cq.answer("✅ All Users + Groups selected")


@app.on_callback_query(filters.regex("^bc_private$") & admin_filter)
async def bc_private_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    session["audience"]   = "private"
    session["join_after"] = None
    session["state"]      = STATE_CONTENT

    await cq.edit_message_text(
        "✍️ <b>Create Your Broadcast Post</b>\n\n"
        "🎯 Audience: <b>PRIVATE</b> (Users in private chat only)\n\n"
        "Send the message you want to broadcast.\n"
        "It can be text, photo, video, file, sticker, or a forwarded channel post.\n\n"
        "Type /cancel to cancel the operation.",
        parse_mode=HTML,
    )
    await cq.answer("✅ Private users selected")


@app.on_callback_query(filters.regex("^bc_groups$") & admin_filter)
async def bc_groups_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    session["audience"]   = "groups"
    session["join_after"] = None
    session["state"]      = STATE_CONTENT

    await cq.edit_message_text(
        "✍️ <b>Create Your Broadcast Post</b>\n\n"
        "🎯 Audience: <b>GROUPS</b> (All managed groups)\n\n"
        "Send the message you want to broadcast.\n"
        "It can be text, photo, video, file, sticker, or a forwarded channel post.\n\n"
        "Type /cancel to cancel the operation.",
        parse_mode=HTML,
    )
    await cq.answer("✅ All groups selected")


@app.on_callback_query(filters.regex("^bc_join_after$") & admin_filter)
async def bc_join_after_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    session["state"] = STATE_JOIN_DATE
    await cq.edit_message_text(
        "📅 <b>Filter Users by Join Date</b>\n\n"
        "Enter a date. Only users who joined <b>after</b> this date will receive the broadcast.\n\n"
        "Supported formats:\n"
        "<code>DD.MM.YYYY HH:MM</code>  →  e.g. <code>25.03.2025 14:30</code>\n"
        "<code>MM/DD/YYYY HH:MM</code>  →  e.g. <code>03/25/2025 14:30</code>\n\n"
        "Type /cancel to cancel the operation.",
        parse_mode=HTML,
    )
    await cq.answer()


@app.on_callback_query(filters.regex("^bc_add_button$") & admin_filter)
async def bc_add_button(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)

    session["state"] = STATE_BUTTONS
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cq.message.reply_text(
        "🔗 <b>Add Inline Button</b>\n\n"
        "Send the button(s) in this format:\n\n"
        "<code>Button Name | https://link.com</code>\n\n"
        "Two buttons in one row:\n"
        "<code>Btn 1 | link1.com && Btn 2 | link2.com</code>\n\n"
        "Multiple rows — one row per line.\n\n"
        "Type /cancel to stop.",
        parse_mode=HTML,
    )
    await cq.answer()


@app.on_callback_query(filters.regex("^bc_attach_media$") & admin_filter)
async def bc_attach_media(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)

    session["state"] = STATE_CONTENT
    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await cq.message.reply_text(
        "🖼 <b>Attach Media</b>\n\n"
        "Send a photo, video, file or sticker.\n"
        "It will be combined with your current text as one message.\n\n"
        "Type /cancel to stop.",
        parse_mode=HTML,
    )
    await cq.answer()


async def _add_quick_button(client, cq, label, url):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)
    existing = session.get("extra_buttons") or []
    new_btn   = {"text": label, "url": url}
    for row in existing:
        if any(b["url"] == url for b in row):
            return await cq.answer("Already added!", show_alert=True)
    existing.append([new_btn])
    session["extra_buttons"] = existing
    await cq.answer(f"✅ '{label}' button added!")
    await refresh_preview(client, session)


@app.on_callback_query(filters.regex("^bc_quick_buypremium$") & admin_filter)
async def bc_quick_buypremium(client: Client, cq: CallbackQuery):
    from config import app as _app
    bot_username = (await _app.get_me()).username
    await _add_quick_button(
        client, cq,
        "💎 Buy Premium",
        f"https://t.me/{bot_username}?start=buypremium"
    )


@app.on_callback_query(filters.regex("^bc_quick_profile$") & admin_filter)
async def bc_quick_profile(client: Client, cq: CallbackQuery):
    from config import app as _app
    bot_username = (await _app.get_me()).username
    await _add_quick_button(
        client, cq,
        "👤 My Profile",
        f"https://t.me/{bot_username}?start=profile"
    )


@app.on_callback_query(filters.regex("^bc_remove_buttons$") & admin_filter)
async def bc_remove_buttons(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    session["extra_buttons"] = None
    await cq.answer("🗑 Buttons removed")
    await refresh_preview(client, session)


@app.on_callback_query(filters.regex("^bc_preview$") & admin_filter)
async def bc_preview(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    extra_kb = None
    if session.get("extra_buttons"):
        extra_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(b["text"], url=b["url"]) for b in row]
            for row in session["extra_buttons"]
        ])

    await cq.answer("Sending preview (auto-deletes in 5 s)…")
    try:
        sent = await send_to_user(client, cq.message.chat.id, session, reply_markup=extra_kb)
        if sent:
            asyncio.create_task(auto_delete(client, cq.message.chat.id, sent.id, delay=5))
    except Exception:
        pass


@app.on_callback_query(filters.regex("^bc_send_now$") & admin_filter)
async def bc_send_now(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)

    total = await count_targets(session)
    aud   = audience_label(session)
    session["state"] = STATE_CONFIRM

    try:
        await cq.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await cq.message.reply_text(
        "🚀 <b>Confirm Broadcast</b>\n\n"
        "You're about to send a broadcast to:\n"
        f"👥 <b>Recipients:</b> {total:,} users\n"
        f"📅 <b>Filter:</b> {aud}\n\n"
        "⚠️ This action cannot be undone once confirmed.",
        parse_mode=HTML,
        reply_markup=kb_confirm(),
    )
    await cq.answer()


@app.on_callback_query(filters.regex("^bc_schedule$") & admin_filter)
async def bc_schedule_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)
    session["state"] = STATE_SCHEDULE
    await cq.answer()
    await cq.message.reply_text(
        "⏰ <b>Schedule Broadcast</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Send the date and time to schedule this broadcast.\n\n"
        "Format (Bangladesh Time — BST/UTC+6):\n"
        "<code>DD.MM.YYYY HH:MM</code>\n\n"
        "Example:\n"
        "<code>25.04.2026 21:30</code>\n\n"
        "Type /cancel to cancel.",
        parse_mode=HTML,
    )


@app.on_callback_query(filters.regex("^sbc_set_schedule$") & admin_filter)
async def sbc_set_schedule_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session or session["state"] != STATE_CUSTOMIZE:
        return await cq.answer("No active session.", show_alert=True)
    session["state"] = STATE_SCHEDULE
    await cq.answer()
    await cq.message.reply_text(
        "⏰ <b>Set Broadcast Schedule</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Send the date and time for this broadcast.\n\n"
        "Format (Bangladesh Time — BST/UTC+6):\n"
        "<code>DD.MM.YYYY HH:MM</code>\n\n"
        "Example:\n"
        "<code>25.04.2026 21:30</code>\n\n"
        "Type /cancel to cancel.",
        parse_mode=HTML,
    )


@app.on_callback_query(filters.regex("^bc_edit_post$") & admin_filter)
async def bc_edit_post(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session:
        return await cq.answer("No active session.", show_alert=True)

    session["state"] = STATE_CUSTOMIZE
    await delete_msg_safe(client, session["chat_id"], cq.message.id)
    if session.get("preview_msg_id"):
        try:
            await client.edit_message_reply_markup(
                chat_id=session["chat_id"],
                message_id=session["preview_msg_id"],
                reply_markup=kb_customize(session.get("extra_buttons"), mode=session.get("mode", "broadcast")),
            )
        except Exception:
            await refresh_preview(client, session)
    await cq.answer()


@app.on_callback_query(filters.regex("^bc_confirm_send$") & admin_filter)
async def bc_confirm_send(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session or session["state"] != STATE_CONFIRM:
        return await cq.answer("No active session.", show_alert=True)

    await cq.answer("📡 Starting broadcast…")
    await cq.edit_message_text(
        "📡 <b>Broadcasting in progress...</b>\n\n"
        "👥 Target Users: calculating...\n"
        "✅ Sent: 0\n❌ Failed: 0\n⏳ Progress: 0%",
        parse_mode=HTML,
    )
    asyncio.create_task(do_broadcast(client, session, cq.message))


@app.on_callback_query(filters.regex("^bc_cancel$") & admin_filter)
async def bc_cancel_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.pop(cq.from_user.id, None)
    if session:
        await delete_msg_safe(client, session["chat_id"], session.get("preview_msg_id"))
    try:
        await cq.edit_message_text("🚫 Cancelled.\nType /broadcast to start again.")
    except Exception:
        try:
            await cq.message.reply_text("🚫 Cancelled.\nType /broadcast to start again.")
        except Exception:
            pass
    await cq.answer()


@app.on_message(
    filters.command("cancel") & admin_filter & filters.private
)
async def bc_cancel_cmd(client: Client, message: Message):
    uid = message.from_user.id
    fj = fj_sessions.pop(uid, None)
    if fj:
        try:
            await client.delete_messages(message.chat.id, fj["wizard_msg_id"])
        except Exception:
            pass
        await message.reply_text(
            "🚫 Force-join wizard cancelled.\n"
            "No channel was added.\n\n"
            "Use /forcejoinadd to start again."
        )
        return

    session = broadcast_sessions.pop(uid, None)
    if session:
        await delete_msg_safe(client, session["chat_id"], session.get("preview_msg_id"))
        await message.reply_text("🚫 Cancelled.\nType /broadcast to start again.")
    else:
        await message.reply_text("No active session to cancel.")
