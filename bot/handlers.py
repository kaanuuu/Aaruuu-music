"""
Aaruu Music - Update Dispatcher & Router
Directs Telegram messages and callback queries to appropriate handlers with full error isolation.
"""

import asyncio
from typing import Any, Dict
from bot.callbacks import handle_callback_query
import bot.commands as cmd
from utils.logging import logger


async def process_update(update: Dict[str, Any]) -> None:
    """Entry point for routing raw Telegram updates."""
    try:
        if "callback_query" in update:
            cq = update["callback_query"]
            chat_info = cq.get("message", {}).get("chat", {})
            if chat_info.get("id"):
                title = chat_info.get("title") or chat_info.get("username", "")
                asyncio.create_task(
                    cmd.db.register_chat(
                        chat_info["id"], title, chat_info.get("type", "")
                    )
                )
            await handle_callback_query(update)
            return

        if "message" in update:
            message = update["message"]
            chat_info = message.get("chat", {})
            chat_id = chat_info.get("id", 0)
            user_id = message.get("from", {}).get("id", 0)

            # Auto-register every chat & user for broadcast
            if chat_id:
                title = (
                    chat_info.get("title")
                    or chat_info.get("username")
                    or message.get("from", {}).get("first_name", "")
                )
                asyncio.create_task(
                    cmd.db.register_chat(chat_id, title, chat_info.get("type", ""))
                )

            text = message.get("text", "")
            if not text.startswith("/"):
                return

            # Enforce permanent block by bot owner
            if user_id and cmd.db.is_user_blocked(user_id):
                logger.info("Blocked user %d attempted command '%s'", user_id, text)
                if chat_id > 0:  # In private chats, notify the user
                    from bot.api import bot_api_client
                    from utils.typography import to_bold_sans
                    await bot_api_client.send_message(
                        chat_id,
                        f"🚫 {to_bold_sans('ACCESS DENIED')}\n\n"
                        "You have been permanently blocked by the bot owner and cannot use Aaruu Music.",
                    )
                # In groups, drop silently to prevent spam
                return

            # Extract command and args
            parts = text.split(maxsplit=1)
            raw_cmd = parts[0][1:]
            # Strip bot username suffix e.g. /play@AaruuMusicBot -> play
            clean_cmd = raw_cmd.split("@")[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if clean_cmd == "start":
                await cmd.handle_start(message, args)
            elif clean_cmd == "help":
                await cmd.handle_help(message)
            elif clean_cmd in ("play", "vplay"):
                await cmd.handle_play(message, args)
            elif clean_cmd in ("search", "song"):
                await cmd.handle_search(message, args)
            elif clean_cmd == "pause":
                await cmd.handle_pause(message)
            elif clean_cmd == "resume":
                await cmd.handle_resume(message)
            elif clean_cmd == "replay":
                await cmd.handle_replay(message)
            elif clean_cmd == "skip":
                await cmd.handle_skip(message)
            elif clean_cmd == "queue":
                await cmd.handle_queue(message)
            elif clean_cmd == "stop":
                await cmd.handle_stop(message)
            elif clean_cmd == "clear":
                await cmd.handle_clear(message)
            elif clean_cmd == "loop":
                await cmd.handle_loop(message, args)
            elif clean_cmd == "seek":
                await cmd.handle_seek(message, args)
            elif clean_cmd == "volume":
                await cmd.handle_volume(message, args)
            elif clean_cmd == "nowplaying":
                await cmd.handle_nowplaying(message)
            elif clean_cmd in ("vc", "vcstatus", "status"):
                await cmd.handle_vc_status(message)
            elif clean_cmd == "settings":
                await cmd.handle_settings(message)
            elif clean_cmd == "block":
                await cmd.handle_block(message, args)
            elif clean_cmd == "unblock":
                await cmd.handle_unblock(message, args)
            elif clean_cmd in ("blocked", "blockedusers"):
                await cmd.handle_blocked(message)
            elif clean_cmd in ("broadcast", "gcast"):
                await cmd.handle_broadcast(message, args)
            elif clean_cmd == "ping":
                await cmd.handle_ping(message)
            elif clean_cmd == "stats":
                await cmd.handle_stats(message)
            elif clean_cmd == "shuffle":
                await cmd.handle_shuffle(message)
            elif clean_cmd in ("admincache", "reloadadmins", "reloadadmin"):
                await cmd.handle_admincache(message)
            elif clean_cmd in ("vctest", "testvc", "diagnostic"):
                await cmd.handle_vctest(message)

    except Exception as e:
        logger.error("Error processing update %s: %s", update.get("update_id"), str(e), exc_info=True)
