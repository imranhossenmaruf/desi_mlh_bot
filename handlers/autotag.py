"""
Auto Tag System
────────────────
/tagall [message]  — mention every fetchable non-bot group member
/tag @u1 @u2 ...  — quick-ping specific users with a message

Works only in groups where the invoking user is an admin.
Note: Telegram limits get_chat_members to admins + recently-active members
in large supergroups (>200). Smaller groups return all members.
"""
import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, app
from helpers import _is_admin_msg, _auto_del


@app.on_message(filters.command("tagall") & filters.group)
async def tagall_cmd(client: Client, message: Message):
    if not await _is_admin_msg(client, message):
        return

    caption = (
        " ".join(message.command[1:]) if len(message.command) > 1 else "📢 Attention!"
    )

    status = await message.reply_text("⏳ Fetching members…")

    members = []
    try:
        async for mem in client.get_chat_members(message.chat.id):
            if mem.user and not mem.user.is_bot and not mem.user.is_deleted:
                members.append(mem.user)
    except Exception as exc:
        await status.edit_text(
            f"❌ Cannot fetch members: <code>{exc}</code>", parse_mode=HTML
        )
        asyncio.create_task(_auto_del(status, 20))
        return

    if not members:
        await status.edit_text("⚠️ No members found to tag.")
        asyncio.create_task(_auto_del(status, 15))
        return

    await status.delete()

    # Mention in batches of 5 per message to stay within Telegram limits
    BATCH = 5
    header_sent = False
    for i in range(0, len(members), BATCH):
        batch    = members[i : i + BATCH]
        mentions = "  ".join(
            f'<a href="tg://user?id={u.id}">{u.first_name}</a>'
            for u in batch
        )
        body = (
            f"📢 <b>{caption}</b>\n\n{mentions}"
            if not header_sent
            else mentions
        )
        header_sent = True
        try:
            await client.send_message(message.chat.id, body, parse_mode=HTML)
        except Exception as exc:
            print(f"[TAGALL] batch {i // BATCH + 1} failed: {exc}")
        await asyncio.sleep(1.2)   # gentle rate-limit

    try:
        await message.delete()
    except Exception:
        pass


@app.on_message(filters.command("tag") & filters.group)
async def tag_cmd(client: Client, message: Message):
    """Quick-tag: /tag @user1 @user2 … or reply to tag that user."""
    if not await _is_admin_msg(client, message):
        return

    targets = []

    # Reply target
    if message.reply_to_message and message.reply_to_message.from_user:
        u = message.reply_to_message.from_user
        targets.append(f'<a href="tg://user?id={u.id}">{u.first_name}</a>')

    # Mentioned entities in the command text
    for ent in (message.entities or []):
        if ent.type.name in ("MENTION", "TEXT_MENTION"):
            if ent.user:
                u = ent.user
                targets.append(
                    f'<a href="tg://user?id={u.id}">{u.first_name}</a>'
                )
            else:
                # @username mention
                offset = ent.offset
                length = ent.length
                targets.append((message.text or "")[offset : offset + length])

    if not targets:
        m = await message.reply_text("❌ Reply to a user or mention someone: /tag @user")
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
