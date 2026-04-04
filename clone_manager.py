import asyncio

from pyrogram import Client, filters
from pyrogram.types import Message

from config import API_ID, API_HASH, BOT_TOKEN, app, clones_col
from helpers import _bot_token_ctx, _clone_config_ctx

_active_clones:  dict[str, Client] = {}
_clone_configs:  dict[str, dict]   = {}   # token → full DB doc (in-memory cache)


def refresh_clone_config(token: str, doc: dict):
    """Update in-memory cache with new DB doc."""
    _clone_configs[token] = doc


async def reload_clone_config(token: str):
    """Re-read config from DB and update cache."""
    doc = await clones_col.find_one({"token": token})
    if doc:
        _clone_configs[token] = doc
    return _clone_configs.get(token)


def _make_token_injector(token: str):
    """Returns a handler function that sets both ContextVars for this clone."""
    async def _inject(client: Client, message: Message):
        _bot_token_ctx.set(token)
        _clone_config_ctx.set(_clone_configs.get(token))
    return _inject


async def _build_clone_client(token: str, session_name: str) -> Client:
    clone = Client(
        session_name,
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=token,
    )
    clone._clone_token = token

    # ── Inject token + config context at group=-99 (first handler) ────────
    from pyrogram.handlers import MessageHandler
    injector = _make_token_injector(token)
    clone.add_handler(MessageHandler(injector), group=-99)

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
        clone = await _build_clone_client(token, session_name)
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
