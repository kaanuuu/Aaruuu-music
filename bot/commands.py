"""
Aaruu Music - Command Handlers
Implements all bot commands with clean, aesthetic Unicode typography
(sans-serif bold, small caps) without raw HTML tags.
Restricts and hides owner-only commands from regular users.
"""

import os
import time
from typing import Any, Dict, List, Optional, Union, Tuple
from bot.api import bot_api_client
from bot.permissions import is_chat_admin, is_owner, is_sudo, can_skip_or_stop
from bot.rich_help import build_start_rich_message
from bot.rich_player import build_player_rich_ui, build_player_rich_message, build_queue_rich_message
from database.db import Database
from player.extractor import MediaExtractor
from player.manager import player_manager
from player.voice_chat import voice_assistant
from utils.escaping import escape_html, sanitize_text
from utils.formatting import format_time
from utils.logging import logger
from utils.typography import to_bold_sans, to_small_caps

extractor = MediaExtractor()
db = Database()
BOT_START_TIME = time.time()

SEARCH_CACHE: Dict[int, List[Any]] = {}

# Only public user commands registered with Telegram BotFather menu
COMMANDS_REGISTRY: List[Dict[str, str]] = [
    {"command": "play", "description": "Play or queue a song or URL"},
    {"command": "vplay", "description": "Stream video directly in Voice Chat"},
    {"command": "search", "description": "Search songs with 1-5 selection buttons"},
    {"command": "autoplay", "description": "Toggle song recommendation mode (on/off)"},
    {"command": "pause", "description": "Pause current playback"},
    {"command": "resume", "description": "Resume paused audio"},
    {"command": "replay", "description": "Replay current song from 0:00"},
    {"command": "skip", "description": "Skip to next queued track"},
    {"command": "stop", "description": "Stop playback and clear queue"},
    {"command": "queue", "description": "Show queued songs & up-next"},
    {"command": "shuffle", "description": "Shuffle tracks in queue"},
    {"command": "clear", "description": "Clear all queued tracks"},
    {"command": "loop", "description": "Loop mode (off, track, queue)"},
    {"command": "seek", "description": "Seek seconds into song (e.g. /seek 60)"},
    {"command": "volume", "description": "Adjust volume (1-100)"},
    {"command": "nowplaying", "description": "Show interactive player"},
    {"command": "vc", "description": "Check Voice Chat connection & audio status"},
    {"command": "settings", "description": "View chat music settings"},
    {"command": "ping", "description": "Check bot latency and uptime"},
    {"command": "help", "description": "Interactive help and guides"},
    {"command": "start", "description": "Start Aaruu Music guide"},
]


async def handle_start(message: Dict[str, Any], args: str = "") -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    first_name = message.get("from", {}).get("first_name", "Friend")

    # In group chats, show a concise rich greeting with quick-actions
    if chat_id < 0:
        group_welcome = (
            f"👋 {to_bold_sans(f'HELLO {first_name.upper()}')}!\n\n"
            f"⚡ {to_bold_sans('AARUU MUSIC')} is active & ready in this group.\n\n"
            f"🎵 {to_bold_sans('STREAMING COMMANDS')}:\n"
            f"• /play <song> - Stream music in Voice Chat\n"
            f"• /vplay <video> - Stream video in Voice Chat\n"
            f"• /search <song> - Search songs with buttons\n"
            f"• /pause | /resume | /skip | /replay | /stop\n"
            f"• /queue | /shuffle | /clear | /loop | /volume\n"
            f"• /nowplaying - Interactive player controller\n\n"
            f"💡 Type / in chat to browse all user commands in the Telegram menu!"
        )
        group_rich = {
            "type": "rich_message",
            "blocks": [
                {
                    "type": "heading",
                    "text": to_bold_sans("AARUU MUSIC"),
                    "size": 1,
                },
                {
                    "type": "paragraph",
                    "text": group_welcome,
                },
                {
                    "type": "buttons",
                    "buttons": [
                        {
                            "text": "+ " + to_small_caps("add to your group"),
                            "url": "https://t.me/Aaruu_musicbot?startgroup=true",
                        },
                        {
                            "text": "≡ " + to_small_caps("support"),
                            "url": "https://t.me/wzzkaanu",
                        },
                    ],
                },
                {
                    "type": "buttons",
                    "buttons": [
                        {
                            "text": "≡ " + to_small_caps("help & commands"),
                            "url": "https://t.me/Aaruu_musicbot?start=help",
                        },
                    ],
                },
            ],
        }
        await bot_api_client.send_rich_message(chat_id, group_rich)
        return

    # In private chat, show full interactive documentation guide
    section = "getting_started" if "help" in args.lower() else "home"
    rich_msg = build_start_rich_message(section, is_owner=is_sudo(from_id))
    await bot_api_client.send_rich_message(chat_id, rich_msg)


async def handle_help(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    rich_msg = build_start_rich_message("home", is_owner=is_sudo(from_id))
    await bot_api_client.send_rich_message(chat_id, rich_msg)


async def handle_search(message: Dict[str, Any], args_text: str) -> None:
    chat_id = message["chat"]["id"]
    user = message.get("from", {})
    user_id = user.get("id", 0)
    username = user.get("username") or user.get("first_name") or "User"

    if not args_text.strip():
        await bot_api_client.send_message(
            chat_id,
            f"🔍 {to_bold_sans('SEARCH USAGE')}: /search <song name or keyword>\n"
            f"💡 {to_small_caps('example')}: /search bairan",
        )
        return

    status_msg = await bot_api_client.send_message(
        chat_id, f"🔎 {to_small_caps('searching songs for')}: {sanitize_text(args_text, 50)}..."
    )
    status_msg_id = status_msg.get("result", {}).get("message_id")

    tracks = await extractor.search_tracks(args_text, limit=5, requester_id=user_id, requester_name=username)

    if status_msg_id:
        await bot_api_client.delete_message(chat_id, status_msg_id)

    if not tracks:
        await bot_api_client.send_message(
            chat_id, f"❌ {to_small_caps('no matching songs found for:')} {sanitize_text(args_text, 40)}"
        )
        return

    SEARCH_CACHE[chat_id] = tracks

    from bot.rich_player import build_search_rich_ui
    search_rich = build_search_rich_ui(args_text, tracks)
    await bot_api_client.send_rich_message(chat_id, search_rich)


async def handle_search_select(
    update: Dict[str, Any], cq_id: str, chat_id: int, message_id: int, user_id: int, username: str, data: str
) -> None:
    parts = data.split(":")
    if len(parts) < 2:
        await bot_api_client.answer_callback_query(cq_id)
        return

    val = parts[1]
    tracks = SEARCH_CACHE.get(chat_id, [])
    from bot.rich_player import build_search_rich_ui

    if val == "close":
        await bot_api_client.answer_callback_query(cq_id, "Search closed.")
        search_rich = build_search_rich_ui("Music", tracks, is_closed=True)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, search_rich)
        return

    try:
        idx = int(val)
    except ValueError:
        await bot_api_client.answer_callback_query(cq_id)
        return

    if not tracks or idx >= len(tracks):
        await bot_api_client.answer_callback_query(cq_id, "Search results expired. Try /search again.", show_alert=True)
        return

    track = tracks[idx]
    await bot_api_client.answer_callback_query(cq_id, f"Preparing '{track.title}'...")

    # Download and prepare local audio file using centralized prepare_track pipeline
    local_path = await extractor.prepare_track(track, is_video=False)
    if not local_path or not os.path.exists(local_path):
        await bot_api_client.answer_callback_query(
            cq_id, f"⚠️ Couldn't download '{track.title}'. Please try another song.", show_alert=True
        )
        return

    search_rich = build_search_rich_ui("Music", tracks, selected_idx=idx)
    await bot_api_client.edit_message_rich_text(chat_id, message_id, search_rich)

    fake_msg = {"chat": {"id": chat_id}, "from": {"id": user_id, "first_name": username}, "message_id": 0}
    from utils.formatting import get_user_mention
    uname = username if (isinstance(username, str) and not username.startswith("User")) else None
    requester_label = get_user_mention(user_id, username or "User", uname)
    await _finish_playback_flow(fake_msg, track, None, user_id, username or "User", uname, requester_label, requester_label, is_video=False)


async def _execute_playback_flow(message: Dict[str, Any], query_text: str, is_video: bool = False) -> None:
    """
    Unified playback lifecycle:
    1. Reply to user request with ONE request message.
    2. Edit same message during search & preparation.
    3. When track is ready and actually plays: DELETE request message & send NEW player message.
    4. If queued: edit same request message to confirm queue position.
    5. Tags requester with username or clickable mention.
    """
    chat_id = message["chat"]["id"]
    reply_to_id = message.get("message_id")
    from_user = message.get("from", {})
    user_id = from_user.get("id", 0)
    first_name = from_user.get("first_name", "User")
    username = from_user.get("username")

    from utils.formatting import get_user_mention
    requester_label = get_user_mention(user_id, first_name, username)
    requester_mention = requester_label

    # Check numerical selection from search results cache (e.g. /play 1)
    clean_arg = query_text.strip()
    if clean_arg.isdigit():
        idx = int(clean_arg) - 1
        cached_tracks = SEARCH_CACHE.get(chat_id, [])
        if cached_tracks and 0 <= idx < len(cached_tracks):
            selected_track = cached_tracks[idx]
            selected_track.is_video = is_video
            selected_track.media_type = "video" if is_video else "audio"
            await _finish_playback_flow(
                message, selected_track, None, user_id, first_name, username, requester_label, requester_mention, is_video
            )
            return

    # Check if direct JioSaavn URL
    if not is_video and "jiosaavn.com" in clean_arg.lower():
        status_msg = await bot_api_client.send_message(
            chat_id,
            f"🔎 {to_small_caps('resolving jiosaavn url')}...",
            parse_mode="HTML",
            reply_to_message_id=reply_to_id,
        )
        status_id = status_msg.get("result", {}).get("message_id")
        from player.providers.jiosaavn import jiosaavn_provider
        import asyncio
        resolved_tracks = []
        try:
            resolved_tracks = await asyncio.get_running_loop().run_in_executor(
                None, jiosaavn_provider.resolve_url, clean_arg, user_id, first_name
            )
        except Exception as e:
            logger.warning("JioSaavn URL resolution error: %s", str(e))

        if not resolved_tracks:
            err_text = f"❌ {to_small_caps('failed to resolve jiosaavn url')}."
            if status_id:
                await bot_api_client.edit_message_text(chat_id, status_id, err_text, parse_mode="HTML")
            else:
                await bot_api_client.send_message(chat_id, err_text, parse_mode="HTML", reply_to_message_id=reply_to_id)
            return

        first_track = resolved_tracks[0]
        first_track.is_video = False
        first_track.media_type = "audio"
        await _finish_playback_flow(
            message, first_track, status_id, user_id, first_name, username, requester_label, requester_mention, is_video=False
        )

        # Queue extra tracks if album or playlist
        if len(resolved_tracks) > 1:
            queue = await player_manager.get_queue(chat_id)
            for extra_tr in resolved_tracks[1:]:
                extra_tr.is_video = False
                extra_tr.media_type = "audio"
                queue.add(extra_tr)
            await bot_api_client.send_message(
                chat_id,
                f"➕ {to_small_caps('queued')} {len(resolved_tracks) - 1} {to_small_caps('more tracks from jiosaavn link')}.",
                reply_to_message_id=reply_to_id,
            )
        return

    # 1. ONE request/status message replying to requester's command message
    if is_video:
        initial_text = f"🎬 {to_small_caps('searching video for')}: \"{sanitize_text(query_text, 45)}\"..."
    else:
        initial_text = f"🎵 {to_small_caps('searching music for')}: \"{sanitize_text(query_text, 45)}\"..."

    status_msg = await bot_api_client.send_message(
        chat_id, initial_text, reply_to_message_id=reply_to_id
    )
    status_id = status_msg.get("result", {}).get("message_id")

    # 2. Search metadata & candidate
    if is_video:
        track = await extractor.extract_video(query_text, user_id, first_name)
    else:
        track = await extractor.extract(query_text, user_id, first_name)

    if not track:
        fail_text = (
            f"❌ {to_small_caps('could not find matching song for')}: \"{sanitize_text(query_text, 35)}\"\n"
            f"💡 {to_small_caps('try searching with')}: /search {sanitize_text(query_text, 25)}"
        )
        if status_id:
            await bot_api_client.edit_message_text(chat_id, status_id, fail_text)
        else:
            await bot_api_client.send_message(chat_id, fail_text, reply_to_message_id=reply_to_id)
        return

    # 3. Edit SAME request message: Found -> Downloading local audio file...
    dur_str = format_time(track.duration) if track.duration else "Live"
    icon = "🎬" if is_video else "🎵"
    action_verb = "downloading & caching video" if is_video else "downloading & caching audio"
    found_text = (
        f"{icon} {track.title} ({dur_str})\n"
        f"👤 {track.artist}\n"
        f"🙋 {to_small_caps('requested by')}: {requester_label}\n"
        f"⬇️ {to_small_caps(action_verb)}..."
    )
    if status_id:
        await bot_api_client.edit_message_text(chat_id, status_id, found_text)

    # 4. Centralized download & cache pipeline
    local_path = await extractor.prepare_track(track, is_video=is_video)
    if not local_path or not os.path.exists(local_path):
        fail_text = (
            f"⚠️ {to_small_caps('could not download this track for playback')}: \"{sanitize_text(track.title, 35)}\"\n"
            f"💡 {to_small_caps('please try another song or keyword')}"
        )
        if status_id:
            await bot_api_client.edit_message_text(chat_id, status_id, fail_text)
        else:
            await bot_api_client.send_message(chat_id, fail_text, reply_to_message_id=reply_to_id)
        return

    await _finish_playback_flow(
        message, track, status_id, user_id, first_name, username, requester_label, requester_mention, is_video
    )


async def _finish_playback_flow(
    message: Dict[str, Any],
    track: Any,
    status_msg_id: Optional[int],
    user_id: int,
    first_name: str,
    username: Optional[str],
    requester_label: str,
    requester_mention: str,
    is_video: bool = False,
) -> None:
    chat_id = message["chat"]["id"]
    reply_to_id = message.get("message_id")
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)

    # Attach all requester info to track
    track.requester_user_id = user_id
    track.requester_name = first_name
    track.requester_username = username
    track.requester_mention = requester_mention
    track.is_video = is_video
    track.media_type = "video" if is_video else "audio"

    # Verify and auto-invite assistant in group chats before streaming
    chat_username = message.get("chat", {}).get("username")
    chat_title = message.get("chat", {}).get("title")
    chat_type = message.get("chat", {}).get("type")

    if chat_id < 0 and voice_assistant.is_configured:
        if voice_assistant.is_connected and voice_assistant.assistant_id:
            try:
                # 1. First check if assistant client can directly resolve/access the chat as a member
                is_member = await voice_assistant.is_member_of_chat(
                    chat_id, chat_username=chat_username, chat_title=chat_title, chat_type=chat_type
                )

                # 2. If not detected via assistant, check via Bot API
                if not is_member:
                    member_resp = await bot_api_client.get_chat_member(chat_id, voice_assistant.assistant_id)
                    if member_resp.get("ok"):
                        member_data = member_resp.get("result", {})
                        status = member_data.get("status", "")
                        if status in ("member", "administrator", "creator") or (status == "restricted" and member_data.get("is_member", True)):
                            is_member = True

                # 3. If still not in group, attempt auto-joining via invite link if exportable
                if not is_member:
                    invite_res = await bot_api_client.export_chat_invite_link(chat_id)
                    invite_link = invite_res.get("result") if invite_res.get("ok") else None
                    if invite_link:
                        is_member = await voice_assistant.join_chat(invite_link)

                # 4. If assistant is genuinely not present in group, notify user cleanly
                if not is_member:
                    asst_tag = (
                        f"@{voice_assistant.assistant_username}"
                        if voice_assistant.assistant_username
                        else "Assistant"
                    )
                    warn_msg = (
                        f"⚠️ {to_bold_sans('ASSISTANT NOT IN GROUP')}\n\n"
                        f"Voice Assistant ({asst_tag}) is not in this group.\n\n"
                        f"👉 {to_bold_sans('HOW TO RESOLVE')}:\n"
                        f"1. Add {asst_tag} to this group as a normal member.\n"
                        f"2. Start Voice Chat in the group.\n\n"
                        f"Then send {'/vplay' if is_video else '/play'} again to stream! 🎵"
                    )
                    if status_msg_id:
                        await bot_api_client.edit_message_text(chat_id, status_msg_id, warn_msg, parse_mode="HTML")
                    else:
                        await bot_api_client.send_message(chat_id, warn_msg, parse_mode="HTML", reply_to_message_id=reply_to_id)
                    return
            except Exception as e:
                logger.debug("Assistant pre-flight membership check note: %s", str(e))

    # Play or Queue
    is_now_playing, state, queue = await player_manager.play_or_queue(
        chat_id,
        track,
        {"id": user_id, "name": first_name, "username": username, "mention": requester_mention},
    )

    last_err = voice_assistant.get_last_error(chat_id)
    if last_err and not state.is_playing and not is_now_playing:
        err_msg = (
            f"❌ {to_bold_sans('PLAYBACK ERROR')}\n\n"
            f"• {to_small_caps('track')}: {track.title}\n"
            f"• {to_small_caps('reason')}: {last_err}"
        )
        if status_msg_id:
            await bot_api_client.edit_message_text(chat_id, status_msg_id, err_msg, parse_mode="HTML")
        else:
            await bot_api_client.send_message(chat_id, err_msg, parse_mode="HTML", reply_to_message_id=reply_to_id)
        return

    if is_now_playing and state.current_track:
        # Step 6: When playback actually starts:
        # Delete the old request message
        if status_msg_id:
            try:
                await bot_api_client.delete_message(chat_id, status_msg_id)
            except Exception:
                pass

        # Create NEW player message for the currently playing track
        rich_player = build_player_rich_message(state, queue)
        send_res = await bot_api_client.send_rich_message(chat_id, rich_player)
        msg_id = send_res.get("result", {}).get("message_id")
        state.player_message_id = msg_id
        state.player_message_chat_id = chat_id
        await db.add_history(
            chat_id,
            track.track_id,
            track.title,
            track.artist,
            track.duration,
            track.source_url,
            first_name,
        )
    elif not is_now_playing and len(queue) > 0:
        # Queued: edit SAME request message
        icon = "🎬" if is_video else "🎵"
        dur_str = format_time(track.duration) if track.duration else "Live"
        q_text = (
            f"➕ {to_bold_sans('ADDED TO QUEUE')}\n\n"
            f"{icon} {track.title} ({dur_str})\n"
            f"👤 {track.artist}\n"
            f"📍 {to_small_caps('position')}: #{len(queue)}\n"
            f"🙋 {to_small_caps('requested by')}: {requester_label}"
        )
        if status_msg_id:
            await bot_api_client.edit_message_text(chat_id, status_msg_id, q_text)
        else:
            await bot_api_client.send_message(chat_id, q_text, reply_to_message_id=reply_to_id)


async def handle_play(message: Dict[str, Any], args_text: str) -> None:
    """Handles audio playback request: /play <song name or link>."""
    chat_id = message["chat"]["id"]
    reply_to_id = message.get("message_id")
    if not args_text.strip():
        await bot_api_client.send_message(
            chat_id,
            f"🎵 {to_bold_sans('USAGE')}: /play <song name or link>\n"
            f"💡 {to_small_caps('example')}: /play barsaat banjaare",
            reply_to_message_id=reply_to_id,
        )
        return

    await _execute_playback_flow(message, args_text, is_video=False)


async def handle_vplay(message: Dict[str, Any], args_text: str) -> None:
    """Handles video playback request: /vplay <video name or YouTube link>."""
    chat_id = message["chat"]["id"]
    reply_to_id = message.get("message_id")
    if not args_text.strip():
        await bot_api_client.send_message(
            chat_id,
            f"🎬 {to_bold_sans('VIDEO USAGE')}: /vplay <video name or YouTube link>\n"
            f"💡 {to_small_caps('example')}: /vplay Alan Walker Faded",
            reply_to_message_id=reply_to_id,
        )
        return

    await _execute_playback_flow(message, args_text, is_video=True)


async def handle_download(message: Dict[str, Any], args_text: str, is_video: bool = False) -> None:
    """Handles direct audio or video file downloads: informs users the feature is removed."""
    chat_id = message["chat"]["id"]
    reply_to_id = message.get("message_id")
    await bot_api_client.send_message(
        chat_id,
        f"⚠️ {to_bold_sans('DOWNLOAD DISABLED')}\n\n"
        f"The download option has been removed from this bot.\n"
        f"Please use /play to stream high-quality music or /vplay to stream video directly in the Voice Chat! 🎵",
        reply_to_message_id=reply_to_id,
    )


async def handle_pause(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)

    if not await can_skip_or_stop(chat_id, from_id, state):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only the requester or administrators can pause the player.")
        )
        return

    success, msg = await player_manager.pause(chat_id, state.session_id)
    if success:
        rich_player = build_player_rich_message(state, queue)
        if state.player_message_id:
            await bot_api_client.edit_message_rich_text(
                chat_id, state.player_message_id, rich_player
            )
        else:
            await bot_api_client.send_rich_message(chat_id, rich_player)
    else:
        await bot_api_client.send_message(chat_id, f"⚠️ {msg}")


async def handle_resume(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)

    if not await can_skip_or_stop(chat_id, from_id, state):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only the requester or administrators can resume the player.")
        )
        return

    success, msg = await player_manager.resume(chat_id, state.session_id)
    if success:
        rich_player = build_player_rich_message(state, queue)
        if state.player_message_id:
            await bot_api_client.edit_message_rich_text(
                chat_id, state.player_message_id, rich_player
            )
        else:
            await bot_api_client.send_rich_message(chat_id, rich_player)
    else:
        await bot_api_client.send_message(chat_id, f"⚠️ {msg}")


async def handle_replay(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)

    if not await can_skip_or_stop(chat_id, from_id, state):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only the requester or administrators can replay the track.")
        )
        return

    success, msg = await player_manager.replay(chat_id, state.session_id)
    if success:
        rich_player = build_player_rich_message(state, queue)
        if state.player_message_id:
            await bot_api_client.edit_message_rich_text(
                chat_id, state.player_message_id, rich_player
            )
        else:
            await bot_api_client.send_rich_message(chat_id, rich_player)
    else:
        await bot_api_client.send_message(chat_id, f"⚠️ {msg}")


async def handle_skip(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)

    if await can_skip_or_stop(chat_id, from_id, state):
        next_track, msg = await player_manager.skip(chat_id)
        if next_track and state and state.current_track:
            rich_player = build_player_rich_message(state, queue)
            res = await bot_api_client.send_rich_message(chat_id, rich_player)
            state.player_message_id = res.get("result", {}).get("message_id")
        else:
            await bot_api_client.send_message(chat_id, f"⏭ {msg}")
    else:
        # Vote Skip
        threshold = 3
        if not hasattr(state, "skip_votes"):
            state.skip_votes = set()

        if from_id in state.skip_votes:
            await bot_api_client.send_message(
                chat_id, f"⚠️ " + to_small_caps(f"you have already voted to skip! ({len(state.skip_votes)}/{threshold})")
            )
            return

        state.skip_votes.add(from_id)

        if len(state.skip_votes) >= threshold:
            state.skip_votes.clear()
            await bot_api_client.send_message(chat_id, f"⏭️ {to_bold_sans('VOTE SKIP SUCCESSFUL')}! Skipping to next track...")
            next_track, msg = await player_manager.skip(chat_id)
            if next_track and state and state.current_track:
                rich_player = build_player_rich_message(state, queue)
                res = await bot_api_client.send_rich_message(chat_id, rich_player)
                state.player_message_id = res.get("result", {}).get("message_id")
            else:
                await bot_api_client.send_message(chat_id, f"⏹ {msg}")
        else:
            await bot_api_client.send_message(
                chat_id, f"⏭️ {to_bold_sans('VOTE SKIP REGISTERED')} • {len(state.skip_votes)}/{threshold} votes"
            )


async def handle_queue(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)
    rich_queue = build_queue_rich_message(state, queue)
    await bot_api_client.send_rich_message(chat_id, rich_queue)


async def handle_stop(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    state = await player_manager.get_state(chat_id)

    if not await can_skip_or_stop(chat_id, from_id, state):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only the requester or administrators can stop the player.")
        )
        return

    success, msg = await player_manager.stop(chat_id)
    await bot_api_client.send_message(chat_id, f"⏹ {msg}")


async def handle_clear(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only chat administrators can clear the queue.")
        )
        return

    queue = await player_manager.get_queue(chat_id)
    count = queue.clear()
    await bot_api_client.send_message(
        chat_id, f"🗑 {to_small_caps('cleared')} {count} {to_small_caps('tracks from queue.')}"
    )


async def handle_loop(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only chat administrators can change loop mode.")
        )
        return

    mode = args.strip().lower()
    if mode not in ("off", "track", "queue"):
        await bot_api_client.send_message(
            chat_id,
            f"🔁 {to_bold_sans('LOOP USAGE')}: /loop <off | track | queue>\n"
            f"• off: {to_small_caps('no repeat')}\n"
            f"• track: {to_small_caps('repeat current song indefinitely')}\n"
            f"• queue: {to_small_caps('cycle entire queue continuously')}",
        )
        return

    success, msg = await player_manager.set_loop_mode(chat_id, mode)
    await bot_api_client.send_message(chat_id, f"🔁 {msg}")


async def handle_seek(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only chat administrators can seek tracks.")
        )
        return

    try:
        seconds = int(args.strip())
        success, msg = await player_manager.seek(chat_id, seconds)
        await bot_api_client.send_message(chat_id, f"⏩ {msg}")
    except ValueError:
        await bot_api_client.send_message(
            chat_id, f"⏩ {to_bold_sans('SEEK USAGE')}: /seek <seconds>\n{to_small_caps('example')}: /seek 60"
        )


async def handle_volume(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only chat administrators can adjust volume.")
        )
        return

    try:
        level = int(args.strip())
        if not (1 <= level <= 100):
            raise ValueError
        success, msg = await player_manager.set_volume(chat_id, level)
        await bot_api_client.send_message(chat_id, f"🔊 {msg}")
    except ValueError:
        await bot_api_client.send_message(
            chat_id, f"🔊 {to_bold_sans('VOLUME USAGE')}: /volume <1-100>\n{to_small_caps('example')}: /volume 80"
        )


async def handle_nowplaying(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)

    if not state.current_track:
        await bot_api_client.send_message(
            chat_id, f"ℹ️ {to_small_caps('no music is currently playing in this chat. use /play to begin.')}"
        )
        return

    rich_player = build_player_rich_message(state, queue)
    res = await bot_api_client.send_rich_message(chat_id, rich_player)
    msg_id = res.get("result", {}).get("message_id")
    state.player_message_id = msg_id
    state.player_message_chat_id = chat_id


async def handle_vc_status(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    asst_tag = f"@{voice_assistant.assistant_username}" if voice_assistant.assistant_username else "Voice Assistant"

    asst_ok = voice_assistant.is_connected
    pytgcalls_ok = bool(voice_assistant.pytgcalls)
    active_in_chat = chat_id in voice_assistant.active_chats

    msg_lines = [
        f"🎙 {to_bold_sans('VOICE CHAT DIAGNOSTICS')}\n",
        f"• {to_bold_sans('Assistant Client')}: {'✅ Connected (' + asst_tag + ')' if asst_ok else '❌ Disconnected'}",
        f"• {to_bold_sans('PyTgCalls Engine')}: {'✅ Active' if pytgcalls_ok else '⚠️ Not Active'}",
        f"• {to_bold_sans('Active in this Chat')}: {'✅ Yes' if active_in_chat else '❌ No'}",
    ]
    if voice_assistant.last_error:
        msg_lines.append(f"\n⚠️ {to_bold_sans('Last Voice Error')}:\n`{voice_assistant.last_error[:150]}`")

    msg_lines.append(
        f"\n💡 {to_bold_sans('IF NO AUDIO IS HEARD IN VC')}:\n"
        f"1. Make sure {asst_tag} is added to this group as a member/admin.\n"
        f"2. Ensure group Voice Chat is started in Telegram group header.\n"
        f"3. In Telegram VC window, tap {asst_tag} -> set {to_bold_sans('Volume to 200%')} & check if unmuted!\n"
        f"4. Give {asst_tag} 'Manage Video Chats' permission in group settings."
    )

    await bot_api_client.send_message(chat_id, "\n".join(msg_lines))


async def handle_settings(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    cfg = await db.get_chat_config(chat_id)
    text = (
        f"⚙️ {to_bold_sans('AARUU MUSIC SETTINGS')}\n\n"
        f"🔊 {to_small_caps('volume')}: {cfg['volume']}%\n"
        f"🔁 {to_small_caps('loop mode')}: {cfg['loop_mode'].upper()}\n"
        f"📦 {to_small_caps('max queue capacity')}: {cfg['max_queue_size']} tracks\n"
        f"🛡 {to_small_caps('admin controls only')}: {'Enabled' if cfg['admin_only_controls'] else 'Disabled'}\n"
        f"💾 {to_small_caps('storage')}: {to_small_caps('persisted in sqlite database')}"
    )
    await bot_api_client.send_message(chat_id, text)


# =========================================================================
# OWNER-ONLY COMMANDS (Protected from non-owners)
# =========================================================================

async def handle_block(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not is_sudo(from_id):
        await bot_api_client.send_message(
            chat_id, "⛔ " + to_small_caps("only bot owner can use this command.")
        )
        return

    target_user_id = None
    reason = "Blocked by bot owner"

    reply = message.get("reply_to_message")
    if reply and "from" in reply:
        target_user_id = reply["from"].get("id")
        if args.strip():
            reason = args.strip()
    elif args.strip():
        parts = args.strip().split(maxsplit=1)
        try:
            target_user_id = int(parts[0])
            if len(parts) > 1:
                reason = parts[1]
        except ValueError:
            await bot_api_client.send_message(
                chat_id, f"🚫 {to_bold_sans('USAGE')}: /block <user_id> [reason] or reply"
            )
            return

    if not target_user_id:
        await bot_api_client.send_message(
            chat_id, f"🚫 {to_bold_sans('USAGE')}: /block <user_id> [reason] or reply"
        )
        return

    if is_sudo(target_user_id):
        await bot_api_client.send_message(chat_id, "❌ " + to_small_caps("cannot block bot owner or sudo."))
        return

    await db.block_user(target_user_id, from_id, reason)
    await bot_api_client.send_message(
        chat_id,
        f"🚫 {to_bold_sans('USER BLOCKED')}\n\n"
        f"👤 {to_small_caps('user id')}: {target_user_id}\n"
        f"📝 {to_small_caps('reason')}: {reason}",
    )


async def handle_unblock(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not is_sudo(from_id):
        await bot_api_client.send_message(
            chat_id, "⛔ " + to_small_caps("only bot owner can use this command.")
        )
        return

    target_user_id = None
    reply = message.get("reply_to_message")
    if reply and "from" in reply:
        target_user_id = reply["from"].get("id")
    elif args.strip():
        try:
            target_user_id = int(args.strip().split()[0])
        except ValueError:
            await bot_api_client.send_message(
                chat_id, f"✅ {to_bold_sans('USAGE')}: /unblock <user_id>"
            )
            return

    if not target_user_id:
        await bot_api_client.send_message(
            chat_id, f"✅ {to_bold_sans('USAGE')}: /unblock <user_id>"
        )
        return

    if not db.is_user_blocked(target_user_id):
        await bot_api_client.send_message(chat_id, f"⚠️ User {target_user_id} is not blocked.")
        return

    await db.unblock_user(target_user_id)
    await bot_api_client.send_message(
        chat_id, f"✅ {to_bold_sans('USER UNBLOCKED')}\n\n👤 {to_small_caps('user id')}: {target_user_id}"
    )


async def handle_blocked(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not is_sudo(from_id):
        await bot_api_client.send_message(
            chat_id, "⛔ " + to_small_caps("only bot owner can use this command.")
        )
        return

    blocked_list = await db.get_blocked_users()
    if not blocked_list:
        await bot_api_client.send_message(chat_id, f"✅ {to_small_caps('no users are currently blocked.')}")
        return

    lines = [f"🚫 {to_bold_sans('BLOCKED USERS')} ({len(blocked_list)}):\n"]
    for idx, u in enumerate(blocked_list[:25], 1):
        uid = u.get("user_id")
        reason = u.get("reason") or "No reason provided"
        lines.append(f"{idx}. {uid} - {reason}")

    await bot_api_client.send_message(chat_id, "\n".join(lines))


async def handle_broadcast(message: Dict[str, Any], args: str) -> None:
    import asyncio
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not is_sudo(from_id):
        await bot_api_client.send_message(
            chat_id, "⛔ " + to_small_caps("only bot owner can use this command.")
        )
        return

    mode = "all"
    text_content = args.strip()
    reply = message.get("reply_to_message")

    if text_content.startswith(("-user", "-dm", "-pm")):
        mode = "user"
        parts = text_content.split(maxsplit=1)
        text_content = parts[1] if len(parts) > 1 else ""
    elif text_content.startswith(("-group", "-groups")):
        mode = "group"
        parts = text_content.split(maxsplit=1)
        text_content = parts[1] if len(parts) > 1 else ""

    if not reply and not text_content:
        await bot_api_client.send_message(
            chat_id,
            f"📢 {to_bold_sans('BROADCAST USAGE')}:\n"
            f"• /broadcast <text> - Send to all chats\n"
            f"• /broadcast -user <text> - Send to all users\n"
            f"• /broadcast -group <text> - Send to all groups",
        )
        return

    all_chats = await db.get_all_chats(mode if mode != "all" else None)
    if not all_chats:
        await bot_api_client.send_message(chat_id, f"⚠️ No registered chats for: {mode}.")
        return

    init_msg = await bot_api_client.send_message(
        chat_id, f"📢 {to_small_caps('broadcasting to')} {len(all_chats)} {to_small_caps('chats')}..."
    )
    init_msg_id = init_msg.get("result", {}).get("message_id")

    success_count = 0
    failed_count = 0
    start_time = time.time()

    for target in all_chats:
        t_id = target["chat_id"]
        try:
            if reply:
                res = await bot_api_client.copy_message(
                    chat_id=t_id,
                    from_chat_id=chat_id,
                    message_id=reply["message_id"],
                )
                if res.get("ok"):
                    success_count += 1
                else:
                    failed_count += 1
            else:
                res = await bot_api_client.send_message(
                    chat_id=t_id,
                    text=f"📢 {to_bold_sans('ANNOUNCEMENT')}\n\n{text_content}",
                )
                if res.get("ok"):
                    success_count += 1
                else:
                    failed_count += 1
            await asyncio.sleep(0.035)
        except Exception:
            failed_count += 1

    duration = round(time.time() - start_time, 2)
    report = (
        f"✅ {to_bold_sans('BROADCAST COMPLETED')}\n\n"
        f"📊 {to_small_caps('delivered')}: {success_count}\n"
        f"❌ {to_small_caps('failed')}: {failed_count}\n"
        f"⏱ {to_small_caps('duration')}: {duration}s"
    )

    if init_msg_id:
        await bot_api_client.edit_message_text(chat_id, init_msg_id, report)
    else:
        await bot_api_client.send_message(chat_id, report)


async def handle_ping(message: Dict[str, Any]) -> None:
    """Calculates Telegram roundtrip latency and continuous 24/7 uptime."""
    chat_id = message["chat"]["id"]
    start_t = time.time()
    sent = await bot_api_client.send_message(chat_id, "🏓 ᴘɪɴɢɪɴɢ...")
    latency_ms = max(1, int((time.time() - start_t) * 1000))

    uptime_sec = int(time.time() - BOT_START_TIME)
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)

    uptime_parts = []
    if days > 0:
        uptime_parts.append(f"{days}d")
    if hours > 0 or days > 0:
        uptime_parts.append(f"{hours}h")
    uptime_parts.append(f"{mins}m {secs}s")
    uptime_str = " ".join(uptime_parts)

    text = (
        f"🏓 {to_bold_sans('PONG!')} {latency_ms}ms\n\n"
        f"⚡ {to_small_caps('status')}: {to_small_caps('online (24/7 resilient)')}\n"
        f"⏱ {to_small_caps('uptime')}: {uptime_str}\n"
        f"💓 {to_small_caps('heartbeat')}: {to_small_caps('active (every 10 min)')}"
    )

    sent_id = sent.get("result", {}).get("message_id")
    if sent_id:
        await bot_api_client.edit_message_text(chat_id, sent_id, text)
    else:
        await bot_api_client.send_message(chat_id, text)


async def handle_stats(message: Dict[str, Any]) -> None:
    """Displays comprehensive statistics (Owner only)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    # Restrict completely to bot owner
    if not is_sudo(from_id):
        await bot_api_client.send_message(
            chat_id, "⛔ " + to_small_caps("only bot owner can access statistics.")
        )
        return

    stats = await db.get_stats()
    uptime_sec = int(time.time() - BOT_START_TIME)
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)

    uptime_parts = []
    if days > 0:
        uptime_parts.append(f"{days}d")
    uptime_parts.append(f"{hours}h {mins}m {secs}s")
    uptime_str = " ".join(uptime_parts)

    active_playbacks = sum(1 for s in player_manager._states.values() if s.is_playing)

    text = (
        f"📊 {to_bold_sans('AARUU MUSIC SYSTEM METRICS')}\n\n"
        f"👥 {to_small_caps('total users (dms)')}: {stats.get('users', 0)}\n"
        f"💬 {to_small_caps('total groups')}: {stats.get('groups', 0)}\n"
        f"🎵 {to_small_caps('songs played')}: {stats.get('history', 0)}\n"
        f"▶ {to_small_caps('active playbacks')}: {active_playbacks}\n"
        f"🚫 {to_small_caps('blocked users')}: {stats.get('blocked', 0)}\n"
        f"⏱ {to_small_caps('uptime')}: {uptime_str}\n"
        f"⚡ {to_small_caps('engine')}: {to_small_caps('24/7 continuous stream pool')}"
    )
    await bot_api_client.send_message(chat_id, text)


async def handle_shuffle(message: Dict[str, Any]) -> None:
    """Shuffles the active track queue (Chat Admins)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only chat administrators can shuffle the queue.")
        )
        return

    count, msg = await player_manager.shuffle(chat_id)
    await bot_api_client.send_message(chat_id, f"🔀 {msg}")


async def handle_admincache(message: Dict[str, Any]) -> None:
    """Reloads admin rights cache (Chat Admins / Owner)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only chat administrators can reload the admin cache.")
        )
        return

    from bot.permissions import clear_admin_cache
    clear_admin_cache(chat_id if not is_sudo(from_id) else None)
    await bot_api_client.send_message(
        chat_id, f"🔄 {to_bold_sans('ADMIN CACHE REFRESHED')}\n\n{to_small_caps('permissions synced with telegram.')}"
    )


async def handle_vctest(message: Dict[str, Any]) -> None:
    """Runs a direct local playback diagnostic test (Chat Admins / Owner / Sudo)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only administrators can run voice chat diagnostics.")
        )
        return

    # Notify we are running the test
    status_msg = await bot_api_client.send_message(
        chat_id, f"🧪 {to_bold_sans('RUNNING PYTGCALLS PIPELINE DIAGNOSTIC')}..."
    )
    
    results = await voice_assistant.run_vc_diagnostic(chat_id)
    
    # Format the results cleanly
    lines = [
        f"📊 {to_bold_sans('DIAGNOSTIC RESULTS')}\n",
        f"📁 {to_small_caps('file exists')}: {'✅ TRUE' if results['file_exists'] else '❌ FALSE'}",
    ]
    if results["file_exists"]:
        lines.append(f"📦 {to_small_caps('file size')}: {results['file_size']} bytes")
        lines.append(f"⚙️ {to_small_caps('ffmpeg valid')}: {'✅ TRUE' if results['ffmpeg_valid'] else '❌ FALSE'}")
        if not results["ffmpeg_valid"]:
            lines.append(f"⚠️ {to_small_caps('ffmpeg error')}: {results['ffmpeg_log']}")
            
    lines.append(f"🤖 {to_small_caps('assistant connected')}: {'✅ TRUE' if results['pytgcalls_connected'] else '❌ FALSE'}")
    lines.append(f"📞 {to_small_caps('vc state')}: {results['vc_state'].upper()}")
    lines.append(f"📦 {to_small_caps('mediastream created')}: {'✅ TRUE' if results['mediastream_created'] else '❌ FALSE'}")
    lines.append(f"▶️ {to_small_caps('pytgcalls play success')}: {'✅ TRUE' if results['play_success'] else '❌ FALSE'}")
    
    if results["error"]:
        lines.append(f"\n❌ {to_bold_sans('DIAGNOSTIC ERROR')}: {results['error']}")
    else:
        lines.append(f"\n🎉 {to_bold_sans('ALL PIPELINE CHECKS PASSED!')}")
        
    await bot_api_client.send_message(chat_id, "\n".join(lines))
    
    # Delete status message
    if status_msg:
        try:
            status_id = status_msg.get("result", {}).get("message_id")
            if status_id:
                await bot_api_client.delete_message(chat_id, status_id)
        except Exception:
            pass


async def handle_autoplay(message: Dict[str, Any], args_text: str = "") -> None:
    """Handles /autoplay [on|off] command to configure chat recommendation mode."""
    chat_id = message["chat"]["id"]
    reply_to_id = message.get("message_id")
    clean_arg = args_text.strip().lower()

    if clean_arg == "on":
        await player_manager.set_autoplay(chat_id, True)
        msg_text = (
            f"🔄 {to_bold_sans('AUTOPLAY ENABLED')}\n\n"
            f"When a song finishes and the queue is empty, Aaruu Music will recommend a similar song with a <b>▶ Play</b> button."
        )
    elif clean_arg == "off":
        await player_manager.set_autoplay(chat_id, False)
        msg_text = (
            f"⏹️ {to_bold_sans('AUTOPLAY DISABLED')}\n\n"
            f"Autoplay recommendations are turned off for this chat."
        )
    elif not clean_arg:
        current_st = await player_manager.get_autoplay(chat_id)
        st_label = "ENABLED 🟢" if current_st else "DISABLED 🔴"
        msg_text = (
            f"ℹ️ {to_bold_sans('AUTOPLAY STATUS')}\n\n"
            f"Autoplay is currently: <b>{st_label}</b>\n\n"
            f"💡 Use <code>/autoplay on</code> or <code>/autoplay off</code> to toggle."
        )
    else:
        msg_text = (
            f"💡 {to_bold_sans('USAGE')}: <code>/autoplay on</code> | <code>/autoplay off</code>"
        )

    await bot_api_client.send_message(
        chat_id, msg_text, parse_mode="HTML", reply_to_message_id=reply_to_id
    )


async def handle_sysinfo(message: Dict[str, Any]) -> None:
    """Displays server system metrics (Owner only, DM only)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    if not is_sudo(from_id):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    if chat_id < 0:
        await bot_api_client.send_message(chat_id, "⚠️ " + to_small_caps("this command is only allowed in private message (dm) of the bot."))
        return
    try:
        import psutil
        import platform
        cpu = psutil.cpu_percent()
        ram = psutil.virtual_memory().percent
        disk = psutil.disk_usage('/').percent
        text = (
            f"🖥️ {to_bold_sans('SYSTEM METRICS')}\n\n"
            f"💻 {to_small_caps('os')}: {platform.system()} {platform.release()}\n"
            f"⚙️ {to_small_caps('cpu usage')}: {cpu}%\n"
            f"💾 {to_small_caps('ram usage')}: {ram}%\n"
            f"💽 {to_small_caps('disk usage')}: {disk}%\n"
            f"🐍 {to_small_caps('python version')}: {platform.python_version()}\n"
            f"🔌 {to_small_caps('active workers')}: {to_small_caps('1 healthy process')}"
        )
        await bot_api_client.send_message(chat_id, text)
    except Exception as e:
        await bot_api_client.send_message(chat_id, f"❌ Failed to fetch sysinfo: {str(e)}")


async def handle_restart(message: Dict[str, Any]) -> None:
    """Gracefully reloads the worker process (Owner only, DM only)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    if not is_sudo(from_id):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    if chat_id < 0:
        await bot_api_client.send_message(chat_id, "⚠️ " + to_small_caps("this command is only allowed in private message (dm) of the bot."))
        return
    await bot_api_client.send_message(chat_id, "🔄 " + to_small_caps("restarting bot process..."))
    import os
    import sys
    os.execl(sys.executable, sys.executable, *sys.argv)


async def handle_ac(message: Dict[str, Any]) -> None:
    """Informs owner about registered groups and current active playback streams (Owner only, DM only)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    if not is_sudo(from_id):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    if chat_id < 0:
        await bot_api_client.send_message(chat_id, "⚠️ " + to_small_caps("this command is only allowed in private message (dm) of the bot."))
        return
    stats = await db.get_stats()
    active_streams = sum(1 for state in player_manager._states.values() if state.is_playing)
    text = (
        f"📊 {to_bold_sans('ACTIVE STREAM STATS')}\n\n"
        f"💬 {to_small_caps('total registered groups')}: {stats.get('groups', 0)}\n"
        f"📻 {to_small_caps('currently streaming in')}: {active_streams} {to_small_caps('groups')}"
    )
    await bot_api_client.send_message(chat_id, text)


async def handle_setbanner(message: Dict[str, Any], args: str) -> None:
    """Updates custom banners for different sections of the help guide (Owner only, DM only)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    if not is_sudo(from_id):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    if chat_id < 0:
        await bot_api_client.send_message(chat_id, "⚠️ " + to_small_caps("this command is only allowed in private message (dm) of the bot."))
        return
    clean_args = args.strip().split(maxsplit=1)
    if len(clean_args) < 2:
        await bot_api_client.send_message(
            chat_id,
            f"🖼️ {to_bold_sans('SETBANNER USAGE')}:\n"
            f"<code>/setbanner <section> <image_url></code>\n\n"
            f"📝 {to_small_caps('valid sections')}:\n"
            f"• <code>home</code>\n• <code>getting_started</code>\n• <code>find_play</code>\n• <code>all_commands</code>\n"
            f"• <code>controls</code>\n• <code>queue_repeat</code>\n• <code>group_settings</code>\n• <code>group_admins</code>\n"
            f"• <code>troubleshooting</code>\n• <code>owner_sudo</code>",
            parse_mode="HTML"
        )
        return
    section = clean_args[0].lower().strip()
    url = clean_args[1].strip()
    VALID_SECTIONS = {"home", "getting_started", "find_play", "all_commands", "controls", "queue_repeat", "group_settings", "group_admins", "troubleshooting", "owner_sudo"}
    if section not in VALID_SECTIONS:
        await bot_api_client.send_message(
            chat_id,
            f"❌ {to_small_caps('invalid section:')} <code>{section}</code>\n"
            f"Please use one of the valid guide sections.",
            parse_mode="HTML"
        )
        return
    if not url.startswith(("http://", "https://")):
        await bot_api_client.send_message(chat_id, f"❌ {to_small_caps('invalid url format. must start with http/https.')}")
        return
    await db.set_banner(section, url)
    await bot_api_client.send_message(
        chat_id,
        f"✅ {to_bold_sans('BANNER UPDATED SUCCESS')}\n\n"
        f"📂 {to_small_caps('section')}: <code>{section}</code>\n"
        f"🖼️ {to_small_caps('url')}: {url}",
        parse_mode="HTML"
    )


