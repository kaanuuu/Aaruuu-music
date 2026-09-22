"""
Aaruu Music - Player Models
Defines Track representation and per-chat independent PlayerState.
"""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional
import uuid


@dataclass
class Track:
    """Represents an individual playable or queued audio track."""

    track_id: str
    title: str
    artist: str
    duration: int  # Duration in seconds
    thumbnail: str  # URL or Telegram file_id
    source_url: str
    requester_user_id: int
    requester_name: str
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "track_id": self.track_id,
            "title": self.title,
            "artist": self.artist,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "source_url": self.source_url,
            "requester_user_id": self.requester_user_id,
            "requester_name": self.requester_name,
            "created_at": self.created_at,
        }


class PlayerState:
    """
    Maintains independent playback state for a single Telegram chat.
    Guarantees thread-safe state inspection and stale-session rejection.
    """

    def __init__(self, chat_id: int):
        self.chat_id: int = chat_id
        self.session_id: str = self._generate_session_id()
        self.current_track: Optional[Track] = None
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.started_at: float = 0.0
        self.paused_at: Optional[float] = None
        self.pause_duration_offset: float = 0.0
        self.player_message_id: Optional[int] = None
        self.player_message_chat_id: Optional[int] = chat_id
        self.requested_by: Dict[str, Any] = {}
        self.loop_mode: str = "off"  # "off", "track", "queue"
        self.volume: int = 100

    @staticmethod
    def _generate_session_id() -> str:
        """Generates a compact, safe session ID (<=12 chars) for Telegram callback_data limits."""
        return uuid.uuid4().hex[:10]

    def new_session(self) -> str:
        """Rotates the session ID to immediately invalidate buttons from old messages."""
        self.session_id = self._generate_session_id()
        return self.session_id

    @property
    def duration(self) -> int:
        if self.current_track:
            return self.current_track.duration
        return 0

    @property
    def current_position(self) -> float:
        """Computes accurate current playback position in seconds."""
        if not self.is_playing or not self.current_track:
            return 0.0

        if self.is_paused and self.paused_at is not None:
            elapsed = self.paused_at - self.started_at - self.pause_duration_offset
        else:
            elapsed = time.time() - self.started_at - self.pause_duration_offset

        pos = max(0.0, elapsed)
        if self.duration > 0:
            return min(pos, float(self.duration))
        return pos

    def play(self, track: Track, requester: Dict[str, Any]) -> str:
        """Transitions state to playing a new track, generating a fresh session."""
        self.current_track = track
        self.requested_by = requester
        self.is_playing = True
        self.is_paused = False
        self.started_at = time.time()
        self.paused_at = None
        self.pause_duration_offset = 0.0
        return self.new_session()

    def pause(self) -> bool:
        """Pauses the active playback."""
        if not self.is_playing or self.is_paused:
            return False
        self.is_paused = True
        self.paused_at = time.time()
        return True

    def resume(self) -> bool:
        """Resumes playback from the paused state."""
        if not self.is_playing or not self.is_paused:
            return False
        if self.paused_at is not None:
            self.pause_duration_offset += time.time() - self.paused_at
        self.is_paused = False
        self.paused_at = None
        return True

    def replay(self) -> None:
        """Resets playback to 0:00."""
        if self.current_track:
            self.started_at = time.time()
            self.pause_duration_offset = 0.0
            if self.is_paused:
                self.paused_at = self.started_at

    def seek(self, target_seconds: float) -> float:
        """Seeks to a specific position in seconds."""
        if not self.current_track or not self.is_playing:
            return 0.0
        target = max(0.0, min(target_seconds, float(self.duration)))
        now = time.time()
        self.started_at = now - target
        self.pause_duration_offset = 0.0
        if self.is_paused:
            self.paused_at = now
        return target

    def stop(self) -> None:
        """Stops current playback and clears track information."""
        self.current_track = None
        self.is_playing = False
        self.is_paused = False
        self.started_at = 0.0
        self.paused_at = None
        self.pause_duration_offset = 0.0
        self.new_session()
