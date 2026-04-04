import asyncio

from pyrogram import idle as pyrogram_idle

from config import app, VIDEO_CHANNEL
from helpers import get_log_channel
import handlers  # noqa: F401 — registers all handlers via @app decorators
from tasks import schedule_loop
from handlers.nightmode import nightmode_loop
from handlers.stars_payment import stars_payment_loop


async def main():
    print("Bot is starting...")
    await app.start()

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
    print("[TASKS] Background loops started (schedule + nightmode + stars_payment).")

    await pyrogram_idle()
    await app.stop()


app.run(main())
