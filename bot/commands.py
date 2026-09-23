"""
Aaruu Music - Command Handlers
Implements all bot commands with clean, aesthetic Unicode typography
(sans-serif bold, small caps) without raw HTML tags.
Restricts and hides owner-only commands from regular users.
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
from player.voice_chat import voice_assistant
from utils.escaping import sanitize_text
from utils.formatting import format_time
from utils.logging import logger
from utils.typography import to_bold_sans, to_small_caps

extractor = MediaExtractor()
db = Database()
BOT_START_TIME = time.time()

# Only public user commands registered with Telegram BotFather menu
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
            f"🎵 {to_bold_sans('HOW TO STREAM')}:\n"
            f"1. Make sure group Voice Chat is started 🎧\n"
            f"2. Send /play <song name> to stream immediately.\n\n"
            f"Need full command help? Tap 'Help & Commands' below to open the interactive guide in PM!"
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
                            "text": "➕ ᴀᴅᴅ ᴛᴏ ʏᴏᴜʀ ɢʀᴏᴜᴘ",
                            "url": "https://t.me/Aaruu_musicbot?startgroup=true",
                        },
                        {
                            "text": "💬 sᴜᴘᴘᴏʀᴛ",
                            "url": "https://t.me/wzzkaanu",
                        },
                    ],
                },
                {
                    "type": "buttons",
                    "buttons": [
                        {
                            "text": "📖 ʜᴇʟᴘ & ᴄᴏᴍᴍᴀɴᴅs",
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


async def handle_play(message: Dict[str, Any], args_text: str) -> None:
    chat_id = message["chat"]["id"]
    user = message.get("from", {})
    user_id = user.get("id", 0)
    username = user.get("username") or user.get("first_name") or "User"

    if not args_text.strip():
        await bot_api_client.send_message(
            chat_id,
            f"🎵 {to_bold_sans('USAGE')}: /play <song name or link>\n"
            f"💡 {to_small_caps('example')}: /play barsaat banjaare",
        )
        return

    # Inform user of resolution
    status_msg = await bot_api_client.send_message(
        chat_id, f"🔎 {to_small_caps('searching and preparing')}: {sanitize_text(args_text, 50)}..."
    )
    status_msg_id = status_msg.get("result", {}).get("message_id")

    track = await extractor.extract(args_text, user_id, username)
    if not track:
        if status_msg_id:
            await bot_api_client.delete_message(chat_id, status_msg_id)
        await bot_api_client.send_message(
            chat_id, f"❌ {to_small_caps('unable to resolve audio. please try another song name.')}"
        )
        return

    # Verify and auto-invite assistant in group chats before streaming
    if chat_id < 0 and voice_assistant.is_configured:
        if voice_assistant.is_connected and voice_assistant.assistant_id:
            member_resp = await bot_api_client.get_chat_member(
                chat_id, voice_assistant.assistant_id
            )
            is_member = False
            if member_resp.get("ok"):
                status = member_resp.get("result", {}).get("status", "")
                if status in ("member", "administrator", "creator"):
                    is_member = True

            if not is_member:
                # Attempt automatic invitation via chat invite link
                invite_res = await bot_api_client.export_chat_invite_link(chat_id)
                invite_link = invite_res.get("result")
                joined = False
                if invite_link:
                    joined = await voice_assistant.join_chat(invite_link)

                if not joined:
                    asst_tag = (
                        f"@{voice_assistant.assistant_username}"
                        if voice_assistant.assistant_username
                        else "Assistant"
                    )
                    if status_msg_id:
                        await bot_api_client.delete_message(chat_id, status_msg_id)
                    await bot_api_client.send_message(
                        chat_id,
                        f"⚠️ {to_bold_sans('ASSISTANT NOT IN GROUP')}\n\n"
                        f"Voice Assistant ({asst_tag}) is not in this group.\n\n"
                        f"👉 {to_bold_sans('HOW TO RESOLVE')}:\n"
                        f"1. Give this bot 'Invite Users via Link' permission so it can automatically invite the assistant.\n"
                        f"2. Or add {asst_tag} directly to this group and start the Voice Chat.\n\n"
                        f"Then send /play again to stream live in VC! 🎵",
                    )
                    return

    is_now_playing, state, queue = await player_manager.play_or_queue(
        chat_id, track, {"id": user_id, "name": username}
    )

    if status_msg_id:
        await bot_api_client.delete_message(chat_id, status_msg_id)

    # If PyTgCalls encountered an active VC error (e.g. VC not started in group)
    if is_now_playing and voice_assistant.last_error:
        err_lower = voice_assistant.last_error.lower()
        if any(term in err_lower for term in ("creategroupcall", "channel_invalid", "noactivegroupcall", "groupcallnotfound", "call_not_found", "chat_admin_required")):
            asst_tag = f"@{voice_assistant.assistant_username}" if voice_assistant.assistant_username else "Voice Assistant"
            vc_alert = {
                "type": "rich_message",
                "blocks": [
                    {
                        "type": "heading",
                        "text": to_bold_sans("VOICE CHAT NOT ACTIVE"),
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
                        "text": (
                            f"⚠️ {to_bold_sans('GROUP VOICE CHAT IS NOT STARTED')}\n\n"
                            f"Assistant {asst_tag} is in this group, but the group Voice Chat is not active yet!\n\n"
                            f"👉 {to_bold_sans('HOW TO START')}:\n"
                            f"1. Tap the group profile / header at top.\n"
                            f"2. Tap the 3-dots (⋮) -> tap {to_bold_sans('Start Video Chat / Voice Chat')}.\n"
                            f"3. (Optional) Promote {asst_tag} to Admin with 'Manage Video Chats' permission.\n\n"
                            f"Once Voice Chat is running in the group, send /play again to stream live! 🎵"
                        ),
                    },
                    {
                        "type": "buttons",
                        "buttons": [
                            {"text": "💬 " + to_small_caps("support"), "url": "https://t.me/wzzkaanu"},
                        ],
                    },
                ],
            }
            state.stop()
            await bot_api_client.send_rich_message(chat_id, vc_alert)
            return

    if is_now_playing:
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
            username,
        )
    else:
        await bot_api_client.send_message(
            chat_id,
            f"➕ {to_small_caps('added to queue')}: {track.title}\n"
            f"📍 {to_small_caps('position')}: #{len(queue)}",
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
        else:
            await bot_api_client.send_rich_message(chat_id, rich_player)
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
        else:
            await bot_api_client.send_rich_message(chat_id, rich_player)
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
        else:
            await bot_api_client.send_rich_message(chat_id, rich_player)
    else:
        await bot_api_client.send_message(chat_id, f"⚠️ {msg}")


async def handle_skip(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only chat administrators can skip tracks.")
        )
        return

    next_track, msg = await player_manager.skip(chat_id)
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)
    if next_track and state and state.current_track:
        rich_player = build_player_rich_message(state, queue)
        res = await bot_api_client.send_rich_message(chat_id, rich_player)
        state.player_message_id = res.get("result", {}).get("message_id")
    else:
        await bot_api_client.send_message(chat_id, f"⏭ {msg}")


async def handle_queue(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)
    rich_queue = build_queue_rich_message(state, queue)
    await bot_api_client.send_rich_message(chat_id, rich_queue)


async def handle_stop(message: Dict[str, Any]) -> None:
    chat_id = message["chat"]["id"]
    from_id = message.get("from", {}).get("id", 0)

    if not await is_chat_admin(chat_id, from_id):
        await bot_api_client.send_message(
            chat_id, "⚠️ " + to_small_caps("only chat administrators can stop the player.")
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
