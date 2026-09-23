"""
Aaruu Music - Main Entry Point & 24/7 Long Polling Worker
Ensures resilient, continuous operation on Railway with automatic fault recovery.
"""

import asyncio
import os
import signal
import sys
# 0. Automatically discover and patch site-packages from all virtual environments
import glob
for _pattern in [
    "/opt/venv/lib/python*/site-packages",
    "/app/.venv/lib/python*/site-packages",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv/lib/python*/site-packages"),
    "/root/.local/lib/python*/site-packages",
    os.path.expanduser("~/.local/lib/python*/site-packages"),
]:
    for _p in glob.glob(_pattern):
        if _p not in sys.path:
            sys.path.insert(0, _p)

# 1. Load environment variables natively (zero external dependency on python-dotenv)
def _load_env():
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(_env_path):
        try:
            with open(_env_path, "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _v = _line.split("=", 1)
                        _k = _k.strip()
                        _v = _v.strip().strip("'\"")
                        if _k:
                            os.environ.setdefault(_k, _v)
        except Exception:
            pass

_load_env()

# -------------------------------------------------------------
# Early Pyrogram v2 & PyTgCalls Compatibility & 64-bit ID Patches
# -------------------------------------------------------------
try:
    import pyrogram
    import pyrogram.errors
    import pyrogram.utils

    # 1. Missing TL Types & Classes for PyTgCalls (e.g. InputGroupCallSlug)
    try:
        import pyrogram.raw.types
        import pyrogram.raw.base

        class _InputGroupCallSlug:
            ID = 0xDBBA8818
            QUALNAME = "types.InputGroupCallSlug"

            def __init__(self, slug: str = ""):
                self.slug = slug

            @classmethod
            def read(cls, b, *args, **kwargs):
                return cls()

            def write(self, *args, **kwargs):
                return b""

        setattr(pyrogram.raw.types, "InputGroupCallSlug", _InputGroupCallSlug)
        if hasattr(pyrogram.raw, "base"):
            setattr(pyrogram.raw.base, "InputGroupCallSlug", _InputGroupCallSlug)

        def _make_dummy_tl(name: str):
            class _DynamicTL:
                ID = 0
                QUALNAME = f"types.{name}"

                def __init__(self, *args, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)

                @classmethod
                def read(cls, *args, **kwargs):
                    return cls()

                def write(self, *args, **kwargs):
                    return b""

            _DynamicTL.__name__ = name
            return _DynamicTL

        # Dynamic fallback for any missing TL types imported by PyTgCalls
        def _patched_raw_types_getattr(name: str):
            cls = _make_dummy_tl(name)
            setattr(pyrogram.raw.types, name, cls)
            return cls

        pyrogram.raw.types.__getattr__ = _patched_raw_types_getattr

        if hasattr(pyrogram.raw, "base"):
            def _patched_raw_base_getattr(name: str):
                cls = _make_dummy_tl(name)
                setattr(pyrogram.raw.base, name, cls)
                return cls
            pyrogram.raw.base.__getattr__ = _patched_raw_base_getattr

        import inspect
        import importlib
        import pkgutil

        def _make_safe_constructor(cls):
            if not isinstance(cls, type):
                return
            if not hasattr(cls, "public_key"):
                setattr(cls, "public_key", None)

            orig_getattr = getattr(cls, "__getattr__", None)
            def _safe_getattr(self, name):
                if name in ("public_key", "block", "video_stopped", "muted", "invite_hash"):
                    return None
                if orig_getattr:
                    return orig_getattr(self, name)
                raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
            cls.__getattr__ = _safe_getattr

            if not hasattr(cls, "__init__"):
                return
            orig_init = cls.__init__
            if getattr(orig_init, "_is_safe_patched", False):
                return
            try:
                sig = inspect.signature(orig_init)
                has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                param_keys = set(sig.parameters.keys())

                def _safe_init(self, *args, **kwargs):
                    pub_key = kwargs.pop("public_key", None)
                    if not has_varkw:
                        extra_keys = set(kwargs.keys()) - param_keys
                        if extra_keys:
                            for k in list(extra_keys):
                                val = kwargs.pop(k, None)
                                setattr(self, k, val)
                    res = orig_init(self, *args, **kwargs)
                    if pub_key is not None:
                        self.public_key = pub_key
                    return res

                _safe_init._is_safe_patched = True
                cls.__init__ = _safe_init
            except Exception:
                pass

        subpackages = [
            "pyrogram.raw.functions.phone",
            "pyrogram.raw.functions.channels",
            "pyrogram.raw.functions.messages",
            "pyrogram.raw.functions.account",
            "pyrogram.raw.functions.users",
            "pyrogram.raw.types",
            "pyrogram.raw.types.phone",
            "pyrogram.raw.base",
            "pyrogram.raw.base.phone",
        ]
        for pkg in subpackages:
            try:
                mod = importlib.import_module(pkg)
                for attr in dir(mod):
                    _make_safe_constructor(getattr(mod, attr, None))
            except Exception:
                pass

        try:
            if hasattr(pyrogram.raw, "__path__"):
                for _, modname, _ in pkgutil.walk_packages(pyrogram.raw.__path__, pyrogram.raw.__name__ + "."):
                    try:
                        mod = importlib.import_module(modname)
                        for attr in dir(mod):
                            _make_safe_constructor(getattr(mod, attr, None))
                    except Exception:
                        pass
        except Exception:
            pass

        try:
            if hasattr(pyrogram.raw, "all") and hasattr(pyrogram.raw.all, "layer"):
                for cls in pyrogram.raw.all.layer.values():
                    _make_safe_constructor(cls)
        except Exception:
            pass
    except Exception:
        pass

    # 2. Patch missing legacy errors that PyTgCalls imports from pyrogram.errors
    for _name in (
        "GroupcallForbidden",
        "GroupcallInvalid",
        "GroupcallAlreadyStarted",
        "GroupcallNotFound",
        "GroupCallNotFound",
        "GroupCallInvalid",
        "NoActiveGroupCall",
        "UserAlreadyParticipant",
        "PhoneCallDiscarded",
    ):
        if not hasattr(pyrogram.errors, _name):
            _exc = type(_name, (Exception,), {})
            setattr(pyrogram.errors, _name, _exc)
            try:
                import pyrogram.errors.exceptions
                setattr(pyrogram.errors.exceptions, _name, _exc)
            except Exception:
                pass

    def _patched_errors_getattr(name: str):
        _exc = type(name, (Exception,), {})
        setattr(pyrogram.errors, name, _exc)
        return _exc

    pyrogram.errors.__getattr__ = _patched_errors_getattr

    # 3. Modern Telegram 64-bit channel IDs patch (e.g. -1003781054777, -1003952024411)
    if hasattr(pyrogram.utils, "MIN_CHANNEL_ID"):
        pyrogram.utils.MIN_CHANNEL_ID = -10099999999999
    if hasattr(pyrogram.utils, "MAX_CHANNEL_ID"):
        pyrogram.utils.MAX_CHANNEL_ID = -1000000000000

    def _safe_get_peer_type(peer_id: int) -> str:
        if peer_id < 0:
            if peer_id <= -1000000000000:
                return "channel"
            return "chat"
        elif peer_id > 0:
            return "user"
        raise ValueError(f"Peer id invalid: {peer_id}")

    pyrogram.utils.get_peer_type = _safe_get_peer_type

    def _safe_get_channel_id(peer_id: int) -> int:
        if str(peer_id).startswith("-100"):
            return int(peer_id)
        return int(f"-100{peer_id}")

    pyrogram.utils.get_channel_id = _safe_get_channel_id

    # 4. Prevent un-cached peer updates from crashing Pyrogram's handle_updates task
    if hasattr(pyrogram, "Client"):
        _orig_handle_updates = getattr(pyrogram.Client, "handle_updates", None)
        if _orig_handle_updates:
            async def _safe_handle_updates(self, updates):
                try:
                    await _orig_handle_updates(self, updates)
                except Exception as exc:
                    exc_str = str(exc).lower()
                    if "peer_id_invalid" in exc_str or "id not found" in exc_str or "peeridinvalid" in type(exc).__name__.lower():
                        pass
                    else:
                        pass

            pyrogram.Client.handle_updates = _safe_handle_updates

    # 5. Patch Pyrogram Raw TL Functions (phone.JoinGroupCall, etc.) to safely absorb Layer 180+ fields like 'public_key'
    import inspect

    def _make_safe_constructor(cls):
        if not isinstance(cls, type):
            return
        # 1. Guarantee public_key attribute exists on the class
        if not hasattr(cls, "public_key"):
            setattr(cls, "public_key", None)

        # 2. Add safe __getattr__ on the class for any other missing TL fields
        orig_getattr = getattr(cls, "__getattr__", None)
        def _safe_getattr(self, name):
            if name in ("public_key", "block", "video_stopped", "muted", "invite_hash"):
                return None
            if orig_getattr:
                return orig_getattr(self, name)
            raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")
        cls.__getattr__ = _safe_getattr

        # 3. Patch __init__ to absorb extra kwargs and initialize public_key
        if not hasattr(cls, "__init__"):
            return
        orig_init = cls.__init__
        if getattr(orig_init, "_is_safe_patched", False):
            return
        try:
            sig = inspect.signature(orig_init)
            has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

            def _safe_init(self, *args, **kwargs):
                self.public_key = kwargs.pop("public_key", None)
                if not has_varkw:
                    extra_keys = set(kwargs.keys()) - set(sig.parameters.keys())
                    if extra_keys:
                        for k in list(extra_keys):
                            val = kwargs.pop(k, None)
                            setattr(self, k, val)
                return orig_init(self, *args, **kwargs)

            _safe_init._is_safe_patched = True
            cls.__init__ = _safe_init
        except Exception:
            pass

    if hasattr(pyrogram, "raw") and hasattr(pyrogram.raw, "functions"):
        import pyrogram.raw.functions as all_raw_funcs
        for sub_name in dir(all_raw_funcs):
            sub_mod = getattr(all_raw_funcs, sub_name, None)
            if sub_mod and hasattr(sub_mod, "__dict__"):
                for attr_name in dir(sub_mod):
                    _make_safe_constructor(getattr(sub_mod, attr_name, None))

    if hasattr(pyrogram, "raw") and hasattr(pyrogram.raw, "types"):
        import pyrogram.raw.types as all_raw_types
        for attr_name in dir(all_raw_types):
            _make_safe_constructor(getattr(all_raw_types, attr_name, None))
except Exception:
    pass

from bot.api import bot_api_client
from bot.commands import COMMANDS_REGISTRY
from bot.handlers import process_update
from database.db import Database
from player.manager import player_manager
from player.voice_chat import voice_assistant
from utils.logging import logger

db = Database()
_SHUTDOWN_EVENT = asyncio.Event()


def handle_stop_signals():
    """Captures SIGINT and SIGTERM for graceful worker teardown on Railway."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _SHUTDOWN_EVENT.set)
        except (NotImplementedError, RuntimeError):
            pass


async def run_keep_alive_heartbeat() -> None:
    """
    24/7 Keep-Alive task that fires every 10 minutes (600s).
    Pings Telegram API and keeps container socket active so free/hosted workers do not idle to sleep.
    """
    logger.info("24/7 Keep-alive background heartbeat activated (Interval: 10 minutes).")
    while not _SHUTDOWN_EVENT.is_set():
        try:
            # 600 seconds = 10 minutes (checked in 5s intervals for prompt shutdown responsiveness)
            for _ in range(120):
                if _SHUTDOWN_EVENT.is_set():
                    return
                await asyncio.sleep(5)

            if _SHUTDOWN_EVENT.is_set():
                return

            res = await bot_api_client.get_me()
            if res.get("ok"):
                logger.info("[24/7 PING] 10-minute keep-alive heartbeat successful. Bot is active.")
            else:
                logger.warning("[24/7 PING] Keep-alive ping Telegram notice: %s", res.get("description"))

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("[24/7 PING] Keep-alive ping heartbeat caught exception: %s", str(e))



async def run_bot():
    """Initializes all subsystems and begins the resilient polling loop."""
    print("Starting Aaruu Music...")
    logger.info("Initializing Aaruu Music Telegram Engine...")

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        logger.error(
            "FATAL: BOT_TOKEN is missing. Please configure BOT_TOKEN in your Railway environment variables or .env file."
        )
        print("Telegram API: FAILED (Missing BOT_TOKEN)")
        sys.exit(1)

    bot_api_client.set_token(token)

    # Verify Telegram API connectivity
    me_resp = await bot_api_client.get_me()
    if not me_resp.get("ok"):
        logger.error("Failed to connect to Telegram API: %s", me_resp.get("description"))
        print(f"Telegram API: FAILED ({me_resp.get('description')})")
        sys.exit(1)

    bot_user = me_resp.get("result", {})
    username = bot_user.get("username", "UnknownBot")
    print("Telegram API: OK")
    logger.info("Connected as @%s (ID: %s)", username, bot_user.get("id"))

    # Initialize Database
    try:
        await db.init()
        print("Database: OK")
    except Exception as e:
        logger.error("Failed to initialize database: %s", str(e))
        print("Database: FAILED")
        sys.exit(1)

    print("Rich Messages: enabled")

    # Initialize Player Manager & Voice Assistant
    if player_manager:
        print("Player manager: OK")

    await voice_assistant.start()

    # Register Bot commands with BotFather API (Commands menu pops up on '/')
    try:
        cmd_res = await bot_api_client.set_my_commands(COMMANDS_REGISTRY)
        if cmd_res.get("ok"):
            logger.info("Bot commands successfully registered with Telegram.")
    except Exception as e:
        logger.warning("Could not register bot commands: %s", str(e))

    # 24/7 Keep-alive heartbeat task (pings every 10 minutes to prevent sleep)
    heartbeat_task = asyncio.create_task(run_keep_alive_heartbeat())

    # Delete any active webhook so long-polling getUpdates can function properly
    try:
        del_res = await bot_api_client.delete_webhook(drop_pending_updates=True)
        if del_res.get("ok"):
            logger.info("Cleared prior Telegram webhook. Clean long-polling ready.")
    except Exception as e:
        logger.warning("Could not auto-clear webhook: %s", str(e))

    print("Polling started")
    print("Aaruu Music is running.")
    logger.info("Aaruu Music worker is active and awaiting commands.")

    offset = None
    consecutive_errors = 0

    while not _SHUTDOWN_EVENT.is_set():
        try:
            updates_resp = await bot_api_client.get_updates(offset=offset, timeout=25)

            if not updates_resp.get("ok"):
                description = updates_resp.get("description", "Unknown Telegram error")
                error_code = updates_resp.get("error_code")
                consecutive_errors += 1

                # If webhook conflict occurs, delete webhook immediately and resume polling
                if "deleteWebhook" in description or "webhook is active" in description.lower():
                    logger.info("Active webhook detected. Purging webhook to enable getUpdates...")
                    try:
                        await bot_api_client.delete_webhook(drop_pending_updates=True)
                    except Exception as e:
                        logger.warning("Failed to purge webhook: %s", str(e))
                    consecutive_errors = 0
                    await asyncio.sleep(1)
                    continue

                # Rate limiting
                if error_code == 429:
                    retry_after = updates_resp.get("parameters", {}).get("retry_after", 5)
                    logger.warning("Telegram 429 RetryAfter received: sleeping %ss", retry_after)
                    await asyncio.sleep(retry_after)
                    continue

                backoff = min(30, 2**min(consecutive_errors, 5))
                logger.warning(
                    "Telegram error during polling: %s. Reconnecting in %ss...",
                    description,
                    backoff,
                )
                await asyncio.sleep(backoff)
                continue

            consecutive_errors = 0
            results = updates_resp.get("result", [])

            for upd in results:
                upd_id = upd.get("update_id")
                if upd_id is not None:
                    offset = upd_id + 1
                # Process update concurrently without blocking the main polling loop
                asyncio.create_task(process_update(upd))

        except asyncio.CancelledError:
            break
        except Exception as e:
            consecutive_errors += 1
            backoff = min(30, 2**min(consecutive_errors, 5))
            logger.error(
                "Unexpected network or runtime exception in polling loop: %s. Resuming in %ss...",
                str(e),
                backoff,
            )
            await asyncio.sleep(backoff)

    logger.info("Shutting down Aaruu Music worker...")
    await bot_api_client.close()
    await db.close()
    logger.info("Aaruu Music stopped cleanly.")


def main():
    """Main application launcher."""
    handle_stop_signals()
    try:
        asyncio.run(run_bot())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Aaruu Music process terminated.")


if __name__ == "__main__":
    main()
