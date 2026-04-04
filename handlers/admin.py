import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, ADMIN_ID, users_col, app
from helpers import log_event, bot_api, admin_filter


async def _resolve_user_private(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        return None, None, None

    raw = args[0].lstrip("@")

    if raw.isdigit():
        doc = await users_col.find_one({"user_id": int(raw)})
    else:
        doc = await users_col.find_one({"username": raw})

    if not doc:
        return None, None, None

    return doc["user_id"], doc.get("first_name") or "", doc.get("username")


@app.on_message(filters.command("blockuser") & admin_filter & filters.private)
async def blockuser_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage:\n"
            "/blockuser @username\n"
            "/blockuser 123456789\n\n"
            "Blocks a user from using the bot."
        )
        return

    target_id, fname, uname = await _resolve_user_private(client, message)
    if not target_id:
        await message.reply_text("❌ User not found in the database.")
        return

    doc = await users_col.find_one({"user_id": target_id})
    if not doc:
        await message.reply_text("❌ User not found.")
        return

    if doc.get("bot_banned"):
        mention = f"@{uname}" if uname else fname or str(target_id)
        await message.reply_text(
            "⚠️ ALREADY BLOCKED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 User : {mention}\n"
            f"🆔 ID   : {target_id}\n\n"
            "This user is already blocked from the bot.\n"
            "Use /unblockuser to restore access.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        )
        return

    mention = f"@{uname}" if uname else fname or str(target_id)
    await users_col.update_one(
        {"user_id": target_id},
        {"$set": {"bot_banned": True}},
    )
    await message.reply_text(
        "🚫 USER BLOCKED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User : {mention}\n"
        f"🆔 ID   : {target_id}\n\n"
        "✅ This user can no longer use the bot.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )
    asyncio.create_task(bot_api("sendMessage", {
        "chat_id": target_id,
        "text": (
            "🚫 YOUR ACCESS HAS BEEN RESTRICTED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your access to this bot has been\n"
            "suspended by the admin.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        ),
    }))
    print(f"[BLOCK] Blocked user={target_id}")
    await log_event(client, f"🚫 <b>User Blocked</b>\n👤 {mention} — 🆔 <code>{target_id}</code>")


@app.on_message(filters.command("unblockuser") & admin_filter & filters.private)
async def unblockuser_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage:\n"
            "/unblockuser @username\n"
            "/unblockuser 123456789"
        )
        return

    raw = args[0].lstrip("@")
    doc = (
        await users_col.find_one({"user_id": int(raw)})
        if raw.isdigit()
        else await users_col.find_one({"username": raw})
    )
    if not doc:
        await message.reply_text("❌ User not found in the database.")
        return

    target_id = doc["user_id"]
    fname     = doc.get("first_name", "") or ""
    uname     = doc.get("username")
    mention   = f"@{uname}" if uname else fname or str(target_id)

    await users_col.update_one(
        {"user_id": target_id},
        {"$set": {"bot_banned": False, "warn_count": 0}},
    )
    await message.reply_text(
        "✅ USER UNBLOCKED\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User : {mention}\n"
        f"🆔 ID   : {target_id}\n\n"
        "✅ Bot access fully restored.\n"
        "⚠️ Warning count also cleared.\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 DESI MLH SYSTEM"
    )
    asyncio.create_task(bot_api("sendMessage", {
        "chat_id": target_id,
        "text": (
            "✅ YOUR BOT ACCESS HAS BEEN RESTORED\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Your access to this bot has been\n"
            "restored by the admin. Welcome back!\n\n"
            "You can now use /video and /daily again.\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 DESI MLH SYSTEM"
        ),
    }))
    print(f"[BLOCK] Unblocked user={target_id}")
    await log_event(client, f"✅ <b>User Unblocked</b>\n👤 {mention} — 🆔 <code>{target_id}</code>")
