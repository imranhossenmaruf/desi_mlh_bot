"""
cmd_control.py — Command ON/OFF Control System
Admin Control Group থেকে যেকোনো command চালু বা বন্ধ করা যাবে।

Commands (Control Group only):
  /cmdon  <cmd>              — globally enable a command
  /cmdoff <cmd>              — globally disable a command
  /gcmdon  <cmd> <group_id> — enable for a specific group
  /gcmdoff <cmd> <group_id> — disable for a specific group
  /setlang <group_id> <en|bn|ar> — set group language
  /cmdlist                   — show all overrides

Language:
  /setlang <group_id> en|bn|ar
"""

import asyncio
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, ADMIN_IDS, app, cmd_control_col, lang_settings_col
from helpers import admin_filter, _auto_del
from strings import SUPPORTED_LANGS

# ── In-memory caches to reduce DB calls ───────────────────────────────────────
# _cmd_cache[key] = True (enabled) | False (disabled)
# key format: "global:<cmd>" or "<chat_id>:<cmd>"
_cmd_cache: dict[str, bool] = {}

# _lang_cache[chat_id] = "en" | "bn" | "ar"
_lang_cache: dict[int, str] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def is_cmd_enabled(cmd: str, chat_id: int | None = None) -> bool:
    """
    Check if a command is enabled.
    Priority: group-specific override > global override > True (default enabled).
    """
    cmd = cmd.lstrip("/").lower()

    # 1. Group-specific check
    if chat_id is not None:
        gkey = f"{chat_id}:{cmd}"
        if gkey in _cmd_cache:
            return _cmd_cache[gkey]
        doc = await cmd_control_col.find_one({"scope": "group", "chat_id": chat_id, "cmd": cmd})
        if doc:
            _cmd_cache[gkey] = doc.get("enabled", True)
            return _cmd_cache[gkey]

    # 2. Global check
    glokey = f"global:{cmd}"
    if glokey in _cmd_cache:
        return _cmd_cache[glokey]
    doc = await cmd_control_col.find_one({"scope": "global", "cmd": cmd})
    if doc:
        _cmd_cache[glokey] = doc.get("enabled", True)
        return _cmd_cache[glokey]

    return True  # default: all commands enabled


async def get_group_lang(chat_id: int) -> str:
    """Return stored language for a group (default 'en')."""
    if chat_id in _lang_cache:
        return _lang_cache[chat_id]
    doc = await lang_settings_col.find_one({"chat_id": chat_id})
    lang = (doc or {}).get("lang", "en")
    _lang_cache[chat_id] = lang
    return lang


async def set_group_lang(chat_id: int, lang: str):
    """Persist and cache group language."""
    lang = lang.lower()
    if lang not in SUPPORTED_LANGS:
        lang = "en"
    _lang_cache[chat_id] = lang
    await lang_settings_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"chat_id": chat_id, "lang": lang, "updated_at": datetime.utcnow()}},
        upsert=True,
    )


def _invalidate_cmd_cache(cmd: str, chat_id: int | None = None):
    if chat_id:
        _cmd_cache.pop(f"{chat_id}:{cmd}", None)
    _cmd_cache.pop(f"global:{cmd}", None)


# ── /cmdon — globally enable ──────────────────────────────────────────────────

@app.on_message(filters.command("cmdon") & admin_filter)
async def cmdon_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        m = await message.reply_text("⚠️ Usage: <code>/cmdon &lt;command&gt;</code>", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 10))
        return
    cmd = args[0].lstrip("/").lower()
    _invalidate_cmd_cache(cmd)
    await cmd_control_col.update_one(
        {"scope": "global", "cmd": cmd},
        {"$set": {"enabled": True, "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    m = await message.reply_text(f"✅ <code>/{cmd}</code> globally <b>ENABLED</b>.", parse_mode=HTML)
    asyncio.create_task(_auto_del(m, 10))


# ── /cmdoff — globally disable ────────────────────────────────────────────────

@app.on_message(filters.command("cmdoff") & admin_filter)
async def cmdoff_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args:
        m = await message.reply_text("⚠️ Usage: <code>/cmdoff &lt;command&gt;</code>", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 10))
        return
    cmd = args[0].lstrip("/").lower()
    _invalidate_cmd_cache(cmd)
    await cmd_control_col.update_one(
        {"scope": "global", "cmd": cmd},
        {"$set": {"enabled": False, "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    m = await message.reply_text(f"🚫 <code>/{cmd}</code> globally <b>DISABLED</b>.", parse_mode=HTML)
    asyncio.create_task(_auto_del(m, 10))


# ── /gcmdon — enable for specific group ───────────────────────────────────────

@app.on_message(filters.command("gcmdon") & admin_filter)
async def gcmdon_cmd(client: Client, message: Message):
    args = message.command[1:]
    if len(args) < 2:
        m = await message.reply_text(
            "⚠️ Usage: <code>/gcmdon &lt;command&gt; &lt;group_id&gt;</code>", parse_mode=HTML
        )
        asyncio.create_task(_auto_del(m, 10))
        return
    cmd = args[0].lstrip("/").lower()
    try:
        chat_id = int(args[1])
    except ValueError:
        m = await message.reply_text("❌ Invalid group ID.", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 10))
        return
    _invalidate_cmd_cache(cmd, chat_id)
    await cmd_control_col.update_one(
        {"scope": "group", "chat_id": chat_id, "cmd": cmd},
        {"$set": {"enabled": True, "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    m = await message.reply_text(
        f"✅ <code>/{cmd}</code> ENABLED for group <code>{chat_id}</code>.", parse_mode=HTML
    )
    asyncio.create_task(_auto_del(m, 10))


# ── /gcmdoff — disable for specific group ────────────────────────────────────

@app.on_message(filters.command("gcmdoff") & admin_filter)
async def gcmdoff_cmd(client: Client, message: Message):
    args = message.command[1:]
    if len(args) < 2:
        m = await message.reply_text(
            "⚠️ Usage: <code>/gcmdoff &lt;command&gt; &lt;group_id&gt;</code>", parse_mode=HTML
        )
        asyncio.create_task(_auto_del(m, 10))
        return
    cmd = args[0].lstrip("/").lower()
    try:
        chat_id = int(args[1])
    except ValueError:
        m = await message.reply_text("❌ Invalid group ID.", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 10))
        return
    _invalidate_cmd_cache(cmd, chat_id)
    await cmd_control_col.update_one(
        {"scope": "group", "chat_id": chat_id, "cmd": cmd},
        {"$set": {"enabled": False, "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    m = await message.reply_text(
        f"🚫 <code>/{cmd}</code> DISABLED for group <code>{chat_id}</code>.", parse_mode=HTML
    )
    asyncio.create_task(_auto_del(m, 10))


# ── /setlang — set group language ────────────────────────────────────────────

@app.on_message(filters.command("setlang") & admin_filter)
async def setlang_cmd(client: Client, message: Message):
    args = message.command[1:]
    if len(args) < 2:
        m = await message.reply_text(
            "⚠️ Usage: <code>/setlang &lt;group_id&gt; &lt;en|bn|ar&gt;</code>\n\n"
            "Supported: <b>en</b> (English), <b>bn</b> (Bengali), <b>ar</b> (Arabic)",
            parse_mode=HTML,
        )
        asyncio.create_task(_auto_del(m, 15))
        return
    try:
        chat_id = int(args[0])
    except ValueError:
        m = await message.reply_text("❌ Invalid group ID.", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 10))
        return
    lang = args[1].lower()
    if lang not in SUPPORTED_LANGS:
        m = await message.reply_text(
            f"❌ Invalid language. Supported: <b>en</b>, <b>bn</b>, <b>ar</b>", parse_mode=HTML
        )
        asyncio.create_task(_auto_del(m, 10))
        return
    await set_group_lang(chat_id, lang)
    lang_names = {"en": "English 🇬🇧", "bn": "Bengali 🇧🇩", "ar": "Arabic 🇸🇦"}
    m = await message.reply_text(
        f"✅ Language for <code>{chat_id}</code> set to <b>{lang_names[lang]}</b>.",
        parse_mode=HTML,
    )
    asyncio.create_task(_auto_del(m, 10))


# ── /cmdlist — show all overrides ────────────────────────────────────────────

@app.on_message(filters.command("cmdlist") & admin_filter)
async def cmdlist_cmd(client: Client, message: Message):
    docs = await cmd_control_col.find({}).to_list(length=200)
    if not docs:
        m = await message.reply_text("📭 No command overrides set.", parse_mode=HTML)
        asyncio.create_task(_auto_del(m, 10))
        return

    lines = ["<b>🔧 Command Control Overrides</b>\n━━━━━━━━━━━━━━━━━━━━━━"]
    for doc in docs:
        scope = doc.get("scope", "?")
        cmd   = doc.get("cmd", "?")
        state = "✅ ON" if doc.get("enabled", True) else "🚫 OFF"
        cid   = f" | Group: <code>{doc.get('chat_id')}</code>" if scope == "group" else " | Global"
        lines.append(f"  /{cmd} — {state}{cid}")

    m = await message.reply_text("\n".join(lines), parse_mode=HTML)
    asyncio.create_task(_auto_del(m, 30))
