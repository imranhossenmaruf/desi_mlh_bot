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
DAILY_VIDEO_LIMIT = 5
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
    "starter": {
        "label":       "🌱 Starter",
        "price":       "$2 USDT",
        "price_usd":   2,
        "stars":       100,
        "days":        3,
        "video_limit": 15,
        "desc":        "3 Days • 15 Videos/Day",
    },
    "basic": {
        "label":       "🥉 Basic",
        "price":       "$5 USDT",
        "price_usd":   5,
        "stars":       250,
        "days":        7,
        "video_limit": 30,
        "desc":        "7 Days • 30 Videos/Day",
    },
    "standard": {
        "label":       "🥈 Standard",
        "price":       "$10 USDT",
        "price_usd":   10,
        "stars":       500,
        "days":        30,
        "video_limit": 60,
        "desc":        "30 Days • 60 Videos/Day",
    },
    "pro": {
        "label":       "🥇 Pro",
        "price":       "$18 USDT",
        "price_usd":   18,
        "stars":       900,
        "days":        60,
        "video_limit": 100,
        "desc":        "60 Days • 100 Videos/Day",
    },
    "vip": {
        "label":       "💎 VIP",
        "price":       "$25 USDT",
        "price_usd":   25,
        "stars":       1250,
        "days":        90,
        "video_limit": 999,
        "desc":        "90 Days • Unlimited Videos",
    },
    "elite": {
        "label":       "👑 Elite",
        "price":       "$40 USDT",
        "price_usd":   40,
        "stars":       2000,
        "days":        180,
        "video_limit": 999,
        "desc":        "180 Days • Unlimited Videos",
    },
}

PACKAGE_ORDER = ["starter", "basic", "standard", "pro", "vip", "elite"]

PAYMENT_METHODS = {
    "binance":  {
        "label": "💛 Binance Pay",
        "type":  "qr",
        "qr":    "assets/binance_qr.png",
        "name":  "Imran_Hossain Maruf",
        "note":  "Search by name or scan QR in Binance app.",
    },
    "redotpay": {
        "label": "🔴 RedotPay",
        "type":  "qr",
        "qr":    "assets/redotpay_qr.jpg",
        "id":    "1329722845",
        "note":  "Send to ID or scan QR in RedotPay app.",
    },
    "trc20": {
        "label":   "🔵 USDT (TRC20)",
        "type":    "address",
        "address": "TLWiVbyExgrVnwRkWE1xewvTmRtWbnzNn6",
        "note":    "Send USDT via Tron (TRC20) network only.",
    },
    "bep20": {
        "label":   "🟡 USDT (BEP20)",
        "type":    "address",
        "address": "0x73a6c57e75d89F45fF5e5161211B3fb862942531",
        "note":    "Send USDT via BNB Smart Chain (BEP20) network only.",
    },
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
