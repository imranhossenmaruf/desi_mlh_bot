import asyncio
from datetime import datetime

from pyrogram import Client

from config import HTML, ADMIN_ID, scheduled_col, del_queue_col
from helpers import log_event, do_broadcast, bot_api


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


async def video_del_loop():
    """Background task: runs every 60s, deletes queued videos whose time has come."""
    print("[VIDEO_DEL] Loop started.")
    while True:
        try:
            now = datetime.utcnow()
            due = await del_queue_col.find({"delete_at": {"$lte": now}}).to_list(length=100)
            for doc in due:
                chat_id = doc["chat_id"]
                msg_id  = doc["msg_id"]
                token   = doc.get("token") or None
                try:
                    r = await bot_api(
                        "deleteMessage",
                        {"chat_id": chat_id, "message_id": msg_id},
                        token=token,
                    )
                    if r.get("ok"):
                        print(f"[VIDEO_DEL] ✅ Deleted msg={msg_id} user={chat_id}")
                    else:
                        print(f"[VIDEO_DEL] ⚠️ msg={msg_id} err={r.get('description','?')}")
                except Exception as de:
                    print(f"[VIDEO_DEL] delete error msg={msg_id}: {de}")
                finally:
                    await del_queue_col.delete_one({"_id": doc["_id"]})
        except Exception as e:
            print(f"[VIDEO_DEL] Loop error: {e}")
        await asyncio.sleep(60)


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
