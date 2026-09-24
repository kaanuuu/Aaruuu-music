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


class PlayerManager:
    """Central coordinator ensuring isolated state and queues for each chat."""

    def __init__(self):
        self._states: Dict[int, PlayerState] = {}
        self._queues: Dict[int, TrackQueue] = {}
        self._locks: Dict[int, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _download_track(self, track: Track) -> None:
        try:
            from player.extractor import MediaExtractor
            extractor = MediaExtractor()
            await extractor.download_track(track)
        except Exception as e:
            logger.warning("Could not download track inline in player manager: %s", str(e))

    async def _update_playback_ui(self, chat_id: int) -> None:
        try:
            state = self._states.get(chat_id)
            queue = self._queues.get(chat_id)
            if state and queue and state.player_message_id:
                from bot.api import bot_api_client
                from bot.rich_player import build_player_rich_message
                rich_player = build_player_rich_message(state, queue)
                await bot_api_client.edit_message_rich_text(
                    chat_id, state.player_message_id, rich_player
                )
        except Exception as e:
            logger.debug("PlayerManager: Failed to update playback UI: %s", str(e))

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
                # Prepare playback track properties
                if state.current_track:
                    state.history.append(state.current_track)
                    if len(state.history) > 20:
                        state.history.pop(0)
                state.current_track = track
                state.requested_by = requester
                state.is_paused = False
                state.new_session()
                
                # 1. Preparing audio status
                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                await self._download_track(track)
                
                # 2. Starting playback status
                state.playback_status = "starting"
                await self._update_playback_ui(chat_id)
                
                # 3. Stream to VC
                stream_ok = await voice_assistant.play_audio(chat_id, track.playable_source)
                if not stream_ok:
                    state.stop()
                    await self._update_playback_ui(chat_id)
                    logger.info("Chat %s: Streaming failed for '%s' (last_error='%s')", chat_id, track.title, voice_assistant.last_error)
                    return False, state, queue
                
                # 4. Success! Mark status as playing
                state.is_playing = True
                state.playback_status = "playing"
                state.started_at = time.time()
                state.paused_at = None
                state.pause_duration_offset = 0.0
                await self._update_playback_ui(chat_id)
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
                stream_ok = await voice_assistant.play_audio(chat_id, state.current_track.playable_source)
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
                
                # 1. Preparing audio status
                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                await self._download_track(prev_track)
                
                # 2. Starting playback status
                state.playback_status = "starting"
                await self._update_playback_ui(chat_id)
                
                # 3. Stream to VC
                stream_ok = await voice_assistant.play_audio(chat_id, prev_track.playable_source)
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
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            queue = self._queues.get(chat_id) or TrackQueue()
            if not state or not state.is_playing:
                return None, "Player inactive."

            old_track = state.current_track

            # Loop mode handling
            if old_track and state.loop_mode == "track":
                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                await self._download_track(old_track)
                
                state.playback_status = "starting"
                await self._update_playback_ui(chat_id)
                
                stream_ok = await voice_assistant.play_audio(chat_id, old_track.playable_source)
                if stream_ok:
                    state.is_playing = True
                    state.playback_status = "playing"
                    state.started_at = time.time()
                    state.paused_at = None
                    state.pause_duration_offset = 0.0
                    await self._update_playback_ui(chat_id)
                    return old_track, f"Looping track: {old_track.title}"
                else:
                    state.stop()
                    await self._update_playback_ui(chat_id)
                    return None, f"Failed to loop track: {old_track.title}"
            elif old_track and state.loop_mode == "queue":
                queue.add(old_track)

            next_track = queue.pop()
            if next_track:
                # Prepare playback track properties
                state.current_track = next_track
                state.requested_by = {"id": next_track.requester_user_id, "name": next_track.requester_name}
                state.is_paused = False
                state.new_session()
                
                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                await self._download_track(next_track)
                
                state.playback_status = "starting"
                await self._update_playback_ui(chat_id)
                
                stream_ok = await voice_assistant.play_audio(chat_id, next_track.playable_source)
                if stream_ok:
                    state.is_playing = True
                    state.playback_status = "playing"
                    state.started_at = time.time()
                    state.paused_at = None
                    state.pause_duration_offset = 0.0
                    await self._update_playback_ui(chat_id)
                    return next_track, f"Now playing: {next_track.title}"
                else:
                    state.stop()
                    await self._update_playback_ui(chat_id)
                    return None, f"Failed to play next track: {next_track.title}"

            # Queue empty -> Check Autoplay
            if state.autoplay and old_track:
                from player.extractor import MediaExtractor
                extractor = MediaExtractor()
                loop = asyncio.get_running_loop()
                auto_track = await loop.run_in_executor(None, extractor.extract_related_track, old_track)
                if auto_track:
                    # Prepare autoplay track properties
                    state.current_track = auto_track
                    state.requested_by = {"id": 0, "name": "Autoplay 📻"}
                    state.is_paused = False
                    state.new_session()
                    
                    state.playback_status = "preparing"
                    await self._update_playback_ui(chat_id)
                    await self._download_track(auto_track)
                    
                    state.playback_status = "starting"
                    await self._update_playback_ui(chat_id)
                    
                    stream_ok = await voice_assistant.play_audio(chat_id, auto_track.playable_source)
                    if stream_ok:
                        state.is_playing = True
                        state.playback_status = "playing"
                        state.started_at = time.time()
                        state.paused_at = None
                        state.pause_duration_offset = 0.0
                        await self._update_playback_ui(chat_id)
                        return auto_track, f"Autoplay: {auto_track.title}"
                    else:
                        state.stop()
                        await self._update_playback_ui(chat_id)
                        return None, f"Failed to play autoplay: {auto_track.title}"

            state.stop()
            await voice_assistant.stop_audio(chat_id)
            await self._update_playback_ui(chat_id)
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
            state = self._states.get(chat_id)
            if not state:
                return False, "Player inactive."
            if session_id and state.session_id != session_id:
                return state.autoplay, "Player inactive."
            ap = state.toggle_autoplay()
            return ap, f"Autoplay is now {'ENABLED' if ap else 'DISABLED'}"

    async def seek(self, chat_id: int, seconds: int) -> Tuple[bool, str]:
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            if not state or not state.current_track:
                return False, "No active playback to seek."
            pos = state.seek(float(seconds))
            return True, f"Seeked to {int(pos)}s."

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
        lock = await self._get_lock(chat_id)
        async with lock:
            state = self._states.get(chat_id)
            queue = self._queues.get(chat_id) or TrackQueue()
            if not state:
                return None, "This player is no longer active."

            if session_id and state.session_id != session_id:
                return None, "This player is no longer active."

            old_track = state.current_track

            # Handle loop mode
            if old_track and state.loop_mode == "track":
                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                await self._download_track(old_track)
                
                state.playback_status = "starting"
                await self._update_playback_ui(chat_id)
                
                stream_ok = await voice_assistant.play_audio(chat_id, old_track.playable_source)
                if stream_ok:
                    state.is_playing = True
                    state.playback_status = "playing"
                    state.started_at = time.time()
                    state.paused_at = None
                    state.pause_duration_offset = 0.0
                    await self._update_playback_ui(chat_id)
                    return old_track, f"Looping track: {old_track.title}"
                else:
                    state.stop()
                    await self._update_playback_ui(chat_id)
                    return None, f"Failed to loop track: {old_track.title}"
            elif old_track and state.loop_mode == "queue":
                queue.add(old_track)

            next_track = queue.pop()
            if next_track:
                # Prepare skip playback track properties
                state.current_track = next_track
                state.requested_by = {"id": next_track.requester_user_id, "name": next_track.requester_name}
                state.is_paused = False
                state.new_session()
                
                state.playback_status = "preparing"
                await self._update_playback_ui(chat_id)
                await self._download_track(next_track)
                
                state.playback_status = "starting"
                await self._update_playback_ui(chat_id)
                
                stream_ok = await voice_assistant.play_audio(chat_id, next_track.playable_source)
                if stream_ok:
                    state.is_playing = True
                    state.playback_status = "playing"
                    state.started_at = time.time()
                    state.paused_at = None
                    state.pause_duration_offset = 0.0
                    await self._update_playback_ui(chat_id)
                    return next_track, f"Skipped to: {next_track.title}"
                else:
                    state.stop()
                    await self._update_playback_ui(chat_id)
                    return None, f"Failed to skip to: {next_track.title}"
            else:
                state.stop()
                await voice_assistant.stop_audio(chat_id)
                await self._update_playback_ui(chat_id)
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

            state.stop()
            if queue:
                queue.clear()
            await voice_assistant.stop_audio(chat_id)
            return True, "Playback stopped and queue cleared."


player_manager = PlayerManager()
