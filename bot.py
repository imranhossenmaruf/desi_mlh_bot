import asyncio

from pyrogram import filters, idle as pyrogram_idle
from pyrogram.types import Message

from config import app, VIDEO_CHANNEL
from helpers import get_log_channel
import handlers  # noqa: F401 — registers all handlers via @app decorators
from tasks import schedule_loop, video_del_loop
from handlers.nightmode import nightmode_loop
from handlers.stars_payment import stars_payment_loop
from clone_manager import start_all_clones, main_bot_mark_active_in


# ── Group presence tracker (group=-95) ────────────────────────────────────────
# Every time the main bot processes a group message, record that chat_id in the
# in-process set so clone bots immediately know the main bot is there.
# New groups are persisted to MongoDB so they survive restarts.
from clone_manager import _main_bot_groups   # noqa: E402  (used below)

@app.on_message(filters.group, group=-95)
async def _main_bot_group_tracker(client, message: Message):
    if not message.chat:
        return
    cid = message.chat.id
    is_new = cid not in _main_bot_groups
    main_bot_mark_active_in(cid)
    if is_new:
        # Persist so next restart can pre-load without API calls
        try:
            from config import db
            await db["known_groups"].update_one(
                {"chat_id": cid},
                {"$set": {"chat_id": cid, "main_bot": True}},
                upsert=True,
            )
        except Exception:
            pass


async def _preload_main_bot_groups():
    """Pre-populate _main_bot_groups from MongoDB (known_groups collection).
    Falls back silently — the per-message tracker + API fallback cover any gaps.
    """
    from config import db
    count = 0
    try:
        col = db["known_groups"]
        async for doc in col.find({"main_bot": True}, {"chat_id": 1}):
            cid = doc.get("chat_id")
            if cid:
                main_bot_mark_active_in(int(cid))
                count += 1
        if count:
            print(f"[CLONE_GUARD] Pre-loaded {count} known group(s) from DB.")
    except Exception as e:
        print(f"[CLONE_GUARD] WARNING: Could not pre-load groups: {e}")


async def main():
    print("Bot is starting...")
    await app.start()

    # Cache main bot's own user ID (needed for clone priority guard)
    import config as _cfg
    try:
        _me = await app.get_me()
        _cfg.MAIN_BOT_ID = _me.id
        print(f"[STARTUP] Main bot ID cached: {_cfg.MAIN_BOT_ID}")
    except Exception as e:
        print(f"[STARTUP] WARNING: Could not get main bot ID: {e}")

    # Cache important peer IDs so "Peer id invalid" never happens
    try:
        await app.get_chat(VIDEO_CHANNEL)
        print(f"[STARTUP] Video channel cached OK ({VIDEO_CHANNEL})")
    except Exception as e:
        print(f"[STARTUP] WARNING: Cannot access video channel {VIDEO_CHANNEL}: {e}")

    try:
        log_cid = await get_log_channel()
        if log_cid:
            await app.get_chat(log_cid)
            print(f"[STARTUP] Log channel cached OK ({log_cid})")
    except Exception as e:
        print(f"[STARTUP] WARNING: Cannot cache log channel: {e}")

    loop = asyncio.get_event_loop()
    loop.create_task(schedule_loop(app))
    loop.create_task(nightmode_loop(app))
    loop.create_task(stars_payment_loop())
    loop.create_task(video_del_loop())
    print("[TASKS] Background loops started (schedule + nightmode + stars_payment + video_del).")

    await start_all_clones()
    print("[CLONE] Clone startup complete.")

    # Pre-load groups where main bot is present (silences clone bots immediately)
    asyncio.get_event_loop().create_task(_preload_main_bot_groups())

    await pyrogram_idle()
    await app.stop()


app.run(main())
