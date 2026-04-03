import os
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, enums

HTML = enums.ParseMode.HTML

mongo_client   = AsyncIOMotorClient(
    os.environ["MONGO_URI"],
    serverSelectionTimeoutMS=8000,
    connectTimeoutMS=8000,
    socketTimeoutMS=10000,
)
db             = mongo_client["telegram_bot"]
users_col      = db["users"]
videos_col     = db["channel_videos"]
vid_hist_col   = db["user_video_history"]
settings_col   = db["bot_settings"]
scheduled_col  = db["scheduled_broadcasts"]
nightmode_col  = db["nightmode_settings"]
shadowban_col  = db["shadowban"]
filters_col    = db["group_filters"]
antiflood_col  = db["antiflood_settings"]
welcome_col    = db["welcome_messages"]
rules_col      = db["group_rules"]
premium_col    = db["premium_users"]

API_ID    = int(os.environ["TELEGRAM_API_ID"])
API_HASH  = os.environ["TELEGRAM_API_HASH"]
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_ID  = int(os.environ["ADMIN_ID"])

VIDEO_CHANNEL     = -1002623940581
DAILY_VIDEO_LIMIT = 10
VIDEO_REPEAT_DAYS = 7

app = Client("telegram_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

BOT_USERNAME: str = ""

REPLIES = {
    "hello":     "Hey there! 👋 How can I help you?",
    "hi":        "Hi! 😊 Type /help to see what I can do.",
    "help":      "Send me a message and I'll do my best to reply!\n\nCommands:\n/start — Register and get started\n/help — Show this message",
    "bye":       "Goodbye! See you next time 👋",
    "thanks":    "You're welcome! 😊",
    "thank you": "You're welcome! 😊",
}

PACKAGES = {
    "bronze": {
        "label":      "🥉 Bronze",
        "price":      "$3 USDT",
        "days":       7,
        "video_limit": 25,
        "desc":       "৭ দিন • প্রতিদিন ২৫টি ভিডিও",
    },
    "silver": {
        "label":      "🥈 Silver",
        "price":      "$8 USDT",
        "days":       30,
        "video_limit": 50,
        "desc":       "৩০ দিন • প্রতিদিন ৫০টি ভিডিও",
    },
    "gold": {
        "label":      "🥇 Gold",
        "price":      "$20 USDT",
        "days":       90,
        "video_limit": 999,
        "desc":       "৯০ দিন • Unlimited ভিডিও",
    },
}

PAYMENT_METHODS = {
    "binance":  {"label": "💛 Binance Pay",  "qr": "assets/binance_qr.png",  "name": "Imran_Hossain Maruf"},
    "redotpay": {"label": "🔴 RedotPay",     "qr": "assets/redotpay_qr.jpg", "id":   "1329722845"},
}

broadcast_sessions:   dict[int, dict]       = {}
fj_sessions:          dict[int, dict]       = {}
flood_tracker:        dict[tuple, list]     = {}
pending_welcome_msgs: dict[int, tuple[int, int]] = {}
proof_sessions:       dict[int, dict]       = {}

STATE_AUDIENCE  = "audience"
STATE_JOIN_DATE = "join_date"
STATE_CONTENT   = "content"
STATE_CUSTOMIZE = "customize"
STATE_BUTTONS   = "buttons"
STATE_CONFIRM   = "confirm"
STATE_SCHEDULE  = "schedule"
