"""
Aaruu Music - Player Manager
Coordinates per-chat player states, queues, concurrency locks, and playback lifecycle.
"""

import asyncio
import time
from typing import Dict, Optional, Tuple
from player.models import PlayerState, Track
from player.queue import TrackQueue
from player.voice_chat import voice_assistant
from utils.logging import logger


from player.extractor import MediaExtractor

RECOMMENDATION_CACHE: Dict[int, Track] = {}
shared_extractor = MediaExtractor()


class PlayerManager:
    """Central coordinator ensuring isolated state and queues for each chat."""

    def __init__(self):
        self._states: Dict[int, PlayerState] = {}
        self._queues: Dict[int, TrackQueue] = {}
        self._locks: Dict[int, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._timeline_tasks: Dict[int, asyncio.Task] = {}
        self.cancelled_requests = set()

    async def _download_track(self, track: Track) -> bool:
        try:
            return await shared_extractor.download_track(track)
        except Exception as e:
            logger.warning("Could not download track inline in player manager: %s", str(e))
            return False

    async def _update_playback_ui(self, chat_id: int) -> None:
        try:
            state = self._states.get(chat_id)
            queue = self._queues.get(chat_id)
            if state and queue and state.player_message_id:
                from bot.api import bot_api_client
                from bot.rich_player import build_player_rich_ui, build_player_rich_message
                rich_player = build_player_rich_ui(state, queue)
                await bot_api_client.edit_message_rich_text(
                    chat_id, state.player_message_id, rich_player
                )
        except Exception as e:
            logger.debug("PlayerManager: Failed to update playback UI: %s", str(e))

    async def _send_new_player_message(self, chat_id: int) -> None:
        try:
            state = self._states.get(chat_id)
            queue = self._queues.get(chat_id)
            if not state or not state.current_track:
                return

            from bot.api import bot_api_client
            from bot.rich_player import build_player_rich_ui, build_player_rich_message

            # Delete old player message if it exists so we never leave duplicate or stale player messages
            if state.player_message_id:
                try:
                    await bot_api_client.delete_message(chat_id, state.player_message_id)
                except Exception:
                    pass
                state.player_message_id = None

            rich_player = build_player_rich_ui(state, queue)
            send_res = await bot_api_client.send_rich_message(chat_id, rich_player)
            state.player_message_id = send_res.get("result", {}).get("message_id")
            state.player_message_chat_id = chat_id
        except Exception as e:
            logger.debug("PlayerManager: Failed to send new player message: %s", str(e))

    async def _send_or_edit_player_message(self, chat_id: int) -> None:
        try:
            state = self._states.get(chat_id)
            queue = self._queues.get(chat_id)
            if not state or not state.current_track:
                return

            from bot.api import bot_api_client
            from bot.rich_player import build_player_rich_ui, build_player_rich_message
            rich_player = build_player_rich_ui(state, queue)

            if state.player_message_id:
                res = await bot_api_client.edit_message_rich_text(
                    chat_id, state.player_message_id, rich_player
                )
                if not res.get("ok"):
                    send_res = await bot_api_client.send_rich_message(chat_id, rich_player)
                    state.player_message_id = send_res.get("result", {}).get("message_id")
            else:
                send_res = await bot_api_client.send_rich_message(chat_id, rich_player)
                state.player_message_id = send_res.get("result", {}).get("message_id")
        except Exception as e:
            logger.debug("PlayerManager: Failed to send/edit player message: %s", str(e))

    def _start_timeline_task(self, chat_id: int) -> None:
        self._stop_timeline_task(chat_id)
        self._timeline_tasks[chat_id] = asyncio.create_task(self._timeline_loop(chat_id))

    def _stop_timeline_task(self, chat_id: int) -> None:
        task = self._timeline_tasks.pop(chat_id, None)
        if task:
            task.cancel()

    async def _timeline_loop(self, chat_id: int) -> None:
        try:
            while True:
                await asyncio.sleep(1)
                state = self._states.get(chat_id)
                if not state or not state.is_playing:
                    break
                if state.is_paused:
                    continue
                await self._update_playback_ui(chat_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("PlayerManager timeline exception in chat %s: %s", chat_id, str(e))

    async def _get_lock(self, chat_id: int) -> asyncio.Lock:
        async with self._global_lock:
            if chat_id not in self._locks:
                self._locks[chat_id] = asyncio.Lock()
            return self._locks[chat_id]

    async def get_state(self, chat_id: int) -> PlayerState:
        lock = await self._get_lock(chat_id)
        async with lock:
            if chat_id not in self._states:
                self._states[chat_id] = PlayerState(chat_id)
            return self._states[chat_id]

    async def get_queue(self, chat_id: int) -> TrackQueue:
        lock = await self._get_lock(chat_id)
        async with lock:
            if chat_id not in self._queues:
                self._queues[chat_id] = TrackQueue(max_size=50)
            return self._queues[chat_id]

    async def play_or_queue(
        self, chat_id: int, track: Track, requester: dict
    ) -> Tuple[bool, PlayerState, TrackQueue]:
        """
        If nothing is currently playing, starts playback immediately.
        Otherwise, adds the track to the chat queue.
        Returns: (is_now_playing, state, queue)
        """
        lock = await self._get_lock(chat_id)
        async with lock:
            if chat_id not in self._states:
                self._states[chat_id] = PlayerState(chat_id)
            if chat_id not in self._queues:
                self._queues[chat_id] = TrackQueue()

            state = self._states[chat_id]
            queue = self._queues[chat_id]

            if not state.is_playing:
                # Check cancellation BEFORE download
                if track.request_id in self.cancelled_requests:
                    logger.info("Play request %s cancelled before download.", track.request_id)
                    return False, state, queue

                # Prepare playback track properties
                if state.current_track:
                    state.history.append(state.current_track)
                    if len(state.history) > 20:
                        state.history.pop(0)
                state.current_track = track
                state.requested_by = requester
                state.is_paused = False
                state.new_session()
                
                # Reset autoplay counter on manual play
                state.autoplay_counter = 0
                
                # Reset any old skip votes for the new track
                if hasattr(state, "skip_votes"):
                    state.skip_votes.clear()
                
                # 1. Preparing audio status
                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                await self._download_track(track)
                
                # Check cancellation AFTER download
                if track.request_id in self.cancelled_requests:
                    logger.info("Play request %s cancelled after download. Cleaning up.", track.request_id)
                    import os
                    if track.local_filepath and os.path.exists(track.local_filepath):
                        try:
                            os.remove(track.local_filepath)
                        except Exception:
                            pass
                    return False, state, queue

                # 2. Starting playback status
                state.playback_status = "starting"
                await self._update_playback_ui(chat_id)
                
                # 3. Stream to VC
                source_to_play = track.playable_source
                if not source_to_play or not isinstance(source_to_play, str):
                    if not voice_assistant.pytgcalls:
                        # In simulated/test mode, fallback gracefully
                        source_to_play = track.source_url or track.title
                    else:
                        logger.warning("Chat %s: No playable audio source or downloaded file for '%s'", chat_id, track.title)
                        voice_assistant.last_error = f"Unable to download or stream track '{track.title}'."
                        state.stop()
                        await self._update_playback_ui(chat_id)
                        return False, state, queue

                stream_ok = await voice_assistant.play_audio(
                    chat_id, source_to_play, is_video=getattr(track, "is_video", False)
                )
                if not stream_ok:
                    state.stop()
                    await self._update_playback_ui(chat_id)
                    err_msg = voice_assistant.get_last_error(chat_id)
                    logger.info("Chat %s: Streaming failed for '%s' (last_error='%s')", chat_id, track.title, err_msg)
                    return False, state, queue
                
                # 4. Success! Mark status as playing and start timeline task
                state.is_playing = True
                state.playback_status = "playing"
                state.started_at = time.time()
                state.paused_at = None
                state.pause_duration_offset = 0.0
                await self._update_playback_ui(chat_id)
                self._start_timeline_task(chat_id)
                logger.info("Chat %s: Now playing '%s'", chat_id, track.title)
                return True, state, queue
            else:
                queued = queue.add(track)
                logger.info(
                    "Chat %s: Queued '%s' (Queue size: %s, Success: %s)",
                    chat_id,
                    track.title,
                    len(queue),
                    queued,
                )
                return False, state, queue

    async def pause(self, chat_id: int, session_id: str) -> Tuple[bool, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            if not state or state.session_id != session_id:
                return False, "This player is no longer active."
            success = state.pause()
            if success:
                await voice_assistant.pause_audio(chat_id)
                return True, "Playback paused."
            return False, "Player is already paused or not playing."

    async def resume(self, chat_id: int, session_id: str) -> Tuple[bool, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            if not state or state.session_id != session_id:
                return False, "This player is no longer active."
            success = state.resume()
            if success:
                await voice_assistant.resume_audio(chat_id)
                return True, "Playback resumed."
            return False, "Player is not paused."

    async def replay(self, chat_id: int, session_id: str) -> Tuple[bool, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            if not state or state.session_id != session_id:
                return False, "This player is no longer active."
            if state.current_track:
                # 1. Preparing audio status
                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                await self._download_track(state.current_track)
                
                # 2. Starting playback status
                state.playback_status = "starting"
                await self._update_playback_ui(chat_id)
                
                # 3. Stream to VC
                source_to_play = state.current_track.playable_source
                if not source_to_play or not isinstance(source_to_play, str):
                    if not voice_assistant.pytgcalls:
                        source_to_play = state.current_track.source_url or state.current_track.title
                    else:
                        stream_ok = False
                        voice_assistant.last_error = f"Unable to replay '{state.current_track.title}' (no playable stream)."

                if source_to_play and isinstance(source_to_play, str):
                    stream_ok = await voice_assistant.play_audio(
                        chat_id, source_to_play, is_video=getattr(state.current_track, "is_video", False)
                    )
                else:
                    stream_ok = False

                if stream_ok:
                    state.replay()
                    state.playback_status = "playing"
                    await self._update_playback_ui(chat_id)
                else:
                    state.stop()
                    await self._update_playback_ui(chat_id)
            return True, "Replaying current track."

    async def previous(
        self, chat_id: int, session_id: Optional[str] = None
    ) -> Tuple[Optional[Track], str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            if not state:
                return None, "This player is no longer active."
            if session_id and state.session_id != session_id:
                return None, "This player is no longer active."

            prev_track = state.pop_previous_track()
            if prev_track:
                # Prepare playback track properties
                state.current_track = prev_track
                state.requested_by = {"id": prev_track.requester_user_id, "name": prev_track.requester_name}
                state.is_paused = False
                state.new_session()
                
                # Reset autoplay counter on manual play
                state.autoplay_counter = 0
                
                # 1. Preparing audio status
                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                await self._download_track(prev_track)
                
                # 2. Starting playback status
                state.playback_status = "starting"
                await self._update_playback_ui(chat_id)
                
                # 3. Stream to VC
                source_to_play = prev_track.playable_source
                if not source_to_play or not isinstance(source_to_play, str):
                    if not voice_assistant.pytgcalls:
                        source_to_play = prev_track.source_url or prev_track.title
                    else:
                        stream_ok = False
                        voice_assistant.last_error = f"Unable to play '{prev_track.title}' (no playable stream)."

                if source_to_play and isinstance(source_to_play, str):
                    stream_ok = await voice_assistant.play_audio(
                        chat_id, source_to_play, is_video=getattr(prev_track, "is_video", False)
                    )
                else:
                    stream_ok = False

                if stream_ok:
                    state.is_playing = True
                    state.playback_status = "playing"
                    state.started_at = time.time()
                    state.paused_at = None
                    state.pause_duration_offset = 0.0
                    await self._update_playback_ui(chat_id)
                    return prev_track, f"Playing previous track: {prev_track.title}"
                else:
                    state.stop()
                    await self._update_playback_ui(chat_id)
                    return None, f"Failed to play previous track: {prev_track.title}"
            return None, "No previous track in playback history."

    async def auto_advance(self, chat_id: int) -> Tuple[Optional[Track], str]:
        """Automatically transitions to next track when song finishes or skip is triggered."""
        return await self.handle_track_finished(chat_id)

    async def handle_track_finished(self, chat_id: int) -> Tuple[Optional[Track], str]:
        """
        Centralized transition logic when a track ends or is skipped.
        Ensures thread-safe isolated state progression, clean up of temporary files,
        proper looping, and automatic VC disconnect if queue becomes empty.
        """
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            queue = self._queues.get(chat_id) or TrackQueue()
            if not state:
                return None, "Player inactive."

            # Stop any running timeline update task
            self._stop_timeline_task(chat_id)

            # Reset skip votes for the transition
            if hasattr(state, "skip_votes"):
                state.skip_votes.clear()

            old_track = state.current_track

            # Cleanup old track's temporary local file
            if old_track and hasattr(old_track, "local_filepath") and old_track.local_filepath:
                import os
                if os.path.exists(old_track.local_filepath):
                    try:
                        os.remove(old_track.local_filepath)
                        logger.info("[CLEANUP] Deleted temporary file: %s", old_track.local_filepath)
                    except Exception as e:
                        logger.warning("[CLEANUP] Failed to delete %s: %s", old_track.local_filepath, str(e))

            # Loop mode track handling
            if old_track and state.loop_mode == "track":
                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                await self._download_track(old_track)
                loop_source = old_track.playable_source
                if loop_source and isinstance(loop_source, str):
                    state.playback_status = "starting"
                    await self._update_playback_ui(chat_id)
                    stream_ok = await voice_assistant.play_audio(chat_id, loop_source)
                    if stream_ok:
                        state.is_playing = True
                        state.playback_status = "playing"
                        state.started_at = time.time()
                        state.paused_at = None
                        state.pause_duration_offset = 0.0
                        await self._update_playback_ui(chat_id)
                        self._start_timeline_task(chat_id)
                        return old_track, f"Looping track: {old_track.title}"
                logger.warning("[PLAYER] Looping track failed: '%s'. Advancing to queue.", old_track.title)

            if old_track and state.loop_mode == "queue":
                queue.add(old_track)

            # Keep popping and playing from queue until we find one that works or queue is empty
            while len(queue) > 0:
                next_track = queue.pop()
                if not next_track:
                    continue

                # Prepare playback track properties (retain all requester info!)
                state.current_track = next_track
                state.requested_by = {
                    "id": next_track.requester_user_id,
                    "name": next_track.requester_name,
                    "username": getattr(next_track, "requester_username", None),
                    "mention": getattr(next_track, "requester_mention", "User"),
                }
                state.is_paused = False
                state.new_session()
                
                # Reset autoplay counter on manual play
                state.autoplay_counter = 0

                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                
                await self._download_track(next_track)
                next_source = next_track.playable_source
                if not next_source or not isinstance(next_source, str):
                    if not voice_assistant.pytgcalls:
                        next_source = next_track.source_url or next_track.title
                    else:
                        logger.warning("[PLAYER] Track failed extraction/download: '%s'. trying next.", next_track.title)
                        try:
                            from bot.api import bot_api_client
                            from utils.formatting import to_small_caps
                            await bot_api_client.send_message(
                                chat_id, f"⚠️ " + to_small_caps(f"extraction failed for '{next_track.title}'. skipping...")
                            )
                        except Exception:
                            pass
                        continue

                state.playback_status = "starting"
                await self._update_playback_ui(chat_id)

                stream_ok = await voice_assistant.play_audio(
                    chat_id, next_source, is_video=getattr(next_track, "is_video", False)
                )
                if stream_ok:
                    state.is_playing = True
                    state.playback_status = "playing"
                    state.started_at = time.time()
                    state.paused_at = None
                    state.pause_duration_offset = 0.0
                    await self._send_new_player_message(chat_id)
                    self._start_timeline_task(chat_id)
                    return next_track, f"Skipped to: {next_track.title}"
                else:
                    err_msg = voice_assistant.get_last_error(chat_id)
                    logger.warning("[PLAYER] Track failed playback in chat %s: '%s' (%s). trying next.", chat_id, next_track.title, err_msg)
                    try:
                        from bot.api import bot_api_client
                        from utils.formatting import to_small_caps
                        await bot_api_client.send_message(
                            chat_id, f"⚠️ " + to_small_caps(f"playback failed for '{next_track.title}': {err_msg or 'Stream error'}. skipping...")
                        )
                    except Exception:
                        pass
                    continue

            # Queue empty -> Check Autoplay
            if state.autoplay and old_track:
                if state.autoplay_counter >= 5:
                    logger.info("[AUTOPLAY] Autoplay limit (5 tracks) reached for chat %s. Stopping automatic playback.", chat_id)
                    try:
                        from bot.api import bot_api_client
                        from utils.typography import to_bold_sans, to_small_caps
                        loop = asyncio.get_running_loop()
                        auto_track = await loop.run_in_executor(None, shared_extractor.extract_related_track, old_track, list(state.history))
                        if auto_track:
                            RECOMMENDATION_CACHE[chat_id] = auto_track
                            rec_text = (
                                f"📻 {to_bold_sans('AUTOPLAY LIMIT REACHED')}\n\n"
                                f"Autoplay has stopped after 5 consecutive tracks to save your server bandwidth.\n\n"
                                f"💡 {to_bold_sans('RECOMMENDED NEXT')}:\n"
                                f"📀 <b>{auto_track.title}</b> — {auto_track.artist}\n"
                                f"⏱️ {to_small_caps('duration')}: {auto_track.duration // 60}:{auto_track.duration % 60:02d}\n\n"
                                f"👇 Tap the button below to resume playing!"
                            )
                            rec_rich = {
                                "type": "rich_message",
                                "blocks": [
                                    {
                                        "type": "heading",
                                        "text": to_bold_sans("AUTOPLAY PAUSED"),
                                        "size": 1,
                                    },
                                ],
                            }
                            # Add thumbnail if available
                            if isinstance(auto_track.thumbnail, str) and auto_track.thumbnail.startswith(("http://", "https://")):
                                rec_rich["blocks"].append({
                                    "type": "photo",
                                    "photo": {
                                        "type": "photo",
                                        "media": auto_track.thumbnail,
                                    },
                                })
                                
                            rec_rich["blocks"].extend([
                                {
                                    "type": "paragraph",
                                    "text": rec_text,
                                },
                                {
                                    "type": "buttons",
                                    "buttons": [
                                        {
                                            "text": "⏭️ Play Next Music",
                                            "style": "success",
                                            "callback_data": f"play_rec:{auto_track.track_id}",
                                        }
                                    ],
                                    "align": "center",
                                },
                            ])
                            await bot_api_client.send_rich_message(chat_id, rec_rich)
                    except Exception as e:
                        logger.warning("Failed to send recommendation rich message on autoplay limit: %s", str(e))
                else:
                    loop = asyncio.get_running_loop()
                    auto_track = await loop.run_in_executor(None, shared_extractor.extract_related_track, old_track, list(state.history))
                    if auto_track:
                        state.autoplay_counter += 1
                        logger.info("[AUTOPLAY] Automatically playing next recommendation '%s' for chat %s (Count: %d/5)", auto_track.title, chat_id, state.autoplay_counter)
                        
                        # Prepare autoplay track session
                        state.current_track = auto_track
                        state.requested_by = {
                            "id": 0,
                            "name": "Autoplay 📻",
                            "username": None,
                            "mention": "Autoplay 📻",
                        }
                        state.is_paused = False
                        state.new_session()

                        state.playback_status = "preparing"
                        await self._update_playback_ui(chat_id)
                        
                        await self._download_track(auto_track)
                        auto_source = auto_track.playable_source
                        
                        if auto_source and isinstance(auto_source, str):
                            state.playback_status = "starting"
                            await self._update_playback_ui(chat_id)
                            
                            stream_ok = await voice_assistant.play_audio(
                                chat_id, auto_source, is_video=getattr(auto_track, "is_video", False)
                            )
                            if stream_ok:
                                state.is_playing = True
                                state.playback_status = "playing"
                                state.started_at = time.time()
                                state.paused_at = None
                                state.pause_duration_offset = 0.0
                                
                                await self._send_new_player_message(chat_id)
                                self._start_timeline_task(chat_id)
                                
                                # Notify the chat group that we automatically started the next similar song!
                                try:
                                    from bot.api import bot_api_client
                                    from utils.typography import to_bold_sans
                                    await bot_api_client.send_message(
                                        chat_id, f"📻 {to_bold_sans('AUTOPLAY')}: Now playing next similar song ({state.autoplay_counter}/5):\n<b>{auto_track.title}</b> — <i>{auto_track.artist}</i>"
                                    )
                                except Exception:
                                    pass
                                
                                return auto_track, f"Autoplayed next song: {auto_track.title}"

            # If autoplay is off and old track finished, generate next recommendation with play button
            if not state.autoplay and old_track:
                try:
                    from bot.api import bot_api_client
                    from utils.typography import to_bold_sans, to_small_caps
                    loop = asyncio.get_running_loop()
                    auto_track = await loop.run_in_executor(None, shared_extractor.extract_related_track, old_track, list(state.history))
                    if auto_track:
                        RECOMMENDATION_CACHE[chat_id] = auto_track
                        rec_text = (
                            f"✅ {to_bold_sans('SONG FINISHED PLAYING')}\n\n"
                            f"🎵 {to_small_caps('finished')}: <b>{old_track.title}</b>\n"
                            f"👤 {to_small_caps('artist')}: {old_track.artist}\n\n"
                            f"💡 {to_bold_sans('NEXT RECOMMENDED MUSIC')}:\n"
                            f"📀 <b>{auto_track.title}</b> — {auto_track.artist}\n"
                            f"⏱️ {to_small_caps('duration')}: {auto_track.duration // 60}:{auto_track.duration % 60:02d}\n\n"
                            f"👇 Tap the button below to start playing the next song instantly!"
                        )
                        rec_rich = {
                            "type": "rich_message",
                            "blocks": [
                                {
                                    "type": "heading",
                                    "text": to_bold_sans("SONG FINISHED"),
                                    "size": 1,
                                },
                            ],
                        }
                        
                        # Add thumbnail if available
                        if isinstance(auto_track.thumbnail, str) and auto_track.thumbnail.startswith(("http://", "https://")):
                            rec_rich["blocks"].append({
                                "type": "photo",
                                "photo": {
                                    "type": "photo",
                                    "media": auto_track.thumbnail,
                                },
                            })
                            
                        rec_rich["blocks"].extend([
                            {
                                "type": "paragraph",
                                "text": rec_text,
                            },
                            {
                                "type": "buttons",
                                "buttons": [
                                    {
                                        "text": "⏭️ Play Next Music",
                                        "style": "success",
                                        "callback_data": f"play_rec:{auto_track.track_id}",
                                    }
                                ],
                                "align": "center",
                            },
                        ])
                        await bot_api_client.send_rich_message(chat_id, rec_rich)
                except Exception as e:
                    logger.warning("Failed to send recommendation rich message when autoplay off: %s", str(e))

            # Delete old player message if any so chat is left completely clean
            if state.player_message_id:
                try:
                    from bot.api import bot_api_client
                    await bot_api_client.delete_message(chat_id, state.player_message_id)
                except Exception:
                    pass
                state.player_message_id = None

            # Clear state, cleanup, and leave VC
            state.stop()
            if queue:
                queue.clear()
            await voice_assistant.stop_audio(chat_id)
            await voice_assistant.leave_chat(chat_id)
            logger.info("[PLAYER] Queue is empty. Assistant left VC for chat %s", chat_id)

            # Send beautiful Queue Finished & Recommendation panel to the group
            try:
                from bot.api import bot_api_client
                from utils.typography import to_bold_sans, to_small_caps
                
                finish_text = (
                    f"🎵 {to_bold_sans('PLAYBACK FINISHED')}\n\n"
                    f"The queue has ended and the assistant has left the voice chat. Thank you for listening! 🎧\n\n"
                )
                
                finish_rich = {
                    "type": "rich_message",
                    "blocks": [
                        {
                            "type": "heading",
                            "text": to_bold_sans("PLAYBACK FINISHED"),
                            "size": 1,
                        },
                    ]
                }
                
                buttons = []
                
                # Fetch a smart recommendation to show as an instant play button!
                if old_track:
                    loop = asyncio.get_running_loop()
                    auto_track = await loop.run_in_executor(None, shared_extractor.extract_related_track, old_track, list(state.history))
                    if auto_track:
                        RECOMMENDATION_CACHE[chat_id] = auto_track
                        # Limit title length for the button
                        btn_title = auto_track.title[:25] + "..." if len(auto_track.title) > 25 else auto_track.title
                        
                        finish_text += (
                            f"💡 {to_bold_sans('RECOMMENDED NEXT')}:\n"
                            f"📀 <b>{auto_track.title}</b> — {auto_track.artist}\n"
                            f"⏱️ {to_small_caps('duration')}: {auto_track.duration // 60}:{auto_track.duration % 60:02d}\n\n"
                            f"Tap the green button below to play this recommended song instantly!"
                        )
                        
                        # Add thumbnail to the finish rich block if available
                        if isinstance(auto_track.thumbnail, str) and auto_track.thumbnail.startswith(("http://", "https://")):
                            finish_rich["blocks"].append({
                                "type": "photo",
                                "photo": {
                                    "type": "photo",
                                    "media": auto_track.thumbnail,
                                },
                            })
                            
                        buttons.append({
                            "text": f"▶️ Play: {btn_title}",
                            "style": "success",
                            "callback_data": f"play_rec:{auto_track.track_id}",
                        })
                
                finish_rich["blocks"].append({
                    "type": "paragraph",
                    "text": finish_text,
                })
                
                # Search button to explore new tracks easily
                buttons.append({
                    "text": "🔎 Search Music",
                    "style": "primary",
                    "callback_data": "help:commands",
                })
                
                finish_rich["blocks"].append({
                    "type": "buttons",
                    "buttons": buttons,
                    "align": "center",
                })
                
                await bot_api_client.send_rich_message(chat_id, finish_rich)
            except Exception as fe:
                logger.warning("Failed to send queue finished notification message: %s", str(fe))

            return None, "Queue is empty. Playback ended."

    async def toggle_loop_mode(
        self, chat_id: int, session_id: Optional[str] = None
    ) -> Tuple[str, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            if not state:
                return "off", "Player inactive."
            if session_id and state.session_id != session_id:
                return state.loop_mode, "Player inactive."
            mode = state.toggle_loop_mode()
            return mode, f"Loop mode set to: {mode.upper()}"

    async def set_loop_mode(self, chat_id: int, mode: str) -> Tuple[bool, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            if not state:
                return False, "Player inactive."
            if mode in ("off", "track", "queue"):
                state.loop_mode = mode
                return True, f"Loop mode set to: {mode.upper()}"
            return False, "Invalid loop mode."

    async def toggle_autoplay(
        self, chat_id: int, session_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            if chat_id not in self._states:
                self._states[chat_id] = PlayerState(chat_id)
            state = self._states[chat_id]
            if session_id and state.session_id != session_id:
                return state.autoplay, "Player inactive."
            ap = state.toggle_autoplay()
            return ap, f"Autoplay is now {'ENABLED' if ap else 'DISABLED'}"

    async def set_autoplay(self, chat_id: int, enabled: bool) -> Tuple[bool, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            if chat_id not in self._states:
                self._states[chat_id] = PlayerState(chat_id)
            state = self._states[chat_id]
            state.autoplay = enabled
            return enabled, f"Autoplay {'ENABLED' if enabled else 'DISABLED'} for this chat."

    async def get_autoplay(self, chat_id: int) -> bool:
        lock = await self._get_lock(chat_id)
        async with lock:
            if chat_id not in self._states:
                self._states[chat_id] = PlayerState(chat_id)
            return self._states[chat_id].autoplay

    async def seek(self, chat_id: int, seconds: int) -> Tuple[bool, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            if not state or not state.current_track:
                return False, "No track is currently playing."
            if seconds < 0:
                return False, "Seek position must be 0 or greater."
            if state.duration > 0 and seconds >= state.duration:
                return False, f"Seek position ({seconds}s) exceeds track duration ({state.duration}s)."

            pos = state.seek(float(seconds))
            
            # 1. Preparing audio status
            state.playback_status = "preparing"
            await self._update_playback_ui(chat_id)
            await self._download_track(state.current_track)

            # 2. Starting playback status
            state.playback_status = "starting"
            await self._update_playback_ui(chat_id)

            # 3. Stream with seek offset
            source_to_play = state.current_track.playable_source
            if not source_to_play and not voice_assistant.pytgcalls:
                source_to_play = state.current_track.source_url or state.current_track.title

            if source_to_play and isinstance(source_to_play, str):
                stream_ok = await voice_assistant.play_audio(
                    chat_id, 
                    source_to_play, 
                    seek_seconds=float(seconds),
                    is_video=getattr(state.current_track, "is_video", False)
                )
            else:
                stream_ok = False
                voice_assistant.last_error = f"Cannot seek '{state.current_track.title}' (no playable stream)."

            if stream_ok:
                state.is_playing = True
                state.playback_status = "playing"
                await self._update_playback_ui(chat_id)
                return True, f"Seeked to {int(pos)} seconds."
            else:
                state.stop()
                await self._update_playback_ui(chat_id)
                err_msg = voice_assistant.get_last_error(chat_id)
                return False, f"Failed to seek to {seconds} seconds: {err_msg or 'Stream error'}"

    async def set_volume(self, chat_id: int, volume: int) -> Tuple[bool, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            if not state:
                return False, "Player inactive."
            state.volume = max(1, min(100, volume))
            return True, f"Volume set to {state.volume}%"


    async def skip(
        self, chat_id: int, session_id: Optional[str] = None
    ) -> Tuple[Optional[Track], str]:
        """
        Skips current song. If queue has items, starts next song.
        If loop mode is active, handles re-queueing.
        """
        state = self._states.get(chat_id)
        if not state:
            return None, "This player is no longer active."
        if session_id and state.session_id != session_id:
            return None, "This player is no longer active."

        next_tr, msg = await self.handle_track_finished(chat_id)
        if next_tr:
            return next_tr, f"Skipped to: {next_tr.title}"
        return None, "Queue is empty. Playback ended."

    async def shuffle(self, chat_id: int) -> Tuple[int, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            queue = self._queues.get(chat_id)
            if not queue or len(queue) == 0:
                return 0, "Queue is empty. Nothing to shuffle."
            count = queue.shuffle()
            return count, f"🔀 Shuffled {count} queued tracks."

    async def stop(self, chat_id: int, session_id: Optional[str] = None) -> Tuple[bool, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            queue = self._queues.get(chat_id)
            if not state:
                return False, "This player is no longer active."
            if session_id and state.session_id != session_id:
                return False, "This player is no longer active."

            # Stop the timeline update task
            self._stop_timeline_task(chat_id)

            # Reset skip votes
            if hasattr(state, "skip_votes"):
                state.skip_votes.clear()

            # Clean up old track local file
            old_track = state.current_track
            if old_track and hasattr(old_track, "local_filepath") and old_track.local_filepath:
                import os
                if os.path.exists(old_track.local_filepath):
                    try:
                        os.remove(old_track.local_filepath)
                    except Exception:
                        pass

            # Clean up queue track local files
            if queue:
                import os
                for tr in queue.to_list():
                    if hasattr(tr, "local_filepath") and tr.local_filepath and os.path.exists(tr.local_filepath):
                        try:
                            os.remove(tr.local_filepath)
                        except Exception:
                            pass
                queue.clear()

            if state.player_message_id:
                try:
                    from bot.api import bot_api_client
                    await bot_api_client.delete_message(chat_id, state.player_message_id)
                except Exception:
                    pass
                state.player_message_id = None

            state.stop()
            await voice_assistant.stop_audio(chat_id)
            await voice_assistant.leave_chat(chat_id)
            return True, "Playback stopped and queue cleared."


player_manager = PlayerManager()
