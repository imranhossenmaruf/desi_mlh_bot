"""
Tag System - গ্রুপের সদস্যদের মেনশন করা
- Batch delay বাড়ানো হয়েছে Telegram flood limit এড়াতে
- Visible mention ব্যবহার করা হয়েছে (invisible ZWNJ সরানো হয়েছে)
- Rate limiting কঠোর করা হয়েছে (5 মিনিট cooldown)
- সদস্য সংখ্যা সীমিত করা হয়েছে (200 জন)
"""

import asyncio
from datetime import datetime
from typing import Dict, List

from pyrogram import Client, filters
from pyrogram.types import Message, ChatMember

from config import HTML, app, db
from helpers import _auto_del, _is_admin_msg

BATCH_SIZE = 5
BATCH_DELAY = 3.0
COOLDOWN_SECONDS = 300

active_tagging_sessions: Dict[int, Dict] = {}
tagging_cooldown: Dict[int, datetime] = {}

tagger_logs_col = db["tagger_logs"]


async def log_tagging_event(chat_id: int, user_id: int, member_count: int, message: str, status: str):
    await tagger_logs_col.insert_one({
        "chat_id": chat_id,
        "admin_id": user_id,
        "member_count": member_count,
        "message": message,
        "status": status,
        "timestamp": datetime.utcnow(),
    })


async def get_group_members(client: Client, chat_id: int) -> List[ChatMember]:
    members = []
    try:
        async for member in client.get_chat_members(chat_id):
            members.append(member)
            if len(members) >= 200:
                break
    except Exception as e:
        print(f"[TAGGER] Error fetching members for {chat_id}: {e}")
        return []
    return members


def _create_mention(user) -> str:
    name = user.first_name or "User"
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def _create_batch_mentions(members: List[ChatMember]) -> str:
    mentions = []
    for member in members:
        if not member.user.is_bot and not member.user.is_deleted:
            mentions.append(_create_mention(member.user))
    return "  ".join(mentions)


def _is_in_cooldown(chat_id: int) -> tuple:
    if chat_id in tagging_cooldown:
        elapsed = (datetime.utcnow() - tagging_cooldown[chat_id]).total_seconds()
        remaining = int(COOLDOWN_SECONDS - elapsed)
        if remaining > 0:
            return True, remaining
    return False, 0


def _set_cooldown(chat_id: int):
    tagging_cooldown[chat_id] = datetime.utcnow()


async def execute_tagall(
    client: Client,
    chat_id: int,
    admin_id: int,
    custom_message: str = None,
) -> tuple:
    if chat_id in active_tagging_sessions:
        session = active_tagging_sessions[chat_id]
        if not session.get("cancelled"):
            return False, "❌ এই গ্রুপে ইতিমধ্যে tagging চলছে।"

    in_cd, remaining = _is_in_cooldown(chat_id)
    if in_cd:
        mins = remaining // 60
        secs = remaining % 60
        return False, f"⏳ Cooldown চলছে। {mins}m {secs}s পরে আবার চেষ্টা করুন।"

    members = await get_group_members(client, chat_id)
    if not members:
        return False, "❌ সদস্যদের তালিকা আনতে পারা যায়নি। বটের পারমিশন চেক করুন।"

    real_members = [m for m in members if not m.user.is_bot and not m.user.is_deleted]
    if not real_members:
        return False, "❌ এই গ্রুপে কোনো সদস্য পাওয়া যায়নি।"

    session = {
        "user_id": admin_id,
        "cancelled": False,
        "started_at": datetime.utcnow(),
        "target_members": real_members,
        "tagged_count": 0,
        "total_count": len(real_members),
        "custom_message": custom_message,
    }

    task = asyncio.create_task(
        _execute_tagging_loop(client, chat_id, session)
    )
    session["task"] = task
    active_tagging_sessions[chat_id] = session
    _set_cooldown(chat_id)

    await log_tagging_event(chat_id, admin_id, len(real_members), custom_message or "[@all]", "started")
    return True, f"🚀 {len(real_members)} জন সদস্যকে tag করা শুরু হচ্ছে...\n⏳ একটু সময় লাগবে..."


async def _execute_tagging_loop(client: Client, chat_id: int, session: Dict):
    admin_id = session["user_id"]
    members = session["target_members"]
    custom_message = session.get("custom_message", "")

    try:
        for i in range(0, len(members), BATCH_SIZE):
            if session.get("cancelled"):
                break

            batch = members[i:i + BATCH_SIZE]
            batch_mentions = _create_batch_mentions(batch)

            if not batch_mentions:
                continue

            if custom_message:
                message_text = f"📢 <b>{custom_message}</b>\n\n{batch_mentions}"
            else:
                message_text = batch_mentions

            try:
                await client.send_message(
                    chat_id,
                    message_text,
                    parse_mode=HTML,
                )
                batch_tagged = len([m for m in batch if not m.user.is_bot])
                session["tagged_count"] += batch_tagged

            except Exception as e:
                print(f"[TAGGER] Batch error in chat {chat_id}: {e}")
                await asyncio.sleep(5)
                continue

            if i + BATCH_SIZE < len(members):
                await asyncio.sleep(BATCH_DELAY)

        total_tagged = session["tagged_count"]
        status = "cancelled" if session.get("cancelled") else "completed"
        await log_tagging_event(
            chat_id, admin_id, total_tagged, custom_message or "[@all]", status
        )

        if not session.get("cancelled"):
            completion_msg = (
                f"✅ <b>Tagging সম্পন্ন!</b>\n\n"
                f"👥 Tagged: <b>{total_tagged}/{len(members)}</b>"
            )
            msg = await client.send_message(chat_id, completion_msg, parse_mode=HTML)
            await asyncio.sleep(10)
            try:
                await msg.delete()
            except Exception:
                pass

    except Exception as e:
        print(f"[TAGGER] Loop error for chat {chat_id}: {e}")
        await log_tagging_event(chat_id, admin_id, session["tagged_count"], custom_message or "[@all]", "error")

    finally:
        if chat_id in active_tagging_sessions:
            del active_tagging_sessions[chat_id]


async def cancel_tagging(chat_id: int) -> tuple:
    if chat_id not in active_tagging_sessions:
        return False, "❌ এই গ্রুপে কোনো active tagging নেই।"

    session = active_tagging_sessions[chat_id]
    session["cancelled"] = True

    try:
        await asyncio.wait_for(session["task"], timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        session["task"].cancel()

    tagged_count = session["tagged_count"]
    return True, f"🛑 Tagging বন্ধ করা হয়েছে। {tagged_count} জনকে tag করা হয়েছিল।"


@app.on_message(filters.command("tagall") & filters.group)
async def tagall_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        m = await message.reply_text(
            "❌ <b>শুধুমাত্র Admin</b>\n\nএই command শুধু গ্রুপ admin ব্যবহার করতে পারবেন।",
            parse_mode=HTML,
        )
        await asyncio.sleep(10)
        try:
            await m.delete()
        except Exception:
            pass
        return

    chat_id = message.chat.id
    admin_id = message.from_user.id

    args = message.command[1:]
    custom_message = " ".join(args) if args else None

    success, status_msg = await execute_tagall(
        client,
        chat_id,
        admin_id,
        custom_message=custom_message,
    )

    m = await message.reply_text(status_msg, parse_mode=HTML)

    if success:
        await asyncio.sleep(5)
        try:
            await m.delete()
        except Exception:
            pass


@app.on_message(filters.command("utag") & filters.group)
async def utag_cmd(client: Client, message: Message):
    await tagall_cmd(client, message)


@app.on_message(filters.command("stoptag") & filters.group)
async def stoptag_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        m = await message.reply_text(
            "❌ <b>শুধুমাত্র Admin</b>",
            parse_mode=HTML,
        )
        await asyncio.sleep(10)
        try:
            await m.delete()
        except Exception:
            pass
        return

    chat_id = message.chat.id
    success, status_msg = await cancel_tagging(chat_id)

    m = await message.reply_text(status_msg, parse_mode=HTML)
    await asyncio.sleep(5)
    try:
        await m.delete()
    except Exception:
        pass


@app.on_message(filters.command("tagstatus") & filters.group)
async def tagstatus_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    chat_id = message.chat.id
    if chat_id not in active_tagging_sessions:
        status = "✅ কোনো active tagging নেই।"
    else:
        session = active_tagging_sessions[chat_id]
        progress = session["tagged_count"]
        total = session["total_count"]
        percent = int((progress / total) * 100) if total > 0 else 0
        status = (
            f"🔄 <b>Tagging চলছে</b>\n"
            f"👥 Tagged: {progress}/{total} ({percent}%)"
        )

    m = await message.reply_text(status, parse_mode=HTML)
    await asyncio.sleep(30)
    try:
        await m.delete()
    except Exception:
        pass
