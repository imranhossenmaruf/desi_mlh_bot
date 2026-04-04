import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, ADMIN_ID, settings_col, inbox_col, app
from helpers import bot_api, log_event


# ─── helpers ──────────────────────────────────────────────────────────────────

async def _get_inbox_group() -> int | None:
    doc = await settings_col.find_one({"key": "inbox_group"})
    return doc.get("chat_id") if doc else None


async def _set_inbox_group(chat_id: int):
    await settings_col.update_one(
        {"key": "inbox_group"},
        {"$set": {"chat_id": chat_id}},
        upsert=True,
    )


# ─── /setinboxgroup command ────────────────────────────────────────────────────

@app.on_message(filters.command("setinboxgroup") & filters.user(ADMIN_ID))
async def set_inbox_group_cmd(client: Client, message: Message):
    print(f"[INBOX] /setinboxgroup triggered  args={message.command[1:]}")
    try:
        args = message.command[1:]

        if not args:
            inbox_id = await _get_inbox_group()
            if inbox_id:
                await message.reply_text(
                    f"📥 <b>Inbox Group</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Currently set: <code>{inbox_id}</code>\n\n"
                    f"To change:\n"
                    f"<code>/setinboxgroup -5149201178</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🤖 DESI MLH SYSTEM",
                    parse_mode=HTML,
                )
            else:
                await message.reply_text(
                    f"📥 <b>Inbox Group — Not Set</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Use: <code>/setinboxgroup -5149201178</code>\n\n"
                    f"💡 Add the bot as admin in the group,\n"
                    f"then send the group ID here.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🤖 DESI MLH SYSTEM",
                    parse_mode=HTML,
                )
            return

        raw = args[0]
        try:
            group_id = int(raw)
        except ValueError:
            await message.reply_text(
                "❌ Invalid group ID. Example:\n"
                "<code>/setinboxgroup -5149201178</code>",
                parse_mode=HTML,
            )
            return

        # Save first — reply regardless of test result
        await _set_inbox_group(group_id)
        print(f"[INBOX] Saved inbox group → {group_id}")

        # Try test message
        test_ok  = False
        test_err = ""
        try:
            test = await bot_api("sendMessage", {
                "chat_id":    group_id,
                "text":       (
                    "✅ <b>Inbox Group Connected!</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "This group is now the <b>User Inbox</b>.\n\n"
                    "• User messages → forwarded here\n"
                    "• Reply to forwarded message → bot sends reply to user\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🤖 DESI MLH SYSTEM"
                ),
                "parse_mode": "HTML",
            })
            test_ok  = test.get("ok", False)
            test_err = test.get("description", "")
        except Exception as te:
            test_err = str(te)

        if test_ok:
            await message.reply_text(
                f"✅ <b>Inbox Group Set!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📥 Group ID : <code>{group_id}</code>\n"
                f"🟢 Test message sent to group ✓\n\n"
                f"User messages will now appear in that group.\n"
                f"Reply to forwarded messages to respond.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 DESI MLH SYSTEM",
                parse_mode=HTML,
            )
            asyncio.create_task(log_event(client,
                f"📥 <b>Inbox Group Configured</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Group ID : <code>{group_id}</code>\n"
                f"✅ Status   : Connected & Working\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 DESI MLH SYSTEM"
            ))
        else:
            await message.reply_text(
                f"⚠️ <b>Group ID Saved but Test Failed</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📥 Group ID : <code>{group_id}</code>\n"
                f"❌ Error    : <code>{test_err or 'unknown'}</code>\n\n"
                f"Possible fix:\n"
                f"• Make sure bot is <b>admin</b> in the group\n"
                f"• Check the group ID is correct\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 DESI MLH SYSTEM",
                parse_mode=HTML,
            )
        print(f"[INBOX] set group={group_id}  test_ok={test_ok}  err={test_err}")

    except Exception as e:
        print(f"[INBOX] set_inbox_group_cmd crash: {e}")
        try:
            await message.reply_text(f"❌ Error: <code>{e}</code>", parse_mode=HTML)
        except Exception:
            pass


# ─── User private message → forward to inbox group ────────────────────────────

@app.on_message(filters.private & filters.incoming, group=10)
async def user_msg_to_inbox(client: Client, message: Message):
    user = message.from_user
    if not user:
        return
    # skip admin
    if user.id == ADMIN_ID:
        return
    # skip commands — they have their own handlers
    text = message.text or message.caption or ""
    if text.startswith("/"):
        return

    inbox_id = await _get_inbox_group()
    if not inbox_id:
        return

    name    = user.first_name or "User"
    uname   = f"@{user.username}" if user.username else "no username"
    mention = f'<a href="tg://user?id={user.id}">{name}</a>'

    header = (
        f"💬 <b>User Message</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 From   : {mention}\n"
        f"🆔 ID     : <code>{user.id}</code>\n"
        f"📛 Handle : {uname}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>↩️ Reply to the forwarded message below to respond.</i>"
    )

    try:
        header_resp = await bot_api("sendMessage", {
            "chat_id":    inbox_id,
            "text":       header,
            "parse_mode": "HTML",
        })
        header_msg_id = (header_resp.get("result") or {}).get("message_id")

        fwd_resp = await bot_api("forwardMessage", {
            "chat_id":      inbox_id,
            "from_chat_id": user.id,
            "message_id":   message.id,
        })
        fwd_result  = fwd_resp.get("result") or {}
        fwd_msg_id  = fwd_result.get("message_id")

        if fwd_msg_id:
            await inbox_col.insert_one({
                "inbox_msg_id":  fwd_msg_id,
                "header_msg_id": header_msg_id,
                "user_id":       user.id,
                "user_name":     name,
                "username":      user.username or "",
                "group_id":      inbox_id,
            })
            print(f"[INBOX] user={user.id} fwd→ group={inbox_id} fwd_msg={fwd_msg_id}")
        else:
            print(f"[INBOX] Forward failed: {fwd_resp}")

    except Exception as e:
        print(f"[INBOX] user_msg_to_inbox error: {e}")


# ─── Admin reply in inbox group → send back to user ───────────────────────────

@app.on_message(filters.group & filters.incoming, group=11)
async def inbox_group_reply(client: Client, message: Message):
    try:
        inbox_id = await _get_inbox_group()
        if not inbox_id:
            return
        if message.chat.id != inbox_id:
            return

        # must be a reply
        replied = message.reply_to_message
        if not replied:
            return

        replied_msg_id = replied.id
        print(f"[INBOX] reply in group={inbox_id} replied_to={replied_msg_id}")

        mapping = await inbox_col.find_one({
            "inbox_msg_id": replied_msg_id,
            "group_id":     inbox_id,
        })
        if not mapping:
            print(f"[INBOX] No mapping found for msg_id={replied_msg_id}")
            return

        target_user_id = mapping["user_id"]
        target_name    = mapping.get("user_name", str(target_user_id))

        # send header to user
        await bot_api("sendMessage", {
            "chat_id":    target_user_id,
            "text":       "📩 <b>Admin Reply</b>\n━━━━━━━━━━━━━━━━━━━━━━",
            "parse_mode": "HTML",
        })

        # send the actual content
        ok = False
        if message.text:
            r = await bot_api("sendMessage", {
                "chat_id":    target_user_id,
                "text":       message.text,
            })
            ok = r.get("ok", False)
        elif message.photo:
            r = await bot_api("sendPhoto", {
                "chat_id": target_user_id,
                "photo":   message.photo.file_id,
                "caption": message.caption or "",
            })
            ok = r.get("ok", False)
        elif message.video:
            r = await bot_api("sendVideo", {
                "chat_id": target_user_id,
                "video":   message.video.file_id,
                "caption": message.caption or "",
            })
            ok = r.get("ok", False)
        elif message.document:
            r = await bot_api("sendDocument", {
                "chat_id":  target_user_id,
                "document": message.document.file_id,
                "caption":  message.caption or "",
            })
            ok = r.get("ok", False)
        elif message.voice:
            r = await bot_api("sendVoice", {
                "chat_id": target_user_id,
                "voice":   message.voice.file_id,
            })
            ok = r.get("ok", False)
        elif message.sticker:
            r = await bot_api("sendSticker", {
                "chat_id": target_user_id,
                "sticker": message.sticker.file_id,
            })
            ok = r.get("ok", False)
        elif message.audio:
            r = await bot_api("sendAudio", {
                "chat_id": target_user_id,
                "audio":   message.audio.file_id,
                "caption": message.caption or "",
            })
            ok = r.get("ok", False)
        else:
            await bot_api("sendMessage", {
                "chat_id": target_user_id,
                "text":    "📎 (Unsupported message type)",
            })
            ok = True

        await bot_api("sendMessage", {
            "chat_id": target_user_id,
            "text":    "━━━━━━━━━━━━━━━━━━━━━━\n🤖 DESI MLH SYSTEM",
        })

        if ok:
            await message.reply_text(
                f"✅ Sent to <b>{target_name}</b> (<code>{target_user_id}</code>)",
                parse_mode=HTML,
            )
            print(f"[INBOX] Admin reply → user={target_user_id} ok")
        else:
            await message.reply_text(
                f"❌ Failed to deliver to <code>{target_user_id}</code>",
                parse_mode=HTML,
            )

    except Exception as e:
        print(f"[INBOX] inbox_group_reply error: {e}")
        try:
            await message.reply_text(f"❌ Error: <code>{e}</code>", parse_mode=HTML)
        except Exception:
            pass
