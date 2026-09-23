"""
Aaruu Music - Callback Query Router
Processes button clicks from Rich Messages, validates sessions, checks permissions,
and triggers instant UI updates with clean typography and owner access control.
"""

from typing import Any, Dict
from bot.api import bot_api_client
from bot.permissions import is_chat_admin, is_sudo
from bot.rich_help import build_start_rich_message
from bot.rich_player import build_player_rich_message, build_queue_rich_message
from database.db import Database
from player.manager import player_manager
from utils.typography import to_small_caps

db = Database()


async def handle_callback_query(update: Dict[str, Any]) -> None:
    """Processes incoming Telegram callback queries with fast responses."""
    cq = update.get("callback_query", {})
    cq_id = cq.get("id")
    data = cq.get("data", "")
    message = cq.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    from_user = cq.get("from", {})
    user_id = from_user.get("id", 0)

    if not cq_id or not chat_id:
        return

    # Enforce permanent block by bot owner on interactive buttons
    if user_id and db.is_user_blocked(user_id):
        await bot_api_client.answer_callback_query(
            cq_id,
            "🚫 You have been blocked by the bot owner and cannot use Aaruu Music.",
            show_alert=True,
        )
        return

    # Check for Help callbacks: help:<section>
    if data.startswith("help:"):
        section = data.split(":", 1)[1]
        if section == "close":
            await bot_api_client.answer_callback_query(cq_id, "Guide closed.")
            await bot_api_client.delete_message(chat_id, message_id)
            return

        user_is_owner = is_sudo(user_id)
        if section == "owner_sudo" and not user_is_owner:
            await bot_api_client.answer_callback_query(
                cq_id, "⛔ Restricted to Bot Owner.", show_alert=True
            )
            return

        await bot_api_client.answer_callback_query(cq_id)
        rich_help = build_start_rich_message(section, is_owner=user_is_owner)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_help)
        return

    # Check for Player and Queue callbacks: player:<action>:<session> or queue:<action>:<session>
    parts = data.split(":")
    if len(parts) < 3:
        await bot_api_client.answer_callback_query(cq_id, "Invalid action.")
        return

    domain, action, session_id = parts[0], parts[1], parts[2]

    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)

    # Stale button verification
    if state.session_id != session_id:
        await bot_api_client.answer_callback_query(
            cq_id, "This player is no longer active.", show_alert=True
        )
        return

    if action == "pause":
        success, msg = await player_manager.pause(chat_id, session_id)
        await bot_api_client.answer_callback_query(cq_id, msg)
        if success:
            rich_msg = build_player_rich_message(state, queue)
            await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

    elif action == "resume":
        success, msg = await player_manager.resume(chat_id, session_id)
        await bot_api_client.answer_callback_query(cq_id, msg)
        if success:
            rich_msg = build_player_rich_message(state, queue)
            await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

    elif action == "replay":
        success, msg = await player_manager.replay(chat_id, session_id)
        await bot_api_client.answer_callback_query(cq_id, msg)
        if success:
            rich_msg = build_player_rich_message(state, queue)
            await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

    elif action == "skip":
        # Skip requires admin authorization in groups
        if not await is_chat_admin(chat_id, user_id):
            await bot_api_client.answer_callback_query(
                cq_id, "Only chat admins can skip tracks.", show_alert=True
            )
            return

        next_track, msg = await player_manager.skip(chat_id, session_id)
        await bot_api_client.answer_callback_query(cq_id, msg)
        if next_track:
            rich_msg = build_player_rich_message(state, queue)
            await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)
        else:
            await bot_api_client.send_message(
                chat_id, "⏹ " + to_small_caps("playback ended. queue is empty.")
            )

    elif action == "queue":
        await bot_api_client.answer_callback_query(cq_id)
        queue_msg = build_queue_rich_message(state, queue)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, queue_msg)

    elif action == "shuffle":
        count, msg = await player_manager.shuffle(chat_id)
        await bot_api_client.answer_callback_query(cq_id, msg)
        rich_msg = build_player_rich_message(state, queue)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

    elif action == "undo":
        if not await is_chat_admin(chat_id, user_id):
            await bot_api_client.answer_callback_query(
                cq_id, "Only chat admins can remove queued songs.", show_alert=True
            )
            return
        removed = queue.undo_last()
        if removed:
            await bot_api_client.answer_callback_query(
                cq_id, f"Removed '{removed.title}' from queue."
            )
        else:
            await bot_api_client.answer_callback_query(cq_id, "Queue is empty.")
        queue_msg = build_queue_rich_message(state, queue)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, queue_msg)

    elif action == "close":
        await bot_api_client.answer_callback_query(cq_id, "Closed.")
        await bot_api_client.delete_message(chat_id, message_id)

    else:
        await bot_api_client.answer_callback_query(cq_id, "Unknown button.")
