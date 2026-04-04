import asyncio
from datetime import datetime, timedelta

from pyrogram import Client
from pyrogram.types import ChatPermissions

from config import (
    HTML, ADMIN_ID,
    nightmode_col, scheduled_col,
)
from helpers import log_event, do_broadcast

_NIGHT_RESTRICTED = ChatPermissions(
    can_send_messages        = False,
    can_send_media_messages  = False,
    can_send_polls           = False,
    can_add_web_page_previews= False,
    can_change_info          = False,
    can_invite_users         = False,
    can_pin_messages         = False,
)

_NIGHT_OPEN = ChatPermissions(
    can_send_messages        = True,
    can_send_media_messages  = True,
    can_send_polls           = True,
    can_add_web_page_previews= True,
    can_change_info          = False,
    can_invite_users         = True,
    can_pin_messages         = False,
)


async def nightmode_loop(client: Client):
    """Background task: enforces night mode every 60 seconds."""
    print("[NIGHTMODE] Loop started.")
    while True:
        try:
            await asyncio.sleep(60)
            now_utc = datetime.utcnow()
            now_min = now_utc.hour * 60 + now_utc.minute

            docs = await nightmode_col.find({"enabled": True}).to_list(length=100)
            for doc in docs:
                chat_id         = doc["chat_id"]
                sh              = doc["start_h_utc"] * 60 + doc["start_m_utc"]
                eh              = doc["end_h_utc"]   * 60 + doc["end_m_utc"]
                currently_night = doc.get("is_night", False)

                if sh < eh:
                    in_night = sh <= now_min < eh
                else:
                    in_night = now_min >= sh or now_min < eh

                if in_night and not currently_night:
                    try:
                        await client.set_chat_permissions(chat_id, _NIGHT_RESTRICTED)
                        await nightmode_col.update_one(
                            {"chat_id": chat_id}, {"$set": {"is_night": True}}
                        )
                        await client.send_message(
                            chat_id,
                            f"🌙 <b>Night Mode ON</b>\n"
                            f"Group is now restricted until {doc['end_h']:02d}:{doc['end_m']:02d} BST.\n"
                            "Only admins can post. Good night! 😴",
                            parse_mode=HTML,
                        )
                        print(f"[NIGHTMODE] Activated for chat={chat_id}")
                    except Exception as e:
                        print(f"[NIGHTMODE] Error activating {chat_id}: {e}")

                elif not in_night and currently_night:
                    try:
                        await client.set_chat_permissions(chat_id, _NIGHT_OPEN)
                        await nightmode_col.update_one(
                            {"chat_id": chat_id}, {"$set": {"is_night": False}}
                        )
                        await client.send_message(
                            chat_id,
                            "☀️ <b>Night Mode OFF</b>\nGroup is now open. Good morning! 🌅",
                            parse_mode=HTML,
                        )
                        print(f"[NIGHTMODE] Deactivated for chat={chat_id}")
                    except Exception as e:
                        print(f"[NIGHTMODE] Error deactivating {chat_id}: {e}")
        except Exception as e:
            print(f"[NIGHTMODE] Loop error: {e}")


async def _run_scheduled(client: Client, session: dict, status_msg, doc_id, label: str):
    try:
        session["entities"] = []
        await do_broadcast(client, session, status_msg)
        await log_event(client,
            f"⏰ <b>Scheduled Broadcast Fired</b>\n"
            f"📅 Label: <b>{label}</b>\n"
            f"🆔 ID: <code>{doc_id}</code>"
        )
    except Exception as e:
        print(f"[SCHEDULE] Fire error id={doc_id}: {e}")
    finally:
        await scheduled_col.delete_one({"_id": doc_id})
        print(f"[SCHEDULE] Cleaned up doc id={doc_id}")


async def schedule_loop(client: Client):
    """Background task: checks every 60s for scheduled broadcasts that are due."""
    print("[SCHEDULE] Loop started.")
    while True:
        try:
            now = datetime.utcnow()
            due = await scheduled_col.find({"send_at": {"$lte": now}}).to_list(length=50)
            for doc in due:
                doc_id  = doc["_id"]
                session = doc.get("session", {})
                label   = doc.get("label", "?")
                print(f"[SCHEDULE] Firing scheduled broadcast id={doc_id} label={label}")
                status_msg = await client.send_message(
                    ADMIN_ID,
                    f"📡 <b>Scheduled Broadcast Starting</b>\n"
                    f"⏰ Scheduled: {label}\n"
                    f"👥 Sending now...",
                    parse_mode=HTML,
                )
                asyncio.create_task(_run_scheduled(client, session, status_msg, doc_id, label))
        except Exception as e:
            print(f"[SCHEDULE] Loop error: {e}")
        await asyncio.sleep(60)
