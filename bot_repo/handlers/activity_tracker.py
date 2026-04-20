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
import time
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import app, HTML, settings_col, clones_col, groups_col, ADMIN_ID, ADMIN_IDS
from helpers import _is_admin_msg, _auto_del, get_cfg, _clone_config_ctx


# ── MongoDB collections ────────────────────────────────────────────────────────
_monitor_col   = None   # chat_monitor_msgs — header/copied IDs → user_id
_tracking_col  = None   # chat_monitor_settings — per-group enabled flag

# ── Forward dedup — prevents both main+clone bot forwarding the same message ──
# Key = (chat_id, msg_id), value = float timestamp. Cleaned every 60 s.
_fwd_dedup: dict[tuple, float] = {}

# ── Group DM sessions ─────────────────────────────────────────────────────────
_groupdm_sessions: dict[int, dict] = {}  # admin_id → session

# ── Reply-in-Group sessions ────────────────────────────────────────────────────
# Key = prompt_msg_id sent by bot in monitor group
# Value = {group_id, group_title, monitor_id}
_greply_prompts: dict[int, dict] = {}


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
    if message.from_user.id not in ADMIN_IDS:
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


@app.on_message(filters.command("setmonitorgroup") & filters.group, group=1)
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
    if message.from_user.id not in ADMIN_IDS:
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

@app.on_message(filters.command("trackchats") & filters.group, group=1)
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
    group=-5,           # runs BEFORE clone_guard (group=-4) — both main+clone can fire
)
async def forward_to_monitor(client: Client, message: Message):
    # Forwarding is now handled exclusively by monitor.py (relay_group_msg_to_monitor).
    # This handler is kept for PATH-B DB records only but disabled to avoid double-forward.
    return

    if not message.from_user:  # noqa: unreachable
        return
    if message.from_user.is_bot or message.from_user.is_self:
        return
    if (message.text or message.caption or "").lstrip().startswith("/"):
        return

    monitor_id = await _get_monitor_group(client)
    if not monitor_id:
        return

    if message.chat.id == monitor_id:
        return

    if not await _is_tracking_enabled(message.chat.id):
        return

    # ── Dedup: if the same (chat, msg) was already forwarded (by the other bot),
    # skip. This prevents duplicate forwards when both main + clone are in the group.
    key = (message.chat.id, message.id)
    now = time.monotonic()

    # Clean stale entries > 60 s old
    stale = [k for k, t in _fwd_dedup.items() if now - t > 60]
    for k in stale:
        _fwd_dedup.pop(k, None)

    if key in _fwd_dedup:
        return   # Already forwarded by the other bot instance
    _fwd_dedup[key] = now   # Claim this forward (atomic in asyncio single-thread)

    uid        = message.from_user.id
    name       = message.from_user.first_name or "User"
    group_id   = message.chat.id
    group_title = message.chat.title or str(group_id)

    # ── সরাসরি message forward করো (header ছাড়া) ──────────────────────────────
    try:
        fwd = await client.forward_messages(
            chat_id=monitor_id,
            from_chat_id=message.chat.id,
            message_ids=message.id,
        )
        # forward_messages returns Message or List[Message]
        if isinstance(fwd, list):
            fwd = fwd[0] if fwd else None
        fwd_msg_id = fwd.id if fwd else None

        # ── forward হওয়া message-এ reaction দাও ──────────────────────────────
        if fwd_msg_id:
            for emoji in ("👁", "👀", "👍"):
                try:
                    await client.send_reaction(
                        chat_id=monitor_id,
                        message_id=fwd_msg_id,
                        emoji=emoji,
                    )
                    break
                except Exception:
                    continue

        await _mon_col().insert_one({
            "fwd_msg_id":     fwd_msg_id,
            "header_msg_id":  fwd_msg_id,   # backward compat for PATH B lookup
            "copied_msg_id":  fwd_msg_id,   # backward compat for PATH B lookup
            "original_msg_id": message.id,
            "user_id":        uid,
            "user_name":      name,
            "group_title":    group_title,
            "group_id":       group_id,
            "monitor_id":     monitor_id,
            "created_at":     datetime.utcnow(),
        })
    except Exception as exc:
        _fwd_dedup.pop(key, None)
        print(f"[MONITOR] Forward failed from {group_id}: {exc}")


# ── "Reply in Group" button callback ─────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^mgr:"))
async def monitor_greply_cb(client: Client, cq: CallbackQuery):
    """Admin clicks 'Reply in Group' on a monitor header → bot sends a prompt."""
    uid = cq.from_user.id if cq.from_user else 0
    if uid not in ADMIN_IDS:
        from config import admins_col
        is_admin = await admins_col.find_one({"user_id": uid, "active": True})
        if not is_admin:
            await cq.answer("❌ Admins only.", show_alert=True)
            return

    group_id = int(cq.data.split(":", 1)[1])
    monitor_id = await _get_monitor_group(client)
    if not monitor_id:
        await cq.answer("Monitor group not set.", show_alert=True)
        return

    # Fetch group title from DB
    doc = await groups_col.find_one({"chat_id": group_id})
    group_title = (doc or {}).get("title", str(group_id))

    try:
        prompt = await client.send_message(
            monitor_id,
            f"✏️ <b>Reply to this message</b> with what you want to send to:\n"
            f"📍 <b>{group_title}</b>",
            parse_mode=HTML,
        )
        _greply_prompts[prompt.id] = {
            "group_id":    group_id,
            "group_title": group_title,
            "monitor_id":  monitor_id,
        }
        await cq.answer("Send your reply now ↑", show_alert=False)
    except Exception as exc:
        await cq.answer(f"Error: {exc}", show_alert=True)


# ── Admin replies in Monitor Group → DM user OR send to original group ────────

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

    # ── PATH A: Admin replied to a "Reply in Group" prompt → send to group ────
    greply = _greply_prompts.pop(replied_id, None)
    if greply:
        target_group = greply["group_id"]
        group_title  = greply["group_title"]
        try:
            await client.copy_message(
                chat_id=target_group,
                from_chat_id=message.chat.id,
                message_id=message.id,
            )
            confirm = await message.reply_text(
                f"✅ Sent to group <b>{group_title}</b>", parse_mode=HTML
            )
            asyncio.create_task(_auto_del(confirm, 8))
        except Exception as exc:
            err = await message.reply_text(
                f"❌ Failed to send to group: <code>{exc}</code>", parse_mode=HTML
            )
            asyncio.create_task(_auto_del(err, 20))
        return  # Done — don't also DM the user

    # ── PATH B: Admin replied to a forwarded message → DM original user ───────
    doc = await _mon_col().find_one({
        "$or": [
            {"header_msg_id": replied_id},
            {"copied_msg_id": replied_id},
        ],
        "monitor_id": monitor_id,
    })
    if not doc:
        return  # Not a tracked message — ignore

    group_id        = doc.get("group_id")
    group_title     = doc.get("group_title", "a group")
    original_msg_id = doc.get("original_msg_id")

    if not group_id:
        err = await message.reply_text("❌ Group ID পাওয়া যায়নি।", parse_mode=HTML)
        asyncio.create_task(_auto_del(err, 15))
        return

    try:
        await client.copy_message(
            chat_id=group_id,
            from_chat_id=message.chat.id,
            message_id=message.id,
            reply_to_message_id=original_msg_id,
        )
        confirm = await message.reply_text(
            f"✅ Reply sent to group <b>{group_title}</b>", parse_mode=HTML
        )
        asyncio.create_task(_auto_del(confirm, 8))
    except Exception as exc:
        err = await message.reply_text(
            f"❌ Reply failed to group <code>{group_id}</code>: <code>{exc}</code>",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(err, 20))
        print(f"[MONITOR] Reply to group failed: {exc}")


# ── /groupdm — DM a message to ALL members of a selected group ───────────────
#
# Usage:
#   /groupdm                  → shows group list (pick with buttons)
#   /groupdm -1001234567890   → jump straight to message step
#
# After picking the group, send the message/photo/video.
# Bot DMs every member and reports sent/failed count.

@app.on_message(filters.command("groupdm") & filters.private, group=-3)
async def groupdm_start(client: Client, message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    args = message.command[1:]

    if args and args[0].lstrip("-").isdigit():
        gid = int(args[0])
        try:
            chat = await client.get_chat(gid)
            title = chat.title or str(gid)
        except Exception:
            await message.reply_text(
                "❌ Cannot access that group. Make sure the bot is in it.",
                parse_mode=HTML,
            )
            return
        _groupdm_sessions[ADMIN_ID] = {
            "step":  "content",
            "gid":   gid,
            "title": title,
        }
        await message.reply_text(
            f"📤 <b>Group DM</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Target: <b>{title}</b>  <code>{gid}</code>\n\n"
            f"Send the message (text/photo/video/sticker) to broadcast.\n"
            f"/cancel to abort.",
            parse_mode=HTML,
        )
        return

    # Show group list as buttons
    docs = await groups_col.find({}).sort("title", 1).to_list(length=50)
    if not docs:
        await message.reply_text("📭 No groups in DB yet.", parse_mode=HTML)
        return

    buttons = []
    for d in docs[:24]:
        cid   = d.get("chat_id")
        label = (d.get("title") or str(cid))[:30]
        buttons.append([InlineKeyboardButton(label, callback_data=f"gdm:{cid}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="gdm:cancel")])

    _groupdm_sessions[ADMIN_ID] = {"step": "pick"}
    await message.reply_text(
        "📤 <b>Group DM</b>\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select the group to DM all members:",
        parse_mode=HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@app.on_callback_query(filters.regex(r"^gdm:"))
async def groupdm_pick_cb(client: Client, cq):
    if cq.from_user.id not in ADMIN_IDS:
        await cq.answer()
        return

    data = cq.data.split(":", 1)[1]
    if data == "cancel":
        _groupdm_sessions.pop(ADMIN_ID, None)
        await cq.edit_message_text("❌ Cancelled.")
        return

    gid = int(data)
    try:
        chat  = await client.get_chat(gid)
        title = chat.title or str(gid)
    except Exception:
        title = str(gid)

    _groupdm_sessions[ADMIN_ID] = {
        "step":  "content",
        "gid":   gid,
        "title": title,
    }
    await cq.edit_message_text(
        f"📤 <b>Group DM</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Target: <b>{title}</b>  <code>{gid}</code>\n\n"
        f"Send the message (text/photo/video/sticker) to broadcast.\n"
        f"/cancel to abort.",
        parse_mode=HTML,
    )


@app.on_message(filters.private, group=5)
async def groupdm_content_handler(client: Client, message: Message):
    """Capture admin's content message after groupdm target is set."""
    if not message.from_user or message.from_user.id not in ADMIN_IDS:
        return

    session = _groupdm_sessions.get(ADMIN_ID)
    if not session or session.get("step") != "content":
        return

    text_in = (message.text or "").strip()
    if text_in == "/cancel":
        _groupdm_sessions.pop(ADMIN_ID, None)
        await message.reply_text("❌ Group DM cancelled.", parse_mode=HTML)
        return

    gid   = session["gid"]
    title = session["title"]
    _groupdm_sessions.pop(ADMIN_ID, None)

    status_msg = await message.reply_text(
        f"⏳ Fetching members of <b>{title}</b>…",
        parse_mode=HTML,
    )

    sent   = 0
    failed = 0
    total  = 0
    src_chat = message.chat.id
    src_mid  = message.id

    try:
        async for member in client.get_chat_members(gid):
            user = member.user
            if user.is_bot or user.is_deleted:
                continue
            total += 1
            try:
                await client.copy_message(
                    chat_id=user.id,
                    from_chat_id=src_chat,
                    message_id=src_mid,
                )
                sent += 1
            except FloodWait as fw:
                await asyncio.sleep(fw.value + 1)
                try:
                    await client.copy_message(
                        chat_id=user.id,
                        from_chat_id=src_chat,
                        message_id=src_mid,
                    )
                    sent += 1
                except Exception:
                    failed += 1
            except Exception:
                failed += 1

            # Progress update every 50 members
            if total % 50 == 0:
                try:
                    await status_msg.edit_text(
                        f"⏳ Sending… {sent}/{total} done, {failed} failed",
                        parse_mode=HTML,
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.05)

    except Exception as exc:
        await status_msg.edit_text(
            f"❌ Error fetching members: <code>{exc}</code>",
            parse_mode=HTML,
        )
        return

    await status_msg.edit_text(
        f"✅ <b>Group DM Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Group: <b>{title}</b>\n"
        f"👥 Total Members: <b>{total}</b>\n"
        f"📤 Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        parse_mode=HTML,
    )
    print(f"[GROUPDM] {title} ({gid}) → sent={sent} failed={failed}")


# ── /groupstats — detailed stats of all groups the bot is in ──────────────────

@app.on_message(filters.command("groupstats") & filters.private, group=-3)
async def groupstats_cmd(client: Client, message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    await message.reply_text("📊 Fetching group stats…", parse_mode=HTML)

    # ── Fetch all groups — sorted by member_count desc ────────────────────────
    docs = await groups_col.find({}).sort("member_count", -1).to_list(length=None)
    # Put docs without member_count at the end
    docs_with = [d for d in docs if d.get("member_count") is not None]
    docs_without = [d for d in docs if d.get("member_count") is None]
    docs = docs_with + docs_without
    total = len(docs)

    if total == 0:
        await message.reply_text("📭 Bot is not in any groups yet.", parse_mode=HTML)
        return

    admin_count   = sum(1 for d in docs if d.get("bot_is_admin"))
    non_admin     = total - admin_count
    total_members = sum(d.get("member_count") or 0 for d in docs)

    # ── Fetch per-group trackchats settings ──────────────────────────────────
    trk_docs = await _trk_col().find({}).to_list(length=None)
    trk_map  = {d["chat_id"]: d.get("enabled", True) for d in trk_docs}

    # ── Summary ──────────────────────────────────────────────────────────────
    lines = [
        f"📊 <b>Group Statistics</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"📌 <b>Total Groups:</b> {total}",
        f"👑 <b>Bot is Admin:</b> {admin_count}   👤 <b>Not Admin:</b> {non_admin}",
        f"👥 <b>Total Members:</b> {total_members:,}",
        f"",
        f"<b>Groups (largest first):</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # ── Per-group list (max 25) ───────────────────────────────────────────────
    for i, d in enumerate(docs[:25], 1):
        cid      = d.get("chat_id", "?")
        title    = (d.get("title") or str(cid))[:25]
        members  = d.get("member_count")
        cnt_str  = f"{members:,}" if members is not None else "?"
        is_admin = "👑" if d.get("bot_is_admin") else "👤"
        tracking = trk_map.get(cid, True)
        trk_icon = "📡" if tracking else "🔇"
        lines.append(f"{i}. {is_admin}{trk_icon} <b>{title}</b>  👥{cnt_str}")

    if total > 25:
        lines.append(f"\n<i>…and {total - 25} more groups.</i>")

    lines += [
        f"",
        f"<b>Legend:</b> 👑admin 👤not-admin 📡tracking-on 🔇tracking-off",
    ]

    await message.reply_text("\n".join(lines), parse_mode=HTML)
