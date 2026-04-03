import asyncio
from config import app

import handlers.start
import handlers.user
import handlers.admin
import handlers.video
import handlers.broadcast
import handlers.forcejoin
import handlers.moderation
import handlers.shadowban
import handlers.filters
import handlers.nightmode
import handlers.antiflood
import handlers.welcome
import handlers.misc
import handlers.premium

from handlers.nightmode import nightmode_loop
from handlers.misc import schedule_loop


async def main():
    await app.start()
    print("[BOT] Started successfully.")
    await asyncio.gather(
        nightmode_loop(app),
        schedule_loop(app),
    )


app.run(main())
