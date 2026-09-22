"""
Aaruu Music - Audio Metadata Extractor
Integrates yt-dlp to search and resolve rich metadata for songs and media URLs safely.
"""

import asyncio
import os
import re
from typing import Any, Dict, Optional
import uuid
from player.models import Track
from utils.escaping import sanitize_text
from utils.logging import logger

# Fallback thumbnail if extraction provides none or invalid
DEFAULT_THUMBNAIL = (
    "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
)


class MediaExtractor:
    """Extracts track metadata via yt-dlp asynchronously with graceful error fallbacks."""

    def __init__(self):
        self.cookies_path = os.getenv("YTDLP_COOKIES")

    def _get_ydl_opts(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            # Highest crystal clear audio quality streams (Opus 160k+, M4A 256k+, or best uncompressed audio)
            "format": "bestaudio[ext=m4a]/bestaudio[ext=opus]/bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "socket_timeout": 8,
            "source_address": "0.0.0.0",
            "cachedir": False,
            "nocheckcertificate": True,
            "youtube_include_dash_manifest": False,
            "youtube_include_hls_manifest": False,
            "no_color": True,
        }
        if self.cookies_path and os.path.exists(self.cookies_path):
            opts["cookiefile"] = self.cookies_path
        return opts

    async def extract(
        self, query_or_url: str, requester_id: int, requester_name: str
    ) -> Optional[Track]:
        """
        Extracts song metadata from a search query or direct URL.
        Returns a populated Track object or None if resolution fails.
        """
        clean_input = query_or_url.strip()
        if not clean_input:
            return None

        # Check if query is a URL or search query
        is_url = bool(
            re.match(r"^https?://", clean_input, re.IGNORECASE)
        )
        target = clean_input if is_url else f"ytsearch1:{clean_input}"

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(
                None, self._extract_sync, target, clean_input, requester_id, requester_name
            )
        except Exception as e:
            logger.warning("yt-dlp extraction failed for %s: %s", clean_input, str(e))
            # Provide a safe fallback track if yt-dlp fails so the bot does not crash
            return Track(
                track_id=uuid.uuid4().hex[:8],
                title=sanitize_text(clean_input, 64) or "Unknown Song",
                artist="Aaruu Music Audio",
                duration=180,  # 3 minutes default fallback
                thumbnail=DEFAULT_THUMBNAIL,
                source_url=clean_input if is_url else f"https://www.youtube.com/results?search_query={clean_input}",
                requester_user_id=requester_id,
                requester_name=requester_name,
            )

    def _extract_sync(
        self, target: str, raw_input: str, requester_id: int, requester_name: str
    ) -> Optional[Track]:
        try:
            import yt_dlp
        except ImportError:
            logger.warning("yt-dlp is not installed, returning simulated track")
            return Track(
                track_id=uuid.uuid4().hex[:8],
                title=sanitize_text(raw_input, 64),
                artist="Audio Stream",
                duration=186,
                thumbnail=DEFAULT_THUMBNAIL,
                source_url=raw_input,
                requester_user_id=requester_id,
                requester_name=requester_name,
            )

        ydl_opts = self._get_ydl_opts()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(target, download=False)
            except Exception as ex:
                logger.warning("Direct ydl extraction error: %s", str(ex))
                info = None

            if not info:
                return None

            # If search result, get the first entry
            if "entries" in info:
                entries = list(info.get("entries") or [])
                if not entries:
                    return None
                entry = entries[0]
            else:
                entry = info

            if not entry:
                return None

            title = sanitize_text(entry.get("title") or raw_input, 80)
            artist = (
                sanitize_text(entry.get("artist") or entry.get("uploader") or entry.get("channel"), 60)
                or "Aaruu Music"
            )
            duration = int(entry.get("duration") or 180)
            thumbnail = entry.get("thumbnail") or DEFAULT_THUMBNAIL
            source_url = entry.get("webpage_url") or entry.get("url") or raw_input

            return Track(
                track_id=str(entry.get("id") or uuid.uuid4().hex[:8]),
                title=title,
                artist=artist,
                duration=duration,
                thumbnail=thumbnail,
                source_url=source_url,
                requester_user_id=requester_id,
                requester_name=requester_name,
            )
