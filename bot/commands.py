"""
Aaruu Music - Command Handlers
Implements all bot commands with clean, aesthetic Unicode typography
(sans-serif bold, small caps) without raw HTML tags.
Restricts and hides owner-only commands from regular users.
"""

import asyncio
import os
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Union, Tuple
from bot.api import bot_api_client
from bot.permissions import is_chat_admin, is_owner, is_sudo, can_skip_or_stop
from bot.rich_help import build_start_rich_message_async
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

# Runtime flags controlled from the owner admin panel (maintenance mode is persisted in the DB)
MAINTENANCE: Dict[str, bool] = {"on": False}

# Telegram bots can upload files up to 50 MB; stay a little below that limit.
TG_UPLOAD_LIMIT_BYTES = 49 * 1024 * 1024

# Commands that only make sense inside a group (music is played in group voice chats, never in DM)
GROUP_ONLY_COMMANDS = {
    "play", "vplay", "search", "pause", "resume", "replay", "skip", "queue", "stop", "clear",
    "loop", "seek", "volume", "nowplaying", "vc", "vcstatus", "status", "shuffle", "vctest",
    "testvc", "diagnostic", "prev", "previous", "forward", "rewind", "remove", "playnext",
    "recommend", "history", "top",
}

# Only public user commands registered with Telegram BotFather menu
COMMANDS_REGISTRY: List[Dict[str, str]] = [
    {"command": "play", "description": "Play or queue a song or URL"},
    {"command": "vplay", "description": "Stream video directly in Voice Chat"},
    {"command": "download", "description": "Download and get MP3 audio or MP4 video file"},
    {"command": "song", "description": "Search and download an MP3 audio track"},
    {"command": "video", "description": "Download an MP4 video clip"},
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
    {"command": "prev", "description": "Play the previous track"},
    {"command": "forward", "description": "Jump forward in the song (e.g. /forward 15)"},
    {"command": "rewind", "description": "Jump back in the song (e.g. /rewind 15)"},
    {"command": "playnext", "description": "Queue a song to play next"},
    {"command": "remove", "description": "Remove a track from the queue (/remove 2)"},
    {"command": "recommend", "description": "Get a similar song to what is playing"},
    {"command": "lyrics", "description": "Find lyrics of a song"},
    {"command": "history", "description": "Recently played songs in this group"},
    {"command": "top", "description": "Most played songs in this group"},
    {"command": "id", "description": "Show your user ID and chat ID"},
    {"command": "help", "description": "Interactive help and guides"},
    {"command": "start", "description": "Start Aaruu Music guide"},
]

# Extra menu entries shown only to the bot owner
OWNER_COMMANDS: List[Dict[str, str]] = [
    {"command": "admin", "description": "Open the owner admin panel"},
    {"command": "stats", "description": "Global bot statistics"},
    {"command": "sysinfo", "description": "Server CPU / RAM / disk"},
    {"command": "broadcast", "description": "Broadcast a message"},
    {"command": "block", "description": "Block a user"},
    {"command": "unblock", "description": "Unblock a user"},
    {"command": "addsudo", "description": "Add a sudo user"},
    {"command": "delsudo", "description": "Remove a sudo user"},
    {"command": "maintenance", "description": "Maintenance mode on/off"},
    {"command": "restart", "description": "Restart the bot"},
]


async def send_group_only_notice(message: Dict[str, Any]) -> None:
    """Music plays only in group voice chats. In DM we explain that and offer an add-to-group button."""
    chat_id = message["chat"]["id"]
    bot_username = (bot_api_client.bot_info or {}).get("username") or "Aaruu_musicbot"
    text = (
        f"🎵 {to_bold_sans('GROUP ONLY')}\n\n"
        f"{to_small_caps('music plays only in group voice chats, not in private chat.')}\n\n"
        f"1. {to_small_caps('add me to your group')}\n"
        f"2. {to_small_caps('start a voice chat')}\n"
        f"3. {to_small_caps('send')} /play <song name>\n\n"
        f"⬇️ {to_small_caps('you can still use')} /download, /song {to_small_caps('and')} /video {to_small_caps('here to get files.')}"
    )
    markup = {
        "inline_keyboard": [[
            {"text": "+ " + to_small_caps("add to your group"),
             "url": f"https://t.me/{bot_username}?startgroup=true"}
        ]]
    }
    await bot_api_client.send_message(
        chat_id, text, reply_markup=markup, reply_to_message_id=message.get("message_id")
    )


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
                    "type": "photo",
                    "photo": {
                        "type": "photo",
                        "media": "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80",
                    },
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
    rich_msg = await build_start_rich_message_async(section, is_owner=is_sudo(from_id))
    await bot_api_client.send_rich_message(chat_id, rich_msg)


async def handle_help(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    rich_msg = await build_start_rich_message_async("home", is_owner=is_sudo(from_id))
    await bot_api_client.send_rich_message(chat_id, rich_msg)


async def handle_search(message: Dict[str, Any], args_text: str) -> None:
    chat_id = message["chat"]["id"]
    if chat_id > 0:
        await send_group_only_notice(message)
        return
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
    if chat_id > 0:
        await bot_api_client.answer_callback_query(
            cq_id, "Music plays only in groups. Add me to a group and use /play there.", show_alert=True
        )
        return

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


async def _execute_playback_flow(
    message: Dict[str, Any], query_text: str, is_video: bool = False, front: bool = False
) -> None:
    """
    Unified playback lifecycle:
    1. Reply to user request with ONE request message.
    2. Edit same message during search & preparation.
    3. When track is ready and actually plays: DELETE request message & send NEW player message.
    4. If queued: edit same request message to confirm queue position.
    5. Tags requester with username or clickable mention.
    """
    chat_id = message["chat"]["id"]
    if chat_id > 0:
        await send_group_only_notice(message)
        return
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
                message, selected_track, None, user_id, first_name, username, requester_label, requester_mention, is_video, front
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
            message, first_track, status_id, user_id, first_name, username, requester_label, requester_mention, is_video=False, front=front
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
        message, track, status_id, user_id, first_name, username, requester_label, requester_mention, is_video, front
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
    front: bool = False,
) -> None:
    chat_id = message["chat"]["id"]
    if chat_id > 0:
        # Safety net: music never plays in DM
        await send_group_only_notice(message)
        return
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
        if front:
            moved = queue.undo_last()
            if moved is not None:
                queue.add_front(moved)
        q_position = 1 if front else len(queue)
        q_text = (
            f"➕ {to_bold_sans('PLAYING NEXT' if front else 'ADDED TO QUEUE')}\n\n"
            f"{icon} {track.title} ({dur_str})\n"
            f"👤 {track.artist}\n"
            f"📍 {to_small_caps('position')}: #{q_position}\n"
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
    """Handles direct audio or video file downloads sent to Telegram chat: /download <song/video name or URL>."""
    chat_id = message["chat"]["id"]
    reply_to_id = message.get("message_id")
    clean_query = args_text.strip()

    if not clean_query:
        cmd_name = "/video" if is_video else "/download"
        await bot_api_client.send_message(
            chat_id,
            f"⬇️ {to_bold_sans('DOWNLOAD USAGE')}: {cmd_name} <song or video name or link>\n"
            f"💡 {to_small_caps('audio example')}: /download barsaat banjaare\n"
            f"💡 {to_small_caps('video example')}: /video Alan Walker Faded",
            reply_to_message_id=reply_to_id,
        )
        return

    # Send initial status message
    icon = "🎬" if is_video else "🎵"
    status_msg = await bot_api_client.send_message(
        chat_id,
        f"{icon} {to_small_caps('searching and downloading')} \"{sanitize_text(clean_query, 40)}\"...",
        reply_to_message_id=reply_to_id,
    )
    status_id = status_msg.get("result", {}).get("message_id")

    from_user = message.get("from", {})
    user_id = from_user.get("id", 0)
    first_name = from_user.get("first_name", "User")

    # Extract track metadata and stream
    if is_video:
        track = await extractor.extract_video(clean_query, user_id, first_name)
    else:
        track = await extractor.extract(clean_query, user_id, first_name)

    if not track:
        err_txt = f"❌ {to_small_caps('could not find media for:')} \"{sanitize_text(clean_query, 35)}\""
        if status_id:
            await bot_api_client.edit_message_text(chat_id, status_id, err_txt)
        else:
            await bot_api_client.send_message(chat_id, err_txt, reply_to_message_id=reply_to_id)
        return

    # Edit status message to downloading
    dur_str = format_time(track.duration) if track.duration else "Live"
    dl_status_text = (
        f"{icon} <b>{escape_html(track.title)}</b> ({dur_str})\n"
        f"👤 {escape_html(track.artist)}\n"
        f"⬇️ {to_small_caps('downloading & uploading file to telegram')}..."
    )
    if status_id:
        await bot_api_client.edit_message_text(chat_id, status_id, dl_status_text, parse_mode="HTML")

    # Ensure track is downloaded to local file
    download_ok = await extractor.download_track(track)
    local_path = track.playable_source

    if not download_ok or not local_path or not os.path.exists(local_path):
        err_txt = f"❌ {to_small_caps('download failed for:')} \"{sanitize_text(track.title, 35)}\""
        if status_id:
            await bot_api_client.edit_message_text(chat_id, status_id, err_txt)
        else:
            await bot_api_client.send_message(chat_id, err_txt, reply_to_message_id=reply_to_id)
        return

    # Force the delivery format: audio is ALWAYS MP3, video is ALWAYS MP4 (and under Telegram's 50 MB bot limit)
    if is_video:
        local_path = await extractor.ensure_mp4(local_path, max_bytes=TG_UPLOAD_LIMIT_BYTES)
    else:
        local_path = await extractor.ensure_mp3(local_path, max_bytes=TG_UPLOAD_LIMIT_BYTES)
        track.local_filepath = local_path

    real_dur = await extractor.probe_duration(local_path)
    if real_dur >= 5:
        track.duration = real_dur
        dur_str = format_time(real_dur)

    if os.path.getsize(local_path) > TG_UPLOAD_LIMIT_BYTES:
        too_big = f"⚠️ {to_small_caps('file is larger than the 50 mb telegram bot upload limit. try a shorter song or video.')}"
        if status_id:
            await bot_api_client.edit_message_text(chat_id, status_id, too_big)
        else:
            await bot_api_client.send_message(chat_id, too_big, reply_to_message_id=reply_to_id)
        return

    # Send audio or video file directly to chat
    caption = (
        f"{icon} <b>{escape_html(track.title)}</b>\n"
        f"👤 <i>{escape_html(track.artist)}</i>\n"
        f"⏱ Duration: {dur_str}\n"
        f"💽 Format: {'MP4' if is_video else 'MP3'}\n"
        f"⚡ Downloaded via @Aaruu_musicbot"
    )

    if is_video:
        res = await bot_api_client.send_video(
            chat_id=chat_id,
            video_path_or_url=local_path,
            caption=caption,
            duration=track.duration,
            reply_to_message_id=reply_to_id,
        )
    else:
        res = await bot_api_client.send_audio(
            chat_id=chat_id,
            audio_path_or_url=local_path,
            caption=caption,
            title=track.title,
            performer=track.artist,
            duration=track.duration,
            reply_to_message_id=reply_to_id,
        )

    # Delete status message once file is sent
    if status_id:
        try:
            await bot_api_client.delete_message(chat_id, status_id)
        except Exception:
            pass

    if not res.get("ok"):
        logger.warning("Download delivery failed: %s", str(res))
        await bot_api_client.send_message(
            chat_id,
            f"⚠️ {to_small_caps('failed to send downloaded file to telegram')}: {res.get('description', 'Unknown error')}",
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
        if not next_track:
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
            if not next_track:
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


async def handle_setbanner(message: Dict[str, Any], args: str) -> None:
    """Owner-only: /setbanner <section_name> <image_url> - sets a custom banner image
    for a specific /help or /start guide section (e.g. all_commands, getting_started)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not is_sudo(from_id):
        await bot_api_client.send_message(
            chat_id, "⛔ " + to_small_caps("only bot owner can use this command.")
        )
        return

    VALID_SECTIONS = (
        "home", "getting_started", "find_play", "all_commands", "controls",
        "queue_repeat", "group_settings", "group_admins", "troubleshooting", "owner_sudo",
    )

    parts = args.strip().split(maxsplit=1)
    if len(parts) < 2:
        await bot_api_client.send_message(
            chat_id,
            f"🖼 {to_bold_sans('SETBANNER USAGE')}:\n"
            f"/setbanner <section_name> <image_url>\n\n"
            f"💡 {to_small_caps('example')}: /setbanner all_commands https://i.ibb.co/xyz123/banner.jpg\n\n"
            f"📋 {to_small_caps('valid sections')}:\n" + ", ".join(f"<code>{s}</code>" for s in VALID_SECTIONS),
            parse_mode="HTML",
        )
        return

    section, image_url = parts[0].strip().lower(), parts[1].strip()

    if section not in VALID_SECTIONS:
        await bot_api_client.send_message(
            chat_id,
            f"❌ {to_small_caps('invalid section name')}: <code>{sanitize_text(section, 40)}</code>\n\n"
            f"📋 {to_small_caps('valid sections')}:\n" + ", ".join(f"<code>{s}</code>" for s in VALID_SECTIONS),
            parse_mode="HTML",
        )
        return

    if not image_url.startswith(("http://", "https://")):
        await bot_api_client.send_message(
            chat_id, "❌ " + to_small_caps("please provide a valid image url starting with http:// or https://")
        )
        return

    await db.set_setting(f"banner:{section}", image_url)

    from bot.rich_help import invalidate_banner_cache
    invalidate_banner_cache()

    await bot_api_client.send_message(
        chat_id,
        f"✅ {to_small_caps('banner updated for section')}: <code>{section}</code>\n"
        f"🔗 {sanitize_text(image_url, 80)}",
        parse_mode="HTML",
    )


async def handle_broadcast(message: Dict[str, Any], args: str) -> None:
    """Owner-only: /broadcast [-user|-group] <text> (or reply to any message to copy it)."""
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
    """
    /autoplay [on|off] - personal autoplay switch.
    The setting is saved PER USER in the database, so it applies in EVERY group the user plays
    music in (not just the group where the command was typed).
    """
    chat_id = message["chat"]["id"]
    reply_to_id = message.get("message_id")
    user_id = message.get("from", {}).get("id", 0)
    clean_arg = args_text.strip().lower()

    if clean_arg in ("on", "enable", "enabled", "1", "true"):
        await player_manager.set_user_autoplay(user_id, True, chat_id if chat_id < 0 else None)
        msg_text = (
            f"🔄 {to_bold_sans('AUTOPLAY ENABLED')}\n\n"
            f"{to_small_caps('when your songs finish, similar songs with the same theme keep playing automatically.')}\n"
            f"🌐 {to_small_caps('this applies in every group you play music in.')}"
        )
    elif clean_arg in ("off", "disable", "disabled", "0", "false"):
        await player_manager.set_user_autoplay(user_id, False, chat_id if chat_id < 0 else None)
        msg_text = (
            f"⏹️ {to_bold_sans('AUTOPLAY DISABLED')}\n\n"
            f"{to_small_caps('autoplay is off in every group. you will get a recommended-song button when a song ends.')}"
        )
    elif not clean_arg:
        current_st = db.get_user_autoplay(user_id)
        st_label = "ENABLED 🟢" if current_st else "DISABLED 🔴"
        msg_text = (
            f"ℹ️ {to_bold_sans('AUTOPLAY STATUS')}\n\n"
            f"{to_small_caps('your autoplay is currently')}: {st_label}\n"
            f"🌐 {to_small_caps('this setting is the same in every group.')}\n\n"
            f"💡 /autoplay on  |  /autoplay off"
        )
    else:
        msg_text = f"💡 {to_bold_sans('USAGE')}: /autoplay on | /autoplay off"

    await bot_api_client.send_message(chat_id, msg_text, reply_to_message_id=reply_to_id)


# =========================================================================
# EXTRA USER COMMANDS
# =========================================================================

def _requester_id_of(track: Any) -> int:
    return int(getattr(track, "requester_user_id", 0) or getattr(track, "requester_id", 0) or 0)


async def handle_id(message: Dict[str, Any]) -> None:
    """/id - shows user, chat (and replied user) IDs."""
    chat_id = message["chat"]["id"]
    user = message.get("from", {})
    lines = [
        f"🆔 {to_bold_sans('ID INFO')}\n",
        f"👤 {to_small_caps('your id')}: {user.get('id', 0)}",
        f"💬 {to_small_caps('chat id')}: {chat_id}",
    ]
    reply = message.get("reply_to_message")
    if reply and reply.get("from"):
        lines.append(f"↩️ {to_small_caps('replied user id')}: {reply['from'].get('id', 0)}")
    await bot_api_client.send_message(chat_id, "\n".join(lines), reply_to_message_id=message.get("message_id"))


async def handle_prev(message: Dict[str, Any]) -> None:
    """/prev - plays the previously played track again."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    state = await player_manager.get_state(chat_id)
    if not await can_skip_or_stop(chat_id, from_id, state):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only the requester or administrators can play the previous track.")
        )
        return
    prev_track, msg = await player_manager.previous(chat_id, None)
    if prev_track:
        await player_manager._send_new_player_message(chat_id)
    else:
        await bot_api_client.send_message(chat_id, f"⚠️ {msg}")


async def _relative_seek(message: Dict[str, Any], args: str, direction: int) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    state = await player_manager.get_state(chat_id)

    if not state.current_track or not state.is_playing:
        await bot_api_client.send_message(
            chat_id, f"ℹ️ {to_small_caps('no music is currently playing in this chat.')}"
        )
        return
    if not await can_skip_or_stop(chat_id, from_id, state):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only the requester or administrators can seek the track.")
        )
        return

    try:
        step = int(args.strip()) if args.strip() else 10
    except ValueError:
        step = -1
    if step <= 0 or step > 3600:
        cmd_name = "/forward" if direction > 0 else "/rewind"
        await bot_api_client.send_message(
            chat_id, f"⏩ {to_bold_sans('USAGE')}: {cmd_name} <seconds>\n{to_small_caps('example')}: {cmd_name} 15"
        )
        return

    target = int(state.current_position) + direction * step
    max_pos = max(0, int(state.duration) - 2) if state.duration else target
    target = max(0, min(target, max_pos))
    success, msg = await player_manager.seek(chat_id, target)
    await bot_api_client.send_message(chat_id, f"{'⏩' if direction > 0 else '⏪'} {msg}")


async def handle_forward(message: Dict[str, Any], args: str) -> None:
    await _relative_seek(message, args, 1)


async def handle_rewind(message: Dict[str, Any], args: str) -> None:
    await _relative_seek(message, args, -1)


async def handle_remove(message: Dict[str, Any], args: str) -> None:
    """/remove <position> - removes one track from the queue."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    queue = await player_manager.get_queue(chat_id)

    try:
        pos = int(args.strip())
    except ValueError:
        await bot_api_client.send_message(
            chat_id, f"🗑 {to_bold_sans('REMOVE USAGE')}: /remove <queue position>\n{to_small_caps('example')}: /remove 2"
        )
        return

    items = queue.to_list()
    if not items:
        await bot_api_client.send_message(chat_id, f"ℹ️ {to_small_caps('the queue is empty.')}")
        return
    if not (1 <= pos <= len(items)):
        await bot_api_client.send_message(
            chat_id, f"⚠️ {to_small_caps('invalid position. the queue has')} {len(items)} {to_small_caps('track(s).')}"
        )
        return

    target = items[pos - 1]
    if _requester_id_of(target) != from_id and not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only the requester or administrators can remove this track.")
        )
        return

    removed = queue.remove(pos - 1)
    if removed and removed.local_filepath and os.path.exists(removed.local_filepath):
        try:
            os.remove(removed.local_filepath)
        except Exception:
            pass
    await bot_api_client.send_message(
        chat_id, f"🗑 {to_small_caps('removed from queue')}: {removed.title if removed else target.title}"
    )


async def handle_playnext(message: Dict[str, Any], args_text: str) -> None:
    """/playnext <song> - queues a song to play right after the current one (admins)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    if not args_text.strip():
        await bot_api_client.send_message(
            chat_id,
            f"⏭ {to_bold_sans('PLAYNEXT USAGE')}: /playnext <song name or link>\n"
            f"💡 {to_small_caps('example')}: /playnext kesariya",
            reply_to_message_id=message.get("message_id"),
        )
        return
    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only chat administrators can use playnext.")
        )
        return
    await _execute_playback_flow(message, args_text, is_video=False, front=True)


async def handle_recommend(message: Dict[str, Any]) -> None:
    """/recommend - suggests a similar-theme song with a one-tap play button."""
    from player.manager import RECOMMENDATION_CACHE, FINISHED_MESSAGES
    from bot.rich_player import build_finished_rich_ui

    chat_id = message["chat"]["id"]
    state = await player_manager.get_state(chat_id)
    if not state.current_track:
        await bot_api_client.send_message(
            chat_id, f"ℹ️ {to_small_caps('play a song first, then use /recommend to get a similar one.')}"
        )
        return

    status = await bot_api_client.send_message(
        chat_id, f"🔎 {to_small_caps('finding a similar song')}...", reply_to_message_id=message.get("message_id")
    )
    status_id = status.get("result", {}).get("message_id")

    seed = state.current_track
    ids = set(state.autoplay_seen_ids)
    ids.add(str(seed.track_id))
    loop = asyncio.get_running_loop()
    rec = None
    try:
        rec = await asyncio.wait_for(
            loop.run_in_executor(
                None, player_manager._get_extractor().extract_related_track,
                seed, ids, set(state.autoplay_seen_keys), set(state.autoplay_seen_titles),
            ),
            timeout=25,
        )
    except Exception as e:
        logger.debug("recommend lookup note: %s", str(e))

    if status_id:
        try:
            await bot_api_client.delete_message(chat_id, status_id)
        except Exception:
            pass

    if not rec:
        await bot_api_client.send_message(
            chat_id, f"❌ {to_small_caps('could not find a similar song right now. try again in a moment.')}"
        )
        return

    rec.is_video = False
    rec.media_type = "audio"
    RECOMMENDATION_CACHE[chat_id] = rec
    old_msg = FINISHED_MESSAGES.pop(chat_id, None)
    if old_msg:
        try:
            await bot_api_client.delete_message(chat_id, old_msg)
        except Exception:
            pass
    res = await bot_api_client.send_rich_message(chat_id, build_finished_rich_ui(seed, rec, mode="recommend"))
    mid = res.get("result", {}).get("message_id") if isinstance(res, dict) else None
    if mid:
        FINISHED_MESSAGES[chat_id] = mid


async def handle_history(message: Dict[str, Any]) -> None:
    """/history - last 10 songs played in this group."""
    chat_id = message["chat"]["id"]
    rows = await db.get_recent_history(chat_id, 10)
    if not rows:
        await bot_api_client.send_message(
            chat_id, f"ℹ️ {to_small_caps('no songs have been played in this group yet.')}"
        )
        return
    lines = [f"🕘 {to_bold_sans('RECENTLY PLAYED')}\n"]
    for i, r in enumerate(rows, 1):
        dur = format_time(r.get("duration") or 0)
        lines.append(f"{i}. {sanitize_text(r.get('title'), 50)} — {sanitize_text(r.get('artist'), 25)} ({dur})")
    await bot_api_client.send_message(chat_id, "\n".join(lines))


async def handle_top(message: Dict[str, Any]) -> None:
    """/top - most played songs in this group."""
    chat_id = message["chat"]["id"]
    rows = await db.get_top_tracks(chat_id, 10)
    if not rows:
        await bot_api_client.send_message(
            chat_id, f"ℹ️ {to_small_caps('no songs have been played in this group yet.')}"
        )
        return
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    lines = [f"🏆 {to_bold_sans('TOP SONGS IN THIS GROUP')}\n"]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"{medals.get(i, str(i) + '.')} {sanitize_text(r.get('title'), 50)} — "
            f"{r.get('plays', 0)} {to_small_caps('plays')}"
        )
    await bot_api_client.send_message(chat_id, "\n".join(lines))


def _fetch_lyrics_sync(query: str) -> Optional[Tuple[str, str, str]]:
    """Looks up lyrics via the free lyrics.ovh API. Returns (artist, title, lyrics) or None."""
    import json
    import urllib.parse
    import urllib.request

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    def _get(url: str) -> Optional[Dict[str, Any]]:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read().decode("utf-8", errors="ignore"))
        except Exception:
            return None

    candidates: List[Tuple[str, str]] = []
    if " - " in query:
        a, t = query.split(" - ", 1)
        candidates.append((a.strip(), t.strip()))
    sugg = _get("https://api.lyrics.ovh/suggest/" + urllib.parse.quote(query))
    if sugg and isinstance(sugg.get("data"), list):
        for item in sugg["data"][:4]:
            try:
                candidates.append((item["artist"]["name"], item["title"]))
            except Exception:
                continue

    for artist, title in candidates:
        data = _get(f"https://api.lyrics.ovh/v1/{urllib.parse.quote(artist)}/{urllib.parse.quote(title)}")
        if data and data.get("lyrics") and str(data["lyrics"]).strip():
            return artist, title, str(data["lyrics"]).strip()
    return None


async def handle_lyrics(message: Dict[str, Any], args_text: str) -> None:
    """/lyrics [song name | artist - song] - finds lyrics (uses the playing song if no name given)."""
    chat_id = message["chat"]["id"]
    reply_to_id = message.get("message_id")
    query = args_text.strip()

    if not query and chat_id < 0:
        state = await player_manager.get_state(chat_id)
        if state.current_track:
            query = extractor._theme_base(state.current_track.title)
    if not query:
        await bot_api_client.send_message(
            chat_id,
            f"📝 {to_bold_sans('LYRICS USAGE')}: /lyrics <song name>\n"
            f"💡 {to_small_caps('example')}: /lyrics tum hi ho",
            reply_to_message_id=reply_to_id,
        )
        return

    status = await bot_api_client.send_message(
        chat_id, f"📝 {to_small_caps('searching lyrics for')}: {sanitize_text(query, 40)}...",
        reply_to_message_id=reply_to_id,
    )
    status_id = status.get("result", {}).get("message_id")

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _fetch_lyrics_sync, query)

    if not result:
        text = f"❌ {to_small_caps('lyrics not found for')}: {sanitize_text(query, 40)}\n💡 {to_small_caps('try')}: /lyrics artist - song"
    else:
        artist, title, lyrics = result
        header = f"📝 {to_bold_sans(sanitize_text(title, 60))}\n👤 {sanitize_text(artist, 40)}\n\n"
        body = lyrics.replace("\r\n", "\n")
        room = 4000 - len(header)
        if len(body) > room:
            body = body[:room].rsplit("\n", 1)[0] + "\n…"
        text = header + body

    if status_id:
        res = await bot_api_client.edit_message_text(chat_id, status_id, text)
        if res.get("ok"):
            return
    await bot_api_client.send_message(chat_id, text, reply_to_message_id=reply_to_id)


# =========================================================================
# OWNER ADMIN PANEL & OWNER TOOLS
# =========================================================================

_RESTART_CONFIRM: Dict[int, float] = {}


def _fmt_uptime(seconds: int) -> str:
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    parts.append(f"{hours}h {mins}m {secs}s")
    return " ".join(parts)


def _read_system_stats_sync() -> Dict[str, Any]:
    info: Dict[str, Any] = {}
    try:
        import psutil
        info["cpu"] = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        info["ram_used"] = mem.used / (1024 ** 2)
        info["ram_total"] = mem.total / (1024 ** 2)
        info["ram_pct"] = mem.percent
        proc = psutil.Process(os.getpid())
        info["bot_ram"] = proc.memory_info().rss / (1024 ** 2)
        info["cores"] = psutil.cpu_count() or 1
    except Exception:
        try:
            load1 = os.getloadavg()[0]
            info["cpu"] = round(load1 * 100 / (os.cpu_count() or 1), 1)
            info["cores"] = os.cpu_count() or 1
        except Exception:
            info["cpu"] = None
            info["cores"] = os.cpu_count() or 1
    try:
        du = shutil.disk_usage(".")
        info["disk_used"] = du.used / (1024 ** 3)
        info["disk_total"] = du.total / (1024 ** 3)
    except Exception:
        pass
    try:
        total = 0
        for d in ("cache/audio", "cache/video"):
            if os.path.isdir(d):
                for f in os.listdir(d):
                    fp = os.path.join(d, f)
                    if os.path.isfile(fp):
                        total += os.path.getsize(fp)
        info["cache_mb"] = total / (1024 ** 2)
    except Exception:
        info["cache_mb"] = 0.0
    return info


async def _system_text() -> str:
    loop = asyncio.get_running_loop()
    st = await loop.run_in_executor(None, _read_system_stats_sync)
    lines = [f"🖥 {to_bold_sans('SERVER STATUS')}\n"]
    if st.get("cpu") is not None:
        lines.append(f"⚙️ {to_small_caps('cpu')}: {st['cpu']}% ({st.get('cores', 1)} {to_small_caps('cores')})")
    if "ram_total" in st:
        lines.append(
            f"🧠 {to_small_caps('ram')}: {st['ram_used']:.0f}/{st['ram_total']:.0f} MB ({st['ram_pct']}%)"
        )
        lines.append(f"🤖 {to_small_caps('bot process')}: {st['bot_ram']:.0f} MB")
    if "disk_total" in st:
        lines.append(f"💾 {to_small_caps('disk')}: {st['disk_used']:.1f}/{st['disk_total']:.1f} GB")
    lines.append(f"🎞 {to_small_caps('media cache')}: {st.get('cache_mb', 0):.0f} MB")
    lines.append(f"⏱ {to_small_caps('uptime')}: {_fmt_uptime(time.time() - BOT_START_TIME)}")
    return "\n".join(lines)


def clear_media_cache() -> Tuple[int, float]:
    """Deletes cached media files that are not currently playing or queued. Returns (files, MB)."""
    protected = set()
    for st in player_manager._states.values():
        if st.current_track and st.current_track.local_filepath:
            protected.add(os.path.abspath(st.current_track.local_filepath))
    for q in player_manager._queues.values():
        for t in q.to_list():
            if t.local_filepath:
                protected.add(os.path.abspath(t.local_filepath))

    removed = 0
    freed = 0
    for d in ("cache/audio", "cache/video", "/tmp/aaruu_cache"):
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            if not os.path.isfile(fp) or os.path.abspath(fp) in protected:
                continue
            try:
                sz = os.path.getsize(fp)
                os.remove(fp)
                removed += 1
                freed += sz
            except Exception:
                continue
    return removed, freed / (1024 ** 2)


async def _admin_text(section: str) -> str:
    """Builds the text for each owner-panel page."""
    if section == "system":
        return await _system_text()

    if section == "active":
        active = [(cid, s) for cid, s in player_manager._states.items() if s.is_playing and s.current_track]
        if not active:
            return f"🎧 {to_bold_sans('ACTIVE VOICE CHATS')}\n\n{to_small_caps('nothing is playing right now.')}"
        lines = [f"🎧 {to_bold_sans('ACTIVE VOICE CHATS')} ({len(active)})\n"]
        for cid, s in active[:20]:
            cfg = await db.get_chat_settings(cid)
            title = sanitize_text(cfg.get("chat_title") or str(cid), 30)
            lines.append(f"• {title}: {sanitize_text(s.current_track.title, 35)}")
        return "\n".join(lines)

    if section == "groups":
        groups = await db.get_all_chats("group")
        lines = [f"👥 {to_bold_sans('GROUPS')} ({len(groups)})\n"]
        for g in groups[:25]:
            lines.append(f"• {sanitize_text(g.get('chat_title') or 'Unknown', 32)} ({g['chat_id']})")
        if len(groups) > 25:
            lines.append(f"… +{len(groups) - 25} {to_small_caps('more')}")
        if not groups:
            lines.append(to_small_caps("no groups registered yet."))
        return "\n".join(lines)

    if section == "blocked":
        blocked = await db.get_blocked_users()
        if not blocked:
            return f"🚫 {to_bold_sans('BLOCKED USERS')}\n\n{to_small_caps('no users are currently blocked.')}"
        lines = [f"🚫 {to_bold_sans('BLOCKED USERS')} ({len(blocked)})\n"]
        for i, u in enumerate(blocked[:25], 1):
            lines.append(f"{i}. {u.get('user_id')} - {sanitize_text(u.get('reason') or 'No reason', 40)}")
        return "\n".join(lines)

    if section == "sudo":
        from bot.permissions import get_sudo_users, get_owner_id
        sudos = sorted(get_sudo_users())
        lines = [f"👑 {to_bold_sans('SUDO USERS')}\n", f"• {to_small_caps('owner')}: {get_owner_id()}"]
        for u in sudos:
            lines.append(f"• {u}")
        if not sudos:
            lines.append(to_small_caps("no extra sudo users. use /addsudo <user_id>."))
        return "\n".join(lines)

    if section == "bchelp":
        return (
            f"📢 {to_bold_sans('BROADCAST HELP')}\n\n"
            f"• /broadcast <text> - {to_small_caps('all chats')}\n"
            f"• /broadcast -user <text> - {to_small_caps('all users (dm)')}\n"
            f"• /broadcast -group <text> - {to_small_caps('all groups')}\n"
            f"• {to_small_caps('reply to any message with')} /broadcast {to_small_caps('to copy it to everyone')}"
        )

    # home / stats
    stats = await db.get_stats()
    active_playbacks = sum(1 for s in player_manager._states.values() if s.is_playing)
    autoplay_users = sum(1 for v in db._autoplay_cache.values() if v)
    asst = voice_assistant.assistant_username
    asst_line = f"✅ @{asst}" if voice_assistant.is_connected and asst else (
        "✅ connected" if voice_assistant.is_connected else "❌ not connected"
    )
    return (
        f"📊 {to_bold_sans('BOT OVERVIEW')}\n\n"
        f"👥 {to_small_caps('users (dm)')}: {stats.get('users', 0)}\n"
        f"💬 {to_small_caps('groups')}: {stats.get('groups', 0)}\n"
        f"🎵 {to_small_caps('songs played')}: {stats.get('history', 0)}\n"
        f"▶ {to_small_caps('active playbacks')}: {active_playbacks}\n"
        f"🔄 {to_small_caps('autoplay users')}: {autoplay_users}\n"
        f"🚫 {to_small_caps('blocked users')}: {stats.get('blocked', 0)}\n"
        f"🤖 {to_small_caps('assistant')}: {asst_line}\n"
        f"🔧 {to_small_caps('maintenance')}: {'ON 🔴' if MAINTENANCE['on'] else 'OFF 🟢'}\n"
        f"⏱ {to_small_caps('uptime')}: {_fmt_uptime(time.time() - BOT_START_TIME)}"
    )


async def handle_admin(message: Dict[str, Any]) -> None:
    """/admin - opens the owner-only admin panel."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    if not is_owner(from_id):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only the bot owner can open the admin panel."))
        return
    from bot.rich_player import build_admin_panel_rich_ui
    text = await _admin_text("home")
    await bot_api_client.send_rich_message(chat_id, build_admin_panel_rich_ui(text, MAINTENANCE["on"], "home"))


async def handle_admin_callback(cq_id: str, chat_id: int, message_id: int, user_id: int, data: str) -> None:
    """Handles every button on the owner admin panel (callback_data = admin:<action>)."""
    from bot.rich_player import build_admin_panel_rich_ui

    if not is_owner(user_id):
        await bot_api_client.answer_callback_query(cq_id, "⛔ Only the bot owner can use this panel.", show_alert=True)
        return

    action = data.split(":", 1)[1] if ":" in data else "home"

    if action == "close":
        await bot_api_client.answer_callback_query(cq_id, "Panel closed.")
        try:
            await bot_api_client.delete_message(chat_id, message_id)
        except Exception:
            pass
        return

    if action == "restart":
        now = time.time()
        if now - _RESTART_CONFIRM.get(user_id, 0) <= 10:
            _RESTART_CONFIRM.pop(user_id, None)
            await bot_api_client.answer_callback_query(cq_id, "Restarting...")
            await bot_api_client.send_message(chat_id, f"🔄 {to_bold_sans('RESTARTING')}...\n{to_small_caps('back in a few seconds.')}")
            asyncio.create_task(_restart_process())
            return
        _RESTART_CONFIRM[user_id] = now
        await bot_api_client.answer_callback_query(cq_id, "Tap Restart again within 10 seconds to confirm.", show_alert=True)
        return

    section = action
    toast = None
    if action == "maint":
        MAINTENANCE["on"] = not MAINTENANCE["on"]
        await db.set_setting("maintenance", "1" if MAINTENANCE["on"] else "0")
        toast = "Maintenance ON" if MAINTENANCE["on"] else "Maintenance OFF"
        section = "home"
    elif action == "cache":
        loop = asyncio.get_running_loop()
        files, mb = await loop.run_in_executor(None, clear_media_cache)
        toast = f"Cleared {files} files ({mb:.0f} MB)"
        section = "system"
    elif action == "stats":
        section = "home"

    await bot_api_client.answer_callback_query(cq_id, toast)
    text = await _admin_text(section)
    await bot_api_client.edit_message_rich_text(
        chat_id, message_id, build_admin_panel_rich_ui(text, MAINTENANCE["on"], section)
    )


async def _restart_process() -> None:
    """Restarts the bot process in place (the hosting platform restarts it if exec fails)."""
    await asyncio.sleep(1.0)
    try:
        await bot_api_client.close()
    except Exception:
        pass
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception:
        os._exit(0)


async def handle_sysinfo(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    if not is_sudo(message.get("from", {}).get("id", 0)):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    await bot_api_client.send_message(chat_id, await _system_text())


async def handle_restart(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    if not is_owner(message.get("from", {}).get("id", 0)):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    await bot_api_client.send_message(
        chat_id, f"🔄 {to_bold_sans('RESTARTING')}...\n{to_small_caps('back in a few seconds.')}"
    )
    asyncio.create_task(_restart_process())


async def _target_user_from(message: Dict[str, Any], args: str) -> Optional[int]:
    reply = message.get("reply_to_message")
    if reply and reply.get("from"):
        return reply["from"].get("id")
    try:
        return int(args.strip().split()[0])
    except (ValueError, IndexError):
        return None


async def handle_addsudo(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    if not is_owner(from_id):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    target = await _target_user_from(message, args)
    if not target:
        await bot_api_client.send_message(chat_id, f"👑 {to_bold_sans('USAGE')}: /addsudo <user_id> {to_small_caps('or reply')}")
        return
    await db.add_sudo(target, from_id)
    await bot_api_client.send_message(chat_id, f"✅ {to_bold_sans('SUDO ADDED')}\n\n👤 {to_small_caps('user id')}: {target}")


async def handle_delsudo(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)
    if not is_owner(from_id):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    target = await _target_user_from(message, args)
    if not target:
        await bot_api_client.send_message(chat_id, f"👑 {to_bold_sans('USAGE')}: /delsudo <user_id> {to_small_caps('or reply')}")
        return
    existed = await db.remove_sudo(target)
    if existed:
        await bot_api_client.send_message(chat_id, f"✅ {to_bold_sans('SUDO REMOVED')}\n\n👤 {to_small_caps('user id')}: {target}")
    else:
        await bot_api_client.send_message(chat_id, f"⚠️ {to_small_caps('that user is not a db-managed sudo user.')}")


async def handle_sudolist(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    if not is_sudo(message.get("from", {}).get("id", 0)):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    await bot_api_client.send_message(chat_id, await _admin_text("sudo"))


async def handle_maintenance(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    if not is_owner(message.get("from", {}).get("id", 0)):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    arg = args.strip().lower()
    if arg in ("on", "off"):
        MAINTENANCE["on"] = arg == "on"
        await db.set_setting("maintenance", "1" if MAINTENANCE["on"] else "0")
    elif arg:
        await bot_api_client.send_message(chat_id, f"🔧 {to_bold_sans('USAGE')}: /maintenance on | off")
        return
    await bot_api_client.send_message(
        chat_id,
        f"🔧 {to_bold_sans('MAINTENANCE MODE')}: {'ON 🔴' if MAINTENANCE['on'] else 'OFF 🟢'}\n"
        f"{to_small_caps('while on, only the owner and sudo users can use the bot.')}",
    )


async def handle_clearcache(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    if not is_sudo(message.get("from", {}).get("id", 0)):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    loop = asyncio.get_running_loop()
    files, mb = await loop.run_in_executor(None, clear_media_cache)
    await bot_api_client.send_message(
        chat_id, f"🧹 {to_bold_sans('CACHE CLEARED')}\n\n{to_small_caps('files removed')}: {files}\n{to_small_caps('space freed')}: {mb:.0f} MB"
    )


async def handle_groups(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    if not is_sudo(message.get("from", {}).get("id", 0)):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    await bot_api_client.send_message(chat_id, await _admin_text("groups"))


async def handle_activevc(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    if not is_sudo(message.get("from", {}).get("id", 0)):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    await bot_api_client.send_message(chat_id, await _admin_text("active"))


async def handle_leave(message: Dict[str, Any], args: str) -> None:
    """/leave <chat_id> - owner makes the bot leave a group."""
    chat_id = message["chat"]["id"]
    if not is_sudo(message.get("from", {}).get("id", 0)):
        await bot_api_client.send_message(chat_id, "⛔ " + to_small_caps("only bot owner can use this command."))
        return
    try:
        target = int(args.strip().split()[0])
    except (ValueError, IndexError):
        await bot_api_client.send_message(chat_id, f"🚪 {to_bold_sans('USAGE')}: /leave <chat_id>")
        return
    if target > 0:
        await bot_api_client.send_message(chat_id, f"⚠️ {to_small_caps('that is not a group id.')}")
        return
    try:
        await player_manager.stop(target)
    except Exception:
        pass
    res = await bot_api_client.bot_api("leaveChat", {"chat_id": target})
    if res.get("ok"):
        await db.remove_chat(target)
        await bot_api_client.send_message(chat_id, f"🚪 {to_bold_sans('LEFT CHAT')}: {target}")
    else:
        await bot_api_client.send_message(
            chat_id, f"❌ {to_small_caps('could not leave')}: {res.get('description', 'Unknown error')}"
        )
