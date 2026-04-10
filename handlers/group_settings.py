"""
Group Settings Manager - Handles all group configuration and automation features
- Granular group settings (toggle features)
- Auto-reaction system
- Keyword-based auto-reply
- Custom buttons in messages
- Auto-approve & confirmation
"""

import asyncio
import json
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatMember
)
from pyrogram.enums import ChatMemberStatus

from config import (
    HTML, ADMIN_ID, db,
    group_settings_col, auto_reactions_col, keyword_triggers_col,
    group_buttons_col, auto_approve_logs_col, join_requests_col,
    app
)
from helpers import log_event, _is_admin_msg, _auto_del, bot_api


# ══════════════════════════════════════════════════════════════════════════════
# ═════════════════════════ DATA MODEL HELPERS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ══════════════════════════════════════════════════════════════════════════════

async def get_group_settings(chat_id: int) -> dict:
    """Get or create default group settings."""
    doc = await group_settings_col.find_one({"chat_id": chat_id})
    if not doc:
        default = {
            "chat_id": chat_id,
            "features": {
                "video": True,
                "video_msg": True,
                "welcome": True,
                "filters": True,
                "antiflood": True,
                "nightmode": False,
                "auto_reactions": False,
                "keyword_reply": False,
            },
            "auto_approve": False,
            "log_channel": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        await group_settings_col.insert_one(default)
        return default
    return doc


async def update_group_settings(chat_id: int, settings: dict) -> None:
    """Update group settings."""
    await group_settings_col.update_one(
        {"chat_id": chat_id},
        {"$set": {**settings, "updated_at": datetime.utcnow()}},
        upsert=True,
    )


async def get_keyword_triggers(chat_id: int) -> list[dict]:
    """Get all keyword triggers for a group."""
    return await keyword_triggers_col.find({"chat_id": chat_id}).to_list(length=None)


async def add_keyword_trigger(chat_id: int, keyword: str, response: str) -> None:
    """Add a keyword trigger."""
    await keyword_triggers_col.update_one(
        {"chat_id": chat_id, "keyword": keyword.lower()},
        {"$set": {
            "chat_id": chat_id,
            "keyword": keyword.lower(),
            "response": response,
            "created_at": datetime.utcnow(),
        }},
        upsert=True,
    )


async def delete_keyword_trigger(chat_id: int, keyword: str) -> bool:
    """Delete a keyword trigger, return True if found."""
    result = await keyword_triggers_col.delete_one({
        "chat_id": chat_id,
        "keyword": keyword.lower()
    })
    return result.deleted_count > 0


async def get_auto_reactions(chat_id: int) -> dict | None:
    """Get auto-reaction config for a group."""
    return await auto_reactions_col.find_one({"chat_id": chat_id})


async def set_auto_reactions(chat_id: int, emoji_list: list[str]) -> None:
    """Set auto-reactions for a group."""
    await auto_reactions_col.update_one(
        {"chat_id": chat_id},
        {"$set": {
            "chat_id": chat_id,
            "reactions": emoji_list,
            "enabled": True,
            "updated_at": datetime.utcnow(),
        }},
        upsert=True,
    )


async def get_group_buttons(chat_id: int) -> dict | None:
    """Get custom buttons template for a group."""
    return await group_buttons_col.find_one({"chat_id": chat_id})


async def set_group_buttons(chat_id: int, buttons_data: dict) -> None:
    """Set custom buttons for group."""
    await group_buttons_col.update_one(
        {"chat_id": chat_id},
        {"$set": {
            "chat_id": chat_id,
            **buttons_data,
            "updated_at": datetime.utcnow(),
        }},
        upsert=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ═════════════════════════ /GROUP COMMAND ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ══════════════════════════════════════════════════════════════════════════════

def _group_settings_keyboard(chat_id: int):
    """Main group settings menu keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚙️ Features", callback_data=f"group_features_{chat_id}"),
            InlineKeyboardButton("📝 Keywords", callback_data=f"group_keywords_{chat_id}"),
        ],
        [
            InlineKeyboardButton("😂 Reactions", callback_data=f"group_reactions_{chat_id}"),
            InlineKeyboardButton("🔘 Buttons", callback_data=f"group_buttons_{chat_id}"),
        ],
        [
            InlineKeyboardButton("✅ Auto-Approve", callback_data=f"group_autoapprove_{chat_id}"),
            InlineKeyboardButton("📋 Info", callback_data=f"group_info_{chat_id}"),
        ],
        [InlineKeyboardButton("❌ Close", callback_data="group_close")],
    ])


@app.on_message(filters.command("group") & filters.group)
async def group_cmd(client: Client, message: Message):
    """Main group settings command."""
    if not await _is_admin_msg(client, message):
        return

    chat_id = message.chat.id
    settings = await get_group_settings(chat_id)

    text = (
        "<b>⚙️ GROUP SETTINGS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Manage and configure bot features for this group:</i>\n\n"
        "• <b>⚙️ Features</b> — Toggle features on/off\n"
        "• <b>📝 Keywords</b> — Setup trigger phrases & responses\n"
        "• <b>😂 Reactions</b> — Auto-reactions to messages\n"
        "• <b>🔘 Buttons</b> — Add custom inline buttons\n"
        "• <b>✅ Auto-Approve</b> — Approve join requests automatically\n"
        "• <b>📋 Info</b> — View current settings\n"
    )

    m = await message.reply_text(
        text,
        parse_mode=HTML,
        reply_markup=_group_settings_keyboard(chat_id),
    )
    asyncio.create_task(_auto_del(m, 120))


# ══════════════════════════════════════════════════════════════════════════════
# ═════════════════════════ CALLBACK HANDLERS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ══════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^group_features_"))
async def handle_group_features(client: Client, query: CallbackQuery):
    """Show feature toggle menu."""
    chat_id = int(query.data.split("_")[-1])
    settings = await get_group_settings(chat_id)
    features = settings.get("features", {})

    text = "<b>⚙️ TOGGLE FEATURES</b>\n" "━━━━━━━━━━━━━━━━━━━━━\n\n"
    buttons = []

    feature_names = {
        "video": "🎬 Video Command",
        "welcome": "👋 Welcome Messages",
        "filters": "🚫 Filters",
        "antiflood": "🌊 Anti-Flood",
        "nightmode": "🌙 Nightmode",
        "auto_reactions": "😂 Auto-Reactions",
        "keyword_reply": "💬 Keyword Reply",
    }

    for feature_key, feature_label in feature_names.items():
        enabled = features.get(feature_key, True)
        status = "✅" if enabled else "❌"
        text += f"{status} {feature_label}\n"
        buttons.append([InlineKeyboardButton(
            f"{status} {feature_key}",
            callback_data=f"group_toggle_{chat_id}_{feature_key}"
        )])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"group_back_{chat_id}")])

    await query.edit_message_text(text, parse_mode=HTML)
    await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
    await query.answer()


@app.on_callback_query(filters.regex(r"^group_toggle_"))
async def handle_toggle_feature(client: Client, query: CallbackQuery):
    """Toggle feature on/off."""
    parts = query.data.split("_")
    chat_id = int(parts[2])
    feature = parts[3]

    settings = await get_group_settings(chat_id)
    current = settings["features"].get(feature, True)
    settings["features"][feature] = not current

    await update_group_settings(chat_id, {"features": settings["features"]})

    await query.answer(
        f"✅ {feature} toggled {'ON' if not current else 'OFF'}",
        show_alert=True
    )

    # Refresh the features menu
    await handle_group_features(client, query)


@app.on_callback_query(filters.regex(r"^group_keywords_"))
async def handle_group_keywords(client: Client, query: CallbackQuery):
    """Show keyword management menu."""
    chat_id = int(query.data.split("_")[-1])
    triggers = await get_keyword_triggers(chat_id)

    text = "<b>📝 KEYWORD TRIGGERS</b>\n" "━━━━━━━━━━━━━━━━━━━━━\n\n"

    if not triggers:
        text += "<i>No keywords set yet.</i>\n"
    else:
        for i, trigger in enumerate(triggers, 1):
            text += f"<b>{i}.</b> <code>{trigger['keyword']}</code>\n"

    text += "\n<i>To add/remove keywords, type in the group:\n" "</i><code>/addkeyword word response</code>\n" "<code>/delkeyword word</code>"

    buttons = [
        [InlineKeyboardButton("🔙 Back", callback_data=f"group_back_{chat_id}")],
    ]

    await query.edit_message_text(text, parse_mode=HTML)
    await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
    await query.answer()


@app.on_callback_query(filters.regex(r"^group_reactions_"))
async def handle_group_reactions(client: Client, query: CallbackQuery):
    """Show auto-reactions menu."""
    chat_id = int(query.data.split("_")[-1])
    reactions_config = await get_auto_reactions(chat_id)

    text = "<b>😂 AUTO-REACTIONS</b>\n" "━━━━━━━━━━━━━━━━━━━━━\n\n"

    if reactions_config and reactions_config.get("enabled"):
        reactions = reactions_config.get("reactions", [])
        text += f"<b>Reactions:</b> {' '.join(reactions)}\n\n"
        text += "<i>Messages will get these reactions automatically.</i>"
    else:
        text += "<i>Auto-reactions are currently disabled.</i>\n\n"

    text += "\n\n<i>To set reactions, type in the group:\n</i><code>/setreactions 😂 😍 🔥 ⭐</code>"

    buttons = [
        [InlineKeyboardButton("🔙 Back", callback_data=f"group_back_{chat_id}")],
    ]

    await query.edit_message_text(text, parse_mode=HTML)
    await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
    await query.answer()


@app.on_callback_query(filters.regex(r"^group_buttons_"))
async def handle_group_buttons(client: Client, query: CallbackQuery):
    """Show custom buttons menu."""
    chat_id = int(query.data.split("_")[-1])
    buttons_config = await get_group_buttons(chat_id)

    text = "<b>🔘 CUSTOM BUTTONS</b>\n" "━━━━━━━━━━━━━━━━━━━━━\n\n"

    if buttons_config:
        text += f"<b>Name:</b> {buttons_config.get('name', 'Default')}\n\n"
        text += "<i>Custom buttons are configured for this group.</i>"
    else:
        text += "<i>No custom buttons configured yet.</i>\n\n"

    text += "\n\n<i>To add buttons, type in the group:\n</i><code>/setbuttons</code>"

    buttons = [
        [InlineKeyboardButton("🔙 Back", callback_data=f"group_back_{chat_id}")],
    ]

    await query.edit_message_text(text, parse_mode=HTML)
    await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
    await query.answer()


@app.on_callback_query(filters.regex(r"^group_autoapprove_"))
async def handle_auto_approve(client: Client, query: CallbackQuery):
    """Show auto-approve settings."""
    chat_id = int(query.data.split("_")[-1])
    settings = await get_group_settings(chat_id)
    auto_approve = settings.get("auto_approve", False)

    text = (
        "<b>✅ AUTO-APPROVE SETTINGS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Status:</b> {'🟢 Enabled' if auto_approve else '🔴 Disabled'}\n\n"
        "When enabled, join requests are approved automatically.\n"
        "A confirmation message is sent to the user.\n"
    )

    buttons = [
        [InlineKeyboardButton(
            "✅ Enable" if not auto_approve else "❌ Disable",
            callback_data=f"group_toggle_autoapprove_{chat_id}"
        )],
        [InlineKeyboardButton("🔙 Back", callback_data=f"group_back_{chat_id}")],
    ]

    await query.edit_message_text(text, parse_mode=HTML)
    await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
    await query.answer()


@app.on_callback_query(filters.regex(r"^group_toggle_autoapprove_"))
async def handle_toggle_autoapprove(client: Client, query: CallbackQuery):
    """Toggle auto-approve."""
    chat_id = int(query.data.split("_")[-1])
    settings = await get_group_settings(chat_id)
    current = settings.get("auto_approve", False)
    new_value = not current

    await update_group_settings(chat_id, {"auto_approve": new_value})
    await query.answer(f"✅ Auto-Approve {'enabled' if new_value else 'disabled'}", show_alert=True)

    # Refresh the menu
    await handle_auto_approve(client, query)


@app.on_callback_query(filters.regex(r"^group_info_"))
async def handle_group_info(client: Client, query: CallbackQuery):
    """Show group info and summary."""
    chat_id = int(query.data.split("_")[-1])
    settings = await get_group_settings(chat_id)
    triggers = await get_keyword_triggers(chat_id)
    reactions_config = await get_auto_reactions(chat_id)

    features = settings.get("features", {})
    enabled_features = [k for k, v in features.items() if v]

    text = (
        "<b>📋 GROUP SETTINGS INFO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Features Enabled:</b> {len(enabled_features)}/7\n"
        f"  {', '.join(enabled_features) if enabled_features else 'None'}\n\n"
        f"<b>Keyword Triggers:</b> {len(triggers)}\n"
        f"<b>Auto-Reactions:</b> {'✅' if reactions_config and reactions_config.get('enabled') else '❌'}\n"
        f"<b>Auto-Approve:</b> {'✅' if settings.get('auto_approve') else '❌'}\n\n"
        f"<b>Last Updated:</b> {settings.get('updated_at', 'Never').strftime('%d %b %Y')}\n"
    )

    buttons = [
        [InlineKeyboardButton("🔙 Back", callback_data=f"group_back_{chat_id}")],
    ]

    await query.edit_message_text(text, parse_mode=HTML)
    await query.edit_message_reply_markup(InlineKeyboardMarkup(buttons))
    await query.answer()


@app.on_callback_query(filters.regex(r"^group_back_"))
async def handle_group_back(client: Client, query: CallbackQuery):
    """Go back to main menu."""
    chat_id = int(query.data.split("_")[-1])
    await query.edit_message_text(
        "<b>⚙️ GROUP SETTINGS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<i>Manage and configure bot features for this group:</i>\n\n"
        "• <b>⚙️ Features</b> — Toggle features on/off\n"
        "• <b>📝 Keywords</b> — Setup trigger phrases & responses\n"
        "• <b>😂 Reactions</b> — Auto-reactions to messages\n"
        "• <b>🔘 Buttons</b> — Add custom inline buttons\n"
        "• <b>✅ Auto-Approve</b> — Approve join requests automatically\n"
        "• <b>📋 Info</b> — View current settings\n",
        parse_mode=HTML,
    )
    await query.edit_message_reply_markup(_group_settings_keyboard(chat_id))
    await query.answer()


@app.on_callback_query(filters.regex(r"^group_close"))
async def handle_group_close(client: Client, query: CallbackQuery):
    """Close settings menu."""
    await query.message.delete()
    await query.answer("✅ Closed", show_alert=False)


# ══════════════════════════════════════════════════════════════════════════════
# ═════════════════════════ KEYWORD AUTO-REPLY ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("addkeyword") & filters.group)
async def addkeyword_cmd(client: Client, message: Message):
    """Add a keyword trigger."""
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    if len(args) < 2:
        m = await message.reply_text(
            "Usage: <code>/addkeyword word response text</code>\n\n"
            "Example:\n"
            "<code>/addkeyword hello Hey there! 👋</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 30))
        return

    keyword = args[0].lower()
    response = " ".join(args[1:])

    await add_keyword_trigger(message.chat.id, keyword, response)
    m = await message.reply_text(
        f"✅ <b>Keyword Added</b>\n"
        f"🔑 Keyword: <code>{keyword}</code>\n"
        f"💬 Response: {response}",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))

    await log_event(client, 
        f"📝 <b>Keyword Added</b>\n"
        f"🔑 {keyword}\n"
        f"📍 {message.chat.title or message.chat.id}"
    )


@app.on_message(filters.command("delkeyword") & filters.group)
async def delkeyword_cmd(client: Client, message: Message):
    """Delete a keyword trigger."""
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    if not args:
        m = await message.reply_text(
            "Usage: <code>/delkeyword word</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 30))
        return

    keyword = args[0].lower()
    deleted = await delete_keyword_trigger(message.chat.id, keyword)

    if deleted:
        m = await message.reply_text(
            f"🗑️ <b>Keyword Deleted</b>\n"
            f"🔑 <code>{keyword}</code>",
            parse_mode=HTML,
        )
    else:
        m = await message.reply_text(
            f"❌ Keyword <code>{keyword}</code> not found.",
            parse_mode=HTML,
        )

    asyncio.create_task(_auto_del(m, 30))


@app.on_message(filters.command("keywords") & filters.group)
async def keywords_cmd(client: Client, message: Message):
    """List all keyword triggers."""
    if not await _is_admin_msg(client, message):
        return

    triggers = await get_keyword_triggers(message.chat.id)

    if not triggers:
        m = await message.reply_text("📭 No keyword triggers set.", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 30))
        return

    text = "<b>📝 KEYWORD TRIGGERS</b>\n" "━━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, trigger in enumerate(triggers, 1):
        text += f"<b>{i}.</b> <code>{trigger['keyword']}</code>\n    → {trigger['response']}\n\n"

    text += "🗑️ Delete: <code>/delkeyword word</code>"

    await message.reply_text(text, parse_mode=HTML)


@app.on_message(filters.incoming & filters.group, group=10)
async def check_keywords(client: Client, message: Message):
    """Check message for keyword triggers and reply."""
    if message.from_user and (message.from_user.is_bot or message.from_user.is_self):
        return

    # Check if keyword reply is enabled
    settings = await get_group_settings(message.chat.id)
    if not settings.get("features", {}).get("keyword_reply", False):
        return

    text = message.text or message.caption or ""
    if not text:
        return

    text_lower = text.lower()
    triggers = await get_keyword_triggers(message.chat.id)

    matched = None
    for trigger in triggers:
        if trigger["keyword"] in text_lower:
            matched = trigger
            break

    if matched:
        try:
            await message.reply_text(
                matched["response"],
                parse_mode=HTML,
                quote=True,
            )
        except Exception as e:
            print(f"[KEYWORDS] Failed to reply: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ═════════════════════════ AUTO-REACTIONS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("setreactions") & filters.group)
async def setreactions_cmd(client: Client, message: Message):
    """Set auto-reactions for the group."""
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    if not args:
        m = await message.reply_text(
            "Usage: <code>/setreactions emoji1 emoji2 emoji3...</code>\n\n"
            "Example:\n"
            "<code>/setreactions 😂 😍 🔥 ⭐</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 30))
        return

    emoji_list = args[:10]  # Limit to 10 emojis
    await set_auto_reactions(message.chat.id, emoji_list)

    m = await message.reply_text(
        f"✅ <b>Auto-Reactions Set</b>\n\n"
        f"Reactions: {' '.join(emoji_list)}\n\n"
        f"<i>Messages will now get these reactions automatically.</i>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))

    await log_event(client,
        f"😂 <b>Auto-Reactions Set</b>\n"
        f"Reactions: {' '.join(emoji_list)}\n"
        f"📍 {message.chat.title or message.chat.id}"
    )


@app.on_message(filters.incoming & filters.group, group=15)
async def apply_auto_reactions(client: Client, message: Message):
    """Apply auto-reactions to messages."""
    if message.from_user and (message.from_user.is_bot or message.from_user.is_self):
        return

    settings = await get_group_settings(message.chat.id)
    if not settings.get("features", {}).get("auto_reactions", False):
        return

    reactions_config = await get_auto_reactions(message.chat.id)
    if not reactions_config or not reactions_config.get("enabled"):
        return

    reactions = reactions_config.get("reactions", [])
    if not reactions:
        return

    # Small delay — let SPAM filter run first (group=0) before we try to react
    await asyncio.sleep(0.5)

    # Bot accounts cannot use MTProto SendReaction — use Bot API instead
    for emoji in reactions:
        try:
            result = await bot_api("setMessageReaction", {
                "chat_id":    message.chat.id,
                "message_id": message.id,
                "reaction":   [{"type": "emoji", "emoji": emoji}],
                "is_big":     False,
            })
            if result.get("ok"):
                break
            err = result.get("description", "")
            # Message already deleted by spam filter — silently skip
            if "message to react not found" in err.lower() or "message not found" in err.lower():
                return
            if "REACTION_INVALID" in err:
                continue
            # Unrecognised error — log once and stop trying
            print(f"[AUTO_REACTIONS] setMessageReaction failed: {err}")
            break
        except Exception as e:
            err_str = str(e).lower()
            if "message to react not found" in err_str or "message not found" in err_str:
                return   # Silently ignore — SPAM filter deleted it
            print(f"[AUTO_REACTIONS] Exception reacting with {emoji!r}: {e}")
            break


# ══════════════════════════════════════════════════════════════════════════════
# ═════════════════════════ AUTO-APPROVE ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ══════════════════════════════════════════════════════════════════════════════

@app.on_chat_join_request(group=25)
async def handle_join_request(client: Client, request):
    """Handle join requests - approve if auto_approve is enabled for this group."""
    if not request.chat or not request.from_user:
        return

    chat_id = request.chat.id
    user = request.from_user

    settings = await get_group_settings(chat_id)
    if not settings.get("auto_approve", False):
        return

    # Approve the join request
    try:
        await client.approve_chat_join_request(chat_id, user.id)

        # Log the approval
        await auto_approve_logs_col.insert_one({
            "chat_id": chat_id,
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "approved_at": datetime.utcnow(),
        })

        # Send confirmation to user
        try:
            await client.send_message(
                user.id,
                f"✅ <b>Request Approved!</b>\n\n"
                f"Your join request has been approved for <b>{request.chat.title}</b>.\n"
                f"Welcome! 👋",
                parse_mode=HTML,
            )
        except Exception:
            pass  # User might have blocked the bot

        # Log the event
        mention = f"@{user.username}" if user.username else user.first_name or "User"
        await log_event(client,
            f"✅ <b>Join Request Approved</b>\n"
            f"👤 {mention}\n"
            f"🆔 <code>{user.id}</code>\n"
            f"📍 {request.chat.title or chat_id}"
        )

        print(f"[AUTO_APPROVE] Approved {user.id} in {chat_id}")

    except Exception as e:
        print(f"[AUTO_APPROVE] Failed to approve {user.id} in {chat_id}: {e}")


@app.on_message(filters.command("autoapprove") & filters.group)
async def autoapprove_cmd(client: Client, message: Message):
    """Toggle auto-approve for the group."""
    if not await _is_admin_msg(client, message):
        return

    settings = await get_group_settings(message.chat.id)
    current = settings.get("auto_approve", False)
    new_value = not current

    await update_group_settings(message.chat.id, {"auto_approve": new_value})

    status = "🟢 Enabled" if new_value else "🔴 Disabled"
    m = await message.reply_text(
        f"✅ <b>Auto-Approve {status}</b>\n\n"
        f"Join requests will now be {'automatically approved' if new_value else 'handled normally'}.",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))


# ══════════════════════════════════════════════════════════════════════════════
# ═════════════════════════ CUSTOM BUTTONS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("setbuttons") & filters.group)
async def setbuttons_cmd(client: Client, message: Message):
    """Setup custom buttons for the group."""
    if not await _is_admin_msg(client, message):
        return

    m = await message.reply_text(
        "<b>🔘 CUSTOM BUTTONS SETUP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send buttons in the format:\n\n"
        "<code>[Button Name|URL] [Button2|URL2]</code>\n\n"
        "Example:\n"
        "<code>[Visit Site|https://example.com] [Join Channel|https://t.me/channel]</code>\n\n"
        "Or inline buttons (each on new line):\n"
        "<code>Button Name|URL</code>\n"
        "<code>Button2|URL2</code>",
        parse_mode=HTML,
    )

    # Here you would typically set up a state machine for collecting button data
    # For now, we just provide the command format to the admin
    asyncio.create_task(_auto_del(m, 60))


@app.on_message(filters.command("attachbuttons") & filters.group)
async def attachbuttons_cmd(client: Client, message: Message):
    """Attach buttons to next bot message."""
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    if not args:
        m = await message.reply_text(
            "Usage: <code>/attachbuttons button_name</code>\n\n"
            "Available buttons:\n"
            "• default\n"
            "• custom_set_name",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 30))
        return

    # Store the button template name for the next message
    # This would be implemented in your message handler
    m = await message.reply_text(
        f"✅ Next bot messages will use buttons: <code>{args[0]}</code>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
