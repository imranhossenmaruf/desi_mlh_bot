import asyncio
from datetime import datetime

from pyrogram import Client, filters, StopPropagation, ContinuePropagation
from pyrogram.types import Message

from config import HTML, ADMIN_ID, ADMIN_IDS, clones_col, app
from helpers import _auto_del, log_event, get_cfg, _clone_config_ctx

def _get_cfg_from_client(client, key, fallback=None):
    """Get clone config value from client attribute (reliable) or ContextVar (fallback)."""
    cfg = getattr(client, "_clone_config", None) or _clone_config_ctx.get()
    if cfg and cfg.get(key) is not None:
        return cfg[key]
    return fallback

# ── In-memory setup wizard sessions ──────────────────────────────────────────
# Key: (token, admin_id) → {"step": "log_group"|"video_channel"|"inbox_group"}
_setup_sessions: dict[tuple, dict] = {}

SETUP_STEPS = ["log_group", "video_channel", "inbox_group"]

STEP_PROMPTS = {
    "log_group": (
        "📋 <b>Step 1 of 3 — Log Group</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "All bot events will be sent here.\n\n"
        "<b>Option A:</b> Add me to your log group as admin, then run <code>/setclonelog</code> there.\n"
        "<b>Option B:</b> Send the group ID here.\n\n"
        "Example: <code>-1001234567890</code>\n\n"
        "Type /skip to skip this step."
    ),
    "video_channel": (
        "📺 <b>Step 2 of 3 — Video Channel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Videos will be fetched from this channel.\n\n"
        "<b>Option A:</b> Forward any message from your channel here.\n"
        "<b>Option B:</b> Send the channel ID here.\n\n"
        "Example: <code>-1001234567890</code>\n\n"
        "Type /skip to skip this step."
    ),
    "inbox_group": (
        "📬 <b>Step 3 of 3 — Inbox Group</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "User messages will be forwarded to this group.\n\n"
        "<b>Option A:</b> Add me to your support group as admin, then run <code>/setcloneinbox</code> there.\n"
        "<b>Option B:</b> Send the group ID here.\n\n"
        "Example: <code>-1001234567890</code>\n\n"
        "Type /skip to skip this step."
    ),
}

STEP_LABELS = {
    "log_group":     "📋 Log Group",
    "video_channel": "📺 Video Channel",
    "inbox_group":   "📬 Inbox Group",
}


def _setup_key(token: str, admin_id: int) -> tuple:
    return (token, admin_id)


def _next_step(current: str) -> str | None:
    idx = SETUP_STEPS.index(current)
    return SETUP_STEPS[idx + 1] if idx + 1 < len(SETUP_STEPS) else None


def _get_clone_client(token: str):
    from clone_manager import get_active_clones
    return get_active_clones().get(token)


def _get_clone_cfg(client) -> dict | None:
    """Get clone config from client attribute (reliable) or ContextVar (fallback)."""
    return getattr(client, "_clone_config", None) or _clone_config_ctx.get()


def _check_clone_admin(cfg: dict, uid: int) -> bool:
    """Check if uid is allowed to configure this clone."""
    if uid in ADMIN_IDS:
        return True
    admin_id = cfg.get("admin_id")
    return admin_id is not None and uid == admin_id


async def _finish_setup(clone_client, cfg: dict, admin_id: int):
    """Send setup complete summary and log group connection message."""
    tok  = cfg.get("token")
    name = cfg.get("name", "Clone Bot")

    from clone_manager import reload_clone_config
    fresh = await reload_clone_config(tok) or cfg
    vc  = fresh.get("video_channel") or "❌ Not set"
    ig  = fresh.get("inbox_group")   or "❌ Not set"
    lg  = fresh.get("log_group")     or "❌ Not set"

    summary = (
        f"✅ <b>Setup Complete! Your bot is ready.</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>{name}</b>\n\n"
        f"📋 Log Group     : <code>{lg}</code>\n"
        f"📺 Video Channel : <code>{vc}</code>\n"
        f"📬 Inbox Group   : <code>{ig}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Your bot is now operational!\n"
        f"Use /cloneconfig to update settings anytime.\n"
        f"Use /help to see all commands."
    )
    try:
        await clone_client.send_message(admin_id, summary, parse_mode=HTML)
    except Exception as e:
        print(f"[CLONE_SETUP] finish_setup send error: {e}")

    lg_id = fresh.get("log_group")
    if lg_id:
        try:
            await clone_client.send_message(
                lg_id,
                f"🟢 <b>Bot Connected!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 <b>{name}</b> is now live.\n"
                f"📺 Video Ch : <code>{vc}</code>\n"
                f"📬 Inbox    : <code>{ig}</code>",
                parse_mode=HTML,
            )
        except Exception as e:
            print(f"[CLONE_SETUP] log_group connect msg error: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SETUP WIZARD HANDLER — group=-1, private messages from clone admin
# Uses INTERNAL check (not filter) to avoid ContextVar-in-filter timing issues
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.private & filters.incoming, group=-1)
async def clone_setup_handler(client: Client, message: Message):
    cfg = _get_clone_cfg(client)
    if not cfg:
        raise ContinuePropagation  # Not in clone context — let next handler try

    if not message.from_user:
        raise ContinuePropagation

    uid = message.from_user.id
    if not _check_clone_admin(cfg, uid):
        raise ContinuePropagation  # Not this clone's admin

    tok = cfg.get("token")
    key = _setup_key(tok, uid)
    session = _setup_sessions.get(key)
    if not session:
        raise ContinuePropagation  # No active setup wizard — let command handlers run

    step  = session.get("step")
    text  = (message.text or "").strip()

    # /skip
    if text.lower() in ("/skip", "skip"):
        nxt = _next_step(step)
        if nxt:
            _setup_sessions[key]["step"] = nxt
            await message.reply_text(f"⏩ Skipped.\n\n{STEP_PROMPTS[nxt]}", parse_mode=HTML)
        else:
            _setup_sessions.pop(key, None)
            await _finish_setup(client, cfg, uid)
        raise StopPropagation

    # /cancel
    if text.lower() in ("/cancel", "cancel"):
        _setup_sessions.pop(key, None)
        await message.reply_text(
            "🚫 Setup cancelled.\nUse /cloneconfig to check settings or /setupclone to restart.",
            parse_mode=HTML,
        )
        raise StopPropagation

    # Skip if it's a bot command (let the command handler handle it)
    if text.startswith("/"):
        return

    # Detect ID
    detected_id = None

    # Forwarded channel message → video channel step
    if message.forward_from_chat and step == "video_channel":
        fwd = message.forward_from_chat
        if hasattr(fwd, "type") and fwd.type.value == "channel":
            detected_id = fwd.id

    # Plain number
    if detected_id is None:
        clean = text.replace(" ", "")
        if clean.lstrip("-").isdigit():
            detected_id = int(clean)

    if detected_id is None:
        await message.reply_text(
            "⚠️ Please send a valid group/channel ID (e.g. <code>-1001234567890</code>)\n"
            "or type /skip to skip.",
            parse_mode=HTML,
        )
        raise StopPropagation

    # Save
    await clones_col.update_one({"token": tok}, {"$set": {step: detected_id}})
    from clone_manager import reload_clone_config
    await reload_clone_config(tok)

    await message.reply_text(
        f"✅ <b>{STEP_LABELS[step]} set!</b>  🆔 <code>{detected_id}</code>",
        parse_mode=HTML,
    )

    nxt = _next_step(step)
    await asyncio.sleep(0.4)
    if nxt:
        _setup_sessions[key]["step"] = nxt
        await client.send_message(uid, STEP_PROMPTS[nxt], parse_mode=HTML)
    else:
        _setup_sessions.pop(key, None)
        from clone_manager import reload_clone_config
        fresh = await reload_clone_config(tok)
        await _finish_setup(client, fresh or cfg, uid)

    raise StopPropagation


# ══════════════════════════════════════════════════════════════════════════════
# CLONE /help — shows when in clone context, otherwise passes through
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("help") & filters.private, group=-1)
async def clone_help_cmd(client: Client, message: Message):
    cfg = _get_clone_cfg(client)
    if not cfg:
        raise ContinuePropagation  # Not clone context — main bot handles /help

    if not message.from_user:
        raise ContinuePropagation
    if not _check_clone_admin(cfg, message.from_user.id):
        raise ContinuePropagation

    name = cfg.get("name", "Clone Bot")
    await message.reply_text(
        f"📋 <b>{name} — Admin Commands</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>⚙️ Setup & Config</b>\n"
        f"  /cloneconfig     — View current config\n"
        f"  /setupclone      — Restart setup wizard\n"
        f"  /setvideochannel — Set video channel\n"
        f"  /setcloneinbox   — Set inbox group\n"
        f"  /setclonelog     — Set log group\n\n"
        f"<b>👤 Users</b>\n"
        f"  /stats  /user {{id}}  /blockuser  /unblockuser\n"
        f"  /addpoints  /removepoints  /setlimit  /resetcount\n\n"
        f"<b>📢 Broadcast</b>\n"
        f"  /broadcast  /sbc\n\n"
        f"<b>📬 Inbox</b>\n"
        f"  /inbox  /chat {{id}}\n\n"
        f"<b>🎬 Videos</b>\n"
        f"  /syncvideos  /listvideos  /delvideo  /clearvideos\n\n"
        f"<b>🛡 Groups</b>\n"
        f"  /groups  /forcejoin\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>{name}</b>",
        parse_mode=HTML,
    )
    raise StopPropagation


# ══════════════════════════════════════════════════════════════════════════════
# /cloneconfig — view config
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("cloneconfig") & filters.private)
async def cloneconfig_cmd(client: Client, message: Message):
    cfg = _get_clone_cfg(client)
    if not cfg:
        return

    uid = message.from_user.id if message.from_user else 0
    if not _check_clone_admin(cfg, uid):
        return

    from clone_manager import reload_clone_config
    fresh = await reload_clone_config(cfg.get("token")) or cfg

    vc   = fresh.get("video_channel") or "❌ Not set"
    ig   = fresh.get("inbox_group")   or "❌ Not set"
    lg   = fresh.get("log_group")     or "❌ Not set"
    adm  = fresh.get("admin_id")      or "—"
    name = fresh.get("name", "?")

    await message.reply_text(
        f"⚙️ <b>Clone Config — {name}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 Admin         : <code>{adm}</code>\n\n"
        f"📋 Log Group     : <code>{lg}</code>\n"
        f"📺 Video Channel : <code>{vc}</code>\n"
        f"📬 Inbox Group   : <code>{ig}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"/setvideochannel  /setcloneinbox  /setclonelog\n"
        f"/setupclone — restart wizard",
        parse_mode=HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
# /setupclone — restart wizard
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("setupclone") & filters.private)
async def restart_setup_cmd(client: Client, message: Message):
    cfg = _get_clone_cfg(client)
    if not cfg:
        return

    uid = message.from_user.id if message.from_user else 0
    if not _check_clone_admin(cfg, uid):
        return

    tok = cfg.get("token")
    key = _setup_key(tok, uid)
    _setup_sessions[key] = {"step": "log_group"}

    await message.reply_text(
        f"🔄 <b>Setup Wizard Started</b>\n\n{STEP_PROMPTS['log_group']}",
        parse_mode=HTML,
    )


# ══════════════════════════════════════════════════════════════════════════════
# /setvideochannel
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("setvideochannel") & filters.private)
async def set_video_channel_cmd(client: Client, message: Message):
    cfg = _get_clone_cfg(client)
    if not cfg:
        return

    uid = message.from_user.id if message.from_user else 0
    if not _check_clone_admin(cfg, uid):
        return

    tok  = cfg.get("token")
    args = message.command[1:]

    if not args:
        cur = cfg.get("video_channel") or "Not set"
        await message.reply_text(
            f"📺 <b>Video Channel</b>\nCurrent: <code>{cur}</code>\n\n"
            f"Send: <code>/setvideochannel -1001234567890</code>\nor forward any message from the channel here.",
            parse_mode=HTML,
        )
        return

    if not args[0].lstrip("-").isdigit():
        await message.reply_text("❌ Invalid channel ID.", parse_mode=HTML)
        return

    ch_id = int(args[0])
    await clones_col.update_one({"token": tok}, {"$set": {"video_channel": ch_id}})
    from clone_manager import reload_clone_config
    await reload_clone_config(tok)

    key = _setup_key(tok, uid)
    session = _setup_sessions.get(key)
    await message.reply_text(
        f"✅ <b>Video Channel Set!</b>\n📺 <code>{ch_id}</code>", parse_mode=HTML
    )
    if session and session.get("step") == "video_channel":
        nxt = _next_step("video_channel")
        _setup_sessions[key]["step"] = nxt
        await asyncio.sleep(0.4)
        await client.send_message(uid, STEP_PROMPTS[nxt], parse_mode=HTML)


# ══════════════════════════════════════════════════════════════════════════════
# /setcloneinbox — group or private
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("setcloneinbox"))
async def set_clone_inbox_cmd(client: Client, message: Message):
    cfg = _get_clone_cfg(client)
    if not cfg:
        return

    uid = message.from_user.id if message.from_user else 0
    if not _check_clone_admin(cfg, uid):
        return

    tok = cfg.get("token")

    if message.chat.type.name in ("GROUP", "SUPERGROUP"):
        group_id = message.chat.id
        await clones_col.update_one({"token": tok}, {"$set": {"inbox_group": group_id}})
        from clone_manager import reload_clone_config
        await reload_clone_config(tok)
        m = await message.reply_text(
            f"✅ <b>Inbox Group Set!</b>\n📬 <code>{group_id}</code>", parse_mode=HTML
        )
        asyncio.create_task(_auto_del(m, 20))
        # Advance wizard
        key = _setup_key(tok, uid)
        session = _setup_sessions.get(key)
        if session and session.get("step") == "inbox_group":
            _setup_sessions.pop(key, None)
            from clone_manager import reload_clone_config
            fresh = await reload_clone_config(tok)
            await _finish_setup(client, fresh or cfg, uid)
        return

    args = message.command[1:]
    if not args:
        cur = cfg.get("inbox_group") or "Not set"
        await message.reply_text(
            f"📬 <b>Inbox Group</b>\nCurrent: <code>{cur}</code>\n\n"
            f"Run <code>/setcloneinbox</code> inside the group,\nor <code>/setcloneinbox -1001234567890</code>",
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

    key = _setup_key(tok, uid)
    session = _setup_sessions.get(key)
    await message.reply_text(
        f"✅ <b>Inbox Group Set!</b>\n📬 <code>{group_id}</code>", parse_mode=HTML
    )
    if session and session.get("step") == "inbox_group":
        _setup_sessions.pop(key, None)
        from clone_manager import reload_clone_config
        fresh = await reload_clone_config(tok)
        await _finish_setup(client, fresh or cfg, uid)


# ══════════════════════════════════════════════════════════════════════════════
# /setclonelog — group or private
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("setclonelog"))
async def set_clone_log_cmd(client: Client, message: Message):
    cfg = _get_clone_cfg(client)
    if not cfg:
        return

    uid = message.from_user.id if message.from_user else 0
    if not _check_clone_admin(cfg, uid):
        return

    tok = cfg.get("token")

    if message.chat.type.name in ("GROUP", "SUPERGROUP"):
        group_id = message.chat.id
        await clones_col.update_one({"token": tok}, {"$set": {"log_group": group_id}})
        from clone_manager import reload_clone_config
        await reload_clone_config(tok)
        m = await message.reply_text(
            f"✅ <b>Log Group Set!</b>\n📋 <code>{group_id}</code>", parse_mode=HTML
        )
        asyncio.create_task(_auto_del(m, 20))
        # Advance wizard
        key = _setup_key(tok, uid)
        session = _setup_sessions.get(key)
        if session and session.get("step") == "log_group":
            nxt = _next_step("log_group")
            _setup_sessions[key]["step"] = nxt
            await client.send_message(uid, STEP_PROMPTS[nxt], parse_mode=HTML)
        return

    args = message.command[1:]
    if not args:
        cur = cfg.get("log_group") or "Not set"
        await message.reply_text(
            f"📋 <b>Log Group</b>\nCurrent: <code>{cur}</code>\n\n"
            f"Run <code>/setclonelog</code> inside the group,\nor <code>/setclonelog -1001234567890</code>",
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

    key = _setup_key(tok, uid)
    session = _setup_sessions.get(key)
    await message.reply_text(
        f"✅ <b>Log Group Set!</b>\n📋 <code>{group_id}</code>", parse_mode=HTML
    )
    if session and session.get("step") == "log_group":
        nxt = _next_step("log_group")
        _setup_sessions[key]["step"] = nxt
        await asyncio.sleep(0.4)
        await client.send_message(uid, STEP_PROMPTS[nxt], parse_mode=HTML)


# ══════════════════════════════════════════════════════════════════════════════
# Forward detection (in-wizard: video channel auto-detect)
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.private & filters.forwarded & filters.incoming, group=-1)
async def forward_detect_channel(client: Client, message: Message):
    cfg = _get_clone_cfg(client)
    if not cfg:
        raise ContinuePropagation

    uid = message.from_user.id if message.from_user else 0
    if not _check_clone_admin(cfg, uid):
        raise ContinuePropagation

    fwd = message.forward_from_chat
    if not fwd or not hasattr(fwd, "type") or fwd.type.value != "channel":
        raise ContinuePropagation

    tok   = cfg.get("token")
    ch_id = fwd.id
    await clones_col.update_one({"token": tok}, {"$set": {"video_channel": ch_id}})
    from clone_manager import reload_clone_config
    await reload_clone_config(tok)

    await message.reply_text(
        f"✅ <b>Video Channel Detected!</b>\n"
        f"📺 <b>{fwd.title}</b>  🆔 <code>{ch_id}</code>",
        parse_mode=HTML,
    )

    key = _setup_key(tok, uid)
    session = _setup_sessions.get(key)
    if session and session.get("step") == "video_channel":
        nxt = _next_step("video_channel")
        _setup_sessions[key]["step"] = nxt
        await asyncio.sleep(0.4)
        await client.send_message(uid, STEP_PROMPTS[nxt], parse_mode=HTML)

    raise StopPropagation


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ADMIN: /addclone  /removeclone  /clones
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("addclone") & filters.user(ADMIN_IDS) & filters.private)
async def addclone_cmd(client: Client, message: Message):
    args = message.command[1:]
    if len(args) < 2:
        await message.reply_text(
            "📌 <b>Usage:</b>\n"
            "<code>/addclone {bot_token} {admin_id} [name]</code>\n\n"
            "• <b>bot_token</b> — from @BotFather\n"
            "• <b>admin_id</b>  — Telegram user ID of this bot's owner\n"
            "• <b>name</b>      — optional label\n\n"
            "Example:\n"
            "<code>/addclone 7123456789:AAH... 987654321 Karim Bot</code>",
            parse_mode=HTML,
        )
        return

    token        = args[0]
    admin_id_str = args[1]
    name         = " ".join(args[2:]) if len(args) > 2 else f"Clone {token[:10]}..."

    if ":" not in token or len(token) < 20:
        await message.reply_text("❌ Invalid bot token format.", parse_mode=HTML)
        return
    if not admin_id_str.lstrip("-").isdigit():
        await message.reply_text("❌ admin_id must be a numeric Telegram user ID.", parse_mode=HTML)
        return

    admin_id = int(admin_id_str)
    existing = await clones_col.find_one({"token": token})
    if existing and existing.get("active"):
        await message.reply_text(
            f"ℹ️ Clone <b>{existing.get('name','?')}</b> already active.", parse_mode=HTML
        )
        return

    wait = await message.reply_text("⏳ Starting clone bot...", parse_mode=HTML)

    doc = {
        "token":         token,
        "name":          name,
        "active":        True,
        "admin_id":      admin_id,
        "video_channel": None,
        "inbox_group":   None,
        "log_group":     None,
        "added_at":      datetime.utcnow(),
        "added_by":      ADMIN_ID,
    }

    from clone_manager import start_clone
    ok = await start_clone(token, name, doc=doc)

    if ok:
        await clones_col.update_one({"token": token}, {"$set": doc}, upsert=True)
        await wait.edit_text(
            f"✅ <b>Clone Bot Started!</b>\n"
            f"🤖 <b>{name}</b>  |  👤 Admin: <code>{admin_id}</code>\n\n"
            f"Setup wizard has been sent to the admin via the clone bot.",
            parse_mode=HTML,
        )
        await log_event(client,
            f"🤖 <b>Clone Bot Added</b>\n📌 {name}\n👤 Admin: <code>{admin_id}</code>"
        )

        # Start setup wizard — message sent FROM CLONE BOT (not main bot)
        clone_client = _get_clone_client(token)
        if clone_client:
            key = _setup_key(token, admin_id)
            _setup_sessions[key] = {"step": "log_group"}
            try:
                await clone_client.send_message(
                    admin_id,
                    f"👋 <b>Welcome! Your bot <i>{name}</i> is live.</b>\n\n"
                    f"Let's set it up step by step.\n\n"
                    f"{STEP_PROMPTS['log_group']}",
                    parse_mode=HTML,
                )
            except Exception as e:
                print(f"[CLONE_SETUP] Could not send wizard to admin {admin_id}: {e}")
        else:
            print(f"[CLONE_SETUP] Could not get clone client for {token[:15]}")
    else:
        await wait.edit_text(
            "❌ <b>Failed to start clone.</b>\n"
            "• Invalid or expired token\n"
            "• Bot already running elsewhere",
            parse_mode=HTML,
        )


@app.on_message(filters.command("removeclone") & filters.user(ADMIN_IDS) & filters.private)
async def removeclone_cmd(client: Client, message: Message):
    docs = await clones_col.find({"active": True}).to_list(length=100)
    if not docs:
        await message.reply_text("📭 No active clones.", parse_mode=HTML)
        return

    args = message.command[1:]
    if not args:
        lines = ["📋 <b>Active Clones:</b>\n"]
        for i, doc in enumerate(docs, 1):
            lines.append(
                f"{i}. <b>{doc.get('name','?')}</b>\n"
                f"   Token: <code>{doc['token'][:20]}...</code>"
            )
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
        f"✅ <b>Clone Removed</b>\n🤖 {doc.get('name','?')}", parse_mode=HTML
    )
    await log_event(client, f"🗑 <b>Clone Removed</b>\n📌 {doc.get('name','?')}")


@app.on_message(filters.command("clones") & filters.user(ADMIN_IDS) & filters.private)
async def clones_list_cmd(client: Client, message: Message):
    from clone_manager import get_active_clones
    docs    = await clones_col.find({"active": True}).to_list(length=100)
    running = get_active_clones()

    if not docs:
        await message.reply_text(
            "📭 No clones.\nUse <code>/addclone {token} {admin_id} [name]</code>",
            parse_mode=HTML,
        )
        return

    lines = ["🤖 <b>CLONE BOTS — DESI MLH</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    for i, doc in enumerate(docs, 1):
        tok      = doc["token"]
        name     = doc.get("name", "?")
        adm      = doc.get("admin_id", "—")
        added_at = doc.get("added_at")
        added_str = added_at.strftime("%d %b %Y") if added_at else "—"
        status   = "🟢 Running" if tok in running else "🔴 Stopped"
        vc = doc.get("video_channel") or "—"
        ig = doc.get("inbox_group")   or "—"
        lg = doc.get("log_group")     or "—"
        lines.append(
            f"{i}. {status}  <b>{name}</b>\n"
            f"   👤 Admin: <code>{adm}</code>\n"
            f"   📺 VC: <code>{vc}</code>  📬 IG: <code>{ig}</code>  📋 LG: <code>{lg}</code>\n"
            f"   📅 {added_str}"
        )
    lines.append(f"━━━━━━━━━━━━━━━━━━━━━━\n📊 {len(docs)} total | 🟢 {len(running)} running")
    await message.reply_text("\n\n".join(lines), parse_mode=HTML)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ADMIN: /refreshguard — clear group presence cache
# ══════════════════════════════════════════════════════════════════════════════

@app.on_message(filters.command("refreshguard") & filters.user(ADMIN_IDS) & filters.private)
async def refreshguard_cmd(client: Client, message: Message):
    """Clears the clone group-priority cache.
    Useful after removing the main bot from a group so clones can take over.
    """
    from clone_manager import invalidate_presence_cache
    args = message.command[1:]
    if args and args[0].lstrip("-").isdigit():
        chat_id = int(args[0])
        invalidate_presence_cache(chat_id)
        await message.reply_text(
            f"✅ <b>Cache Cleared</b>\n"
            f"Group <code>{chat_id}</code> will be re-checked on next message.\n\n"
            f"If you removed the main bot from that group, clone bots will now respond there.",
            parse_mode=HTML,
        )
    else:
        invalidate_presence_cache()
        await message.reply_text(
            "✅ <b>Full Cache Cleared</b>\n"
            "All groups will be re-checked on the next message.\n\n"
            "Clone bots will re-detect where the main bot is/isn't present.",
            parse_mode=HTML,
        )
