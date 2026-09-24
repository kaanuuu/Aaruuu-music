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
    stream_url: Optional[str] = None
    local_filepath: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    # Required attributes from requester system
    source: str = "youtube"
    requester_id: int = 0
    requester_username: Optional[str] = None
    requester_mention: str = "User"
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    thumbnail_url: str = ""
    is_video: bool = False
    media_type: str = "audio"  # "audio" or "video"

    def __post_init__(self):
        if not self.requester_id:
            self.requester_id = self.requester_user_id
        if not self.thumbnail_url:
            self.thumbnail_url = self.thumbnail
        if not self.requester_mention or self.requester_mention == "User":
            from utils.formatting import get_user_mention
            self.requester_mention = get_user_mention(self.requester_id, self.requester_name, self.requester_username)

    @property
    def playable_source(self) -> Optional[str]:
        """Returns local file path if downloaded and exists, else stream_url if valid. Never returns a watch webpage URL."""
        import os
        if hasattr(self, "local_filepath") and self.local_filepath and os.path.exists(self.local_filepath):
            try:
                if os.path.getsize(self.local_filepath) > 0:
                    return self.local_filepath
            except Exception:
                pass
        
        # Ensure stream_url is a direct media stream URL and not a watch webpage URL
        if self.stream_url and ("youtube.com/watch" not in self.stream_url and "youtu.be/" not in self.stream_url):
            return self.stream_url

        if self.source_url and ("youtube.com/watch" not in self.source_url and "youtu.be/" not in self.source_url):
            return self.source_url

        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "track_id": self.track_id,
            "title": self.title,
            "artist": self.artist,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "source_url": self.source_url,
            "stream_url": self.stream_url,
            "local_filepath": getattr(self, "local_filepath", None),
            "requester_user_id": self.requester_user_id,
            "requester_name": self.requester_name,
            "created_at": self.created_at,
            "source": self.source,
            "requester_id": self.requester_id,
            "requester_username": self.requester_username,
            "requester_mention": self.requester_mention,
            "request_id": self.request_id,
            "thumbnail_url": self.thumbnail_url,
            "is_video": getattr(self, "is_video", False),
            "media_type": getattr(self, "media_type", "audio"),
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
        self.history: List[Track] = []
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.started_at: float = 0.0
        self.paused_at: Optional[float] = None
        self.pause_duration_offset: float = 0.0
        self.player_message_id: Optional[int] = None
        self.player_message_chat_id: Optional[int] = chat_id
        self.requested_by: Dict[str, Any] = {}
        self.loop_mode: str = "off"  # "off", "track", "queue"
        self.autoplay: bool = True  # Autoplay enabled by default when queue ends
        self.volume: int = 100
        self.playback_status: str = "stopped"

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

    def play(self, track: Track, requester: Dict[str, Any], push_history: bool = True) -> str:
        """Transitions state to playing a new track, generating a fresh session."""
        if push_history and self.current_track:
            # Maintain history stack up to 20 previous tracks
            self.history.append(self.current_track)
            if len(self.history) > 20:
                self.history.pop(0)

        self.current_track = track
        self.requested_by = requester
        self.is_playing = True
        self.is_paused = False  # NEVER remain paused when starting a new track!
        self.started_at = time.time()
        self.paused_at = None
        self.pause_duration_offset = 0.0
        return self.new_session()

    def pop_previous_track(self) -> Optional[Track]:
        """Pops and returns the most recent track from playback history."""
        if self.history:
            return self.history.pop()
        return None

    def toggle_loop_mode(self) -> str:
        """Cycles loop mode: off -> track -> queue -> off."""
        modes = ["off", "track", "queue"]
        idx = modes.index(self.loop_mode) if self.loop_mode in modes else 0
        self.loop_mode = modes[(idx + 1) % len(modes)]
        return self.loop_mode

    def toggle_autoplay(self) -> bool:
        """Toggles autoplay on/off."""
        self.autoplay = not self.autoplay
        return self.autoplay

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
        """Resets playback to 0:00 and forces unpaused playback."""
        if self.current_track:
            self.started_at = time.time()
            self.pause_duration_offset = 0.0
            self.is_paused = False
            self.paused_at = None

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

