import asyncio
import io
import re
from datetime import datetime, timedelta

from pyrogram import Client, filters
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
        "direction": direction,   # "in" = user→bot  |  "out" = admin→user
        "content":   content,
        "msg_type":  msg_type,
        "timestamp": datetime.utcnow(),
    })


def _parse_period(arg: str) -> datetime | None:
    """Parse '7_days', '2024-01-15', '15-01-2024' → cutoff datetime."""
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


def _format_conversations(docs: list, title: str) -> str:
    if not docs:
        return f"{title}\n\nNo messages found."
    lines = [title, "=" * 60, ""]
    current_uid = None
    for doc in sorted(docs, key=lambda d: d.get("timestamp", datetime.min)):
        uid      = doc.get("user_id")
        name     = doc.get("user_name", "Unknown")
        uname    = f"@{doc['username']}" if doc.get("username") else "no username"
        ts       = doc.get("timestamp", datetime.utcnow()).strftime("%Y-%m-%d %H:%M UTC")
        direction = doc.get("direction", "in")
        content   = doc.get("content", "")
        if uid != current_uid:
            current_uid = uid
            lines.append(f"\n{'─' * 50}")
            lines.append(f"👤 {name}  ({uname})  |  ID: {uid}")
            lines.append("─" * 50)
        prefix = "   ADMIN ➤" if direction == "out" else f"   {name} ➤"
        lines.append(f"  [{ts}]  {prefix}  {content}")
    lines += ["", "=" * 60, f"Total: {len(docs)} messages"]
    return "\n".join(lines)


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


# ─── Admin reply in inbox group → send to user + react ✅ ────────────────────

@app.on_message(filters.group & filters.incoming, group=11)
async def inbox_group_reply(client: Client, message: Message):
    try:
        inbox_id = await _get_inbox_group()
        if not inbox_id or message.chat.id != inbox_id:
            return

        replied = message.reply_to_message
        if not replied:
            return

        sender_id = getattr(message.from_user, "id", None)
        if not sender_id:
            return

        mapping = await inbox_col.find_one({
            "inbox_msg_id": replied.id,
            "group_id":     inbox_id,
        })
        if not mapping:
            print(f"[INBOX] No mapping for replied_msg={replied.id}")
            return

        target_uid  = mapping["user_id"]
        target_name = mapping.get("user_name", str(target_uid))
        target_uname = mapping.get("username", "")
        content, msg_type = _msg_content_and_type(message)

        ok = False
        if message.text:
            r = await bot_api("sendMessage", {"chat_id": target_uid, "text": message.text})
            ok = r.get("ok", False)
        elif message.photo:
            r = await bot_api("sendPhoto", {"chat_id": target_uid, "photo": message.photo.file_id, "caption": message.caption or ""})
            ok = r.get("ok", False)
        elif message.video:
            r = await bot_api("sendVideo", {"chat_id": target_uid, "video": message.video.file_id, "caption": message.caption or ""})
            ok = r.get("ok", False)
        elif message.document:
            r = await bot_api("sendDocument", {"chat_id": target_uid, "document": message.document.file_id, "caption": message.caption or ""})
            ok = r.get("ok", False)
        elif message.voice:
            r = await bot_api("sendVoice", {"chat_id": target_uid, "voice": message.voice.file_id})
            ok = r.get("ok", False)
        elif message.audio:
            r = await bot_api("sendAudio", {"chat_id": target_uid, "audio": message.audio.file_id, "caption": message.caption or ""})
            ok = r.get("ok", False)
        elif message.sticker:
            r = await bot_api("sendSticker", {"chat_id": target_uid, "sticker": message.sticker.file_id})
            ok = r.get("ok", False)
        else:
            r = await bot_api("sendMessage", {"chat_id": target_uid, "text": "📎 (Unsupported message type)"})
            ok = r.get("ok", False)

        if ok:
            await _save_msg(target_uid, target_name, target_uname, "out", content, msg_type)
            try:
                await bot_api("setMessageReaction", {
                    "chat_id":    inbox_id,
                    "message_id": message.id,
                    "reaction":   [{"type": "emoji", "emoji": "👍"}],
                })
            except Exception as re_err:
                print(f"[INBOX] Reaction failed: {re_err}")
            print(f"[INBOX] Admin reply → user={target_uid} ok")
        else:
            m = await message.reply_text(
                f"❌ Failed to deliver to <code>{target_uid}</code>",
                parse_mode=HTML,
            )
            asyncio.create_task(_auto_del(m, 10))

    except Exception as e:
        print(f"[INBOX] inbox_group_reply error: {e}")


# ─── /chat {user_id | @username} — export full conversation as file ───────────

@app.on_message(filters.command("chat") & filters.user(ADMIN_ID))
async def chat_export_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        await message.reply_text(
            "📤 <b>Export conversation with a user</b>\n\n"
            "Usage:\n"
            "<code>/chat {user_id}</code>\n"
            "<code>/chat @username</code>",
            parse_mode=HTML,
        )
        return

    raw = args[0].lstrip("@")
    user_id = None

    if raw.isdigit():
        user_id = int(raw)
    else:
        doc = await conversations_col.find_one({"username": raw})
        if doc:
            user_id = doc["user_id"]

    if not user_id:
        await message.reply_text(
            f"❌ User not found: <code>{raw}</code>\n\n"
            "Make sure the user has sent a message to the bot.",
            parse_mode=HTML,
        )
        return

    docs = await conversations_col.find({"user_id": user_id}).sort("timestamp", 1).to_list(length=None)
    if not docs:
        await message.reply_text(
            f"📭 No conversation found with <code>{user_id}</code>.",
            parse_mode=HTML,
        )
        return

    name  = docs[0].get("user_name", str(user_id))
    uname = f"@{docs[0]['username']}" if docs[0].get("username") else "no username"
    title = (
        f"Conversation: {name} ({uname})  |  ID: {user_id}\n"
        f"Exported: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    text = _format_conversations(docs, title)
    buf  = io.BytesIO(text.encode("utf-8"))
    buf.name = f"chat_{user_id}.txt"

    await message.reply_document(
        document=buf,
        caption=(
            f"💬 <b>Conversation Export</b>\n"
            f"👤 {name}  ({uname})\n"
            f"🆔 <code>{user_id}</code>\n"
            f"📨 {len(docs)} messages total"
        ),
        parse_mode=HTML,
    )


# ─── /inbox — list users / export by period / delete ─────────────────────────

@app.on_message(filters.command("inbox") & filters.user(ADMIN_ID))
async def inbox_cmd(client: Client, message: Message):
    args = message.command[1:]

    # ── /inbox  (no args) — list all users who messaged ──────────────────────
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
        if not docs:
            await message.reply_text("📭 No inbox messages yet.")
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
        await message.reply_text("\n".join(lines), parse_mode=HTML, disable_web_page_preview=True)
        return

    # ── /inbox delete … ───────────────────────────────────────────────────────
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

    # ── /inbox {period | date} — export as .txt file ──────────────────────────
    period_arg = args[0].lower()
    is_all     = period_arg == "all"
    is_date    = _is_date_arg(period_arg)
    is_period  = bool(re.match(r"^\d+_days?$", period_arg, re.IGNORECASE))

    if not (is_all or is_date or is_period):
        await message.reply_text(
            "📥 <b>Inbox Export</b>\n\n"
            "Examples:\n"
            "<code>/inbox all</code>        — All messages\n"
            "<code>/inbox 1_days</code>     — Last 1 day\n"
            "<code>/inbox 7_days</code>     — Last 7 days\n"
            "<code>/inbox 30_days</code>    — Last 30 days\n"
            "<code>/inbox 2024-01-15</code> — Specific date\n\n"
            "👥 Manage:\n"
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
    if not docs:
        await message.reply_text(
            f"📭 No inbox messages for: <b>{period_arg}</b>",
            parse_mode=HTML,
        )
        return

    title = (
        f"Inbox Export — {title_suffix}\n"
        f"Exported: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    )
    text = _format_conversations(docs, title)
    buf  = io.BytesIO(text.encode("utf-8"))
    buf.name = f"inbox_{period_arg}.txt"

    await message.reply_document(
        document=buf,
        caption=(
            f"📥 <b>Inbox Export</b>  —  {title_suffix}\n"
            f"📨 {len(docs)} messages"
        ),
        parse_mode=HTML,
    )
