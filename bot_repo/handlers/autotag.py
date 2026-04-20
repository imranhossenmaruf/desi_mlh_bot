"""
Quick Tag Commands
──────────────────
/tag @u1 @u2 ...  — quick-ping specific users with a message
/taggroup is the full group tagging system (see tagger.py)

Note: /tagall is handled by tagger.py to avoid duplicate registration.
"""
import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, app
from helpers import _is_admin_msg, _auto_del


@app.on_message(filters.command("tag") & filters.group)
async def tag_cmd(client: Client, message: Message):
    """Quick-tag: /tag @user1 @user2 … or reply to tag that user."""
    if not await _is_admin_msg(client, message):
        return

    targets = []

    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        targets.append(f'<a href="tg://user?id={u.id}">{u.first_name}</a>')

    for ent in (message.entities or []):
        if ent.type.name in ("MENTION", "TEXT_MENTION"):
            if ent.user:
                u = ent.user
                targets.append(
                    f'<a href="tg://user?id={u.id}">{u.first_name}</a>'
                )
            else:
                offset = ent.offset
                length = ent.length
                targets.append((message.text or "")[offset : offset + length])

    if not targets:
        m = await message.reply_text(
            "❌ Reply to a user or mention someone: /tag @user"
        )
        asyncio.create_task(_auto_del(m, 15))
        return

    caption_words = [
        w for w in message.command[1:]
        if not w.startswith("@")
    ]
    caption = " ".join(caption_words) if caption_words else "👋 Hey!"

    try:
        await client.send_message(
            message.chat.id,
            f"📌 {caption}\n\n" + "  ".join(targets),
            parse_mode=HTML,
        )
        await message.delete()
    except Exception as exc:
        print(f"[TAG] Failed: {exc}")
