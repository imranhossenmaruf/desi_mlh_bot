import asyncio
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import Message, ChatPermissions

from config import HTML, ADMIN_ID, antiflood_col, flood_tracker, app
from helpers import log_event, _is_admin_msg, _auto_del, _FULL_PERMS


@app.on_message(filters.command("antiflood") & filters.group)
async def antiflood_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    if not args:
        await message.reply_text(
            "Usage:\n"
            "<code>/antiflood on [msgs] [secs] [action]</code>\n"
            "  Default: 5 messages in 10 seconds → mute\n"
            "  Action: mute | ban | kick\n\n"
            "<code>/antiflood off</code>    — Disable\n"
            "<code>/antiflood status</code> — Current settings",
            parse_mode=HTML,
        )
        return

    sub = args[0].lower()

    if sub == "off":
        await antiflood_col.update_one(
            {"chat_id": message.chat.id},
            {"$set": {"enabled": False}},
            upsert=True,
        )
        m = await message.reply_text("✅ <b>Anti-flood disabled.</b>", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 20))
        return

    if sub == "status":
        doc = await antiflood_col.find_one({"chat_id": message.chat.id})
        if not doc or not doc.get("enabled"):
            await message.reply_text("🌊 Anti-flood is <b>disabled</b> for this chat.", parse_mode=HTML)
        else:
            await message.reply_text(
                f"🌊 Anti-flood: <b>ON</b>\n"
                f"📩 Limit : {doc.get('msgs', 5)} messages\n"
                f"⏱ Window: {doc.get('secs', 10)} seconds\n"
                f"⚡ Action: {doc.get('action', 'mute')}",
                parse_mode=HTML,
            )
        return

    if sub == "on":
        msgs   = int(args[1])   if len(args) > 1 and args[1].isdigit()  else 5
        secs   = int(args[2])   if len(args) > 2 and args[2].isdigit()  else 10
        action = args[3].lower() if len(args) > 3                        else "mute"
        if action not in ("mute", "ban", "kick"):
            action = "mute"
        await antiflood_col.update_one(
            {"chat_id": message.chat.id},
            {"$set": {
                "enabled": True, "chat_id": message.chat.id,
                "msgs": msgs, "secs": secs, "action": action,
            }},
            upsert=True,
        )
        m = await message.reply_text(
            f"🌊 <b>Anti-flood Enabled</b>\n"
            f"📩 Limit : {msgs} messages in {secs}s\n"
            f"⚡ Action: <b>{action}</b>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 30))
        asyncio.create_task(log_event(client,
            f"🌊 <b>Anti-flood Enabled</b>  {msgs}msgs/{secs}s → {action}"
            f"  📍 {message.chat.title or message.chat.id}"
        ))
        return

    await message.reply_text("Use: <code>on</code>, <code>off</code>, or <code>status</code>.", parse_mode=HTML)


@app.on_message(filters.incoming & filters.group)
async def antiflood_enforcer(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    if await _is_admin_msg(client, message):
        return

    doc = await antiflood_col.find_one({"chat_id": message.chat.id})
    if not doc or not doc.get("enabled"):
        return

    limit_msgs = doc.get("msgs", 5)
    limit_secs = doc.get("secs", 10)
    action     = doc.get("action", "mute")

    key    = (message.chat.id, user.id)
    now    = datetime.utcnow()
    cutoff = now - timedelta(seconds=limit_secs)

    times = flood_tracker.get(key, [])
    times = [t for t in times if t > cutoff]
    times.append(now)
    flood_tracker[key] = times

    if len(times) < limit_msgs:
        return

    flood_tracker.pop(key, None)

    name    = user.first_name or "User"
    mention = f'<a href="tg://user?id={user.id}">{name}</a>'

    try:
        await message.delete()
    except Exception:
        pass

    if action == "mute":
        try:
            await client.restrict_chat_member(
                message.chat.id, user.id,
                ChatPermissions(can_send_messages=False),
            )
            m = await message.reply_text(
                f"🌊 <b>Flood detected!</b>\n"
                f"👤 {mention} has been <b>muted</b>.\n"
                f"📩 Sent {len(times)} messages in {limit_secs}s",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 30))
        except Exception:
            pass

    elif action == "ban":
        try:
            await client.ban_chat_member(message.chat.id, user.id)
            m = await message.reply_text(
                f"🌊 <b>Flood detected!</b>\n"
                f"👤 {mention} has been <b>banned</b>.\n"
                f"📩 Sent {len(times)} messages in {limit_secs}s",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 30))
        except Exception:
            pass

    elif action == "kick":
        try:
            await client.ban_chat_member(message.chat.id, user.id)
            await asyncio.sleep(1)
            await client.unban_chat_member(message.chat.id, user.id)
            m = await message.reply_text(
                f"🌊 <b>Flood detected!</b>\n"
                f"👤 {mention} has been <b>kicked</b>.\n"
                f"📩 Sent {len(times)} messages in {limit_secs}s",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 30))
        except Exception:
            pass

    asyncio.create_task(log_event(client,
        f"🌊 <b>Flood Detected</b>  👤 {name} <code>{user.id}</code>"
        f"  ⚡ {action}  📍 {message.chat.title or message.chat.id}"
    ))
