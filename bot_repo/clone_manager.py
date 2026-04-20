import asyncio

from pyrogram import Client, filters, StopPropagation
from pyrogram.types import Message

from config import API_ID, API_HASH, BOT_TOKEN, app, clones_col
from helpers import _bot_token_ctx, _clone_config_ctx

_active_clones:  dict[str, Client] = {}
_clone_configs:  dict[str, dict]   = {}   # token → full DB doc (in-memory cache)

# ── Main bot presence tracking ─────────────────────────────────────────────
# Two-layer:
#   _main_bot_groups  — fast in-process set, filled as main bot processes msgs
#   _main_bot_presence — slower cache filled by API lookup on first miss
#
# Clone priority guard checks _main_bot_groups first (no I/O), then falls
# back to an API call using the MAIN bot's own session (reliable: a bot can
# always look up its own membership status).
_main_bot_groups:   set[int]       = set()   # groups main bot is known-present in
_main_bot_presence: dict[int, bool] = {}     # chat_id → True/False (API cache)


def invalidate_presence_cache(chat_id: int | None = None):
    """Clear the presence cache for a specific group (or all groups)."""
    if chat_id is None:
        _main_bot_presence.clear()
        _main_bot_groups.clear()
    else:
        _main_bot_presence.pop(chat_id, None)
        _main_bot_groups.discard(chat_id)


def main_bot_mark_active_in(chat_id: int):
    """Called whenever the main bot handles a message in a group.
    Keeps _main_bot_groups up-to-date with no API cost.
    """
    _main_bot_groups.add(chat_id)
    _main_bot_presence[chat_id] = True


def main_bot_mark_left(chat_id: int):
    """Called when the main bot leaves or is removed from a group."""
    _main_bot_groups.discard(chat_id)
    _main_bot_presence[chat_id] = False


async def _is_main_bot_in_chat(clone_client: Client, chat_id: int) -> bool:
    """True if the main bot is an active member of chat_id.

    Priority:
      1. In-process set  (_main_bot_groups)  — O(1), no network
      2. Presence cache  (_main_bot_presence) — already-fetched result
      3. API lookup via the MAIN bot's own Pyrogram session — always reliable
         because a bot can always retrieve its own membership status.
         (Using the clone bot's session fails when clone is not an admin.)
    """
    # ── Fast path: already known present ──────────────────────────────────
    if chat_id in _main_bot_groups:
        return True

    # ── Cache path: previously resolved ───────────────────────────────────
    if chat_id in _main_bot_presence:
        return _main_bot_presence[chat_id]

    # ── Slow path: ask Telegram using the main bot's own session ──────────
    import config as _cfg
    main_id = getattr(_cfg, "MAIN_BOT_ID", 0)
    if not main_id:
        return False           # main bot ID not cached yet — don't block clones

    try:
        from pyrogram import enums as _enums
        # Use the MAIN APP's client — it can always check its own status
        member = await _cfg.app.get_chat_member(chat_id, main_id)
        present = member.status not in (
            _enums.ChatMemberStatus.LEFT,
            _enums.ChatMemberStatus.BANNED,
        )
    except Exception:
        # UserNotParticipant, ChatForbidden, etc. → main bot is NOT in the chat
        present = False

    _main_bot_presence[chat_id] = present
    if present:
        _main_bot_groups.add(chat_id)
    return present


def refresh_clone_config(token: str, doc: dict):
    """Update in-memory cache with new DB doc."""
    _clone_configs[token] = doc


async def reload_clone_config(token: str):
    """Re-read config from DB, update in-memory cache AND client attribute."""
    doc = await clones_col.find_one({"token": token})
    if doc:
        _clone_configs[token] = doc
        # Also update the client object attribute so handlers see fresh config
        clone = _active_clones.get(token)
        if clone:
            clone._clone_config = doc
    return _clone_configs.get(token)


def _make_token_injector(token: str):
    """Returns a handler function that sets both ContextVars for this clone."""
    async def _inject(client: Client, message: Message):
        _bot_token_ctx.set(token)
        _clone_config_ctx.set(_clone_configs.get(token))
    return _inject


async def _main_bot_priority_guard(client: Client, message: Message):
    """If the main bot is present in this group, the clone bot stays silent.
    Registered at group=-98 (runs right after the injector at group=-99).
    Only applies to group/supergroup chats — private chats are unaffected.
    """
    chat = message.chat
    if chat is None:
        return
    chat_type = getattr(chat, "type", None)
    if chat_type is None:
        return
    type_value = chat_type.value if hasattr(chat_type, "value") else str(chat_type)
    if type_value not in ("group", "supergroup"):
        return
    if await _is_main_bot_in_chat(client, chat.id):
        raise StopPropagation


async def _build_clone_client(token: str, session_name: str, doc: dict = None) -> Client:
    clone = Client(
        session_name,
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=token,
    )
    # ── Store config & token directly on the client object (most reliable) ─
    clone._clone_token  = token
    clone._bot_token    = token
    clone._clone_config = doc or _clone_configs.get(token)

    from pyrogram.handlers import MessageHandler

    # ── group=-99: Inject token + config ContextVars ───────────────────────
    injector = _make_token_injector(token)
    clone.add_handler(MessageHandler(injector), group=-99)

    # ── group=-98: Main bot priority guard ────────────────────────────────
    # If main bot is in a group, clone bot does nothing there.
    clone.add_handler(MessageHandler(_main_bot_priority_guard), group=-98)

    # ── Copy all handlers from main app ───────────────────────────────────
    for group_id in sorted(app.dispatcher.groups.keys()):
        for handler in app.dispatcher.groups[group_id]:
            clone.add_handler(handler, group=group_id)

    return clone


async def start_clone(token: str, name: str, doc: dict = None) -> bool:
    if token in _active_clones:
        return False
    try:
        # Cache config before starting
        if doc:
            _clone_configs[token] = doc
        else:
            await reload_clone_config(token)

        session_name = f"clone_{abs(hash(token)) % 10**8}"
        clone = await _build_clone_client(token, session_name, doc=_clone_configs.get(token))
        await clone.start()
        _active_clones[token] = clone
        print(f"[CLONE] ✅ Started: {name}")
        return True
    except Exception as e:
        print(f"[CLONE] ❌ Failed to start {name}: {e}")
        return False


async def stop_clone(token: str) -> bool:
    clone = _active_clones.pop(token, None)
    _clone_configs.pop(token, None)
    if clone:
        try:
            await clone.stop()
        except Exception:
            pass
        print(f"[CLONE] 🛑 Stopped: {token[:15]}...")
        return True
    return False


# ── Cache invalidation when main bot joins/leaves a group ─────────────────
# This handler runs on the main bot (app), not clone clients.

@app.on_chat_member_updated()
async def _main_bot_group_membership_changed(client: Client, update):
    """When the main bot's own membership changes, update the presence tracking
    so clone bots immediately respect / stop respecting the priority guard.
    """
    import config as _cfg
    from pyrogram import enums as _enums
    main_id = getattr(_cfg, "MAIN_BOT_ID", 0)
    if not main_id:
        return
    new = update.new_chat_member
    if not (new and new.user and new.user.id == main_id):
        return
    chat_id = update.chat.id if update.chat else None
    if not chat_id:
        return
    is_left = new.status in (_enums.ChatMemberStatus.LEFT, _enums.ChatMemberStatus.BANNED)
    if is_left:
        main_bot_mark_left(chat_id)
        print(f"[CLONE_GUARD] Main bot left/banned group {chat_id} — clone bots now active")
        try:
            await clones_col.database["known_groups"].delete_one({"chat_id": chat_id})
        except Exception:
            pass
    else:
        main_bot_mark_active_in(chat_id)
        print(f"[CLONE_GUARD] Main bot joined group {chat_id} — clone bots silenced")
        try:
            await clones_col.database["known_groups"].update_one(
                {"chat_id": chat_id},
                {"$set": {"chat_id": chat_id, "main_bot": True}},
                upsert=True,
            )
        except Exception:
            pass


async def start_all_clones():
    """Called at startup — loads all active clones from MongoDB."""
    docs = await clones_col.find({"active": True}).to_list(length=100)
    if not docs:
        print("[CLONE] No active clones.")
        return
    print(f"[CLONE] Starting {len(docs)} clone(s)...")
    for doc in docs:
        token = doc["token"]
        name  = doc.get("name", token[:15])
        await start_clone(token, name, doc=doc)


def get_active_clones() -> dict:
    return {token: client for token, client in _active_clones.items()}
