"""
main.py — Correct entrypoint for DESI MLH Bot.

All handler registration, background loops (nightmode, schedule, stars_payment,
video_del), and clone management are handled inside bot.py.
This file simply runs bot.py as the authoritative startup sequence.
"""
import runpy
runpy.run_path("bot.py", run_name="__main__")
