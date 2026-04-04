import asyncio
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, ADMIN_ID, clones_col, app
from helpers import _auto_del, log_event, clone_admin_filter, get_cfg, _clone_config_ctx


# ─── /addclone ────────────────────────────────────────────────────────────────

@app.on_message(filters.command("addclone") & filters.user(ADMIN_ID) & filters.private)
async def addclone_cmd(client: Client, message: Message):
    args = message.command[1:]
    if len(args) < 2:
        await message.reply_text(
            "📌 <b>Usage:</b>\n"
            "<code>/addclone {bot_token} {admin_id} [name]</code>\n\n"
            "• <b>bot_token</b> — from @BotFather\n"
            "• <b>admin_id</b>  — Telegram user ID of this clone's owner\n"
            "• <b>name</b>      — optional label\n\n"
            "Example:\n"
            "<code>/addclone 7123456789:AAH... 987654321 Karim Bot</code>",
            parse_mode=HTML,
        )
        return

    token    = args[0]
    admin_id_str = args[1]
    name     = " ".join(args[2:]) if len(args) > 2 else f"Clone {token[:10]}..."

    if ":" not in token or len(token) < 20:
        await message.reply_text("❌ Invalid bot token format.", parse_mode=HTML)
        return
    if not admin_id_str.lstrip("-").isdigit():
        await message.reply_text("❌ Invalid admin_id. Must be a numeric Telegram user ID.", parse_mode=HTML)
        return

    admin_id = int(admin_id_str)

    existing = await clones_col.find_one({"token": token})
    if existing and existing.get("active"):
        await message.reply_text(
            f"ℹ️ Clone <b>{existing.get('name','?')}</b> is already active.",
            parse_mode=HTML,
        )
        return

    wait = await message.reply_text("⏳ Starting clone bot...", parse_mode=HTML)

    doc = {
        "token":    token,
        "name":     name,
        "active":   True,
        "admin_id": admin_id,
        "video_channel": None,
        "inbox_group":   None,
        "log_group":     None,
        "added_at":      datetime.utcnow(),
        "added_by":      ADMIN_ID,
    }

    from clone_manager import start_clone
    ok = await start_clone(token, name, doc=doc)

    if ok:
        await clones_col.update_one(
            {"token": token},
            {"$set": doc},
            upsert=True,
        )
        await wait.edit_text(
            f"✅ <b>Clone Bot Started!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Name     : <b>{name}</b>\n"
            f"🔑 Token    : <code>{token[:20]}...</code>\n"
            f"👤 Admin ID : <code>{admin_id}</code>\n\n"
            f"<b>Next steps — clone admin must configure:</b>\n"
            f"  <code>/setvideochannel</code>  — add video channel\n"
            f"  <code>/setcloneinbox</code>    — set inbox group\n"
            f"  <code>/setclonelog</code>      — set log group\n"
            f"  <code>/cloneconfig</code>      — view current settings\n\n"
            f"All commands work when messaging the clone bot directly.",
            parse_mode=HTML,
        )
        await log_event(client,
            f"🤖 <b>Clone Bot Added</b>\n"
            f"📌 Name: {name}\n"
            f"👤 Admin: <code>{admin_id}</code>\n"
            f"🔑 Token: <code>{token[:20]}...</code>"
        )
        # Notify the clone admin
        try:
            await client.send_message(
                admin_id,
                f"✅ <b>Your clone bot is now live!</b>\n\n"
                f"🤖 Bot Name : <b>{name}</b>\n\n"
                f"<b>You are the admin of this bot. Set it up:</b>\n"
                f"  /setvideochannel — connect a video channel\n"
                f"  /setcloneinbox   — set inbox/support group\n"
                f"  /setclonelog     — set log/report group\n"
                f"  /cloneconfig     — view all settings\n\n"
                f"<i>Message the clone bot directly to use these commands.</i>",
                parse_mode=HTML,
            )
        except Exception:
            pass
    else:
        await wait.edit_text(
            "❌ <b>Failed to start clone.</b>\n\n"
            "• Invalid or expired token\n"
            "• Bot already running elsewhere",
            parse_mode=HTML,
        )


# ─── /removeclone ─────────────────────────────────────────────────────────────

@app.on_message(filters.command("removeclone") & filters.user(ADMIN_ID) & filters.private)
async def removeclone_cmd(client: Client, message: Message):
    docs = await clones_col.find({"active": True}).to_list(length=100)
    if not docs:
        await message.reply_text("📭 No active clones.", parse_mode=HTML)
        return

    args = message.command[1:]
    if not args:
        lines = ["📋 <b>Active Clones:</b>\n"]
        for i, doc in enumerate(docs, 1):
            lines.append(f"{i}. <b>{doc.get('name','?')}</b>")
            lines.append(f"   Token: <code>{doc['token'][:20]}...</code>")
        lines.append("\nUsage: <code>/removeclone {token}</code>")
        await message.reply_text("\n".join(lines), parse_mode=HTML)
        return

    token = args[0]
    doc   = await clones_col.find_one({"token": token, "active": True})
    if not doc:
        await message.reply_text("❌ Clone not found.", parse_mode=HTML)
        return

    from clone_manager import stop_clone
    await stop_clone(token)
    await clones_col.update_one({"token": token}, {"$set": {"active": False}})

    await message.reply_text(
        f"✅ <b>Clone Removed</b>\n🤖 {doc.get('name','?')}",
        parse_mode=HTML,
    )
    await log_event(client, f"🗑 <b>Clone Bot Removed</b>\n📌 {doc.get('name','?')}")


# ─── /clones ──────────────────────────────────────────────────────────────────

@app.on_message(filters.command("clones") & filters.user(ADMIN_ID) & filters.private)
async def clones_list_cmd(client: Client, message: Message):
    from clone_manager import get_active_clones
    docs    = await clones_col.find({"active": True}).to_list(length=100)
    running = get_active_clones()

    if not docs:
        await message.reply_text(
            "📭 <b>No clones configured.</b>\n\n"
            "Use <code>/addclone {token} {admin_id} [name]</code> to add a clone bot.",
            parse_mode=HTML,
        )
        return

    lines = ["🤖 <b>CLONE BOTS — DESI MLH</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    for i, doc in enumerate(docs, 1):
        token     = doc["token"]
        name      = doc.get("name", "?")
        added_at  = doc.get("added_at")
        admin_id_v = doc.get("admin_id", "—")
        added_str = added_at.strftime("%d %b %Y") if added_at else "—"
        status    = "🟢 Running" if token in running else "🔴 Stopped"
        vc   = doc.get("video_channel") or "—"
        ig   = doc.get("inbox_group")   or "—"
        lg   = doc.get("log_group")     or "—"
        lines.append(
            f"{i}. {status}  <b>{name}</b>\n"
            f"   👤 Admin: <code>{admin_id_v}</code>\n"
            f"   🔑 Token: <code>{token[:20]}...</code>\n"
            f"   📺 Video Ch: <code>{vc}</code>\n"
            f"   📬 Inbox Grp: <code>{ig}</code>\n"
            f"   📋 Log Grp: <code>{lg}</code>\n"
            f"   📅 Added: {added_str}"
        )

    lines.append(
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Total: {len(docs)}  |  🟢 {len(running)} running"
    )
    await message.reply_text("\n\n".join(lines), parse_mode=HTML)


# ═══════════════════════════════════════════════════════════════════════════════
# CLONE ADMIN CONFIG COMMANDS (work only when messaging the clone bot)
# ═══════════════════════════════════════════════════════════════════════════════

async def _update_clone_cfg(field: str, value) -> bool:
    """Update a field in this clone's DB doc and refresh in-memory cache."""
    from clone_manager import reload_clone_config
    token = _clone_config_ctx.get()
    if token is None:
        return False
    tok = token.get("token") if isinstance(token, dict) else None
    if not tok:
        cfg = _clone_config_ctx.get()
        tok = cfg.get("token") if cfg else None
    if not tok:
        return False
    await clones_col.update_one({"token": tok}, {"$set": {field: value}})
    await reload_clone_config(tok)
    return True


async def _get_current_clone_token() -> str | None:
    cfg = _clone_config_ctx.get()
    return cfg.get("token") if cfg else None


# ─── /cloneconfig ─────────────────────────────────────────────────────────────

@app.on_message(filters.command("cloneconfig") & clone_admin_filter & filters.private)
async def cloneconfig_cmd(client: Client, message: Message):
    cfg = _clone_config_ctx.get()
    if not cfg:
        await message.reply_text(
            "ℹ️ This command only works when messaging the <b>clone bot</b> directly.",
            parse_mode=HTML,
        )
        return

    vc  = cfg.get("video_channel") or "❌ Not set"
    ig  = cfg.get("inbox_group")   or "❌ Not set"
    lg  = cfg.get("log_group")     or "❌ Not set"
    adm = cfg.get("admin_id")      or "❌ Not set"
    name = cfg.get("name", "?")

    await message.reply_text(
        f"⚙️ <b>Clone Bot Configuration</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Name        : <b>{name}</b>\n"
        f"👤 Admin ID    : <code>{adm}</code>\n\n"
        f"📺 Video Channel : <code>{vc}</code>\n"
        f"📬 Inbox Group   : <code>{ig}</code>\n"
        f"📋 Log Group     : <code>{lg}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Setup commands:</b>\n"
        f"  /setvideochannel — set video source channel\n"
        f"  /setcloneinbox   — set inbox/support group\n"
        f"  /setclonelog     — set log group",
        parse_mode=HTML,
    )


# ─── /setvideochannel ─────────────────────────────────────────────────────────

@app.on_message(filters.command("setvideochannel") & clone_admin_filter & filters.private)
async def set_video_channel_cmd(client: Client, message: Message):
    cfg = _clone_config_ctx.get()
    if not cfg:
        await message.reply_text("ℹ️ This command works only in the clone bot.", parse_mode=HTML)
        return

    args = message.command[1:]
    if not args:
        current = cfg.get("video_channel") or "Not set"
        await message.reply_text(
            f"📺 <b>Set Video Channel</b>\n\n"
            f"Current: <code>{current}</code>\n\n"
            f"Usage: <code>/setvideochannel -1001234567890</code>\n\n"
            f"Or forward any message from the channel here and I'll detect the ID automatically.",
            parse_mode=HTML,
        )
        return

    ch_id_str = args[0]
    if not ch_id_str.lstrip("-").isdigit():
        await message.reply_text("❌ Invalid channel ID.", parse_mode=HTML)
        return

    ch_id = int(ch_id_str)
    tok   = cfg.get("token")
    await clones_col.update_one({"token": tok}, {"$set": {"video_channel": ch_id}})
    from clone_manager import reload_clone_config
    await reload_clone_config(tok)

    await message.reply_text(
        f"✅ <b>Video Channel Set!</b>\n"
        f"📺 Channel ID: <code>{ch_id}</code>\n\n"
        f"Videos will now be sent from this channel.",
        parse_mode=HTML,
    )


# ─── /setcloneinbox ───────────────────────────────────────────────────────────

@app.on_message(filters.command("setcloneinbox") & clone_admin_filter)
async def set_clone_inbox_cmd(client: Client, message: Message):
    cfg = _clone_config_ctx.get()
    if not cfg:
        await message.reply_text("ℹ️ This command works only in the clone bot.", parse_mode=HTML)
        return

    tok = cfg.get("token")

    # Used in group: set that group as inbox
    if message.chat.type.name in ("GROUP", "SUPERGROUP"):
        group_id = message.chat.id
        await clones_col.update_one({"token": tok}, {"$set": {"inbox_group": group_id}})
        from clone_manager import reload_clone_config
        await reload_clone_config(tok)
        m = await message.reply_text(
            f"✅ <b>Inbox Group Set!</b>\n"
            f"📬 This group (<code>{group_id}</code>) is now the inbox for <b>{cfg.get('name','clone')}</b>.\n\n"
            f"User messages will be forwarded here.",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 15))
        return

    # Used in private: accept ID
    args = message.command[1:]
    if not args:
        current = cfg.get("inbox_group") or "Not set"
        await message.reply_text(
            f"📬 <b>Set Inbox Group</b>\n\n"
            f"Current: <code>{current}</code>\n\n"
            f"<b>Option 1:</b> Run <code>/setcloneinbox</code> inside the group\n"
            f"<b>Option 2:</b> <code>/setcloneinbox -1001234567890</code>",
            parse_mode=HTML,
        )
        return

    if not args[0].lstrip("-").isdigit():
        await message.reply_text("❌ Invalid group ID.", parse_mode=HTML)
        return

    group_id = int(args[0])
    await clones_col.update_one({"token": tok}, {"$set": {"inbox_group": group_id}})
    from clone_manager import reload_clone_config
    await reload_clone_config(tok)

    await message.reply_text(
        f"✅ <b>Inbox Group Set!</b>\n📬 Group: <code>{group_id}</code>",
        parse_mode=HTML,
    )


# ─── /setclonelog ─────────────────────────────────────────────────────────────

@app.on_message(filters.command("setclonelog") & clone_admin_filter)
async def set_clone_log_cmd(client: Client, message: Message):
    cfg = _clone_config_ctx.get()
    if not cfg:
        await message.reply_text("ℹ️ This command works only in the clone bot.", parse_mode=HTML)
        return

    tok = cfg.get("token")

    # Used in group: set that group as log
    if message.chat.type.name in ("GROUP", "SUPERGROUP"):
        group_id = message.chat.id
        await clones_col.update_one({"token": tok}, {"$set": {"log_group": group_id}})
        from clone_manager import reload_clone_config
        await reload_clone_config(tok)
        m = await message.reply_text(
            f"✅ <b>Log Group Set!</b>\n"
            f"📋 This group (<code>{group_id}</code>) will now receive logs for <b>{cfg.get('name','clone')}</b>.",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 15))
        return

    # Used in private: accept ID
    args = message.command[1:]
    if not args:
        current = cfg.get("log_group") or "Not set"
        await message.reply_text(
            f"📋 <b>Set Log Group</b>\n\n"
            f"Current: <code>{current}</code>\n\n"
            f"<b>Option 1:</b> Run <code>/setclonelog</code> inside the group\n"
            f"<b>Option 2:</b> <code>/setclonelog -1001234567890</code>",
            parse_mode=HTML,
        )
        return

    if not args[0].lstrip("-").isdigit():
        await message.reply_text("❌ Invalid group ID.", parse_mode=HTML)
        return

    group_id = int(args[0])
    await clones_col.update_one({"token": tok}, {"$set": {"log_group": group_id}})
    from clone_manager import reload_clone_config
    await reload_clone_config(tok)

    await message.reply_text(
        f"✅ <b>Log Group Set!</b>\n📋 Group: <code>{group_id}</code>",
        parse_mode=HTML,
    )


# ─── Detect forwarded message to auto-set video channel ───────────────────────

@app.on_message(
    clone_admin_filter & filters.private & filters.forwarded
    & ~filters.command(["setvideochannel", "setcloneinbox", "setclonelog", "cloneconfig"])
)
async def forward_detect_channel(client: Client, message: Message):
    cfg = _clone_config_ctx.get()
    if not cfg:
        return

    fwd_chat = message.forward_from_chat
    if not fwd_chat or fwd_chat.type.value not in ("channel",):
        return

    tok = cfg.get("token")
    ch_id = fwd_chat.id
    await clones_col.update_one({"token": tok}, {"$set": {"video_channel": ch_id}})
    from clone_manager import reload_clone_config
    await reload_clone_config(tok)

    await message.reply_text(
        f"✅ <b>Video Channel Auto-Detected!</b>\n"
        f"📺 <b>{fwd_chat.title}</b>\n"
        f"🆔 <code>{ch_id}</code>\n\n"
        f"Videos will now be sent from this channel.",
        parse_mode=HTML,
    )
