import asyncio
import csv
import io
import re
from datetime import datetime, timedelta

from pyrogram import Client, filters, StopPropagation
from pyrogram.types import Message

from config import (
    HTML, ADMIN_ID, settings_col, inbox_col, conversations_col, app,
)
from helpers import bot_api, _auto_del


# ─── Internal helpers ──────────────────────────────────────────────────────────

async def _get_inbox_group() -> int | None:
    doc = await settings_col.find_one({"key": "inbox_group"})
    return doc.get("chat_id") if doc else None


async def _set_inbox_group(chat_id: int):
    await settings_col.update_one(
        {"key": "inbox_group"},
        {"$set": {"chat_id": chat_id}},
        upsert=True,
    )


def _msg_content_and_type(message: Message) -> tuple[str, str]:
    if message.text:
        return message.text, "text"
    elif message.photo:
        return message.caption or "[Photo]", "photo"
    elif message.video:
        return message.caption or "[Video]", "video"
    elif message.voice:
        return "[Voice message]", "voice"
    elif message.audio:
        return message.caption or "[Audio]", "audio"
    elif message.document:
        fname = getattr(message.document, "file_name", "") or ""
        return message.caption or f"[Document: {fname}]", "document"
    elif message.sticker:
        emoji = getattr(message.sticker, "emoji", "") or ""
        return f"[Sticker {emoji}]", "sticker"
    elif message.video_note:
        return "[Video note]", "video_note"
    else:
        return "[Unsupported message]", "unknown"


async def _save_msg(
    user_id: int, user_name: str, username: str,
    direction: str, content: str, msg_type: str,
):
    await conversations_col.insert_one({
        "user_id":   user_id,
        "user_name": user_name,
        "username":  username,
        "direction": direction,
        "content":   content,
        "msg_type":  msg_type,
        "timestamp": datetime.utcnow(),
    })


def _parse_period(arg: str) -> datetime | None:
    m = re.match(r"^(\d+)_days?$", arg, re.IGNORECASE)
    if m:
        return datetime.utcnow() - timedelta(days=int(m.group(1)))
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(arg, fmt)
        except ValueError:
            continue
    return None


def _is_date_arg(arg: str) -> bool:
    return bool(re.match(r"^\d{4}[/-]\d{2}[/-]\d{2}$|^\d{2}[/-]\d{2}[/-]\d{4}$", arg))


def _to_csv_bytes(docs: list) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "user_id", "user_name", "username", "direction", "type", "content"])
    for doc in sorted(docs, key=lambda d: d.get("timestamp", datetime.min)):
        writer.writerow([
            doc.get("timestamp", datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S"),
            doc.get("user_id", ""),
            doc.get("user_name", ""),
            doc.get("username", ""),
            doc.get("direction", ""),
            doc.get("msg_type", ""),
            doc.get("content", ""),
        ])
    return buf.getvalue().encode("utf-8-sig")


# ─── /setinboxgroup ────────────────────────────────────────────────────────────

@app.on_message(filters.command("setinboxgroup") & filters.user(ADMIN_ID))
async def set_inbox_group_cmd(client: Client, message: Message):
    args = message.command[1:]

    if not args:
        inbox_id = await _get_inbox_group()
        if inbox_id:
            await message.reply_text(
                f"📥 <b>Inbox Group</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✅ Currently set: <code>{inbox_id}</code>\n\n"
                f"To change:\n<code>/setinboxgroup -100xxxxxxxxxx</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n🤖 DESI MLH SYSTEM",
                parse_mode=HTML,
            )
        else:
            await message.reply_text(
                "📥 <b>Inbox Group — Not Set</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Use: <code>/setinboxgroup -100xxxxxxxxxx</code>\n\n"
                "💡 Make the bot admin in the group first.\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n🤖 DESI MLH SYSTEM",
                parse_mode=HTML,
            )
        return

    try:
        group_id = int(args[0])
    except ValueError:
        await message.reply_text("❌ Invalid ID. Must be a number like <code>-100123456789</code>.", parse_mode=HTML)
        return

    await _set_inbox_group(group_id)

    test_ok = False
    test_err = ""
    try:
        res = await bot_api("sendMessage", {
            "chat_id":    group_id,
            "text":       "✅ <b>Inbox Group Connected!</b>\n━━━━━━━━━━━━━━━━━━━━━━\nUser messages will be forwarded here.\nReply to any forwarded message to respond.\n━━━━━━━━━━━━━━━━━━━━━━\n🤖 DESI MLH SYSTEM",
            "parse_mode": "HTML",
        })
        test_ok  = res.get("ok", False)
        test_err = res.get("description", "")
    except Exception as e:
        test_err = str(e)

    status = "🟢 Test message sent ✓" if test_ok else f"⚠️ Test failed: {test_err or 'unknown'}"
    await message.reply_text(
        f"{'✅' if test_ok else '⚠️'} <b>Inbox Group {'Set' if test_ok else 'Saved (check error)'}!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📥 Group ID : <code>{group_id}</code>\n"
        f"{status}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n🤖 DESI MLH SYSTEM",
        parse_mode=HTML,
    )


# ─── User message → forward to inbox group (no header, no log) ────────────────

@app.on_message(filters.private & filters.incoming, group=10)
async def user_msg_to_inbox(client: Client, message: Message):
    user = message.from_user
    if not user or user.id == ADMIN_ID:
        return
    text = message.text or message.caption or ""
    if text.startswith("/"):
        return

    inbox_id = await _get_inbox_group()
    if not inbox_id:
        return

    name  = user.first_name or "User"
    uname = user.username or ""
    content, msg_type = _msg_content_and_type(message)

    try:
        fwd = await bot_api("forwardMessage", {
            "chat_id":      inbox_id,
            "from_chat_id": user.id,
            "message_id":   message.id,
        })
        fwd_msg_id = (fwd.get("result") or {}).get("message_id")

        if fwd_msg_id:
            await inbox_col.insert_one({
                "inbox_msg_id": fwd_msg_id,
                "user_id":      user.id,
                "user_name":    name,
                "username":     uname,
                "group_id":     inbox_id,
            })
            await _save_msg(user.id, name, uname, "in", content, msg_type)
            print(f"[INBOX] user={user.id} fwd→group={inbox_id} msg={fwd_msg_id}")
        else:
            print(f"[INBOX] Forward failed: {fwd}")
    except Exception as e:
        print(f"[INBOX] user_msg_to_inbox error: {e}")


# ─── Admin reply in inbox group → send to user + react 👍 ────────────────────

@app.on_message(filters.group & filters.incoming, group=11)
async def inbox_group_reply(client: Client, message: Message):
    try:
        inbox_id = await _get_inbox_group()
        if not inbox_id:
            return
        if message.chat.id != inbox_id:
            return

        # Only process reply messages
        replied = message.reply_to_message
        if not replied:
            return

        # Skip bot commands — handled by their own handlers
        raw_text = message.text or message.caption or ""
        if raw_text.startswith("/"):
            return

        print(f"[INBOX_REPLY] chat={message.chat.id} replied_to={replied.id} text={bool(message.text)}")

        # ── Find who this reply is for ──────────────────────────────────────
        # Primary lookup: by inbox_msg_id + group_id
        mapping = await inbox_col.find_one({
            "inbox_msg_id": replied.id,
            "group_id":     inbox_id,
        })
        # Fallback: by inbox_msg_id only (handles group_id type mismatch)
        if not mapping:
            mapping = await inbox_col.find_one({"inbox_msg_id": replied.id})

        if not mapping:
            print(f"[INBOX_REPLY] No mapping for replied_msg={replied.id} — ignoring")
            return

        target_uid   = mapping["user_id"]
        target_name  = mapping.get("user_name", str(target_uid))
        target_uname = mapping.get("username", "")
        content, msg_type = _msg_content_and_type(message)

        # ── Send to user ─────────────────────────────────────────────────────
        ok  = False
        err = ""
        if message.text:
            r = await bot_api("sendMessage", {
                "chat_id": target_uid,
                "text":    message.text,
                "parse_mode": "HTML",
            })
            ok = r.get("ok", False)
            err = r.get("description", "")
        elif message.photo:
            r = await bot_api("sendPhoto", {
                "chat_id": target_uid,
                "photo":   message.photo.file_id,
                "caption": message.caption or "",
                "parse_mode": "HTML",
            })
            ok = r.get("ok", False)
            err = r.get("description", "")
        elif message.video:
            r = await bot_api("sendVideo", {
                "chat_id": target_uid,
                "video":   message.video.file_id,
                "caption": message.caption or "",
                "parse_mode": "HTML",
            })
            ok = r.get("ok", False)
            err = r.get("description", "")
        elif message.document:
            r = await bot_api("sendDocument", {
                "chat_id":  target_uid,
                "document": message.document.file_id,
                "caption":  message.caption or "",
                "parse_mode": "HTML",
            })
            ok = r.get("ok", False)
            err = r.get("description", "")
        elif message.voice:
            r = await bot_api("sendVoice", {
                "chat_id": target_uid,
                "voice":   message.voice.file_id,
            })
            ok = r.get("ok", False)
            err = r.get("description", "")
        elif message.audio:
            r = await bot_api("sendAudio", {
                "chat_id": target_uid,
                "audio":   message.audio.file_id,
                "caption": message.caption or "",
                "parse_mode": "HTML",
            })
            ok = r.get("ok", False)
            err = r.get("description", "")
        elif message.sticker:
            r = await bot_api("sendSticker", {
                "chat_id": target_uid,
                "sticker": message.sticker.file_id,
            })
            ok = r.get("ok", False)
            err = r.get("description", "")
        elif message.video_note:
            r = await bot_api("sendVideoNote", {
                "chat_id":    target_uid,
                "video_note": message.video_note.file_id,
            })
            ok = r.get("ok", False)
            err = r.get("description", "")
        else:
            r = await bot_api("sendMessage", {
                "chat_id": target_uid,
                "text":    "📎 Admin sent a message (unsupported type in inbox).",
            })
            ok = r.get("ok", False)
            err = r.get("description", "")

        if ok:
            await _save_msg(target_uid, target_name, target_uname, "out", content, msg_type)
            try:
                await bot_api("setMessageReaction", {
                    "chat_id":    inbox_id,
                    "message_id": message.id,
                    "reaction":   [{"type": "emoji", "emoji": "👍"}],
                })
            except Exception:
                pass
            print(f"[INBOX_REPLY] ✅ Delivered to user={target_uid}")
        else:
            print(f"[INBOX_REPLY] ❌ Failed to deliver user={target_uid} err={err}")
            uname_str = f"@{target_uname}" if target_uname else str(target_uid)
            note = ""
            if "blocked" in err.lower():
                note = "\n<i>User has blocked the bot.</i>"
            elif "not found" in err.lower() or "deactivated" in err.lower():
                note = "\n<i>User account not found or deactivated.</i>"
            m = await message.reply_text(
                f"❌ <b>Delivery Failed</b>\n"
                f"👤 {target_name} ({uname_str})\n"
                f"📛 {err}{note}",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 15))

    except Exception as e:
        import traceback
        print(f"[INBOX_REPLY] EXCEPTION: {e}")
        traceback.print_exc()


# ─── /chat — export conversation as CSV (private or inbox-group reply) ─────────

@app.on_message(filters.command("chat") & filters.user(ADMIN_ID), group=-2)
async def chat_export_cmd(client: Client, message: Message):
    print(f"[CHAT] triggered  chat={message.chat.id}  args={message.command[1:]}")
    try:
        args      = message.command[1:]
        user_id   = None
        user_name = ""
        username  = ""

        # Method 1: admin replies to a forwarded message in inbox group with /chat
        replied = message.reply_to_message
        if replied and message.chat.type.name != "PRIVATE":
            inbox_id = await _get_inbox_group()
            print(f"[CHAT] group reply mode  inbox_id={inbox_id}  replied_to={replied.id}")
            if inbox_id and message.chat.id == inbox_id:
                mapping = await inbox_col.find_one({
                    "inbox_msg_id": replied.id,
                    "group_id":     inbox_id,
                })
                if mapping:
                    user_id   = mapping["user_id"]
                    user_name = mapping.get("user_name", str(user_id))
                    username  = mapping.get("username", "")
                    print(f"[CHAT] mapped → user_id={user_id}")
                else:
                    await message.reply_text(
                        "❌ Could not identify user from this message.\n"
                        "Make sure you're replying to a <b>forwarded user message</b>.",
                        parse_mode=HTML,
                    )
                    return

        # Method 2: /chat {user_id} or /chat @username in private
        if not user_id:
            if not args:
                await message.reply_text(
                    "📤 <b>Export Conversation</b>\n\n"
                    "<b>In inbox group:</b>  Reply to any forwarded user message with <code>/chat</code>\n\n"
                    "<b>In private:</b>\n"
                    "<code>/chat {user_id}</code>\n"
                    "<code>/chat @username</code>",
                    parse_mode=HTML,
                )
                return

            raw = args[0].lstrip("@")
            if raw.isdigit():
                user_id = int(raw)
                print(f"[CHAT] numeric id={user_id}")
            else:
                doc = await conversations_col.find_one({"username": raw})
                if doc:
                    user_id   = doc["user_id"]
                    user_name = doc.get("user_name", "")
                    username  = doc.get("username", "")
                    print(f"[CHAT] username lookup → user_id={user_id}")

        if not user_id:
            await message.reply_text(
                f"❌ User not found: <code>{args[0] if args else '?'}</code>\n\n"
                "Make sure the user has sent a message to the bot.",
                parse_mode=HTML,
            )
            return

        docs = await conversations_col.find({"user_id": user_id}).sort("timestamp", 1).to_list(length=None)
        print(f"[CHAT] found {len(docs)} records for user_id={user_id}")

        if not docs:
            await message.reply_text(
                f"📭 No conversation history for <code>{user_id}</code>.\n\n"
                f"<i>Only messages received after the latest bot update are stored.</i>",
                parse_mode=HTML,
            )
            return

        if not user_name:
            user_name = docs[0].get("user_name", str(user_id))
        if not username:
            username  = docs[0].get("username", "")

        uname_display = f"@{username}" if username else "no username"
        csv_bytes = _to_csv_bytes(docs)
        buf = io.BytesIO(csv_bytes)
        buf.name = f"chat_{user_id}.csv"

        await message.reply_document(
            document=buf,
            caption=(
                f"💬 <b>Conversation Export</b>\n"
                f"👤 {user_name}  ({uname_display})\n"
                f"🆔 <code>{user_id}</code>\n"
                f"📨 {len(docs)} messages"
            ),
            parse_mode=HTML,
        )
        print(f"[CHAT] CSV sent for user_id={user_id}")

    except Exception as e:
        print(f"[CHAT] error: {e}")
        try:
            await message.reply_text(f"❌ Error: <code>{e}</code>", parse_mode=HTML)
        except Exception:
            pass
    raise StopPropagation


# ─── /inbox — list / export CSV / delete ──────────────────────────────────────

@app.on_message(filters.command("inbox") & filters.user(ADMIN_ID), group=-2)
async def inbox_cmd(client: Client, message: Message):
    print(f"[INBOX_CMD] triggered  chat={message.chat.id}  args={message.command[1:]}")
    try:
        args = message.command[1:]

        # ── /inbox  (no args) — show all users ───────────────────────────────
        if not args:
            pipeline = [
                {"$match": {"direction": "in"}},
                {"$group": {
                    "_id":       "$user_id",
                    "user_name": {"$last": "$user_name"},
                    "username":  {"$last": "$username"},
                    "count":     {"$sum": 1},
                    "last_msg":  {"$max": "$timestamp"},
                }},
                {"$sort": {"last_msg": -1}},
            ]
            docs = await conversations_col.aggregate(pipeline).to_list(length=None)
            print(f"[INBOX_CMD] users found={len(docs)}")

            if not docs:
                await message.reply_text(
                    "📭 <b>No inbox messages yet.</b>\n\n"
                    "<i>Messages appear here once users chat with the bot.</i>",
                    parse_mode=HTML,
                )
                return

            lines = [f"📥 <b>Inbox — {len(docs)} Users</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
            for d in docs[:50]:
                uid   = d["_id"]
                name  = d.get("user_name", "Unknown")
                uname = f"@{d['username']}" if d.get("username") else "no username"
                cnt   = d.get("count", 0)
                last  = d.get("last_msg", datetime.utcnow()).strftime("%d %b %Y")
                lines.append(
                    f"\n👤 <a href='tg://user?id={uid}'>{name}</a>  ({uname})\n"
                    f"   🆔 <code>{uid}</code>  |  📨 {cnt} msgs  |  📅 {last}\n"
                    f"   ↳ <code>/chat {uid}</code>"
                )
            await message.reply_text(
                "\n".join(lines),
                parse_mode=HTML,
                disable_web_page_preview=True,
            )
            return

        # ── /inbox delete … ───────────────────────────────────────────────────
        if args[0].lower() == "delete":
            sub = args[1].lower() if len(args) > 1 else ""

            if sub == "all":
                r1 = await conversations_col.delete_many({})
                r2 = await inbox_col.delete_many({})
                await message.reply_text(
                    f"🗑️ <b>All Inbox Data Deleted</b>\n"
                    f"Conversations: <b>{r1.deleted_count}</b>\n"
                    f"Mappings: <b>{r2.deleted_count}</b>",
                    parse_mode=HTML,
                )
                return

            if sub == "user" and len(args) > 2:
                raw = args[2].lstrip("@")
                uid = int(raw) if raw.isdigit() else None
                if not uid:
                    doc = await conversations_col.find_one({"username": raw})
                    uid = doc["user_id"] if doc else None
                if not uid:
                    await message.reply_text(f"❌ User not found: <code>{raw}</code>", parse_mode=HTML)
                    return
                r1 = await conversations_col.delete_many({"user_id": uid})
                await inbox_col.delete_many({"user_id": uid})
                await message.reply_text(
                    f"🗑️ Deleted <b>{r1.deleted_count}</b> messages for user <code>{uid}</code>.",
                    parse_mode=HTML,
                )
                return

            if sub == "date" and len(args) > 2:
                raw_date = args[2]
                dt = _parse_period(raw_date)
                if not dt:
                    await message.reply_text(f"❌ Invalid date: <code>{raw_date}</code>", parse_mode=HTML)
                    return
                end_of_day = dt.replace(hour=23, minute=59, second=59)
                result = await conversations_col.delete_many({
                    "timestamp": {"$gte": dt, "$lte": end_of_day}
                })
                await message.reply_text(
                    f"🗑️ Deleted <b>{result.deleted_count}</b> messages from <code>{raw_date}</code>.",
                    parse_mode=HTML,
                )
                return

            await message.reply_text(
                "🗑️ <b>Delete Inbox Data</b>\n\n"
                "<code>/inbox delete all</code>               — Delete everything\n"
                "<code>/inbox delete user {id/@user}</code>   — Delete one user\n"
                "<code>/inbox delete date 2024-01-15</code>   — Delete by date",
                parse_mode=HTML,
            )
            return

        # ── /inbox {period | date | all} — export as CSV ──────────────────────
        period_arg = args[0].lower()
        is_all    = period_arg == "all"
        is_date   = _is_date_arg(period_arg)
        is_period = bool(re.match(r"^\d+_days?$", period_arg, re.IGNORECASE))

        if not (is_all or is_date or is_period):
            await message.reply_text(
                "📥 <b>Inbox Export</b>\n\n"
                "Examples:\n"
                "<code>/inbox all</code>        — All messages\n"
                "<code>/inbox 1_days</code>     — Last 1 day\n"
                "<code>/inbox 7_days</code>     — Last 7 days\n"
                "<code>/inbox 30_days</code>    — Last 30 days\n"
                "<code>/inbox 2024-01-15</code> — Specific date\n\n"
                "Manage:\n"
                "<code>/inbox</code>            — List all users\n"
                "<code>/chat {id}</code>        — Export one user's chat\n"
                "<code>/inbox delete …</code>   — Delete records",
                parse_mode=HTML,
            )
            return

        query: dict = {"direction": "in"}
        title_suffix = "All Time"

        if is_all:
            pass
        elif is_period:
            cutoff = _parse_period(period_arg)
            query["timestamp"] = {"$gte": cutoff}
            days_n = period_arg.split("_")[0]
            title_suffix = f"Last {days_n} Days"
        else:
            dt = _parse_period(period_arg)
            if not dt:
                await message.reply_text(f"❌ Invalid date: <code>{period_arg}</code>", parse_mode=HTML)
                return
            end_of_day = dt.replace(hour=23, minute=59, second=59)
            query["timestamp"] = {"$gte": dt, "$lte": end_of_day}
            title_suffix = dt.strftime("%Y-%m-%d")

        docs = await conversations_col.find(query).sort("timestamp", 1).to_list(length=None)
        print(f"[INBOX_CMD] export found {len(docs)} records")

        if not docs:
            await message.reply_text(
                f"📭 No inbox messages for: <b>{period_arg}</b>",
                parse_mode=HTML,
            )
            return

        safe_name = re.sub(r"[^\w\-]", "_", period_arg)
        csv_bytes = _to_csv_bytes(docs)
        buf = io.BytesIO(csv_bytes)
        buf.name = f"inbox_{safe_name}.csv"

        await message.reply_document(
            document=buf,
            caption=(
                f"📥 <b>Inbox Export</b>  —  {title_suffix}\n"
                f"📨 {len(docs)} messages\n"
                f"📅 {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
            ),
            parse_mode=HTML,
        )
        print(f"[INBOX_CMD] export sent period={period_arg} count={len(docs)}")

    except Exception as e:
        print(f"[INBOX_CMD] error: {e}")
        try:
            await message.reply_text(f"❌ Error: <code>{e}</code>", parse_mode=HTML)
        except Exception:
            pass
    raise StopPropagation
