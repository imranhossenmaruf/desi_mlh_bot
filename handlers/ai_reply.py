"""
AI Auto-Reply System  +  Auto Language Detection
─────────────────────────────────────────────────
Triggers when:
  • Bot is @mentioned in a group
  • Someone replies to the bot's own message in a group

Language: auto-detected (Bengali ↔ English via Unicode range check).
Backend:  Google Gemini 1.5 Flash (free-tier REST API).
Requires: GEMINI_API_KEY environment secret.
Scope:    Only in groups where this bot is admin.
"""
import asyncio
import os
import re

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message

from config import app

# ── Config ────────────────────────────────────────────────────────────────────
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)
_BN_RE = re.compile(r"[\u0980-\u09FF]")   # Bengali Unicode block


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_lang(text: str) -> str:
    """Return 'bn' if Bengali characters found, else 'en'."""
    return "bn" if _BN_RE.search(text) else "en"


async def _gemini(user_text: str, lang: str) -> str:
    """Call Gemini Flash and return reply text (empty string on failure)."""
    if not GEMINI_KEY:
        return ""

    sys_prompt = (
        "তুমি একটি Telegram গ্রুপের বন্ধুত্বপূর্ণ সহকারী বট। "
        "সর্বোচ্চ ২-৩ বাক্যে সংক্ষেপে উত্তর দাও। "
        "Markdown বা HTML ব্যবহার করবে না।"
        if lang == "bn"
        else
        "You are a friendly assistant bot in a Telegram group. "
        "Reply concisely in 2-3 sentences. No markdown or HTML."
    )

    payload = {
        "contents": [
            {"parts": [{"text": f"{sys_prompt}\n\nUser: {user_text}"}]}
        ],
        "generationConfig": {
            "maxOutputTokens": 300,
            "temperature":     0.75,
        },
    }
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                f"{_GEMINI_URL}?key={GEMINI_KEY}",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return (
                        data["candidates"][0]["content"]["parts"][0]["text"]
                        .strip()
                    )
                err = await resp.text()
                print(f"[AI] Gemini {resp.status}: {err[:200]}")
    except asyncio.TimeoutError:
        print("[AI] Gemini timeout")
    except Exception as exc:
        print(f"[AI] Gemini error: {exc}")
    return ""


async def _bot_is_admin(client: Client, chat_id: int) -> bool:
    try:
        me = await client.get_me()
        m  = await client.get_chat_member(chat_id, me.id)
        return str(m.status) in (
            "ChatMemberStatus.ADMINISTRATOR",
            "ChatMemberStatus.OWNER",
        )
    except Exception:
        return False


def _clean_text(text: str, bot_username: str) -> str:
    """Strip bot @mention from the text."""
    return re.sub(
        rf"@{re.escape(bot_username)}\s*", "", text, flags=re.IGNORECASE
    ).strip()


async def _send_ai_reply(client: Client, message: Message, text: str):
    """Detect language, call Gemini, reply."""
    if not text:
        text = "Hello"
    lang  = _detect_lang(text)
    await client.send_chat_action(message.chat.id, "typing")
    reply = await _gemini(text, lang)
    if not reply:
        if not GEMINI_KEY:
            reply = (
                "⚙️ AI সেটআপ হয়নি। Admin GEMINI_API_KEY সেট করুন।"
                if lang == "bn"
                else "⚙️ AI not configured. Admin must set GEMINI_API_KEY."
            )
        else:
            reply = (
                "দুঃখিত, এই মুহূর্তে উত্তর দিতে পারছি না।"
                if lang == "bn"
                else "Sorry, I couldn't generate a response right now."
            )
    try:
        await message.reply_text(reply)
    except Exception as exc:
        print(f"[AI] Reply failed: {exc}")


# ── Handler 1: @mention ───────────────────────────────────────────────────────

@app.on_message(filters.group & filters.mentioned, group=6)
async def ai_on_mention(client: Client, message: Message):
    if not message.from_user:
        return
    if not await _bot_is_admin(client, message.chat.id):
        return

    me   = await client.get_me()
    raw  = (message.text or message.caption or "").strip()
    text = _clean_text(raw, me.username or "")
    await _send_ai_reply(client, message, text)


# ── Handler 2: Reply to bot message ──────────────────────────────────────────

@app.on_message(filters.group & filters.reply, group=6)
async def ai_on_reply_to_bot(client: Client, message: Message):
    if not message.from_user:
        return
    rto = message.reply_to_message
    if not rto or not rto.from_user:
        return
    me = await client.get_me()
    if rto.from_user.id != me.id:
        return
    if not await _bot_is_admin(client, message.chat.id):
        return

    text = (message.text or message.caption or "").strip()
    await _send_ai_reply(client, message, text)
