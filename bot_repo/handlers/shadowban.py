import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, ADMIN_ID, shadowban_col, app
from helpers import log_event, _is_admin_msg


@app.on_message(filters.command("shadowban") & filters.group)
async def shadowban_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("Reply to a user's message to shadow-ban them.")
        return

    user = message.reply_to_message.from_user
    await shadowban_col.update_one(
        {"chat_id": message.chat.id, "user_id": user.id},
        {"$set": {"chat_id": message.chat.id, "user_id": user.id}},
        upsert=True,
    )
    try:
        await message.delete()
        await message.reply_to_message.delete()
    except Exception:
        pass
    asyncio.create_task(log_event(client,
        f"🕵️ <b>Shadow Banned</b>  👤 {user.first_name} <code>{user.id}</code>"
        f"  📍 {message.chat.title or message.chat.id}"
    ))
    print(f"[SHADOWBAN] user={user.id} in chat={message.chat.id}")


@app.on_message(filters.command("unshadowban") & filters.group)
async def unshadowban_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply_text("Reply to a shadow-banned user's message.")
        return

    user = message.reply_to_message.from_user
    await shadowban_col.delete_one({"chat_id": message.chat.id, "user_id": user.id})
    m = await message.reply_text(
        f"✅ <b>Shadow-ban removed</b> for {user.first_name} (<code>{user.id}</code>).",
        parse_mode=HTML,
    )
    asyncio.create_task(log_event(client,
        f"✅ <b>Shadow-ban Removed</b>  👤 {user.first_name} <code>{user.id}</code>"
        f"  📍 {message.chat.title or message.chat.id}"
    ))


@app.on_message(filters.command("shadowbans") & filters.group)
async def shadowbans_list_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return
    docs  = await shadowban_col.find({"chat_id": message.chat.id}).to_list(length=None)
    total = len(docs)
    if not total:
        await message.reply_text("✅ No shadow-banned users in this group.")
        return
    lines = [f"🕵️ <b>Shadow-banned Users ({total})</b>"]
    for i, d in enumerate(docs, 1):
        lines.append(f"  {i}. <code>{d['user_id']}</code>")
    await message.reply_text("\n".join(lines), parse_mode=HTML)


@app.on_message(filters.command("clearshadowbans") & filters.group)
async def clearshadowbans_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return
    result = await shadowban_col.delete_many({"chat_id": message.chat.id})
    await message.reply_text(
        f"🧹 <b>Cleared {result.deleted_count} shadow-ban(s).</b>",
        parse_mode=HTML,
    )
    asyncio.create_task(log_event(client,
        f"🧹 <b>Shadow-bans Cleared</b>  📍 {message.chat.title or message.chat.id}  "
        f"count={result.deleted_count}"
    ))


@app.on_message(filters.incoming & filters.group)
async def shadowban_enforcer(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    doc = await shadowban_col.find_one({"chat_id": message.chat.id, "user_id": user.id})
    if not doc:
        return
    try:
        await message.delete()
    except Exception:
        pass
