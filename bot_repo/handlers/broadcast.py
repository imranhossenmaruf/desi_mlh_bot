"""
Multi-Level Broadcast System
==============================
Command: /broadcast [all|private|groups]

Sub-options:
  all      — broadcast to everyone (users + groups)
  private  — only individual private chat users
  groups   — only managed groups

Safety: 0.2-second delay between every single message sent.
"""

import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    HTML, ADMIN_ID, ADMIN_IDS,
    broadcast_sessions,
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

BROADCAST_DELAY = 0.2  # 0.2 seconds between each message


def _new_session(chat_id: int, mode: str = "broadcast") -> dict:
    return {
        "state":            STATE_AUDIENCE,
        "audience":         "all",
        "join_after":       None,
        "msg_type":         None,
        "text":             "",
        "entities":         [],
        "media_chat_id":    None,
        "media_msg_id":     None,
        "extra_buttons":    None,
        "preview_msg_id":   None,
        "chat_id":          chat_id,
        "mode":             mode,
        "broadcast_delay":  BROADCAST_DELAY,
    }


@app.on_message(
    filters.command("broadcast") & admin_filter & filters.private
)
async def broadcast_start(client: Client, message: Message):
    """
    /broadcast            — show audience menu
    /broadcast all        — everyone (users + groups)
    /broadcast private    — private users only
    /broadcast groups     — groups only
    """
    args = message.command[1:]
    session = _new_session(message.chat.id, mode="broadcast")

    audience_map = {"all": "all", "private": "private", "groups": "groups"}
    if args and args[0].lower() in audience_map:
        # Direct shortcut: /broadcast all / private / groups
        session["audience"] = audience_map[args[0].lower()]
        session["state"]    = STATE_CONTENT
        broadcast_sessions[message.from_user.id] = session

        label = {
            "all":     "ALL (Users + Groups)",
            "private": "PRIVATE (Users only)",
            "groups":  "GROUPS only",
        }[session["audience"]]

        await message.reply_text(
            f"<b>Broadcast</b>\n\n"
            f"Audience: <b>{label}</b>\n"
            f"Delay: <b>{BROADCAST_DELAY}s</b> between each message\n\n"
            f"Send the message you want to broadcast.\n"
            f"Type /cancel to cancel.",
            parse_mode=HTML,
        )
        return

    # No sub-option — show buttons
    broadcast_sessions[message.from_user.id] = session
    await message.reply_text(
        "<b>Broadcast — Choose Audience</b>\n\n"
        "Who should receive this broadcast?\n\n"
        "Or use a shortcut:\n"
        "  /broadcast all\n"
        "  /broadcast private\n"
        "  /broadcast groups",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("All (Users + Groups)", callback_data="bc_all")],
            [
                InlineKeyboardButton("Private Users Only", callback_data="bc_private"),
                InlineKeyboardButton("Groups Only",        callback_data="bc_groups"),
            ],
        ]),
    )


@app.on_callback_query(filters.regex("^bc_all$") & admin_filter)
async def bc_select_all(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session:
        return await cq.answer("No active session.", show_alert=True)
    session["audience"] = "all"
    session["state"]    = STATE_CONTENT
    await cq.edit_message_text(
        f"<b>Broadcast to ALL</b>\n\n"
        f"Audience: Users + Groups\n"
        f"Delay: {BROADCAST_DELAY}s per message\n\n"
        f"Send your broadcast message.\nType /cancel to cancel.",
        parse_mode=HTML,
    )
    await cq.answer("All selected")


@app.on_callback_query(filters.regex("^bc_private$") & admin_filter)
async def bc_private_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session:
        return await cq.answer("No active session.", show_alert=True)
    session["audience"] = "private"
    session["state"]    = STATE_CONTENT
    await cq.edit_message_text(
        f"<b>Broadcast to Private Users</b>\n\n"
        f"Audience: Individual users only\n"
        f"Delay: {BROADCAST_DELAY}s per message\n\n"
        f"Send your broadcast message.\nType /cancel to cancel.",
        parse_mode=HTML,
    )
    await cq.answer("Private users selected")


@app.on_callback_query(filters.regex("^bc_groups$") & admin_filter)
async def bc_groups_cb(client: Client, cq: CallbackQuery):
    session = broadcast_sessions.get(cq.from_user.id)
    if not session:
        return await cq.answer("No active session.", show_alert=True)
    session["audience"] = "groups"
    session["state"]    = STATE_CONTENT
    await cq.edit_message_text(
        f"<b>Broadcast to Groups</b>\n\n"
        f"Audience: All managed groups\n"
        f"Delay: {BROADCAST_DELAY}s per message\n\n"
        f"Send your broadcast message.\nType /cancel to cancel.",
        parse_mode=HTML,
    )
    await cq.answer("Groups selected")


@app.on_message(filters.command("sbc") & admin_filter & filters.private)
async def sbc_start(client: Client, message: Message):
    """Scheduled broadcast — same audience menu."""
    session = _new_session(message.chat.id, mode="sbc")
    broadcast_sessions[message.from_user.id] = session
    await message.reply_text(
        "<b>Scheduled Broadcast</b>\n\nChoose who to send to:\n\nType /cancel to cancel.",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("All (Users + Groups)", callback_data="bc_all")],
            [
                InlineKeyboardButton("Private Users Only", callback_data="bc_private"),
                InlineKeyboardButton("Groups Only",        callback_data="bc_groups"),
            ],
        ]),
    )


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
        "<b>Add Inline Button</b>\n\n"
        "Format: <code>Button Name | https://link.com</code>\n\n"
        "Two buttons in one row:\n"
        "<code>Btn 1 | link1.com && Btn 2 | link2.com</code>\n\n"
        "Multiple rows — one per line.\n\nType /cancel to stop.",
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
        "<b>Attach Media</b>\n\nSend a photo, video, file, or sticker.\nType /cancel to stop.",
        parse_mode=HTML,
    )
    await cq.answer()
