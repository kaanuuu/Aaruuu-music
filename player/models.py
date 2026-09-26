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
        """Returns local file path if downloaded and exists (>0 bytes). Never returns YouTube watch URLs or direct unbuffered stream URLs."""
        import os
        if hasattr(self, "local_filepath") and self.local_filepath and isinstance(self.local_filepath, str) and os.path.exists(self.local_filepath):
            try:
                if os.path.getsize(self.local_filepath) > 0:
                    return self.local_filepath
            except Exception:
                pass

        # Allow direct non-YouTube CDN media URLs (e.g. JioSaavn / direct MP3 files)
        if self.stream_url and isinstance(self.stream_url, str):
            if "youtube.com" not in self.stream_url and "youtu.be" not in self.stream_url and "googlevideo.com" not in self.stream_url:
                if self.stream_url.startswith(("http://", "https://")):
                    return self.stream_url

        if self.source_url and isinstance(self.source_url, str):
            if "youtube.com" not in self.source_url and "youtu.be" not in self.source_url and "googlevideo.com" not in self.source_url:
                if self.source_url.startswith(("http://", "https://")) and any(ext in self.source_url for ext in (".mp3", ".m4a", ".aac", ".ogg", "saavncdn", "sndcdn")):
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
            "request_id": getattr(self, "request_id", ""),
            "thumbnail_url": getattr(self, "thumbnail_url", ""),
            "is_video": getattr(self, "is_video", False),
            "media_type": getattr(self, "media_type", "audio"),
        }


class MediaSourceType:
    LOCAL = "LOCAL_FILE"
    DIRECT_AUDIO = "DIRECT_HTTP_MEDIA"
    DIRECT_VIDEO = "DIRECT_VIDEO"
    YOUTUBE_PAGE = "YOUTUBE_WATCH_URL"
    OTHER_WEBPAGE = "OTHER_WEBPAGE"
    UNKNOWN = "UNKNOWN"


def classify_media_source(source: Optional[str]) -> str:
    """
    Classifies media source into MediaSourceType constants:
    - LOCAL_FILE: local path on disk that exists and is non-empty
    - DIRECT_HTTP_MEDIA: direct audio/video HTTP(S) stream
    - YOUTUBE_WATCH_URL: raw YouTube watch/shorts webpage URL (must NOT be passed to FFmpeg)
    - UNKNOWN: missing, invalid, or unrecognized
    """
    import os
    if not source or not isinstance(source, str):
        return MediaSourceType.UNKNOWN

    clean = source.strip()
    if not clean:
        return MediaSourceType.UNKNOWN

    if os.path.exists(clean) and os.path.isfile(clean):
        try:
            if os.path.getsize(clean) > 0:
                return MediaSourceType.LOCAL
        except Exception:
            pass
        return MediaSourceType.UNKNOWN

    if is_youtube_watch_url(clean):
        return MediaSourceType.YOUTUBE_PAGE

    if is_search_url(clean):
        return MediaSourceType.OTHER_WEBPAGE

    if is_direct_media_url(clean):
        return MediaSourceType.DIRECT_AUDIO

    return MediaSourceType.UNKNOWN


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
        self.autoplay_counter: int = 0  # Counter for sequential autoplay recommendations
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


def is_youtube_watch_url(url: Optional[str]) -> bool:
    """Returns True if the URL points to a YouTube watch, share, short, or live webpage."""
    if not url or not isinstance(url, str):
        return False
    clean = url.lower().strip()
    return any(yt in clean for yt in ("youtube.com/watch", "youtu.be/", "youtube.com/shorts", "youtube.com/live", "youtube.com/embed"))


def is_search_url(url: Optional[str]) -> bool:
    """Returns True if the URL points to a search results webpage or search query string."""
    if not url or not isinstance(url, str):
        return False
    clean = url.lower().strip()
    return any(s in clean for s in ("youtube.com/results", "soundcloud.com/search", "ytsearch", "scsearch"))


def is_direct_media_url(url: Optional[str]) -> bool:
    """Returns True if the URL points to a direct HTTP media stream (not a webpage, YouTube watch URL, or search URL)."""
    if not url or not isinstance(url, str) or not url.strip():
        return False
    clean = url.lower().strip()
    if is_youtube_watch_url(clean) or is_search_url(clean):
        return False
    return clean.startswith(("http://", "https://"))


def classify_media_source(source_path_or_url: Optional[str]) -> str:
    """
    Classifies a candidate media source to avoid passing YouTube watch URLs or invalid streams to FFmpeg:
    Returns one of: 'LOCAL_FILE', 'DIRECT_HTTP_MEDIA' / 'DIRECT_MEDIA', 'YOUTUBE_WATCH_URL' / 'YOUTUBE_WATCH', 'SEARCH_URL', 'UNKNOWN'
    """
    if not source_path_or_url or not isinstance(source_path_or_url, str) or not source_path_or_url.strip():
        return "UNKNOWN"
    
    clean = source_path_or_url.strip()

    # 1. Local file
    import os
    if os.path.exists(clean):
        try:
            if os.path.getsize(clean) > 0:
                return "LOCAL_FILE"
        except Exception:
            pass

    # 2. YouTube watch and shorts URLs
    if is_youtube_watch_url(clean):
        return "YOUTUBE_WATCH_URL"

    # 3. Search URLs
    if is_search_url(clean):
        return "SEARCH_URL"

    # 4. Direct HTTP media URLs
    if is_direct_media_url(clean):
        return "DIRECT_HTTP_MEDIA"

    return "UNKNOWN"

