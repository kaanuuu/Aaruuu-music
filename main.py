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
