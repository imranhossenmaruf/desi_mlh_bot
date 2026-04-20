"""
Safe Tag System — Telegram TOS Compliant
- /taggroup [message]  — mention all group members in batches of 4, 3-second delay between batches
- /tagall [message]    — alias for /taggroup
- /stoptag             — cancel active tagging
- /tagstatus           — check tagging progress

Only group admins can use these commands.
Invisible tag features have been removed for TOS compliance.
"""

import asyncio
from datetime import datetime
from typing import Dict, List

from pyrogram import Client, filters
from pyrogram.types import Message, ChatMember

from config import HTML, app, db
from helpers import _auto_del, _is_admin_msg

BATCH_SIZE = 4       # Exactly 4 mentions per message (TOS compliant)
BATCH_DELAY = 3.0    # 3-second mandatory delay between batches (anti-ban)
COOLDOWN_SECONDS = 300

active_tagging_sessions: Dict[int, Dict] = {}
tagging_cooldown: Dict[int, datetime] = {}

tagger_logs_col = db["tagger_logs"]


async def log_tagging_event(
    chat_id: int, user_id: int, member_count: int, message: str, status: str
):
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


def _create_visible_mention(user) -> str:
    """Create a visible mention using the user's real name (TOS compliant — no invisible tags)."""
    name = (user.first_name or "").strip() or "Member"
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def _is_in_cooldown(chat_id: int) -> tuple:
    if chat_id in tagging_cooldown:
        elapsed = (datetime.utcnow() - tagging_cooldown[chat_id]).total_seconds()
        remaining = int(COOLDOWN_SECONDS - elapsed)
        if remaining > 0:
            return True, remaining
    return False, 0


def _set_cooldown(chat_id: int):
    tagging_cooldown[chat_id] = datetime.utcnow()


async def execute_taggroup(
    client: Client,
    chat_id: int,
    admin_id: int,
    custom_message: str = None,
) -> tuple:
    if chat_id in active_tagging_sessions:
        session = active_tagging_sessions[chat_id]
        if not session.get("cancelled"):
            return False, "Tagging is already in progress in this group."

    in_cd, remaining = _is_in_cooldown(chat_id)
    if in_cd:
        mins = remaining // 60
        secs = remaining % 60
        return False, f"Cooldown active. Try again in {mins}m {secs}s."

    members = await get_group_members(client, chat_id)
    if not members:
        return False, "Could not fetch members. Check bot permissions."

    real_members = [m for m in members if not m.user.is_bot and not m.user.is_deleted]
    if not real_members:
        return False, "No members found to tag."

    session = {
        "user_id": admin_id,
        "cancelled": False,
        "started_at": datetime.utcnow(),
        "target_members": real_members,
        "tagged_count": 0,
        "total_count": len(real_members),
        "custom_message": custom_message,
    }

    task = asyncio.create_task(_execute_tagging_loop(client, chat_id, session))
    session["task"] = task
    active_tagging_sessions[chat_id] = session
    _set_cooldown(chat_id)

    await log_tagging_event(
        chat_id, admin_id, len(real_members),
        custom_message or "[@all]", "started"
    )
    return True, f"Starting to tag {len(real_members)} members in batches of {BATCH_SIZE} with {int(BATCH_DELAY)}s delay between batches..."


async def _execute_tagging_loop(client: Client, chat_id: int, session: Dict):
    admin_id = session["user_id"]
    members = session["target_members"]
    custom_message = session.get("custom_message", "")

    try:
        for i in range(0, len(members), BATCH_SIZE):
            if session.get("cancelled"):
                break

            batch = members[i: i + BATCH_SIZE]
            real = [m for m in batch if not m.user.is_bot and not m.user.is_deleted]
            if not real:
                continue

            # Visible mentions only — no invisible/zero-width tricks
            mentions = "  ".join(_create_visible_mention(m.user) for m in real)

            if custom_message:
                body = f"<b>{custom_message}</b>

{mentions}"
            else:
                body = mentions

            try:
                await client.send_message(chat_id, body, parse_mode=HTML)
                session["tagged_count"] += len(real)
            except Exception as e:
                print(f"[TAGGER] Batch error in chat {chat_id}: {e}")
                await asyncio.sleep(5)
                continue

            # Mandatory 3-second anti-ban delay between every batch
            if i + BATCH_SIZE < len(members):
                await asyncio.sleep(BATCH_DELAY)

        total = session["tagged_count"]
        status = "cancelled" if session.get("cancelled") else "completed"
        await log_tagging_event(
            chat_id, admin_id, total, custom_message or "[@all]", status
        )

        if not session.get("cancelled"):
            done_msg = await client.send_message(
                chat_id,
                f"<b>Tagging complete!</b>

Tagged: <b>{total}/{len(members)}</b> members",
                parse_mode=HTML,
            )
            await asyncio.sleep(10)
            try:
                await done_msg.delete()
            except Exception:
                pass

    except Exception as e:
        print(f"[TAGGER] Loop error for chat {chat_id}: {e}")
        await log_tagging_event(
            chat_id, admin_id, session["tagged_count"],
            custom_message or "[@all]", "error"
        )
    finally:
        active_tagging_sessions.pop(chat_id, None)


async def cancel_tagging(chat_id: int) -> tuple:
    if chat_id not in active_tagging_sessions:
        return False, "No active tagging in this group."

    session = active_tagging_sessions[chat_id]
    session["cancelled"] = True

    try:
        await asyncio.wait_for(session["task"], timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        session["task"].cancel()

    tagged_count = session["tagged_count"]
    return True, f"Tagging stopped. {tagged_count} members were tagged."


@app.on_message(filters.command("taggroup") & filters.group)
async def taggroup_cmd(client: Client, message: Message):
    """Tag all group members in visible batches of 4. Admin only."""
    if not await _is_admin_msg(client, message):
        m = await message.reply_text(
            "<b>Admins Only</b>

Only group admins can use /taggroup.",
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
    custom = " ".join(args) if args else None

    success, status_msg = await execute_taggroup(
        client, chat_id, admin_id, custom_message=custom
    )

    m = await message.reply_text(status_msg, parse_mode=HTML)
    if success:
        await asyncio.sleep(5)
        try:
            await m.delete()
        except Exception:
            pass
    try:
        await message.delete()
    except Exception:
        pass


@app.on_message(filters.command("tagall") & filters.group)
async def tagall_cmd(client: Client, message: Message):
    await taggroup_cmd(client, message)


@app.on_message(filters.command("stoptag") & filters.group)
async def stoptag_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        m = await message.reply_text("<b>Admins Only</b>", parse_mode=HTML)
        await asyncio.sleep(10)
        try:
            await m.delete()
        except Exception:
            pass
        return

    success, status_msg = await cancel_tagging(message.chat.id)
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
        status = "No active tagging in progress."
    else:
        session = active_tagging_sessions[chat_id]
        progress = session["tagged_count"]
        total = session["total_count"]
        percent = int((progress / total) * 100) if total > 0 else 0
        status = (
            f"<b>Tagging in progress</b>\n"
            f"Tagged: {progress}/{total} ({percent}%)\n"
            f"Batch size: {BATCH_SIZE} | Delay: {int(BATCH_DELAY)}s"
        )

    m = await message.reply_text(status, parse_mode=HTML)
    await asyncio.sleep(30)
    try:
        await m.delete()
    except Exception:
        pass
