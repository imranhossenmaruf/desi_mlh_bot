import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message, ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton

from config import HTML, ADMIN_ID, filters_col, users_col, app
from helpers import log_event, _is_admin_msg, _auto_del, MAX_WARNS


@app.on_message(filters.command("addfilter") & filters.group)
async def addfilter_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    if len(args) < 2:
        await message.reply_text(
            "Usage: <code>/addfilter [word] [delete|warn|mute|ban]</code>\n"
            "Example: <code>/addfilter spam delete</code>",
            parse_mode=HTML,
        )
        return

    pattern = args[0].lower()
    action  = args[1].lower()
    if action not in ("delete", "warn", "mute", "ban"):
        await message.reply_text(
            "❌ Invalid action. Use: <code>delete</code>, <code>warn</code>, <code>mute</code>, or <code>ban</code>.",
            parse_mode=HTML,
        )
        return

    await filters_col.update_one(
        {"chat_id": message.chat.id, "pattern": pattern},
        {"$set": {"chat_id": message.chat.id, "pattern": pattern, "action": action}},
        upsert=True,
    )
    m = await message.reply_text(
        f"✅ <b>Filter Added</b>\n"
        f"📋 Word  : <code>{pattern}</code>\n"
        f"⚙️ Action : <b>{action}</b>",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 30))
    asyncio.create_task(log_event(client,
        f"⚙️ <b>Filter Added</b>  📋 {pattern}  ⚙️ {action}"
        f"  📍 {message.chat.title or message.chat.id}"
    ))


@app.on_message(filters.command("delfilter") & filters.group)
async def delfilter_improved_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage:\n"
            "<code>/delfilter spam</code>       — by word\n"
            "<code>/delfilter #2</code>         — by list number",
            parse_mode=HTML,
        )
        return

    query = args[0]

    if query.startswith("#"):
        num_str = query[1:]
        if not num_str.isdigit():
            await message.reply_text("❌ Invalid number.", parse_mode=HTML)
            return
        idx  = int(num_str) - 1
        docs = await filters_col.find({"chat_id": message.chat.id}).to_list(length=None)
        if idx < 0 or idx >= len(docs):
            await message.reply_text(f"❌ No filter #{num_str}. Use /filters to see the list.")
            return
        pattern = docs[idx]["pattern"]
    else:
        pattern = query.lower()

    result = await filters_col.delete_one({"chat_id": message.chat.id, "pattern": pattern})
    if result.deleted_count:
        m = await message.reply_text(
            f"🗑️ <b>Filter Deleted</b>: <code>{pattern}</code>", parse_mode=HTML
        )
        asyncio.create_task(_auto_del(m, 20))
    else:
        await message.reply_text(f"❌ Filter <code>{pattern}</code> not found.", parse_mode=HTML)


@app.on_message(filters.command("filters") & filters.group)
async def list_filters_improved_cmd(client: Client, message: Message):
    docs = await filters_col.find({"chat_id": message.chat.id}).to_list(length=None)
    if not docs:
        await message.reply_text("📭 No filters set in this group.")
        return
    lines = ["⚙️ <b>Active Filters</b>"]
    for i, d in enumerate(docs, 1):
        lines.append(f"  {i}. <code>{d['pattern']}</code>  →  {d['action']}")
    lines.append("\n🗑️ Delete: <code>/delfilter #N</code> or <code>/delfilter word</code>")
    await message.reply_text("\n".join(lines), parse_mode=HTML)


@app.on_message(filters.command("clearfilters") & filters.group)
async def clearfilters_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return
    result = await filters_col.delete_many({"chat_id": message.chat.id})
    m = await message.reply_text(
        f"🧹 <b>Cleared {result.deleted_count} filter(s).</b>", parse_mode=HTML
    )
    asyncio.create_task(_auto_del(m, 20))
    asyncio.create_task(log_event(client,
        f"🧹 <b>Filters Cleared</b>  count={result.deleted_count}"
        f"  📍 {message.chat.title or message.chat.id}"
    ))


@app.on_message(filters.incoming & filters.group)
async def filter_enforcer(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    if await _is_admin_msg(client, message):
        return

    text = (message.text or message.caption or "").lower()
    if not text:
        return

    docs = await filters_col.find({"chat_id": message.chat.id}).to_list(length=None)
    matched = None
    for d in docs:
        if d["pattern"] in text:
            matched = d
            break

    if not matched:
        return

    action = matched["action"]
    try:
        await message.delete()
    except Exception:
        pass

    if action == "delete":
        return

    name    = user.first_name or "User"
    mention = f'<a href="tg://user?id={user.id}">{name}</a>'

    if action == "warn":
        doc   = await users_col.find_one({"user_id": user.id})
        warns = (doc or {}).get("warn_count", 0) + 1
        await users_col.update_one(
            {"user_id": user.id},
            {"$set": {"warn_count": warns}},
            upsert=True,
        )
        _couple_btn = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔵✓⃝𝐂𝐎𝐔𝐏𝐋𝐄✓⃝🔵", url="https://t.me/+Rbj5D5wXoB4yMjE1")
        ]])
        m = await message.reply_text(
            f"⚠️ <b>Filter Warning {warns}/{MAX_WARNS}</b>\n"
            f"👤 {mention}\n"
            f"📋 Trigger: <code>{matched['pattern']}</code>",
            parse_mode=HTML,
            reply_markup=_couple_btn,
        )
        asyncio.create_task(_auto_del(m, 30))
        if warns >= MAX_WARNS:
            try:
                await client.ban_chat_member(message.chat.id, user.id)
                await users_col.update_one({"user_id": user.id}, {"$set": {"warn_count": 0}})
            except Exception:
                pass

    elif action == "mute":
        try:
            await client.restrict_chat_member(
                message.chat.id, user.id,
                ChatPermissions(can_send_messages=False),
            )
            m = await message.reply_text(
                f"🔇 <b>Auto-Muted</b>  {mention}\n"
                f"📋 Trigger: <code>{matched['pattern']}</code>",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 30))
        except Exception:
            pass

    elif action == "ban":
        try:
            await client.ban_chat_member(message.chat.id, user.id)
            m = await message.reply_text(
                f"🚫 <b>Auto-Banned</b>  {mention}\n"
                f"📋 Trigger: <code>{matched['pattern']}</code>",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 30))
        except Exception:
            pass
