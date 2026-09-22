"""
Aaruu Music - Player Manager
Coordinates per-chat player states, queues, concurrency locks, and playback lifecycle.
"""

import asyncio
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
                state.play(track, requester)
                await voice_assistant.play_audio(chat_id, track.source_url)
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
            state.replay()
            if state.current_track:
                await voice_assistant.play_audio(chat_id, state.current_track.source_url)
            return True, "Replaying current track."

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
                state.play(old_track, state.requested_by)
                await voice_assistant.play_audio(chat_id, old_track.source_url)
                return old_track, f"Looping track: {old_track.title}"
            elif old_track and state.loop_mode == "queue":
                queue.add(old_track)

            next_track = queue.pop()
            if next_track:
                state.play(next_track, {"user_id": next_track.requester_user_id, "name": next_track.requester_name})
                await voice_assistant.play_audio(chat_id, next_track.source_url)
                return next_track, f"Skipped to: {next_track.title}"
            else:
                state.stop()
                await voice_assistant.stop_audio(chat_id)
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
