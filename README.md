# 🤖 DESI MLH Bot

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?style=for-the-badge&logo=python" />
  <img src="https://img.shields.io/badge/Pyrogram-2.x-green?style=for-the-badge&logo=telegram" />
  <img src="https://img.shields.io/badge/MongoDB-Motor-brightgreen?style=for-the-badge&logo=mongodb" />
  <img src="https://img.shields.io/badge/Platform-Telegram-2CA5E0?style=for-the-badge&logo=telegram" />
</p>

A powerful, feature-rich Telegram bot for managing multiple groups from a single **Control Group**. Built with [Pyrogram](https://pyrogram.org/) and MongoDB.

---

## ✨ Features Overview

| Category | Features |
|---|---|
| 🎛️ Control Group | Manage all groups from one private group |
| 🎬 Video Delivery | Auto-send videos with per-user daily limits |
| 🛡️ Protection System | Anti-forward, anti-link, anti-spam with silent/warn toggle |
| 👋 Welcome Messages | Custom welcome messages per group |
| 🏷️ Invisible Tag | Tag all members silently using Zero-Width Space |
| 📬 Inbox System | Relay user DMs to a dedicated inbox group |
| 📊 Monitor Group | Live activity relay + admin reply from monitor |
| 🌙 Night Mode | Scheduled group lock/unlock |
| 🔑 Keyword Auto-Reply | Trigger custom replies from keywords |
| 😂 Auto Reactions | Auto-react to messages with custom emojis |
| 💎 Premium System | Tiered packages, payment proofs, Telegram Stars |
| 🤖 Clone Bots | Run multiple bot tokens from one setup |
| 🚫 Moderation | Ban, mute, kick, warn, shadowban |
| 📋 Daily Auto-Report | Nightly summary sent to Control Group |
| 🔒 Force Join | Require channel membership to use the bot |
| 🤝 Auto Approve | Auto-approve group join requests |

---

## ⚙️ Environment Variables (`.env`)

Create a `.env` file in the **root of the project**:

```env
# ─── Telegram Bot ──────────────────────────────────────────
TELEGRAM_BOT_TOKEN=your_bot_token_here

# ─── Telegram API  (get from https://my.telegram.org) ──────
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890

# ─── MongoDB Atlas ──────────────────────────────────────────
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/telegram_bot

# ─── Admin IDs  (comma-separated Telegram user IDs) ────────
ADMIN_IDS=123456789,987654321

# ─── Optional ───────────────────────────────────────────────
VIDEO_CHANNEL=-1002623940581   # Channel ID where videos are stored
LOG_CHANNEL=0                  # Log channel ID (0 = disabled)
SESSION_SECRET=any_random_string
```

### Where to get each value

| Variable | Source |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_API_ID` & `TELEGRAM_API_HASH` | [my.telegram.org](https://my.telegram.org) → API Development Tools |
| `MONGO_URI` | [MongoDB Atlas](https://cloud.mongodb.com) → Connect → Drivers |
| `ADMIN_IDS` | Your Telegram user ID — find it via [@userinfobot](https://t.me/userinfobot) |
| `VIDEO_CHANNEL` | Your video storage channel ID (starts with `-100...`) |

> ⚠️ The `.env` file is in `.gitignore` — it will **never** be pushed to GitHub.

---

## 🚀 Installation & Run

```bash
# 1. Clone the repository
git clone https://github.com/imranhossenmaruf/desi_mlh_bot_updated.git
cd desi_mlh_bot_updated

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create .env file with your credentials (see above)

# 4. Run the bot
python main.py
```

---

## 🎛️ Control Group Setup

The **Control Group** is a private Telegram group from which you manage all other groups.

1. Create a private group on Telegram
2. Add the bot and make it **Admin** (with all permissions)
3. Send `/setcontrolgroup` inside that group
4. Use `/ctrlhelp` to see all available commands

---

## 📋 Command Reference

### 🎛️ Control Group Commands

| Command | Description |
|---|---|
| `/ctrlhelp` | Show full control group command list |
| `/groups` | List all managed groups |
| `/overview [days\|date]` | Activity statistics (default: last 7 days) |
| `/syscheck` | Full system health check |

#### 📤 Broadcasting
| Command | Description |
|---|---|
| `/sendall [msg]` | Broadcast message to all groups |
| `/sendall` *(reply)* | Broadcast the replied-to message |
| `/sendto` | Pick a group interactively to send to |
| `/sendto [chat_id] [msg]` | Send directly to a group by ID |

#### 🏷️ Invisible Tag (Zero-Width Space)
| Command | Description |
|---|---|
| `/taggroup [gid] [msg]` | Tag all members in a specific group invisibly |
| `/tagall [gid] [msg]` | Same as taggroup |
| `/cancel` | Cancel any active tag session |

> **Adding buttons:** After the tag message sends, tap ➕ — then type one or more lines of `Button Text | https://url` to attach inline buttons.

#### 🎬 Video System
| Command | Description |
|---|---|
| `/videoon [gid]` | Enable video delivery in a group |
| `/videooff [gid]` | Disable video delivery |
| `/videomsgon [gid]` | Show redirect message when video is off |
| `/videomsgoff [gid]` | Hide redirect message when video is off |

#### 👋 Welcome Messages
| Command | Description |
|---|---|
| `/welcomeon [gid]` | Enable welcome message for a group |
| `/welcomeoff [gid]` | Disable welcome message |

#### 🌊 Anti-Flood
| Command | Description |
|---|---|
| `/antifloodon [gid]` | Enable anti-flood |
| `/antifloodoff [gid]` | Disable anti-flood |

#### 😂 Auto Reactions
| Command | Description |
|---|---|
| `/autoreactionon [gid]` | Enable auto reactions |
| `/autoreactionoff [gid]` | Disable auto reactions |

#### 🛡️ Protection System
| Command | Description |
|---|---|
| `/forwardon [gid]` | Enable anti-forward protection |
| `/forwardoff [gid]` | Disable anti-forward protection |
| `/linkon [gid]` | Enable link protection |
| `/linkoff [gid]` | Disable link protection |
| `/spamon [gid]` | Enable spam protection |
| `/spamoff [gid]` | Disable spam protection |
| `/warnon [gid]` | Send warning message after deleting ✅ |
| `/warnoff [gid]` | Delete silently — no warning message 🔇 |
| `/protect [gid] spam_limit N` | Set spam threshold (messages per 10 sec) |
| `/protections` | View all groups' protection settings |

#### 📋 Daily Auto-Report
| Command | Description |
|---|---|
| `/dailyreport` | Get yesterday's summary report now |
| `/dailyreporton` | Enable automatic nightly report |
| `/dailyreportoff` | Disable automatic nightly report |
| `/reporttime HH:MM` | Set report time in BDT (default: `00:00`) |

#### 🔑 Keyword Auto-Reply
| Command | Description |
|---|---|
| `/kw add [word] [reply]` | Add a keyword trigger |
| `/kw del [word]` | Remove a keyword |
| `/kw list` | List all keywords |
| `/kw clear` | Clear all keywords |

#### 🔧 Setup Commands
| Command | Description |
|---|---|
| `/setcontrolgroup` | Register this group as Control Group |
| `/setmonitorgroup [gid]` | Set monitor group |
| `/setinboxgroup [gid]` | Set inbox group |

---

### 🏘️ In-Group Commands (for Group Admins)

#### 🚫 Moderation
| Command | Description |
|---|---|
| `/ban` | Ban a user (reply or mention) |
| `/unban` | Unban a user |
| `/mute` | Restrict a user from sending messages |
| `/unmute` | Remove restriction |
| `/kick` | Kick a user |
| `/warn` | Issue a warning to a user |
| `/warns` | View a user's warnings |
| `/clearwarn` | Clear all warnings for a user |
| `/del` | Delete the replied-to message |
| `/ro` | Set group to read-only mode |
| `/pin` | Pin a message |
| `/unpin` | Unpin a message |
| `/report` | Report a message to admins |

#### 🕵️ Shadow Ban
| Command | Description |
|---|---|
| `/shadowban` | Silently hide a user's messages from others |
| `/unshadowban` | Remove shadow ban |
| `/shadowbans` | List all shadow-banned users |
| `/clearshadowbans` | Remove all shadow bans |

#### ⚙️ Group Settings
| Command | Description |
|---|---|
| `/group` | Open interactive group settings panel |
| `/nightmode on/off HH:MM-HH:MM` | Schedule automatic group lock |
| `/autoapprove` | Toggle auto-approve for join requests |
| `/rules` | Show group rules |
| `/setrules [text]` | Set group rules |
| `/welcome` | Preview current welcome message |

#### 🏷️ Tagger (In-Group)
| Command | Description |
|---|---|
| `/tag [msg]` | Tag all group members with a message |
| `/utag` | Stop ongoing tag operation |
| `/taggerhelp` | Show tagger command help |

---

### 💎 Premium System (Private DM with Bot)

| Command | Description |
|---|---|
| `/buypremium` | Browse and purchase premium packages |
| `/mypremium` | Check your current premium status |
| `/packages` | List all available packages |

#### Premium Packages

| Package | Price | Duration | Videos/Day |
|---|---|---|---|
| 🌱 Starter | $2 USDT | 3 days | 15 |
| 🥉 Basic | $5 USDT | 7 days | 30 |
| 🥈 Standard | $10 USDT | 30 days | 60 |
| 🥇 Pro | $18 USDT | 60 days | 100 |
| 💎 VIP | $25 USDT | 90 days | Unlimited |
| 👑 Elite | $40 USDT | 180 days | Unlimited |

**Payment Methods:** Binance Pay · RedotPay · USDT TRC20 · USDT BEP20 · Telegram Stars ⭐

---

### 🔐 Admin-Only Commands (Private DM)

| Command | Description |
|---|---|
| `/blockuser [id]` | Block a user from the bot |
| `/unblockuser [id]` | Unblock a user |
| `/premiumlist` | List all premium users |
| `/revokepremium [id]` | Remove a user's premium |
| `/setprice [pkg] [amount]` | Update a package price |
| `/grouplist` | List all groups with details |
| `/groupstats` | Detailed group statistics |
| `/broadcast [msg]` | Broadcast to all private users |
| `/forcejoin` | Manage required channels |
| `/overview [days]` | Activity stats (DM version) |
| `/dailyreport` | Get daily report in DM |

---

## 🤖 Clone Bot System

Run multiple bot tokens that all manage the same groups:

| Command | Description |
|---|---|
| `/addclone` | Add a new clone bot token |
| `/removeclone` | Remove a clone bot |
| `/clones` | List all active clones |
| `/cloneconfig` | Configure a clone |
| `/setupclone` | Full clone setup wizard |

Each clone automatically follows the same protection rules and group settings as the main bot.

---

## 📊 Monitor Group

A dedicated group where the bot forwards real-time events:

- 📥 New user registrations
- ⚠️ Anti-forward / anti-link violations
- 🌊 Anti-flood triggers
- 📨 Inbox messages (admins can reply directly from monitor)
- 📡 Group join/leave events

**Setup:** `/setmonitorgroup [chat_id]` from the Control Group

---

## 📬 Inbox Group

User DMs sent to the bot in private are relayed to the Inbox Group. Admins can:

- Reply to users directly from the inbox group
- Use `/user` to view a user's full profile
- Use `/chat` to open a continuous DM session with the user

**Setup:** `/setinboxgroup [chat_id]` from the Control Group

---

## 📋 Daily Auto-Report

Every night at **00:00 BDT** (configurable with `/reporttime`), the bot sends a summary to the Control Group:

```
📋 Daily Report — 09 April 2026
━━━━━━━━━━━━━━━━━━━━━━
👤 Users      Total: 1,234   New: +12
💎 Premium    Active: 45 / 89
📬 Inbox      Received: 67   Replied: 54   Response Rate: 80%
🎬 Videos     Sent: 234
🏘️ Groups     Total: 8   Members: 12,450
🔝 Top Groups → ...
🛡️ Active Protection → Forward: 6 | Link: 5 | Spam: 7 groups
━━━━━━━━━━━━━━━━━━━━━━
🕐 Report generated: 09 Apr 2026 00:00 BDT
```

---

## 🗂️ Project Structure

```
📦 desi_mlh_bot_updated/
├── main.py                  # Entrypoint
├── bot.py                   # Startup & background loops
├── config.py                # Environment, DB collections, constants
├── helpers.py               # Shared utility functions
├── tasks.py                 # Schedule & video-del loops
├── clone_manager.py         # Clone bot lifecycle management
├── requirements.txt         # Python dependencies
└── handlers/
    ├── control_group.py     # 🎛️ Control Group commands
    ├── protection.py        # 🛡️ Anti-forward / anti-link / anti-spam
    ├── groups.py            # 🏘️ Group event handlers
    ├── video.py             # 🎬 Video delivery system
    ├── welcome.py           # 👋 Welcome & goodbye messages
    ├── moderation.py        # 🚫 Ban / mute / kick / warn
    ├── antiflood.py         # 🌊 Flood detection & action
    ├── nightmode.py         # 🌙 Scheduled lock/unlock
    ├── keyword_reply.py     # 🔑 Keyword auto-reply
    ├── tagger.py            # 🏷️ Invisible tag system
    ├── inbox.py             # 📬 Inbox relay
    ├── monitor.py           # 📊 Monitor group relay
    ├── activity_tracker.py  # 📈 Stats & overview commands
    ├── daily_report.py      # 📋 Daily auto-report loop
    ├── premium.py           # 💎 Premium packages & proofs
    ├── stars_payment.py     # ⭐ Telegram Stars payment polling
    ├── forcejoin.py         # 🔒 Force-join channel system
    ├── shadowban.py         # 🕵️ Shadow ban enforcement
    ├── clone.py             # 🤖 Clone bot handlers
    ├── group_settings.py    # ⚙️ Group settings panel
    ├── admin.py             # 🔐 Admin-only commands
    ├── admin_mgmt.py        # 👑 Admin management
    ├── broadcast.py         # 📢 Broadcast system
    ├── start.py             # /start & /help handler
    └── misc.py              # Miscellaneous & schedule handlers
```

---

## 🔧 Tech Stack

| Tool | Purpose |
|---|---|
| [Pyrogram](https://pyrogram.org/) | Telegram MTProto client (fast & async) |
| [Motor](https://motor.readthedocs.io/) | Async MongoDB driver |
| [MongoDB Atlas](https://cloud.mongodb.com/) | Cloud database |
| [python-dotenv](https://pypi.org/project/python-dotenv/) | `.env` file loading |
| [aiohttp](https://docs.aiohttp.org/) | Async HTTP for Bot API calls |
| [tgcrypto](https://pypi.org/project/TgCrypto/) | Pyrogram encryption speedup |

---

## 📄 License

This project is private. All rights reserved © DESI MLH.
