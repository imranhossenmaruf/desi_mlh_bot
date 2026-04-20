import asyncio

from pyrogram import filters, idle as pyrogram_idle
from pyrogram.types import Message

from config import app, VIDEO_CHANNEL, ADMIN_IDS
from helpers import get_log_channel, send_to_monitor
import handlers  # noqa: F401 — registers all handlers via @app decorators
from tasks import schedule_loop, video_del_loop
from handlers.nightmode import nightmode_loop
from handlers.stars_payment import stars_payment_loop
from handlers.daily_report import daily_report_loop
from clone_manager import start_all_clones, main_bot_mark_active_in
from handlers.admin import load_package_overrides


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


async def _startup_health_check():
    """Run after bot is fully started.
    সব ঠিক থাকলে কোনো মেসেজ পাঠাবে না।
    কিছু missing থাকলে admins + monitor group-এ পাঠাবে এবং ২০ সেকেন্ড পর মুছবে।
    """
    from datetime import datetime as _dt
    from handlers.control_group import get_monitor_group, _run_syscheck
    from config import HTML as _HTML

    await asyncio.sleep(5)  # Let everything settle first

    try:
        report = await _run_syscheck(app)
    except Exception as e:
        report = f"⚠️ syscheck failed: {e}"
        print(f"[STARTUP_HC] syscheck error: {e}")
        return

    # সব ঠিক থাকলে পাঠাবে না
    has_issues = ("❌" in report or "⚠️" in report)
    if not has_issues:
        print("[STARTUP_HC] All systems OK — no alert sent.")
        return

    now_str = _dt.utcnow().strftime("%d %b %Y %H:%M UTC")
    msg = (
        f"⚠️ <b>Bot Startup — মিসিং কনফিগ পাওয়া গেছে</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕛 {now_str}\n\n"
        f"{report}\n\n"
        f"<i>(এই মেসেজটি ২০ সেকেন্ড পরে মুছে যাবে)</i>"
    )

    async def _send_and_del(chat_id):
        try:
            sent = await app.send_message(chat_id, msg, parse_mode=_HTML)
            await asyncio.sleep(20)
            try:
                await sent.delete()
            except Exception:
                pass
        except Exception as e:
            print(f"[STARTUP_HC] Send to {chat_id} failed: {e}")

    tasks = [asyncio.create_task(_send_and_del(aid)) for aid in ADMIN_IDS]

    try:
        mg = await get_monitor_group()
        if mg:
            tasks.append(asyncio.create_task(_send_and_del(mg)))
    except Exception:
        pass

    print(f"[STARTUP_HC] Issues found — alert sent to {len(tasks)} recipient(s).")


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
        from config import LOG_CHANNEL as _ENV_LC, db as _db, settings_col as _sc
        if _ENV_LC:
            # Sync env LOG_CHANNEL to BOTH MongoDB collections used by the bot
            await _sc.update_one(                          # bot_settings (read by get_log_channel)
                {"key": "log_channel"},
                {"$set": {"key": "log_channel", "chat_id": _ENV_LC}},
                upsert=True,
            )
            await _db["settings"].update_one(              # settings (read by syscheck)
                {"key": "log_channel"},
                {"$set": {"key": "log_channel", "chat_id": _ENV_LC}},
                upsert=True,
            )
            print(f"[STARTUP] Log channel synced from env: {_ENV_LC}")
    except Exception as e:
        print(f"[STARTUP] WARNING: Could not sync log channel: {e}")

    loop = asyncio.get_event_loop()
    loop.create_task(schedule_loop(app))
    loop.create_task(nightmode_loop(app))
    loop.create_task(stars_payment_loop())
    loop.create_task(video_del_loop())
    loop.create_task(daily_report_loop(app))
    print("[TASKS] Background loops started (schedule + nightmode + stars_payment + video_del + daily_report).")

    # Load admin-set package price overrides from MongoDB
    await load_package_overrides()

    await start_all_clones()
    print("[CLONE] Clone startup complete.")

    # Pre-load groups where main bot is present (silences clone bots immediately)
    asyncio.get_event_loop().create_task(_preload_main_bot_groups())

    # Startup health check — notify admins + monitor group
    asyncio.get_event_loop().create_task(_startup_health_check())

    await pyrogram_idle()
    await app.stop()


app.run(main())
