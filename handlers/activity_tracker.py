"""
Chat Monitor Group System
──────────────────────────
• All group messages (text + media) → Monitor Group (default: ON)
• /trackchats on/off  — enable / disable per group
• Admin replies in Monitor Group → bot DMs the original user
• Works for BOTH main bot and clone bot groups

Log Group   = bot operational logs only
Inbox Group = private DMs only (unchanged)

Setup:
  1. Create a dedicated Telegram group (e.g. "Chat Monitor")
  2. Add the bot as admin there
  3. Send /setmonitorgroup in that group
  4. Optionally disable specific groups: /trackchats off (inside that group)
"""
import asyncio
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from config import app, HTML, settings_col, clones_col, groups_col, ADMIN_ID
from helpers import _is_admin_msg, _auto_del, get_cfg, _clone_config_ctx


# ── MongoDB collections ────────────────────────────────────────────────────────
_monitor_col   = None   # chat_monitor_msgs — header/copied IDs → user_id
_tracking_col  = None   # chat_monitor_settings — per-group enabled flag


def _mon_col():
    global _monitor_col
    if _monitor_col is None:
        from config import db
        _monitor_col = db["chat_monitor_msgs"]
    return _monitor_col


def _trk_col():
    global _tracking_col
    if _tracking_col is None:
        from config import db
        _tracking_col = db["chat_monitor_settings"]
    return _tracking_col


# In-memory cache {chat_id: bool}
_trk_cache: dict[int, bool] = {}


async def _is_tracking_enabled(chat_id: int) -> bool:
    """Per-group toggle — default ON (True)."""
    if chat_id in _trk_cache:
        return _trk_cache[chat_id]
    doc = await _trk_col().find_one({"chat_id": chat_id})
    val = doc.get("enabled", True) if doc else True   # default ON
    _trk_cache[chat_id] = val
    return val


# ── Clone-aware monitor group helpers ─────────────────────────────────────────

async def _get_monitor_group(client=None) -> int | None:
    if client is not None:
        cfg = getattr(client, "_clone_config", None)
        if cfg and cfg.get("monitor_group"):
            return int(cfg["monitor_group"])
    clone_mg = get_cfg("monitor_group")
    if clone_mg:
        return int(clone_mg)
    doc = await settings_col.find_one({"key": "chat_monitor_group"})
    if doc and doc.get("chat_id"):
        return int(doc["chat_id"])
    return None


async def _set_monitor_group(chat_id: int):
    cfg = _clone_config_ctx.get()
    if cfg:
        from clone_manager import reload_clone_config
        tok = cfg.get("token")
        await clones_col.update_one(
            {"token": tok},
            {"$set": {"monitor_group": chat_id}},
            upsert=True,
        )
        await reload_clone_config(tok)
        return
    await settings_col.update_one(
        {"key": "chat_monitor_group"},
        {"$set": {"chat_id": chat_id}},
        upsert=True,
    )


def _now() -> str:
    return datetime.utcnow().strftime("%d %b %Y %H:%M UTC")


def _link(user) -> str:
    name = (user.first_name or "User") if user else "User"
    uid  = user.id if user else "?"
    return f'<a href="tg://user?id={uid}">{name}</a>'


# ── /setmonitorgroup — works from PRIVATE DM or from inside the target group ──
#
# From private DM (recommended, no confusion):
#   /setmonitorgroup -1001234567890
#
# From inside the monitor group (bot must be admin there):
#   /setmonitorgroup
#
# Both main bot AND clone bot automatically use the same monitor group.

@app.on_message(filters.command("setmonitorgroup") & filters.private, group=-3)
async def set_monitor_group_private(client: Client, message: Message):
    """Set monitor group from private DM — just send the group ID."""
    if message.from_user.id != ADMIN_ID:
        return

    args = message.command[1:]
    if not args:
        # Show current setting + usage
        current = await _get_monitor_group(client)
        cur_str = f"<code>{current}</code>" if current else "<i>Not set</i>"
        await message.reply_text(
            f"📡 <b>Monitor Group Setup</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Current: {cur_str}\n\n"
            f"<b>How to set:</b>\n"
            f"1️⃣ Add the bot as admin to your monitor group\n"
            f"2️⃣ Get the group ID (forward any message to @userinfobot)\n"
            f"3️⃣ Send here:\n"
            f"<code>/setmonitorgroup -1001234567890</code>\n\n"
            f"<b>✅ Both main bot &amp; clone bot will use the same group.</b>",
            parse_mode=HTML,
        )
        return

    raw = args[0].strip()
    if not raw.lstrip("-").isdigit():
        await message.reply_text(
            "❌ Invalid group ID. Example:\n"
            "<code>/setmonitorgroup -1001234567890</code>",
            parse_mode=HTML,
        )
        return

    chat_id = int(raw)

    # Verify bot can access this group
    try:
        chat = await client.get_chat(chat_id)
        chat_title = chat.title or str(chat_id)
    except Exception:
        chat_title = str(chat_id)

    # Force-save to settings_col (bypasses clone context — shared by all bots)
    await settings_col.update_one(
        {"key": "chat_monitor_group"},
        {"$set": {"chat_id": chat_id}},
        upsert=True,
    )

    await message.reply_text(
        f"✅ <b>Monitor Group Set!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Group: <b>{chat_title}</b>\n"
        f"🆔 ID: <code>{chat_id}</code>\n\n"
        f"✔️ Main bot &amp; clone bot will both forward here.\n"
        f"✔️ Reply to any message → DMs that user.\n"
        f"✔️ Use /trackchats on/off in any group to toggle.",
        parse_mode=HTML,
    )
    print(f"[MONITOR] Monitor group set → {chat_id} ({chat_title})")


@app.on_message(filters.command("setmonitorgroup") & filters.group)
async def set_monitor_group_group(client: Client, message: Message):
    """Set monitor group from inside the group (fallback method)."""
    if not await _is_admin_msg(client, message):
        return
    chat_id = message.chat.id
    # Force-save to settings_col (shared by all bots)
    await settings_col.update_one(
        {"key": "chat_monitor_group"},
        {"$set": {"chat_id": chat_id}},
        upsert=True,
    )
    m = await message.reply_text(
        f"✅ <b>Monitor Group Set!</b>\n"
        f"📍 ID: <code>{chat_id}</code>\n"
        f"Both main bot &amp; clone bot will forward messages here.",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 20))
    try:
        await message.delete()
    except Exception:
        pass


# ── /monitorstatus — check current monitor group and tracking settings ────────

@app.on_message(filters.command("monitorstatus") & filters.private, group=-3)
async def monitor_status_cmd(client: Client, message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    current = await _get_monitor_group(client)
    if current:
        try:
            chat = await client.get_chat(current)
            gname = chat.title or str(current)
        except Exception:
            gname = str(current)
        status = f"✅ Set: <b>{gname}</b>  <code>{current}</code>"
    else:
        status = "❌ Not set — use /setmonitorgroup &lt;group_id&gt;"

    await message.reply_text(
        f"📡 <b>Monitor Group Status</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{status}\n\n"
        f"Both bots share the same group.\n"
        f"Commands:\n"
        f"• /setmonitorgroup &lt;id&gt; — change group\n"
        f"• /trackchats on|off — per-group toggle (in that group)",
        parse_mode=HTML,
    )


# ── /trackchats on|off — per-group toggle ────────────────────────────────────

@app.on_message(filters.command("trackchats") & filters.group)
async def trackchats_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    args = message.command[1:]
    if not args or args[0].lower() not in ("on", "off"):
        m = await message.reply_text(
            "⚠️ Usage: <code>/trackchats on</code> or <code>/trackchats off</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 20))
        return

    enabled = args[0].lower() == "on"
    chat_id = message.chat.id
    _trk_cache[chat_id] = enabled

    await _trk_col().update_one(
        {"chat_id": chat_id},
        {"$set": {"enabled": enabled}},
        upsert=True,
    )

    status = "✅ Enabled" if enabled else "❌ Disabled"
    action = "will now be forwarded" if enabled else "will NO LONGER be forwarded"
    m = await message.reply_text(
        f"{status} — This group's messages {action} to the monitor group.",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 20))
    try:
        await message.delete()
    except Exception:
        pass


# ── Forward group messages → Monitor Group ────────────────────────────────────

_CONTENT_FILTER = (
    filters.text
    | filters.photo
    | filters.video
    | filters.voice
    | filters.sticker
    | filters.document
    | filters.audio
    | filters.animation
    | filters.video_note
)


@app.on_message(
    filters.group & ~filters.service & _CONTENT_FILTER,
    group=8,
)
async def forward_to_monitor(client: Client, message: Message):
    if not message.from_user:
        return
    if message.from_user.is_bot:
        return
    if (message.text or message.caption or "").lstrip().startswith("/"):
        return

    monitor_id = await _get_monitor_group(client)
    if not monitor_id:
        return

    # Don't monitor the monitor group itself
    if message.chat.id == monitor_id:
        return

    # Per-group toggle check
    if not await _is_tracking_enabled(message.chat.id):
        return

    user  = message.from_user
    uid   = user.id
    uname = f"@{user.username}" if user.username else "—"
    name  = user.first_name or "User"
    chat  = message.chat.title or str(message.chat.id)

    header = (
        f"💬 <b>Group Message</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {_link(user)}  <code>{uid}</code>\n"
        f"🔖 {uname}\n"
        f"📍 {chat}\n"
        f"🕒 {_now()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>↩️ Reply here to DM this user</i>"
    )

    try:
        header_msg = await client.send_message(monitor_id, header, parse_mode=HTML)
        copied = await client.copy_message(
            chat_id=monitor_id,
            from_chat_id=message.chat.id,
            message_id=message.id,
        )
        await _mon_col().insert_one({
            "header_msg_id": header_msg.id,
            "copied_msg_id": copied.id if copied else None,
            "user_id":       uid,
            "user_name":     name,
            "group_title":   chat,
            "group_id":      message.chat.id,
            "monitor_id":    monitor_id,
            "created_at":    datetime.utcnow(),
        })
        print(f"[MONITOR] {uid} in '{chat}' → monitor {monitor_id}")
    except Exception as exc:
        print(f"[MONITOR] Forward failed from {message.chat.id}: {exc}")


# ── Admin replies in Monitor Group → DM original user ────────────────────────

@app.on_message(filters.reply & filters.group, group=9)
async def monitor_reply_handler(client: Client, message: Message):
    if not message.from_user:
        return

    monitor_id = await _get_monitor_group(client)
    if not monitor_id or message.chat.id != monitor_id:
        return

    if not await _is_admin_msg(client, message):
        return

    replied_id = message.reply_to_message.id if message.reply_to_message else None
    if not replied_id:
        return

    doc = await _mon_col().find_one({
        "$or": [
            {"header_msg_id": replied_id},
            {"copied_msg_id": replied_id},
        ],
        "monitor_id": monitor_id,
    })
    if not doc:
        return  # Not a tracked message — ignore

    user_id     = doc["user_id"]
    group_title = doc.get("group_title", "a group")

    intro = (
        f"📩 <b>Admin Reply</b>\n"
        f"<i>(regarding your message in {group_title})</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    try:
        await client.send_message(user_id, intro, parse_mode=HTML)
        await client.copy_message(
            chat_id=user_id,
            from_chat_id=message.chat.id,
            message_id=message.id,
        )
        confirm = await message.reply_text(
            f"✅ Sent to <code>{user_id}</code>", parse_mode=HTML
        )
        asyncio.create_task(_auto_del(confirm, 8))
    except Exception as exc:
        err = await message.reply_text(
            f"❌ DM failed to <code>{user_id}</code>: <code>{exc}</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(err, 20))
        print(f"[MONITOR] Reply DM failed: {exc}")


# ── /groupstats — detailed stats of all groups the bot is in ──────────────────

@app.on_message(filters.command("groupstats") & filters.private, group=-3)
async def groupstats_cmd(client: Client, message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    await message.reply_text("📊 Fetching group stats…", parse_mode=HTML)

    # ── Fetch all groups from DB ──────────────────────────────────────────────
    docs = await groups_col.find({}).sort("updated_at", -1).to_list(length=None)
    total = len(docs)
    if total == 0:
        await message.reply_text("📭 Bot is not in any groups yet.", parse_mode=HTML)
        return

    admin_count    = sum(1 for d in docs if d.get("bot_is_admin"))
    non_admin      = total - admin_count

    # ── Fetch per-group trackchats settings ──────────────────────────────────
    trk_docs = await _trk_col().find({}).to_list(length=None)
    trk_map  = {d["chat_id"]: d.get("enabled", True) for d in trk_docs}

    # ── Summary ──────────────────────────────────────────────────────────────
    lines = [
        f"📊 <b>Group Statistics</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📌 <b>Total Groups:</b> {total}",
        f"👑 <b>Bot is Admin:</b> {admin_count}   |   👤 <b>Not Admin:</b> {non_admin}",
        f"",
        f"<b>Top Groups</b> (by activity):",
        f"━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # ── Per-group list (max 20) ───────────────────────────────────────────────
    shown = docs[:20]
    for i, d in enumerate(shown, 1):
        cid   = d.get("chat_id", "?")
        title = (d.get("title") or str(cid))[:28]
        is_admin = "👑" if d.get("bot_is_admin") else "👤"
        tracking = trk_map.get(cid, True)
        trk_icon = "📡" if tracking else "🔇"
        lines.append(f"{i}. {is_admin}{trk_icon} <b>{title}</b>  <code>{cid}</code>")

    if total > 20:
        lines.append(f"\n<i>…and {total - 20} more groups.</i>")

    lines += [
        f"",
        f"<b>Legend:</b>",
        f"👑 Bot is admin  👤 Not admin",
        f"📡 Tracking ON   🔇 Tracking OFF",
        f"",
        f"<i>Use /trackchats on|off inside a group to toggle tracking.</i>",
    ]

    await message.reply_text("\n".join(lines), parse_mode=HTML)
