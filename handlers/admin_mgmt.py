import asyncio
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import Message

from config import HTML, ADMIN_ID, admins_col, app
from helpers import _auto_del, log_event


# ─── Helper: check if a user is super-admin or sub-admin ─────────────────────

async def is_any_admin(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    doc = await admins_col.find_one({"user_id": user_id, "active": True})
    return doc is not None


async def _get_all_admins() -> list[dict]:
    return await admins_col.find({"active": True}).to_list(length=50)


# ─── /addadmin ────────────────────────────────────────────────────────────────

@app.on_message(filters.command("addadmin") & filters.user(ADMIN_ID) & filters.private)
async def addadmin_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args or not args[0].lstrip("@").isdigit():
        await message.reply_text(
            "📌 <b>Usage:</b> <code>/addadmin {user_id} [label]</code>\n\n"
            "Example:\n"
            "<code>/addadmin 123456789 Karim</code>",
            parse_mode=HTML,
        )
        return

    new_uid = int(args[0].lstrip("@"))
    label   = " ".join(args[1:]) if len(args) > 1 else f"Admin {new_uid}"

    if new_uid == ADMIN_ID:
        await message.reply_text("⚠️ That's already the super admin.", parse_mode=HTML)
        return

    existing = await admins_col.find_one({"user_id": new_uid})
    if existing and existing.get("active"):
        await message.reply_text(
            f"ℹ️ <code>{new_uid}</code> is already an admin.",
            parse_mode=HTML,
        )
        return

    await admins_col.update_one(
        {"user_id": new_uid},
        {"$set": {
            "user_id":    new_uid,
            "label":      label,
            "active":     True,
            "added_at":   datetime.utcnow(),
            "added_by":   ADMIN_ID,
        }},
        upsert=True,
    )

    await message.reply_text(
        f"✅ <b>Admin Added</b>\n"
        f"👤 {label}\n"
        f"🆔 <code>{new_uid}</code>",
        parse_mode=HTML,
    )

    await log_event(client,
        f"👑 <b>New Admin Added</b>\n"
        f"👤 Label : {label}\n"
        f"🆔 ID    : <code>{new_uid}</code>"
    )

    # Notify the new admin
    try:
        await client.send_message(
            new_uid,
            "✅ <b>You have been added as an admin of DESI MLH Bot!</b>\n\n"
            "You can now use admin commands in this bot's private chat.\n"
            "Type /help to see available commands.",
            parse_mode=HTML,
        )
    except Exception:
        pass


# ─── /removeadmin ─────────────────────────────────────────────────────────────

@app.on_message(filters.command("removeadmin") & filters.user(ADMIN_ID) & filters.private)
async def removeadmin_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args or not args[0].lstrip("@").isdigit():
        await message.reply_text(
            "📌 <b>Usage:</b> <code>/removeadmin {user_id}</code>",
            parse_mode=HTML,
        )
        return

    uid = int(args[0].lstrip("@"))
    res = await admins_col.update_one(
        {"user_id": uid},
        {"$set": {"active": False}},
    )

    if res.modified_count:
        await message.reply_text(
            f"🗑 <b>Admin Removed</b>\n🆔 <code>{uid}</code>",
            parse_mode=HTML,
        )
        await log_event(client,
            f"🗑 <b>Admin Removed</b>\n🆔 ID: <code>{uid}</code>"
        )
        try:
            await client.send_message(
                uid,
                "⚠️ <b>Your admin access to DESI MLH Bot has been removed.</b>",
                parse_mode=HTML,
            )
        except Exception:
            pass
    else:
        await message.reply_text(
            f"❌ <code>{uid}</code> was not found as an admin.",
            parse_mode=HTML,
        )


# ─── /admins ─────────────────────────────────────────────────────────────────

@app.on_message(filters.command("admins") & filters.user(ADMIN_ID) & filters.private)
async def admins_list_cmd(client: Client, message: Message):
    docs = await _get_all_admins()

    lines = [
        "👑 <b>ADMIN LIST — DESI MLH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔴 <b>Super Admin</b>\n"
        f"   🆔 <code>{ADMIN_ID}</code>  (You)\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]

    if docs:
        lines.append("🟡 <b>Sub Admins</b>")
        for i, doc in enumerate(docs, 1):
            added = doc.get("added_at")
            added_str = added.strftime("%d %b %Y") if added else "—"
            lines.append(
                f"  {i}. 👤 {doc.get('label','?')}  "
                f"🆔 <code>{doc['user_id']}</code>\n"
                f"       📅 Added: {added_str}"
            )
    else:
        lines.append("📭 No sub admins added yet.\n"
                     "Use <code>/addadmin {user_id} {label}</code> to add one.")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━\n🤖 DESI MLH SYSTEM")
    await message.reply_text("\n".join(lines), parse_mode=HTML)
