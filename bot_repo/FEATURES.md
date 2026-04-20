# 🤖 Group Settings & Automation Features

This document describes the new core features added to the DESI MLH Bot for enhanced group management and automation.

## Overview

Seven major features have been implemented to give admins comprehensive control over their groups:

1. **Granular Group Settings** - Toggle features on/off per group
2. **Auto-Reaction System** - Automatic reactions to messages
3. **Custom Buttons** - Inline buttons in bot messages
4. **Keyword-Based Auto-Reply** - Trigger responses to specific keywords
5. **Auto-Approve Join Requests** - Automatically approve new members
6. **Configuration Management** - Persistent storage in MongoDB
7. **Invisible Tagall** - Notify all members invisibly with ZWNJ mentions

---

## 1. Granular Group Settings ⚙️

### Overview
Admins can toggle features on/off for their specific group.

### Available Features
- `video` - Enable/disable `/video` command
- `welcome` - Enable/disable welcome messages
- `filters` - Enable/disable word filters
- `antiflood` - Enable/disable flood protection
- `nightmode` - EnaEnablesable nightmode
- `auto_reactions` - Enable/disable auto-reactions
- `keyword_reply` - Enable/disable keyword-based replies

### Commands

#### In Group
```
/group              → Open group settings menu
/groupinfo          → View current settings info
```

#### For Super Admins (Private)
```
/grouplist          → List all groups using the bot
/groupinfo <chat_id> → Get info about a specific group
/togglegroupfeature <chat_id> <feature> → Toggle a feature
/resetgroup <chat_id> → Reset all group settings
```

### Usage Example

1. **In your group**, type `/group` as an admin
2. A menu appears with options:
   - ⚙️ Features - Toggle each feature
   - 📝 Keywords - Manage keyword triggers
   - 😂 Reactions - Configure auto-reactions
   - 🔘 Buttons - Setup custom buttons
   - ✅ Auto-Approve - Enable auto-approvals
   - 📋 Info - View current configuration

---

## 2. Auto-Reaction System 😂

### Overview
The bot automatically reacts to messages with emoji when enabled.

### Database Collection
- **Collection:** `auto_reactions`
- **Fields:**
  ```
  {
    "chat_id": -1001234567890,
    "reactions": ["😂", "😍", "🔥", "⭐"],
    "enabled": true,
    "updated_at": "2024-01-15T10:30:00"
  }
  ```

### Commands

#### In Group
```
/setreactions emoji1 emoji2 emoji3...
```

**Example:**
```
/setreactions 😂 😍 🔥 ⭐
```

### How It Works
1. Admin sets reaction emojis for the group
2. When any message is sent in the group:
   - Bot checks if `auto_reactions` feature is enabled
   - Bot retrieves the emoji list for that group
   - Bot reacts with the first emoji

### Enable/Disable
- Via `/group` → Reactions → Toggle
- Reactions are disabled by default for new groups

---

## 3. Keyword-Based Auto-Reply 🔑

### Overview
When users mention specific keywords, the bot automatically replies with a pre-configured message.

### Database Collection
- **Collection:** `keyword_triggers`
- **Fields:**
  ```
  {
    "chat_id": -1001234567890,
    "keyword": "hello",
    "response": "Hey there! 👋 How can I help?",
    "created_at": "2024-01-15T10:30:00"
  }
  ```

### Commands

#### In Group
```
/addkeyword <keyword> <response>    → Add a keyword trigger
/delkeyword <keyword>                → Delete a keyword
/keywords                            → List all keywords
```

**Examples:**
```
/addkeyword hello Hey there! 👋
/addkeyword rules Check the pinned message for rules
```

### How It Works
1. Admin adds a keyword and response
2. When a message containing the keyword is posted:
   - Bot finds the keyword trigger
   - Bot replies with the configured response
   - The bot quotes the original message

### Enable/Disable
- Via `/group` → Keywords → Info
- Keyword reply feature disabled by default

### Important Notes
- Keyword matching is case-insensitive
- One keyword per entry (no multi-word phrase matching currently)
- Response supports HTML formatting

---

## 4. Custom Buttons 🔘

### Overview
Admins can attach custom inline buttons to bot messages.

### Database Collection
- **Collection:** `group_buttons`
- **Fields:**
  ```
  {
    "chat_id": -1001234567890,
    "name": "video_buttons",
    "buttons": [
      {
        "text": "Join Channel",
        "url": "https://t.me/mychannel"
      },
      {
        "text": "Buy Premium",
        "url": "https://t.me/bot?start=premium"
      }
    ],
    "updated_at": "2024-01-15T10:30:00"
  }
  ```

### Commands

#### In Group
```
/setbuttons         → Start custom buttons setup
/attachbuttons <name> → Attach buttons to next message
```

### How It Works
1. Admin uses `/setbuttons` to see the format
2. Buttons are set in the format: `[Button Text|URL]`
3. Multiple buttons are supported (up to 10)
4. Buttons are automatically added when bot sends messages

### Button Format
```
[Visit Site|https://example.com] [Join Channel|https://t.me/channel]
```

---

## 5. Auto-Approve Join Requests ✅

### Overview
When enabled, the bot automatically approves new join requests in the group.

### Database Collections
- **Collection:** `auto_approve_logs`
- **Fields:**
  ```
  {
    "chat_id": -1001234567890,
    "user_id": 123456789,
    "username": "john_doe",
    "first_name": "John",
    "approved_at": "2024-01-15T10:30:00"
  }
  ```

### Commands

#### In Group
```
/autoapprove        → Toggle auto-approve for the group
```

### How It Works
1. Admin enables auto-approve via `/group` → Auto-Approve
2. When a user sends a join request:
   - Bot automatically approves the request
   - User receives a confirmation message
   - Entry is logged in `auto_approve_logs`
   - Admin receives log event

### Features
- ✅ **Auto-Approve:** Join requests are instantly approved
- ✅ **Confirmation:** User gets a DM confirming approval
- ✅ **Logging:** All approvals are logged with timestamps
- ✅ **Admin Notification:** Admin receives approval logs

### Disable Auto-Approve
If you need to disable it:
```
/group → Auto-Approve → Disable
```

---

## 6. Configuration Management 🗄️

### Database Schema

#### group_settings Collection
```javascript
{
  "chat_id": -1001234567890,
  "features": {
    "video": true,
    "welcome": true,
    "filters": true,
    "antiflood": true,
    "nightmode": false,
    "auto_reactions": false,
    "keyword_reply": false
  },
  "auto_approve": false,
  "log_channel": null,
  "created_at": "2024-01-15T10:30:00",
  "updated_at": "2024-01-15T10:30:00"
}
```

#### auto_reactions Collection
```javascript
{
  "chat_id": -1001234567890,
  "reactions": ["😂", "😍", "🔥"],
  "enabled": true,
  "updated_at": "2024-01-15T10:30:00"
}
```

#### keyword_triggers Collection
```javascript
{
  "chat_id": -1001234567890,
  "keyword": "hello",
  "response": "Hey there! 👋",
  "created_at": "2024-01-15T10:30:00"
}
```

#### auto_approve_logs Collection
```javascript
{
  "chat_id": -1001234567890,
  "user_id": 123456789,
  "username": "john_doe",
  "first_name": "John",
  "approved_at": "2024-01-15T10:30:00"
}
```

#### group_buttons Collection
```javascript
{
  "chat_id": -1001234567890,
  "name": "default",
  "buttons": [
    {
      "text": "Get Video",
      "url": "https://t.me/bot?start=video"
    }
  ],
  "updated_at": "2024-01-15T10:30:00"
}
```

---

## Architecture

### Handler Organization
- **handlers/group_settings.py** - Main group settings manager
  - `/group` command and menu
  - Callback query handlers for settings
  - Keyword triggers logic
  - Auto-reactions logic
  - Auto-approve logic

- **handlers/groups.py** - Enhanced with feature checks
  - Video command now checks if feature is enabled
  - Group tracking and management
  - Join request monitoring

- **handlers/admin.py** - Admin management tools
  - `/grouplist` - List all groups
  - `/groupinfo` - Get group details
  - `/togglegroupfeature` - Toggle features per group
  - `/resetgroup` - Reset group configuration

### Data Flow

```
User Message in Group
    ↓
Check if keyword matches (if enabled)
    ↓
If match → Send auto-reply
    ↓
Apply auto-reactions (if enabled)
    ↓
Process other handlers normally
```

---

## Usage Guide

### For Group Admins

#### Setup Initial Settings
1. Add bot to group and give it admin permissions
2. Use `/group` to open settings menu
3. Configure features as needed
4. Add keywords if you want auto-reply
5. Set auto-reactions for engagement

#### Manage Keywords
```
# Add: /addkeyword help Type /help for commands
# Remove: /delkeyword help
# List: /keywords
```

#### Enable Auto-Reactions
```
/setreactions 😂 😍 🔥 ⭐
# Up to 10 emojis supported
```

#### Auto-Approve Members
```
/autoapprove  # Toggle on/off
```

### For Super Admin

#### View All Groups
```
/grouplist
```

#### Check Specific Group
```
/groupinfo -1001234567890
```

#### Toggle Feature for Specific Group
```
/togglegroupfeature -1001234567890 video
```

#### Reset Group Configuration
```
/resetgroup -1001234567890
```

---

## Troubleshooting

### Issue: Auto-reactions not working
- ✅ Check if feature is enabled via `/group`
- ✅ Verify emojis were set with `/setreactions`
- ✅ Check bot has permission to add reactions

### Issue: Keywords not triggering
- ✅ Check if `keyword_reply` feature is enabled
- ✅ Verify keyword was added with `/addkeyword`
- ✅ Note: Matching is case-insensitive but requires the exact word

### Issue: Auto-approve not working
- ✅ Check if enabled with `/autoapprove`
- ✅ Verify bot has `can_invite_users` permission
- ✅ Check MongoDB `auto_approve_logs` collection for errors

### Issue: Custom buttons not showing
- ✅ Check if buttons were properly configured
- ✅ Verify button format is correct
- ✅ Use `/setbuttons` to view correct format

---

## Performance Notes

- ✅ All settings are cached in MongoDB
- ✅ Feature toggles are checked per-message
- ✅ Keyword matching uses simple string search (O(n))
- ✅ Auto-reactions react with first emoji only
- ✅ Suitable for groups with 1000+ members

---

## Future Enhancements

- [ ] Multi-word keyword phrase support
- [ ] Regex pattern matching for keywords
- [ ] Multiple reactions per message (round-robin)
- [ ] Scheduled messages with custom buttons
- [ ] Message filtering by role/permission
- [ ] Analytics dashboard for group activity
- [ ] Custom emoji reactions based on user roles
- [ ] Keyword cooldown periods

---

## 7. Invisible Tagall Feature 🔔

### Overview

The Invisible Tagall feature allows group admins to notify all members at once using invisible mentions. Members receive notifications without cluttering the chat with a long list of display names or IDs.

### Features

✅ **Invisible Mentions** - Uses ZWNJ (Zero-Width Non-Joiner) character for hidden notifications
✅ **Batch Processing** - Splits members into small batches (8 per message) to avoid Telegram flood limits  
✅ **Session Management** - Tracks active tagging processes and prevents simultaneous operations
✅ **Cooldown System** - 5-second cooldown between tags per group
✅ **Progress Tracking** - Real-time status of tagging progress
✅ **Safe Execution** - Auto-excludes bots, handles errors gracefully
✅ **Admin Only** - Proper permission checks before execution
✅ **Cancellation** - Stop tags in progress with `/cancel`

### Database Collection

**Collection:** `tagger_logs`

```javascript
{
  "chat_id": -1001234567890,
  "admin_id": 123456789,
  "member_count": 45,
  "message": "Meeting at 5 PM",
  "status": "completed",  // "started", "completed", "cancelled", "error"
  "timestamp": "2024-01-15T10:30:00"
}
```

### Commands

#### Main Commands
```
/tagall [message]        → Tag all members with optional message
/utag [message]          → Alias for /tagall
/tagstatus               → View current tagging progress
/cancel                  → Cancel active tagging process
/stoptag                 → Alias for /cancel
/taggerhelp              → Show detailed help for tagging commands
```

### Usage Examples

#### Basic Tagging
```
/tagall
```
**Result:** All members tagged invisibly with no message

#### Custom Message
```
/tagall Meeting rescheduled to 6 PM!
```
**Result:** All members tagged invisibly + custom message appears once

#### Using Emoji
```
/utag 🔔 Important announcement
```
**Result:** All members tagged invisibly + announcement with emoji

#### Check Progress
```
/tagstatus
```
**Response:**
```
🔄 Tagging in Progress
👥 Tagged: 23/45 (51%)
⏱️ Started: 10:30:15
```

#### Stop Currently Active Tagging
```
/cancel
```
**Result:** Stops the ongoing tagging process and reports statistics

### How It Works

1. **Admin sends command** - `/tagall [optional message]`
2. **Bot fetches members** - Retrieves list of all group members (excluding bots)
3. **Session created** - Tracks the tagging session with progress info
4. **Batches sent** - 
   - Groups members into batches of 8
   - Creates invisible mentions using ZWNJ character
   - Sends each batch as a separate message
   - Delays 1.5 seconds between batches to avoid flooding
5. **Notifications sent** - Telegram notifies all tagged members
6. **Session cleanup** - Session removed after completion or cancellation
7. **Log recorded** - Event logged in `tagger_logs` collection

### Technical Details

#### ZWNJ Implementation
Zero-Width Non-Joiner (U+200C) is a Unicode character that:
- Displays as invisible (no visual space)
- Separates mention links without breaking text
- Telegram still processes mentions normally
- Creates effective "invisible" notifications

**Formula:**
```
<a href='tg://user?id={user_id}'>‌</a> (separated by ZWNJ)
```

#### Batching Strategy

| Feature | Setting |
|---------|----------|
| Batch Size | 8 members per message |
| Batch Delay | 1.5 seconds |
| Max Members | 5,000 per tag |
| Simultaneous Processes | Max 2 per bot |
| Cooldown | 5 seconds per group |
| Session Timeout | 1 hour |

#### Message Flow

```
Admin: /tagall Important update
  ↓
Bot fetches 150 members
  ↓
Batch 1 (1-8):    [Msg] "Important update" + invisible mentions
  ↓ (delay 1.5s)
Batch 2 (9-16):   [Msg] invisible mentions only
  ↓ (delay 1.5s)
Batch 3 (17-24):  [Msg] invisible mentions only
  ... (continues)
  ↓
Completion Msg:   "✅ Tagging Complete! 150 tagged"
  ↓
Log Event:        Recorded in tagger_logs collection
```

### Error Handling

| Error | Handling |
|-------|----------|
| Bot not admin | Message: "❌ Bot needs admin permissions" |
| No members found | Message: "❌ No members found in group" |
| Already tagging | Message: "❌ Tagging already in progress" |
| In cooldown | Message: "⏳ Please wait before tagging again" |
| Fetch errors | Continues with available members, logs error |
| Large groups (5000+) | Limits to 5000, shows warning |

### Performance

- **Fetching members**: 5-15 seconds depending on group size
- **Per batch**: ~1-2 seconds (sending + delay)
- **150 members**: ~30 seconds total
- **1000 members**: ~3-5 minutes total

### Security & Safety

✅ **Admin-only** - All commands require group admin status
✅ **Bot-excluded** - Automatically skips other bots
✅ **Rate-limited** - 5-second cooldown prevents spam
✅ **Session-tracked** - Prevents concurrent tagging issues
✅ **Stale cleanup** - Auto-removes orphaned sessions after 1 hour
✅ **Flood protection** - Batch delays prevent Telegram limits
✅ **Logged** - All operations recorded in MongoDB

### Troubleshooting

#### Issue: "Bot needs admin permissions"
**Solution:** Ensure bot has admin rights in the group:
1. Remove the bot from the group
2. Add it back with admin permissions
3. Go to Admins → Assign admin → give it necessary permissions

#### Issue: Takes too long to tag large groups
**Solution:** This is normal!
- 1000 members = ~3-5 minutes
- This is intentional to avoid Telegram flood limits
- Use `/tagstatus` to track progress

#### Issue: Members not receiving notifications
**Solution:** Check:
1. Bot has admin permissions
2. Members haven't disabled group notifications
3. Bot is still active in the group
4. No Telegram API issues (rare)

#### Issue: Tagging process stalled
**Solution:**
1. Use `/tagstatus` to check if still active
2. Use `/cancel` to stop it
3. Wait 5 seconds (cooldown)
4. Try `/tagall` again

### Admin Considerations

**When to use:**
- Important announcements
- Urgent group meetings
- Critical updates
- Event reminders

**When NOT to use:**
- Spam or promotion
- Excessive tagging (multiple times per hour)
- Testing purposes
- High-frequency notifications

### Statistics & Monitoring

Check tagging activity:

```javascript
// Get all tagging events
db.tagger_logs.find({ chat_id: -1001234567890 })

// Count successful tags per group
db.tagger_logs.countDocuments({ status: "completed" })

// Get most active admins
db.tagger_logs.aggregate([
  { $group: { _id: "$admin_id", count: { $sum: 1 } } },
  { $sort: { count: -1 } }
])
```

---

## Files Modified

1. **config.py** - Added new database collections (tagger_logs)
2. **handlers/group_settings.py** - Group settings manager (750+ lines)
3. **handlers/groups.py** - Enhanced with feature checks
4. **handlers/admin.py** - Added group management commands
5. **handlers/tagger.py** - NEW: Invisible tagall system (500+ lines)
6. **handlers/__init__.py** - Imported new handlers
7. **main.py** - Imported new handler modules

---

## Testing Checklist

- [x] All syntax validated
- [x] All imports correct
- [ ] Test /group command in test group
- [ ] Test feature toggles
- [ ] Test keyword triggers
- [ ] Test auto-reactions
- [ ] Test auto-approve
- [ ] Test admin commands

---

## Support

For issues or questions, contact the admin or check the logging output when features are used.

---

**Version:** 1.0.0
**Date:** January 2024
**Compatibility:** Python 3.8+, Pyrogram 2.0+, MongoDB 4.0+
