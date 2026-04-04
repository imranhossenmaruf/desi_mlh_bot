import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, ADMIN_ID, welcome_col, rules_col, app
from helpers import log_event, _is_admin_msg, _auto_del


@app.on_message(filters.command("welcome") & filters.group)
async def welcome_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args    = message.command[1:]
    sub     = args[0].lower() if args else ""
    chat_id = message.chat.id

    if sub == "off":
        await welcome_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"enabled": False}},
            upsert=True,
        )
        m = await message.reply_text("❌ <b>Welcome message disabled.</b>", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 20))
        return

    if sub == "status":
        doc = await welcome_col.find_one({"chat_id": chat_id})
        if not doc or not doc.get("enabled"):
            await message.reply_text("❌ Welcome message is <b>disabled</b>.", parse_mode=HTML)
        else:
            text = doc.get("text", "(empty)")
            await message.reply_text(
                f"✅ <b>Welcome Message (ON)</b>\n\n{text}",
                parse_mode=HTML,
            )
        return

    if sub == "set":
        custom_text = " ".join(args[1:]) if len(args) > 1 else ""
        if not custom_text:
            await message.reply_text(
                "Usage: <code>/welcome set Hello {name}, welcome to {group}!</code>\n\n"
                "Variables: <code>{name}</code>, <code>{group}</code>",
                parse_mode=HTML,
            )
            return
        await welcome_col.update_one(
            {"chat_id": chat_id},
            {"$set": {"chat_id": chat_id, "enabled": True, "text": custom_text}},
            upsert=True,
        )
        preview = custom_text.replace("{name}", "John").replace("{group}", message.chat.title or "Group")
        await message.reply_text(
            f"✅ <b>Welcome Message Set</b>\n\n"
            f"Preview:\n{preview}",
            parse_mode=HTML,
        )
        return

    await message.reply_text(
        "Usage:\n"
        "<code>/welcome set [text]</code>  — Set (use {name}, {group})\n"
        "<code>/welcome off</code>          — Disable\n"
        "<code>/welcome status</code>       — Show current",
        parse_mode=HTML,
    )


@app.on_message(filters.new_chat_members, group=5)
async def welcome_new_member(client: Client, message: Message):
    chat_id     = message.chat.id
    doc         = await welcome_col.find_one({"chat_id": chat_id})
    group_title = message.chat.title or "the group"

    for user in message.new_chat_members:
        if user.is_bot:
            continue

        name = user.first_name or "User"

        # ── Group welcome (custom or default) ────────────────────────────────
        if doc and doc.get("enabled"):
            template = doc.get("text", "Welcome {name} to {group}!")
            grp_text = template.replace("{name}", name).replace("{group}", group_title)
        else:
            grp_text = (
                f"👋 Welcome, <b>{name}</b>!\n\n"
                f"Glad to have you in <b>{group_title}</b>. "
                f"Feel free to start chatting! 🎉"
            )

        try:
            m = await client.send_message(chat_id, grp_text, parse_mode=HTML)
            asyncio.create_task(_auto_del(m, 120))
        except Exception as e:
            print(f"[WELCOME] Group msg failed: {e}")

        # ── DM welcome ────────────────────────────────────────────────────────
        try:
            bot_username = ""
            try:
                me = await client.get_me()
                bot_username = me.username or ""
            except Exception:
                pass

            dm_text = (
                f"👋 <b>Welcome to {group_title}, {name}!</b>\n\n"
                f"I'm the group assistant bot. Here's what I can do for you:\n\n"
                f"🎬 <b>/video</b> — Watch exclusive videos\n"
                f"🎁 <b>/daily</b> — Claim daily bonus\n"
                f"💎 <b>/buypremium</b> — Unlock premium features\n"
                f"👤 <b>/profile</b> — View your profile\n\n"
                f"Type /start to register and get full access!"
            )
            await client.send_message(user.id, dm_text, parse_mode=HTML)
        except Exception:
            pass  # user hasn't started the bot yet — silent fail is correct


@app.on_message(filters.command("setrules") & filters.group)
async def setrules_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage: <code>/setrules [text]</code>\n\n"
            "Example:\n"
            "<code>/setrules 1. Be respectful\n2. No spam\n3. No adult content</code>",
            parse_mode=HTML,
        )
        return

    rules_text = " ".join(args)
    await rules_col.update_one(
        {"chat_id": message.chat.id},
        {"$set": {"chat_id": message.chat.id, "text": rules_text}},
        upsert=True,
    )
    m = await message.reply_text(
        f"✅ <b>Rules Updated</b>\n\n{rules_text}",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 60))
    asyncio.create_task(log_event(client,
        f"📜 <b>Rules Set</b>  📍 {message.chat.title or message.chat.id}"
    ))


@app.on_message(filters.command("rules") & filters.group)
async def rules_cmd(client: Client, message: Message):
    doc = await rules_col.find_one({"chat_id": message.chat.id})
    if not doc or not doc.get("text"):
        await message.reply_text("📭 No rules set for this group yet.")
        return
    m = await message.reply_text(
        f"📜 <b>GROUP RULES</b>\n\n{doc['text']}",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 120))


@app.on_message(filters.command("clearrules") & filters.group)
async def clearrules_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return
    await rules_col.delete_one({"chat_id": message.chat.id})
    m = await message.reply_text("🗑️ <b>Rules cleared.</b>", parse_mode=HTML)
    asyncio.create_task(_auto_del(m, 20))
