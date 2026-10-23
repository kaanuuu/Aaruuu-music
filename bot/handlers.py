"""
Aaruu Music - Update Dispatcher & Router
Directs Telegram messages and callback queries to appropriate handlers with full error isolation.
"""

import asyncio
from typing import Any, Dict
from bot.callbacks import handle_callback_query
from bot.permissions import is_sudo
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

            text = str(message.get("text") or "")
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

            # Maintenance mode: only owner/sudo may use the bot (basic info commands stay open)
            if cmd.MAINTENANCE["on"] and not is_sudo(user_id) and clean_cmd not in ("start", "help", "ping", "id"):
                from bot.api import bot_api_client
                from utils.typography import to_bold_sans, to_small_caps
                await bot_api_client.send_message(
                    chat_id,
                    f"🔧 {to_bold_sans('UNDER MAINTENANCE')}\n\n"
                    f"{to_small_caps('aaruu music is being updated. please try again in a few minutes.')}",
                    reply_to_message_id=message.get("message_id"),
                )
                return

            # Music plays ONLY in group voice chats - never in DM
            if chat_id > 0 and clean_cmd in cmd.GROUP_ONLY_COMMANDS:
                await cmd.send_group_only_notice(message)
                return

            if clean_cmd == "start":
                await cmd.handle_start(message, args)
            elif clean_cmd == "help":
                await cmd.handle_help(message)
            elif clean_cmd == "play":
                await cmd.handle_play(message, args)
            elif clean_cmd == "vplay":
                await cmd.handle_vplay(message, args)
            elif clean_cmd in ("download", "down", "downloadsong", "getaudio"):
                await cmd.handle_download(message, args, is_video=False)
            elif clean_cmd in ("video", "downloadvideo", "getvideo"):
                await cmd.handle_download(message, args, is_video=True)
            elif clean_cmd == "song":
                await cmd.handle_download(message, args, is_video=False)
            elif clean_cmd == "search":
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
            elif clean_cmd == "setbanner":
                await cmd.handle_setbanner(message, args)
            elif clean_cmd == "ping":
                await cmd.handle_ping(message)
            elif clean_cmd == "stats":
                await cmd.handle_stats(message)
            elif clean_cmd == "shuffle":
                await cmd.handle_shuffle(message)
            elif clean_cmd == "autoplay":
                await cmd.handle_autoplay(message, args)
            elif clean_cmd in ("admincache", "reloadadmins", "reloadadmin"):
                await cmd.handle_admincache(message)
            elif clean_cmd in ("vctest", "testvc", "diagnostic"):
                await cmd.handle_vctest(message)
            elif clean_cmd in ("prev", "previous"):
                await cmd.handle_prev(message)
            elif clean_cmd == "forward":
                await cmd.handle_forward(message, args)
            elif clean_cmd == "rewind":
                await cmd.handle_rewind(message, args)
            elif clean_cmd == "remove":
                await cmd.handle_remove(message, args)
            elif clean_cmd == "playnext":
                await cmd.handle_playnext(message, args)
            elif clean_cmd == "recommend":
                await cmd.handle_recommend(message)
            elif clean_cmd == "lyrics":
                await cmd.handle_lyrics(message, args)
            elif clean_cmd == "history":
                await cmd.handle_history(message)
            elif clean_cmd == "top":
                await cmd.handle_top(message)
            elif clean_cmd == "id":
                await cmd.handle_id(message)
            # ---- Owner / admin panel ----
            elif clean_cmd in ("admin", "panel"):
                await cmd.handle_admin(message)
            elif clean_cmd == "sysinfo":
                await cmd.handle_sysinfo(message)
            elif clean_cmd == "restart":
                await cmd.handle_restart(message)
            elif clean_cmd == "addsudo":
                await cmd.handle_addsudo(message, args)
            elif clean_cmd == "delsudo":
                await cmd.handle_delsudo(message, args)
            elif clean_cmd == "sudolist":
                await cmd.handle_sudolist(message)
            elif clean_cmd == "maintenance":
                await cmd.handle_maintenance(message, args)
            elif clean_cmd == "clearcache":
                await cmd.handle_clearcache(message)
            elif clean_cmd == "groups":
                await cmd.handle_groups(message)
            elif clean_cmd == "activevc":
                await cmd.handle_activevc(message)
            elif clean_cmd == "leave":
                await cmd.handle_leave(message, args)
            else:
                # Unrecognized command: silently ignore instead of replying, so the bot
                # doesn't spam "command not recognized" in busy group chats where other
                # bots' commands or unrelated slash-messages are common.
                pass

    except Exception as e:
        logger.error("Error processing update %s: %s", update.get("update_id"), str(e), exc_info=True)
