"""
Invisible Tagall Feature - Mention all group members invisibly
- Uses ZWNJ (Zero-Width Non-Joiner) characters for invisible mentions
- Batches mentions to avoid Telegram flood limits
- Session tracking for active tagging processes
- Admin-only commands with proper permission checks
"""

import asyncio
from datetime import datetime
from typing import Dict, List, Set

from pyrogram import Client, filters
from pyrogram.types import Message, ChatMember
from pyrogram.enums import ChatMemberStatus

from config import HTML, app, db
from helpers import log_event, _auto_del, _is_admin_msg

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION & SESSION MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

# ZWNJ character (invisible separator for mentions)
ZWNJ = "\u200c"

# Batching configuration
BATCH_SIZE = 8            # Members per batch
BATCH_DELAY = 1.5        # Seconds between batches (to avoid flooding)
MAX_SIMULTANEOUS = 2     # Max simultaneous tagging processes

# Session tracking for active tagging
active_tagging_sessions: Dict[int, Dict] = {}
# Format: {chat_id: {
#     "user_id": admin_id,
#     "task": asyncio.Task,
#     "message_id": message_id,
#     "cancelled": bool,
#     "started_at": datetime,
#     "target_members": [members...],
#     "tagged_count": int,
#     "total_count": int
# }}

# Rate limiting
tagging_cooldown: Dict[int, datetime] = {}
COOLDOWN_SECONDS = 5


# ══════════════════════════════════════════════════════════════════════════════
# COLLECTION MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

tagger_logs_col = db["tagger_logs"]


async def log_tagging_event(chat_id: int, user_id: int, member_count: int, message: str, status: str):
    """Log tagging events to database."""
    await tagger_logs_col.insert_one({
        "chat_id": chat_id,
        "admin_id": user_id,
        "member_count": member_count,
        "message": message,
        "status": status,  # "started", "completed", "cancelled", "error"
        "timestamp": datetime.utcnow(),
    })


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

async def get_group_members(client: Client, chat_id: int) -> List[ChatMember]:
    """
    Get all members of a group (including bots).
    This is a moderately intensive operation for large groups.
    """
    members = []
    try:
        # Get chat to know member count
        chat = await client.get_chat(chat_id)
        member_count = getattr(chat, "members_count", None)
        
        if member_count and member_count > 10000:
            # For very large groups, warn and limit
            print(f"[TAGGER] Group {chat_id} has {member_count} members (limits may apply)")
        
        # Iterate through members
        async for member in client.get_chat_members(chat_id):
            members.append(member)
            # Safety limit: stop at 5000 members to avoid memory issues
            if len(members) >= 5000:
                print(f"[TAGGER] Reached member limit (5000) for chat {chat_id}")
                break
    
    except Exception as e:
        print(f"[TAGGER] Error fetching members for {chat_id}: {e}")
        return []
    
    return members


def _create_invisible_mention(user_id: int, name: str = None) -> str:
    """
    Create an invisible mention using ZWNJ character.
    The link text is always ZWNJ so the mention is invisible in chat.
    """
    return f"<a href='tg://user?id={user_id}'>{ZWNJ}</a>"


def _create_batch_mentions(members: List[ChatMember]) -> str:
    """
    Create a string of invisible mentions for a batch of members.
    Each mention is separated by ZWNJ to keep them invisible.
    """
    mentions = []
    for member in members:
        if not member.user.is_bot:  # Skip bots
            mention = _create_invisible_mention(member.user.id)
            mentions.append(mention)
    
    return ZWNJ.join(mentions)


async def check_tagging_active(chat_id: int) -> bool:
    """Check if tagging is already active in this chat."""
    if chat_id in active_tagging_sessions:
        session = active_tagging_sessions[chat_id]
        if not session.get("cancelled"):
            return True
    return False


def _is_in_cooldown(chat_id: int) -> bool:
    """Check if chat is in tagging cooldown."""
    if chat_id in tagging_cooldown:
        elapsed = (datetime.utcnow() - tagging_cooldown[chat_id]).total_seconds()
        return elapsed < COOLDOWN_SECONDS
    return False


def _set_cooldown(chat_id: int):
    """Set cooldown for chat."""
    tagging_cooldown[chat_id] = datetime.utcnow()


# ══════════════════════════════════════════════════════════════════════════════
# TAGGING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

async def execute_tagall(
    client: Client,
    chat_id: int,
    admin_id: int,
    custom_message: str = None,
    reply_to_msg_id: int = None,
) -> tuple[bool, str]:
    """
    Execute the tagall process.
    Returns: (success: bool, status_message: str)
    """
    
    # Check if already tagging
    if await check_tagging_active(chat_id):
        return False, "❌ Tagging already in progress in this group!"
    
    # Check cooldown
    if _is_in_cooldown(chat_id):
        return False, f"⏳ Please wait before tagging again. Cooldown active."
    
    # Fetch members
    print(f"[TAGGER] Fetching members for chat {chat_id}...")
    members = await get_group_members(client, chat_id)
    
    if not members:
        return False, "❌ Could not fetch group members. Check bot permissions."
    
    # Filter out bots
    real_members = [m for m in members if not m.user.is_bot]
    
    if not real_members:
        return False, "❌ No members found in the group."
    
    print(f"[TAGGER] Found {len(real_members)} members in chat {chat_id}")
    
    # Create tagging session
    session = {
        "user_id": admin_id,
        "cancelled": False,
        "started_at": datetime.utcnow(),
        "target_members": real_members,
        "tagged_count": 0,
        "total_count": len(real_members),
        "custom_message": custom_message,
    }
    
    # Start tagging task
    task = asyncio.create_task(
        _execute_tagging_loop(client, chat_id, session)
    )
    
    session["task"] = task
    active_tagging_sessions[chat_id] = session
    _set_cooldown(chat_id)
    
    # Log event
    await log_tagging_event(chat_id, admin_id, len(real_members), custom_message or "[@all]", "started")
    
    return True, f"🚀 Starting to tag {len(real_members)} members...\n⏳ This will take a moment..."


async def _execute_tagging_loop(client: Client, chat_id: int, session: Dict):
    """
    Main tagging loop - processes members in batches.
    """
    admin_id = session["user_id"]
    members = session["target_members"]
    custom_message = session.get("custom_message", "")
    
    try:
        # Process members in batches
        for i in range(0, len(members), BATCH_SIZE):
            # Check if cancelled
            if session.get("cancelled"):
                print(f"[TAGGER] Tagging cancelled for chat {chat_id}")
                break
            
            batch = members[i:i + BATCH_SIZE]
            batch_number = i // BATCH_SIZE + 1
            total_batches = (len(members) + BATCH_SIZE - 1) // BATCH_SIZE
            
            # Create invisible mentions for this batch
            invisible_mentions = _create_batch_mentions(batch)
            
            # Build message with custom text + invisible mentions
            if custom_message:
                message_text = f"{custom_message}\n\n{invisible_mentions}"
            else:
                message_text = invisible_mentions
            
            # Send the batch
            try:
                await client.send_message(
                    chat_id,
                    message_text,
                    parse_mode=HTML,
                )
                
                batch_tagged = len([m for m in batch if not m.user.is_bot])
                session["tagged_count"] += batch_tagged
                
                print(f"[TAGGER] Batch {batch_number}/{total_batches} sent ({batch_tagged} members) in chat {chat_id}")
                
            except Exception as e:
                print(f"[TAGGER] Error sending batch {batch_number} in chat {chat_id}: {e}")
                await asyncio.sleep(2)  # Longer delay on error
                continue
            
            # Delay between batches to avoid flooding
            if i + BATCH_SIZE < len(members):
                await asyncio.sleep(BATCH_DELAY)
        
        # Tagging complete
        total_tagged = session["tagged_count"]
        print(f"[TAGGER] Tagging complete for chat {chat_id} ({total_tagged} members)")
        
        # Log completion
        status = "cancelled" if session.get("cancelled") else "completed"
        await log_tagging_event(
            chat_id, admin_id, total_tagged, custom_message or "[@all]", status
        )
        
        # Send completion message
        completion_msg = (
            f"✅ <b>Tagging Complete!</b>\n\n"
            f"👥 Members Tagged: <b>{total_tagged}/{len(members)}</b>\n"
            f"⏱️ Time: {datetime.utcnow().strftime('%H:%M:%S')}"
        )
        msg = await client.send_message(chat_id, completion_msg, parse_mode=HTML)
        await asyncio.sleep(10)
        try:
            await msg.delete()
        except:
            pass
        
    except Exception as e:
        print(f"[TAGGER] Tagging loop error for chat {chat_id}: {e}")
        await log_tagging_event(chat_id, admin_id, session["tagged_count"], custom_message or "[@all]", "error")
    
    finally:
        # Clean up session
        if chat_id in active_tagging_sessions:
            del active_tagging_sessions[chat_id]


async def cancel_tagging(chat_id: int) -> tuple[bool, str]:
    """
    Cancel the current tagging process in a chat.
    """
    if chat_id not in active_tagging_sessions:
        return False, "❌ No active tagging process in this group."
    
    session = active_tagging_sessions[chat_id]
    session["cancelled"] = True
    
    # Wait for task to finish
    try:
        await asyncio.wait_for(session["task"], timeout=5)
    except asyncio.TimeoutError:
        session["task"].cancel()
    
    tagged_count = session["tagged_count"]
    return True, f"🛑 Tagging cancelled. {tagged_count} members were tagged."


async def get_tagging_status(chat_id: int) -> str:
    """Get current tagging status for a chat."""
    if chat_id not in active_tagging_sessions:
        return "✅ No active tagging process."
    
    session = active_tagging_sessions[chat_id]
    progress = session["tagged_count"]
    total = session["total_count"]
    percent = int((progress / total) * 100) if total > 0 else 0
    
    return (
        f"🔄 <b>Tagging in Progress</b>\n"
        f"👥 Tagged: {progress}/{total} ({percent}%)\n"
        f"⏱️ Started: {session['started_at'].strftime('%H:%M:%S')}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("tagall") & filters.group)
async def tagall_cmd(client: Client, message: Message):
    """Main /tagall command - tag all members invisibly."""
    
    # Check admin permission
    if not await _is_admin_msg(client, message):
        m = await message.reply_text(
            "❌ <b>Admin Only</b>\n\n"
            "Only group admins can use this command.",
            parse_mode=HTML,
        )
        await asyncio.sleep(10)
        try:
            await m.delete()
        except:
            pass
        return
    
    chat_id = message.chat.id
    admin_id = message.from_user.id
    
    # Get custom message if provided
    args = message.command[1:]
    custom_message = " ".join(args) if args else None
    
    # Execute tagging
    success, status_msg = await execute_tagall(
        client,
        chat_id,
        admin_id,
        custom_message=custom_message,
        reply_to_msg_id=message.id,
    )
    
    # Send status
    m = await message.reply_text(status_msg, parse_mode=HTML)
    
    # Auto-delete after 5 seconds if successful
    if success:
        await asyncio.sleep(5)
        try:
            await m.delete()
        except:
            pass


@app.on_message(filters.command("utag") & filters.group)
async def utag_cmd(client: Client, message: Message):
    """Alias for /tagall command."""
    # Reuse tagall logic
    await tagall_cmd(client, message)


@app.on_message(filters.command("cancel") & filters.group)
async def cancel_cmd(client: Client, message: Message):
    """Cancel current tagging process."""
    
    # Check admin permission
    if not await _is_admin_msg(client, message):
        m = await message.reply_text(
            "❌ <b>Admin Only</b>\n\n"
            "Only group admins can use this command.",
            parse_mode=HTML,
        )
        await asyncio.sleep(10)
        try:
            await m.delete()
        except:
            pass
        return
    
    chat_id = message.chat.id
    
    # Cancel tagging
    success, status_msg = await cancel_tagging(chat_id)
    
    # Send response
    m = await message.reply_text(status_msg, parse_mode=HTML)
    await asyncio.sleep(5)
    try:
        await m.delete()
    except:
        pass


@app.on_message(filters.command("stoptag") & filters.group)
async def stoptag_cmd(client: Client, message: Message):
    """Alias for /cancel command."""
    await cancel_cmd(client, message)


@app.on_message(filters.command("tagstatus") & filters.group)
async def tagstatus_cmd(client: Client, message: Message):
    """Check tagging status."""
    
    # Check admin permission
    if not await _is_admin_msg(client, message):
        m = await message.reply_text(
            "❌ <b>Admin Only</b>\n\n"
            "Only group admins can use this command.",
            parse_mode=HTML,
        )
        await asyncio.sleep(10)
        try:
            await m.delete()
        except:
            pass
        return
    
    chat_id = message.chat.id
    status = await get_tagging_status(chat_id)
    
    m = await message.reply_text(status, parse_mode=HTML)
    
    # Auto-delete status messages after 30 seconds
    await asyncio.sleep(30)
    try:
        await m.delete()
    except:
        pass


@app.on_message(filters.command("taggerhelp") & filters.group)
async def taggerhelp_cmd(client: Client, message: Message):
    """Show help for tagging commands."""
    
    # Check admin permission
    if not await _is_admin_msg(client, message):
        m = await message.reply_text(
            "❌ <b>Admin Only</b>\n\n"
            "Only group admins can use this command.",
            parse_mode=HTML,
        )
        await asyncio.sleep(10)
        try:
            await m.delete()
        except:
            pass
        return
    
    help_text = (
        "<b>🔔 TAGALL COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Main Commands:</b>\n"
        "  <code>/tagall [message]</code>   — Tag all members invisibly\n"
        "  <code>/utag [message]</code>     — Alias for /tagall\n\n"
        "<b>Control Commands:</b>\n"
        "  <code>/tagstatus</code>          — Check tagging progress\n"
        "  <code>/cancel</code>             — Stop active tagging\n"
        "  <code>/stoptag</code>            — Alias for /cancel\n\n"
        "<b>Examples:</b>\n"
        "  <code>/tagall</code>             — Tag all with no message\n"
        "  <code>/tagall Meeting at 5 PM</code>  — Tag with custom message\n"
        "  <code>/utag 🔔 Important Update</code> — Use alias with emoji\n\n"
        "<b>ℹ️ How It Works:</b>\n"
        "  • Uses invisible mentions (ZWNJ) to notify members\n"
        "  • Batches members (8 per message) to avoid flooding\n"
        "  • 5-second cooldown between tags per group\n"
        "  • Admin-only feature\n"
        "  • Real-time progress tracking\n\n"
        "<b>⚠️ Notes:</b>\n"
        "  • Bots are automatically excluded\n"
        "  • Large groups (5000+ members) may take time\n"
        "  • Mentions are invisible but members will be notified\n"
        "━━━━━━━━━━━━━━━━━━━━━"
    )
    
    m = await message.reply_text(help_text, parse_mode=HTML)
    await asyncio.sleep(60)
    try:
        await m.delete()
    except:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# CLEANUP & INITIALIZATION
# ══════════════════════════════════════════════════════════════════════════════

async def cleanup_stale_sessions():
    """
    Cleanup stale tagging sessions (safety measure).
    Called periodically to ensure no orphaned sessions.
    """
    now = datetime.utcnow()
    stale_chats = []
    
    for chat_id, session in active_tagging_sessions.items():
        elapsed = (now - session["started_at"]).total_seconds()
        # If session is older than 1 hour, mark as stale
        if elapsed > 3600:
            stale_chats.append(chat_id)
            print(f"[TAGGER] Cleaning up stale session for chat {chat_id}")
    
    for chat_id in stale_chats:
        if chat_id in active_tagging_sessions:
            del active_tagging_sessions[chat_id]
