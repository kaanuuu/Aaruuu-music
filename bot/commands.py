"""
Aaruu Music - Command Handlers
Implements all bot commands: /start, /help, /play, /pause, /resume, /replay, /skip,
/queue, /stop, /clear, /volume, /loop, /seek, /nowplaying, /settings.
"""

import time
from typing import Any, Dict, List
from bot.api import bot_api_client
from bot.permissions import is_chat_admin, is_owner, is_sudo
from bot.rich_help import build_start_rich_message
from bot.rich_player import build_player_rich_message, build_queue_rich_message
from database.db import Database
from player.extractor import MediaExtractor
from player.manager import player_manager
from utils.escaping import escape_html, sanitize_text
from utils.formatting import format_time
from utils.logging import logger

extractor = MediaExtractor()
db = Database()
BOT_START_TIME = time.time()

COMMANDS_REGISTRY: List[Dict[str, str]] = [
    {"command": "play", "description": "Play or queue a song or URL"},
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
    {"command": "settings", "description": "View chat music settings"},
    {"command": "ping", "description": "Check bot latency and uptime"},
    {"command": "stats", "description": "View users, groups & system stats"},
    {"command": "broadcast", "description": "Broadcast announcement (Owner only)"},
    {"command": "block", "description": "Block user permanently (Owner only)"},
    {"command": "unblock", "description": "Unblock user (Owner only)"},
    {"command": "blocked", "description": "List blocked users (Owner only)"},
    {"command": "admincache", "description": "Reload admin rights cache"},
    {"command": "help", "description": "Interactive help and guides"},
    {"command": "start", "description": "Start Aaruu Music guide"},
]


async def handle_start(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    rich_msg = build_start_rich_message("home")
    await bot_api_client.send_rich_message(chat_id, rich_msg)


async def handle_help(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    rich_msg = build_start_rich_message("home")
    await bot_api_client.send_rich_message(chat_id, rich_msg)


async def handle_play(message: Dict[str, Any], args_text: str) -> None:
    chat_id = message["chat"]["id"]
    user = message.get("from", {})
    user_id = user.get("id", 0)
    username = user.get("username") or user.get("first_name") or "User"

    if not args_text.strip():
        await bot_api_client.send_message(
            chat_id,
            "<b>Usage:</b> <code>/play &lt;song name or YouTube URL&gt;</code>\n"
            "<i>Example:</i> <code>/play barsaat banjaare</code>",
        )
        return

    # Inform user of resolution
    status_msg = await bot_api_client.send_message(
        chat_id, f"🔎 Searching and preparing: <i>{escape_html(sanitize_text(args_text, 50))}</i>..."
    )
    status_msg_id = status_msg.get("result", {}).get("message_id")

    track = await extractor.extract(args_text, user_id, username)
    if not track:
        if status_msg_id:
            await bot_api_client.bot_api("deleteMessage", {"chat_id": chat_id, "message_id": status_msg_id})
        await bot_api_client.send_message(
            chat_id, "❌ Unable to resolve audio for the specified query or URL."
        )
        return

    is_now_playing, state, queue = await player_manager.play_or_queue(
        chat_id, track, {"id": user_id, "name": username}
    )

    if status_msg_id:
        await bot_api_client.bot_api("deleteMessage", {"chat_id": chat_id, "message_id": status_msg_id})

    if is_now_playing:
        rich_player = build_player_rich_message(state, queue)
        send_res = await bot_api_client.send_rich_message(chat_id, rich_player)
        msg_id = send_res.get("result", {}).get("message_id")
        state.player_message_id = msg_id
        state.player_message_chat_id = chat_id
        # Log to db history
        await db.add_history(
            chat_id,
            track.track_id,
            track.title,
            track.artist,
            track.duration,
            track.source_url,
            username,
        )
    else:
        await bot_api_client.send_message(
            chat_id,
            f"➕ Added to queue: <b>{escape_html(track.title)}</b>\n"
            f"Position in queue: <b>#{len(queue)}</b>",
        )


async def handle_pause(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)
    success, msg = await player_manager.pause(chat_id, state.session_id)
    if success:
        rich_player = build_player_rich_message(state, queue)
        if state.player_message_id:
            await bot_api_client.edit_message_rich_text(
                chat_id, state.player_message_id, rich_player
            )
        await bot_api_client.send_message(chat_id, "⏸ <b>Playback paused.</b>")
    else:
        await bot_api_client.send_message(chat_id, f"⚠️ {msg}")


async def handle_resume(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)
    success, msg = await player_manager.resume(chat_id, state.session_id)
    if success:
        rich_player = build_player_rich_message(state, queue)
        if state.player_message_id:
            await bot_api_client.edit_message_rich_text(
                chat_id, state.player_message_id, rich_player
            )
        await bot_api_client.send_message(chat_id, "▶ <b>Playback resumed.</b>")
    else:
        await bot_api_client.send_message(chat_id, f"⚠️ {msg}")


async def handle_replay(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)
    success, msg = await player_manager.replay(chat_id, state.session_id)
    if success:
        rich_player = build_player_rich_message(state, queue)
        if state.player_message_id:
            await bot_api_client.edit_message_rich_text(
                chat_id, state.player_message_id, rich_player
            )
        await bot_api_client.send_message(chat_id, "↩ <b>Replaying track from beginning.</b>")
    else:
        await bot_api_client.send_message(chat_id, f"⚠️ {msg}")


async def handle_skip(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, user_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ Only chat administrators can skip tracks."
        )
        return

    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)
    next_track, msg = await player_manager.skip(chat_id, state.session_id)
    if next_track:
        rich_player = build_player_rich_message(state, queue)
        send_res = await bot_api_client.send_rich_message(chat_id, rich_player)
        state.player_message_id = send_res.get("result", {}).get("message_id")
    else:
        await bot_api_client.send_message(chat_id, f"⏹ {msg}")


async def handle_queue(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)
    rich_queue = build_queue_rich_message(state, queue)
    await bot_api_client.send_rich_message(chat_id, rich_queue)


async def handle_stop(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, user_id):
        await bot_api_client.send_message(chat_id, "⚠️ Only chat admins can stop playback.")
        return

    state = await player_manager.get_state(chat_id)
    success, msg = await player_manager.stop(chat_id, state.session_id)
    await bot_api_client.send_message(chat_id, f"⏹ <b>{msg}</b>")


async def handle_clear(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, user_id):
        await bot_api_client.send_message(chat_id, "⚠️ Only chat admins can clear the queue.")
        return

    queue = await player_manager.get_queue(chat_id)
    count = queue.clear()
    await bot_api_client.send_message(chat_id, f"🗑 <b>Cleared {count} tracks from the queue.</b>")


async def handle_loop(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, user_id):
        await bot_api_client.send_message(chat_id, "⚠️ Only chat admins can configure loop mode.")
        return

    state = await player_manager.get_state(chat_id)
    clean_arg = args.strip().lower()
    if clean_arg in ("off", "track", "queue"):
        state.loop_mode = clean_arg
        await db.update_chat_settings(chat_id, loop_mode=clean_arg)
        await bot_api_client.send_message(
            chat_id, f"🔁 Loop mode set to: <b>{clean_arg.capitalize()}</b>"
        )
    else:
        await bot_api_client.send_message(
            chat_id,
            "<b>Usage:</b> <code>/loop &lt;off|track|queue&gt;</code>\n"
            f"Current mode: <b>{state.loop_mode}</b>",
        )


async def handle_seek(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, user_id):
        await bot_api_client.send_message(chat_id, "⚠️ Only chat admins can seek playback.")
        return

    state = await player_manager.get_state(chat_id)
    if not state.current_track or not state.is_playing:
        await bot_api_client.send_message(chat_id, "⚠️ No active track to seek.")
        return

    try:
        seconds = float(args.strip())
    except ValueError:
        await bot_api_client.send_message(
            chat_id, "<b>Usage:</b> <code>/seek &lt;seconds&gt;</code> (e.g. <code>/seek 45</code>)"
        )
        return

    target = state.seek(seconds)
    queue = await player_manager.get_queue(chat_id)
    if state.player_message_id:
        rich_player = build_player_rich_message(state, queue)
        await bot_api_client.edit_message_rich_text(chat_id, state.player_message_id, rich_player)

    await bot_api_client.send_message(
        chat_id, f"⏩ Seeked to <b>{format_time(target)}</b>"
    )


async def handle_volume(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    user_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, user_id):
        await bot_api_client.send_message(chat_id, "⚠️ Only chat admins can adjust volume.")
        return

    state = await player_manager.get_state(chat_id)
    if not args.strip():
        await bot_api_client.send_message(
            chat_id, f"🔊 Current volume: <b>{state.volume}%</b>\nUsage: <code>/volume &lt;1-100&gt;</code>"
        )
        return

    try:
        vol = max(1, min(100, int(args.strip())))
        state.volume = vol
        await db.update_chat_settings(chat_id, volume=vol)
        await bot_api_client.send_message(chat_id, f"🔊 Volume updated to <b>{vol}%</b>")
    except ValueError:
        await bot_api_client.send_message(chat_id, "⚠️ Please provide a volume number between 1 and 100.")


async def handle_nowplaying(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)
    rich_player = build_player_rich_message(state, queue)
    send_res = await bot_api_client.send_rich_message(chat_id, rich_player)
    msg_id = send_res.get("result", {}).get("message_id")
    if msg_id:
        state.player_message_id = msg_id


async def handle_settings(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    state = await player_manager.get_state(chat_id)
    settings = await db.get_chat_settings(chat_id)

    text = (
        "<b>⚙️ Aaruu Music Settings</b>\n\n"
        f"• <b>Chat ID:</b> <code>{chat_id}</code>\n"
        f"• <b>Output Volume:</b> <code>{state.volume}%</code>\n"
        f"• <b>Loop Mode:</b> <code>{state.loop_mode}</code>\n"
        f"• <b>Database Sync:</b> Active (SQLite)\n"
        f"• <b>Rich Messages:</b> Enabled\n\n"
        "<i>Use /volume, /loop, and /clear to modify settings.</i>"
    )
    await bot_api_client.send_message(chat_id, text)


async def handle_block(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not is_sudo(from_id):
        await bot_api_client.send_message(chat_id, "⚠️ Only the bot owner can block users.")
        return

    target_user_id = None
    reason = "Blocked by bot owner"

    # Check if this command was sent as a reply to another user's message
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
                chat_id,
                "<b>Usage:</b>\n"
                "• <code>/block &lt;user_id&gt; [reason]</code>\n"
                "• Or reply to a user's message with <code>/block [reason]</code>",
            )
            return

    if not target_user_id:
        await bot_api_client.send_message(
            chat_id,
            "<b>Usage:</b>\n"
            "• <code>/block &lt;user_id&gt; [reason]</code>\n"
            "• Or reply to a user's message with <code>/block [reason]</code>",
        )
        return

    # Owner or sudo cannot be blocked
    if is_sudo(target_user_id):
        await bot_api_client.send_message(
            chat_id, "❌ You cannot block the bot owner or designated sudo users."
        )
        return

    # Check if target is bot itself
    me = await bot_api_client.get_me()
    bot_id = me.get("result", {}).get("id")
    if bot_id and target_user_id == bot_id:
        await bot_api_client.send_message(chat_id, "❌ You cannot block the bot itself.")
        return

    if db.is_user_blocked(target_user_id):
        await bot_api_client.send_message(
            chat_id, f"⚠️ User <code>{target_user_id}</code> is already blocked."
        )
        return

    await db.block_user(target_user_id, from_id, reason)
    await bot_api_client.send_message(
        chat_id,
        f"🚫 <b>User Blocked Permanently</b>\n\n"
        f"• <b>User ID:</b> <code>{target_user_id}</code>\n"
        f"• <b>Reason:</b> {escape_html(reason)}\n"
        f"• <b>Blocked By:</b> <code>{from_id}</code>\n\n"
        f"This user can no longer use Aaruu Music commands or player buttons until unblocked.",
    )


async def handle_unblock(message: Dict[str, Any], args: str) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not is_sudo(from_id):
        await bot_api_client.send_message(chat_id, "⚠️ Only the bot owner can unblock users.")
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
                chat_id,
                "<b>Usage:</b> <code>/unblock &lt;user_id&gt;</code> or reply with <code>/unblock</code>",
            )
            return

    if not target_user_id:
        await bot_api_client.send_message(
            chat_id,
            "<b>Usage:</b> <code>/unblock &lt;user_id&gt;</code> or reply with <code>/unblock</code>",
        )
        return

    if not db.is_user_blocked(target_user_id):
        await bot_api_client.send_message(
            chat_id, f"⚠️ User <code>{target_user_id}</code> is not in the blocked list."
        )
        return

    await db.unblock_user(target_user_id)
    await bot_api_client.send_message(
        chat_id,
        f"✅ <b>User Unblocked</b>\n\n"
        f"• <b>User ID:</b> <code>{target_user_id}</code>\n\n"
        f"This user can now use Aaruu Music again.",
    )


async def handle_blocked(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not is_sudo(from_id):
        await bot_api_client.send_message(chat_id, "⚠️ Only the bot owner can view blocked users.")
        return

    blocked_list = await db.get_blocked_users()
    if not blocked_list:
        await bot_api_client.send_message(chat_id, "✅ No users are currently blocked.")
        return

    lines = [f"🚫 <b>Blocked Users ({len(blocked_list)})</b>:\n"]
    for idx, u in enumerate(blocked_list[:30], 1):
        uid = u.get("user_id")
        reason = u.get("reason") or "No reason provided"
        lines.append(f"{idx}. <code>{uid}</code> — {escape_html(reason)}")

    if len(blocked_list) > 30:
        lines.append(f"\n... and {len(blocked_list) - 30} more users.")

    await bot_api_client.send_message(chat_id, "\n".join(lines))


async def handle_broadcast(message: Dict[str, Any], args: str) -> None:
    """Broadcasts message to all users, groups, or both (Owner/Sudo only)."""
    import asyncio

    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not is_sudo(from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ Only the bot owner can broadcast announcements."
        )
        return

    mode = "all"
    text_content = args.strip()
    reply = message.get("reply_to_message")

    # Check for target flags
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
            "<b>📢 Broadcast Usage:</b>\n\n"
            "• <code>/broadcast &lt;message&gt;</code> — Send to all users & groups\n"
            "• <code>/broadcast -user &lt;message&gt;</code> — Send to all user DMs only\n"
            "• <code>/broadcast -group &lt;message&gt;</code> — Send to all groups only\n\n"
            "<i>Tip: You can also reply to any photo or audio with <code>/broadcast [-user|-group]</code> to forward it intact!</i>",
        )
        return

    all_chats = await db.get_all_chats(mode if mode != "all" else None)
    if not all_chats:
        await bot_api_client.send_message(
            chat_id, f"⚠️ No registered chats found for target: <code>{mode.upper()}</code>."
        )
        return

    init_msg = await bot_api_client.send_message(
        chat_id,
        f"📢 <b>Broadcast In Progress...</b>\n\n"
        f"• <b>Target:</b> <code>{mode.upper()}</code>\n"
        f"• <b>Target Count:</b> <code>{len(all_chats)} chats</code>\n"
        f"<i>Please wait while delivering...</i>",
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
                    text=f"📢 <b>Announcement</b>\n\n{text_content}",
                    parse_mode="HTML",
                )
                if res.get("ok"):
                    success_count += 1
                else:
                    failed_count += 1

            # Sleep 35ms between dispatches to comfortably respect Telegram's rate-limits
            await asyncio.sleep(0.035)

        except Exception as e:
            failed_count += 1
            logger.debug("Broadcast send failed for chat %s: %s", t_id, str(e))

    duration = round(time.time() - start_time, 2)
    report = (
        f"✅ <b>Broadcast Completed!</b>\n\n"
        f"• <b>Scope:</b> <code>{mode.upper()}</code>\n"
        f"• <b>Delivered:</b> <code>{success_count}</code>\n"
        f"• <b>Failed / Blocked:</b> <code>{failed_count}</code>\n"
        f"• <b>Duration:</b> <code>{duration}s</code>"
    )

    if init_msg_id:
        await bot_api_client.edit_message_text(chat_id, init_msg_id, report)
    else:
        await bot_api_client.send_message(chat_id, report)


async def handle_ping(message: Dict[str, Any]) -> None:
    """Calculates Telegram roundtrip latency and continuous 24/7 uptime."""
    chat_id = message["chat"]["id"]
    t0 = time.time()
    sent = await bot_api_client.send_message(chat_id, "🏓 <i>Pinging Telegram servers...</i>")
    t1 = time.time()
    latency_ms = max(1, int((t1 - t0) * 1000))

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
        f"🏓 <b>Pong!</b> <code>{latency_ms}ms</code>\n\n"
        f"• <b>Status:</b> <code>Online (24/7 Resilient)</code>\n"
        f"• <b>Uptime:</b> <code>{uptime_str}</code>\n"
        f"• <b>Heartbeat:</b> <code>Every 10 minutes (Active)</code>"
    )

    sent_id = sent.get("result", {}).get("message_id")
    if sent_id:
        await bot_api_client.edit_message_text(chat_id, sent_id, text)
    else:
        await bot_api_client.send_message(chat_id, text)


async def handle_stats(message: Dict[str, Any]) -> None:
    """Displays comprehensive statistics: users, groups, active streams, and uptime."""
    chat_id = message["chat"]["id"]
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
        f"📊 <b>Aaruu Music System Statistics</b>\n\n"
        f"👥 <b>Total Users (DMs):</b> <code>{stats.get('users', 0)}</code>\n"
        f"💬 <b>Total Groups:</b> <code>{stats.get('groups', 0)}</code>\n"
        f"🎵 <b>Songs Played:</b> <code>{stats.get('history', 0)}</code>\n"
        f"▶ <b>Active Playbacks:</b> <code>{active_playbacks}</code>\n"
        f"🚫 <b>Blocked Users:</b> <code>{stats.get('blocked', 0)}</code>\n"
        f"⏱ <b>Uptime:</b> <code>{uptime_str}</code>\n"
        f"⚡ <b>Engine:</b> <code>Zero-Lag High Audio Pool</code>"
    )
    await bot_api_client.send_message(chat_id, text)


async def handle_shuffle(message: Dict[str, Any]) -> None:
    """Shuffles the active track queue (Chat Admins)."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ Only chat administrators can shuffle the queue."
        )
        return

    count, msg = await player_manager.shuffle(chat_id)
    await bot_api_client.send_message(chat_id, msg)


async def handle_admincache(message: Dict[str, Any]) -> None:
    """Reloads the chat admin rights cache from Telegram servers."""
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ Only chat administrators can reload the admin cache."
        )
        return

    from bot.permissions import clear_admin_cache
    clear_admin_cache(chat_id if not is_sudo(from_id) else None)
    await bot_api_client.send_message(
        chat_id,
        "🔄 <b>Admin Cache Refreshed</b>\n\n"
        "Chat administrator permissions have been successfully synced with Telegram.",
    )


