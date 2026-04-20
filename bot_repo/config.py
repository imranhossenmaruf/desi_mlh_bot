import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, enums

load_dotenv()

HTML = enums.ParseMode.HTML

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MONGO_URI = os.environ["MONGO_URI"]
_raw_ids = os.environ.get("ADMIN_IDS", os.environ.get("ADMIN_ID", ""))
ADMIN_IDS = [int(x.strip()) for x in _raw_ids.split(",") if x.strip()]
ADMIN_ID = ADMIN_IDS[0]
API_ID = int(os.environ["TELEGRAM_API_ID"])
API_HASH = os.environ["TELEGRAM_API_HASH"]
VIDEO_CHANNEL = int(os.environ.get("VIDEO_CHANNEL", -1002623940581))
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", 0))
BOT_OWNER_USERNAME = os.environ.get("BOT_OWNER_USERNAME", "IH_Maruf")

mongo_client = AsyncIOMotorClient(
    MONGO_URI,
    serverSelectionTimeoutMS=8000,
    connectTimeoutMS=8000,
    socketTimeoutMS=10000,
)
db = mongo_client["telegram_bot"]
users_col = db["users"]
videos_col = db["channel_videos"]
vid_hist_col = db["user_video_history"]
settings_col = db["bot_settings"]
scheduled_col = db["scheduled_broadcasts"]
nightmode_col = db["nightmode_settings"]
shadowban_col = db["shadowban"]
filters_col = db["group_filters"]
antiflood_col = db["antiflood_settings"]
welcome_col = db["welcome_messages"]
rules_col = db["group_rules"]
premium_col = db["premium_users"]
inbox_col = db["inbox_messages"]
conversations_col = db["inbox_conversations"]
groups_col = db["bot_groups"]
del_queue_col = db["video_del_queue"]
admins_col = db["bot_admins"]
clones_col = db["bot_clones"]
group_settings_col = db["group_settings"]
auto_reactions_col = db["auto_reactions"]
keyword_triggers_col = db["keyword_triggers"]
group_buttons_col = db["group_buttons"]
auto_approve_logs_col = db["auto_approve_logs"]
join_requests_col = db["join_requests"]
tagger_logs_col = db["tagger_logs"]
cmd_control_col = db["cmd_controls"]

DAILY_VIDEO_LIMIT = 5
VIDEO_REPEAT_DAYS = 7

app = Client("telegram_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

BOT_USERNAME: str = ""
MAIN_BOT_ID: int = 0

broadcast_sessions: dict = {}
fj_sessions: dict = {}
flood_tracker: dict = {}
pending_welcome_msgs: dict = {}
proof_sessions: dict = {}

STATE_AUDIENCE = "audience"
STATE_JOIN_DATE = "join_date"
STATE_CONTENT = "content"
STATE_CUSTOMIZE = "customize"
STATE_BUTTONS = "buttons"
STATE_CONFIRM = "confirm"
STATE_SCHEDULE = "schedule"

PACKAGES = {
    "starter": {
        "label": "Starter",
        "price": "$2 USDT",
        "price_usd": 2,
        "stars": 100,
        "days": 3,
        "video_limit": 15,
        "desc": "3 Days, 15 Videos/Day",
    },
    "basic": {
        "label": "Basic",
        "price": "$5 USDT",
        "price_usd": 5,
        "stars": 250,
        "days": 7,
        "video_limit": 30,
        "desc": "7 Days, 30 Videos/Day",
    },
    "standard": {
        "label": "Standard",
        "price": "$10 USDT",
        "price_usd": 10,
        "stars": 500,
        "days": 30,
        "video_limit": 60,
        "desc": "30 Days, 60 Videos/Day",
    },
    "pro": {
        "label": "Pro",
        "price": "$18 USDT",
        "price_usd": 18,
        "stars": 900,
        "days": 60,
        "video_limit": 100,
        "desc": "60 Days, 100 Videos/Day",
    },
    "vip": {
        "label": "VIP",
        "price": "$25 USDT",
        "price_usd": 25,
        "stars": 1250,
        "days": 90,
        "video_limit": 999,
        "desc": "90 Days, Unlimited Videos",
    },
    "elite": {
        "label": "Elite",
        "price": "$40 USDT",
        "price_usd": 40,
        "stars": 2000,
        "days": 180,
        "video_limit": 999,
        "desc": "180 Days, Unlimited Videos",
    },
}

PACKAGE_ORDER = ["starter", "basic", "standard", "pro", "vip", "elite"]
PAYMENT_METHODS: dict = {}
