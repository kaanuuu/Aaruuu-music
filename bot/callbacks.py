"""
Aaruu Music - Callback Query Router
Processes button clicks from Rich Messages, validates sessions, checks permissions,
and triggers instant UI updates with clean typography and owner access control.
"""

import time
from typing import Any, Dict
from bot.api import bot_api_client
from bot.permissions import is_chat_admin, is_sudo, can_skip_or_stop
from bot.rich_help import build_start_rich_message
from bot.rich_player import build_player_rich_ui, build_player_rich_message, build_queue_rich_message
from database.db import Database
from player.manager import player_manager
from utils.typography import to_bold_sans, to_small_caps

db = Database()
_DEBOUNCE_TIMESTAMPS: Dict[str, float] = {}


async def handle_callback_query(update: Dict[str, Any]) -> None:
    """Processes incoming Telegram callback queries with fast responses and secure validation."""
    cq = update.get("callback_query", {})
    cq_id = cq.get("id")
    data = str(cq.get("data") or "")
    message = cq.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    from_user = cq.get("from", {})
    user_id = from_user.get("id", 0)

    if not cq_id or not chat_id:
        return

    # Debounce: ignore duplicate rapid double-taps within 700ms on the same button
    now = time.time()
    debounce_key = f"{chat_id}:{message_id}:{data}"
    if now - _DEBOUNCE_TIMESTAMPS.get(debounce_key, 0) < 0.7:
        await bot_api_client.answer_callback_query(cq_id)
        return
    _DEBOUNCE_TIMESTAMPS[debounce_key] = now

    # Enforce permanent block by bot owner on interactive buttons
    if user_id and db.is_user_blocked(user_id):
        await bot_api_client.answer_callback_query(
            cq_id,
            "🚫 You have been blocked by the bot owner and cannot use this music bot.",
            show_alert=True,
        )
        return

    # UNIVERSAL CANCEL Callback Handler
    if data.startswith("request:cancel:"):
        parts = data.split(":")
        if len(parts) >= 4:
            req_id_str = parts[2]
            requester_id = int(parts[3])
            
            # User A cannot cancel User B's request (unless they are admin or owner)
            is_adm = await is_chat_admin(chat_id, user_id)
            if user_id != requester_id and not is_adm:
                await bot_api_client.answer_callback_query(
                    cq_id, "⚠️ You cannot cancel another user's request!", show_alert=True
                )
                return
            
            # Record cancellation in the centralized player manager
            player_manager.cancelled_requests.add(req_id_str)
            await bot_api_client.answer_callback_query(cq_id, "■ Request cancelled.")
            from bot.rich_player import build_cancel_rich_ui
            cancel_rich = build_cancel_rich_ui(
                "REQUEST CANCELLED",
                req_id_str,
                requester_id,
                status_text="■ " + to_small_caps("request was cancelled by the user."),
                button_text="■ Cancelled",
                button_style="danger",
            )
            await bot_api_client.edit_message_rich_text(chat_id, message_id, cancel_rich)
        return

    # Check for Search callbacks: search_select:<index>
    if data.startswith("search_select:"):
        from bot.commands import handle_search_select
        await handle_search_select(
            update, cq_id, chat_id, message_id, user_id, from_user.get("first_name", "User"), data
        )
        return

    # Check for Recommendation callback: play_rec:<track_id>
    if data.startswith("play_rec:"):
        from player.manager import RECOMMENDATION_CACHE
        rec_tr = RECOMMENDATION_CACHE.get(chat_id)
        if not rec_tr:
            await bot_api_client.answer_callback_query(cq_id, "Recommendation expired. Try /play again.", show_alert=True)
            return
        await bot_api_client.answer_callback_query(cq_id, f"Preparing '{rec_tr.title}'...")
        fake_msg = {"chat": {"id": chat_id}, "from": {"id": user_id, "first_name": from_user.get("first_name", "User")}, "message_id": message_id}
        from bot.commands import _finish_playback_flow
        from utils.formatting import get_user_mention
        fname = from_user.get("first_name", "User")
        uname = from_user.get("username")
        requester_label = get_user_mention(user_id, fname, uname)
        await _finish_playback_flow(
            fake_msg, rec_tr, None, user_id, fname, uname, requester_label, requester_label, is_video=False
        )
        return

    # Check for Search pagination: search_page:<page>
    if data.startswith("search_page:"):
        page_str = data.split(":", 1)[1]
        try:
            page = int(page_str)
        except ValueError:
            page = 0
        from bot.commands import SEARCH_CACHE
        tracks = SEARCH_CACHE.get(chat_id, [])
        if not tracks:
            await bot_api_client.answer_callback_query(cq_id, "Search session expired.", show_alert=True)
            return
        await bot_api_client.answer_callback_query(cq_id)
        from bot.rich_player import build_search_rich_ui
        search_rich = build_search_rich_ui("Music", tracks, page=page)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, search_rich)
        return

    # Check for Help callbacks: help:<section>
    if data.startswith("help:"):
        section = data.split(":", 1)[1]
        if section == "close":
            await bot_api_client.answer_callback_query(cq_id, "Guide closed.")
            rich_help = build_start_rich_message("close", is_owner=is_sudo(user_id))
            await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_help)
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

    if action == "close":
        await bot_api_client.answer_callback_query(cq_id, "Closed.")
        state = await player_manager.get_state(chat_id)
        queue = await player_manager.get_queue(chat_id)
        queue_msg = build_queue_rich_message(state, queue, is_closed=True)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, queue_msg)
        return

    state = await player_manager.get_state(chat_id)
    queue = await player_manager.get_queue(chat_id)

    # Invalidate old buttons if session rotates
    if state.session_id != session_id and not state.current_track:
        await bot_api_client.answer_callback_query(
            cq_id, "This player is no longer active.", show_alert=True
        )
        return

    # Secure Player Controls
    if action in ("pause", "resume", "replay"):
        # Server-side validation: Requester or Admin only
        if not await can_skip_or_stop(chat_id, user_id, state):
            await bot_api_client.answer_callback_query(
                cq_id, "⚠️ Only the requester or administrators can control the player.", show_alert=True
            )
            return

        if action == "pause":
            success, msg = await player_manager.pause(chat_id, session_id)
            await bot_api_client.answer_callback_query(cq_id, msg)
        elif action == "resume":
            success, msg = await player_manager.resume(chat_id, session_id)
            await bot_api_client.answer_callback_query(cq_id, msg)
        elif action == "replay":
            success, msg = await player_manager.replay(chat_id, session_id)
            await bot_api_client.answer_callback_query(cq_id, msg)

        rich_msg = build_player_rich_message(state, queue)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

    elif action == "skip":
        # Server-side permission check for Skip
        if await can_skip_or_stop(chat_id, user_id, state):
            # Direct Skip authorized!
            next_track, msg = await player_manager.skip(chat_id, session_id)
            await bot_api_client.answer_callback_query(cq_id, msg)
            rich_msg = build_player_rich_ui(state, queue)
            await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)
            if not next_track:
                await bot_api_client.send_message(
                    chat_id, "⏹ " + to_small_caps("playback ended. queue is empty.")
                )
        else:
            # Unauthorized -> Start set-based Vote Skip!
            threshold = 3
            if not hasattr(state, "skip_votes"):
                state.skip_votes = set()

            # Ignore duplicates
            if user_id in state.skip_votes:
                await bot_api_client.answer_callback_query(
                    cq_id, f"⚠️ You have already voted to skip! ({len(state.skip_votes)}/{threshold})", show_alert=True
                )
                return

            state.skip_votes.add(user_id)

            if len(state.skip_votes) >= threshold:
                state.skip_votes.clear()
                await bot_api_client.answer_callback_query(cq_id, "» Vote threshold reached! Skipping...")
                await bot_api_client.send_message(chat_id, f"» {to_bold_sans('VOTE SKIP SUCCESSFUL')}! Skipping to next track...")
                next_track, msg = await player_manager.skip(chat_id, session_id)
                rich_msg = build_player_rich_ui(state, queue)
                await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)
                if not next_track:
                    await bot_api_client.send_message(
                        chat_id, "■ " + to_small_caps("playback ended. queue is empty.")
                    )
            else:
                await bot_api_client.answer_callback_query(
                    cq_id, f"» Skip — {len(state.skip_votes)}/{threshold} votes", show_alert=True
                )
                rich_msg = build_player_rich_ui(state, queue)
                await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

    elif action in ("loop", "autoplay", "shuffle", "undo"):
        # Administrative commands require chat admin check
        if not await is_chat_admin(chat_id, user_id):
            await bot_api_client.answer_callback_query(
                cq_id, "⚠️ Only administrators can change playback settings.", show_alert=True
            )
            return

        if action == "loop":
            mode, msg = await player_manager.toggle_loop_mode(chat_id, session_id)
            await bot_api_client.answer_callback_query(cq_id, msg)
            rich_msg = build_player_rich_message(state, queue)
            await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

        elif action == "autoplay":
            ap, msg = await player_manager.toggle_autoplay(chat_id, session_id)
            await bot_api_client.answer_callback_query(cq_id, msg)
            rich_msg = build_player_rich_message(state, queue)
            await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

        elif action == "shuffle":
            count, msg = await player_manager.shuffle(chat_id)
            await bot_api_client.answer_callback_query(cq_id, msg)
            rich_msg = build_player_rich_message(state, queue)
            await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

        elif action == "undo":
            removed = queue.undo_last()
            if removed:
                # Cleanup removed track's temporary file
                if hasattr(removed, "local_filepath") and removed.local_filepath:
                    import os
                    if os.path.exists(removed.local_filepath):
                        try:
                            os.remove(removed.local_filepath)
                        except Exception:
                            pass
                await bot_api_client.answer_callback_query(
                    cq_id, f"Removed '{removed.title}' from queue."
                )
            else:
                await bot_api_client.answer_callback_query(cq_id, "Queue is empty.")
            queue_msg = build_queue_rich_message(state, queue)
            await bot_api_client.edit_message_rich_text(chat_id, message_id, queue_msg)

    elif action == "nowplaying":
        await bot_api_client.answer_callback_query(cq_id)
        rich_msg = build_player_rich_message(state, queue)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

    elif action == "queue":
        await bot_api_client.answer_callback_query(cq_id)
        queue_msg = build_queue_rich_message(state, queue)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, queue_msg)

    elif action == "prev":
        if not await is_chat_admin(chat_id, user_id):
            await bot_api_client.answer_callback_query(
                cq_id, "⚠️ Only administrators can change tracks.", show_alert=True
            )
            return
        prev_track, msg = await player_manager.previous(chat_id, session_id)
        await bot_api_client.answer_callback_query(cq_id, msg)
        rich_msg = build_player_rich_message(state, queue)
        await bot_api_client.edit_message_rich_text(chat_id, message_id, rich_msg)

    else:
        await bot_api_client.answer_callback_query(cq_id, "Unknown button action.")
