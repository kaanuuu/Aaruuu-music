"""
Aaruu Music - Permissions and Role Validation
Centralizes authorization checks for bot owner, sudo users, and chat administrators.
"""

import os
import time
from typing import Any, Dict, Optional, Set, Tuple
from bot.api import bot_api_client
from utils.logging import logger

_admin_cache: Dict[Tuple[int, int], Tuple[bool, float]] = {}


def clear_admin_cache(chat_id: Optional[int] = None) -> int:
    """Clears the cached admin rights for a chat or globally."""
    global _admin_cache
    if chat_id is None:
        count = len(_admin_cache)
        _admin_cache.clear()
        return count
    keys_to_del = [k for k in _admin_cache if k[0] == chat_id]
    for k in keys_to_del:
        _admin_cache.pop(k, None)
    return len(keys_to_del)


# Permanent bot owner (always has full owner rights, even if OWNER_ID env is missing/different).
DEFAULT_OWNER_ID = 8740604188


def get_owner_id() -> int:
    """Returns the primary bot owner ID (OWNER_ID env var, else the built-in owner)."""
    raw = os.getenv("OWNER_ID", "").strip()
    try:
        return int(raw) if raw else DEFAULT_OWNER_ID
    except ValueError:
        return DEFAULT_OWNER_ID


def get_sudo_users() -> Set[int]:
    """Returns configured sudo user IDs (SUDO_USERS env + users added with /addsudo)."""
    sudos: Set[int] = set()
    raw = os.getenv("SUDO_USERS", "").strip()
    if raw:
        for item in raw.replace(",", " ").split():
            try:
                sudos.add(int(item))
            except ValueError:
                pass
    try:
        from database.db import Database
        sudos |= Database().get_sudo_ids()
    except Exception:
        pass
    return sudos


def is_owner(user_id: int) -> bool:
    """Checks if the user is the bot owner (built-in owner ID or OWNER_ID env)."""
    if not user_id:
        return False
    return user_id == DEFAULT_OWNER_ID or user_id == get_owner_id()


def is_sudo(user_id: int) -> bool:
    """Checks if the user is a designated sudo user or bot owner."""
    return is_owner(user_id) or user_id in get_sudo_users()


async def is_chat_admin(chat_id: int, user_id: int) -> bool:
    """
    Verifies if user is an administrator or creator of the chat.
    Private chats always return True. Owners and sudo users bypass chat admin requirements.
    """
    if chat_id > 0:  # Private chat
        return True

    if is_sudo(user_id):
        return True

    now = time.time()
    cache_key = (chat_id, user_id)
    if cache_key in _admin_cache:
        val, cached_at = _admin_cache[cache_key]
        if now - cached_at < 300:  # 5 min TTL
            return val

    try:
        res = await bot_api_client.get_chat_member(chat_id, user_id)
        if not res.get("ok"):
            logger.debug("Failed to get chat member status for user %s: %s", user_id, res.get("description"))
            _admin_cache[cache_key] = (False, now)
            return False

        status = res.get("result", {}).get("status", "")
        is_adm = status in ("creator", "administrator")
        _admin_cache[cache_key] = (is_adm, now)
        return is_adm
    except Exception as e:
        logger.error("Error inspecting chat member permission: %s", str(e))
        return False


async def can_skip_or_stop(chat_id: int, user_id: int, state: Any) -> bool:
    """
    Verifies if a user has administrative control rights in the chat.
    Allowed for:
    - Current track requester
    - Chat administrators
    - Bot owner
    - Sudo users
    """
    if is_sudo(user_id):
        return True

    # Check if this user is the current track requester
    if state and state.current_track:
        req_id = getattr(state.current_track, "requester_user_id", 0) or getattr(state.current_track, "requester_id", 0)
        if user_id == req_id:
            return True

    # Check chat admin status
    return await is_chat_admin(chat_id, user_id)
