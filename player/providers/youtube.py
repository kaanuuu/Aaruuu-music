"""
Aaruu Music - YouTube Provider
Handles YouTube search scraping, official API search, and yt-dlp direct stream extraction.
"""

import json
import os
import re
import uuid
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from player.models import Track
from player.providers.base import BaseProvider
from utils.cookie_manager import get_youtube_cookie_file, log_cookie_status_at_startup
from utils.escaping import sanitize_text
from utils.logging import logger

DEFAULT_THUMBNAIL = (
    "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
)


class YtDlpQuietLogger:
    def debug(self, msg):
        pass
    def warning(self, msg):
        pass
    def error(self, msg):
        pass


def classify_youtube_exception(e: Exception) -> str:
    """Classifies yt-dlp exception into safe error category without leaking sensitive details."""
    msg = str(e).lower()
    if "403" in msg or "forbidden" in msg:
        return "HTTP_403"
    if "429" in msg or "too many requests" in msg:
        return "HTTP_429"
    if "sign in" in msg or "login" in msg or "confirm you're not a bot" in msg or "bot" in msg:
        return "BOT_CHECK"
    if "po_token" in msg or "proof of origin" in msg or "pot" in msg:
        return "PO_TOKEN_REQUIRED"
    if "private" in msg or "members-only" in msg or "account" in msg:
        return "AUTH_REQUIRED"
    if "network" in msg or "connection" in msg or "timeout" in msg or "timed out" in msg:
        return "NETWORK_ERROR"
    return "EXTRACTION_ERROR"


def parse_audio_stream_from_entry(entry: Dict[str, Any]) -> Tuple[Optional[str], int, int]:
    """
    Inspects yt-dlp entry dictionary and selects the best playable audio stream URL.
    Returns (stream_url, total_formats_count, audio_formats_count).
    """
    from player.models import is_youtube_watch_url, is_direct_media_url

    formats = entry.get("formats") or []
    total_count = len(formats)

    # 1. Check top-level url property if it's already a direct media URL
    direct_prop = entry.get("url")
    if direct_prop and isinstance(direct_prop, str) and is_direct_media_url(direct_prop) and not is_youtube_watch_url(direct_prop):
        return direct_prop, total_count, total_count

    # 2. Inspect formats for audio-only streams
    audio_formats = []
    for f in formats:
        f_url = f.get("url")
        if not f_url or not isinstance(f_url, str):
            continue
        if is_youtube_watch_url(f_url) or not is_direct_media_url(f_url):
            continue
        acodec = f.get("acodec")
        vcodec = f.get("vcodec")
        if acodec not in (None, "none", "None"):
            audio_formats.append(f)

    audio_count = len(audio_formats)
    if audio_formats:
        # Sort by audio bitrate (abr or tbr)
        audio_formats.sort(key=lambda x: (x.get("abr") or x.get("tbr") or x.get("quality") or 0), reverse=True)
        return audio_formats[0]["url"], total_count, audio_count

    # 3. Fallback to any valid direct media stream format
    any_formats = [
        f for f in formats
        if f.get("url") and isinstance(f.get("url"), str) and is_direct_media_url(f.get("url")) and not is_youtube_watch_url(f.get("url"))
    ]
    if any_formats:
        any_formats.sort(key=lambda x: (x.get("tbr") or x.get("abr") or 0), reverse=True)
        return any_formats[0]["url"], total_count, 0

    return None, total_count, 0


class YouTubeProvider(BaseProvider):
    """Encapsulates all YouTube metadata extraction and direct audio stream parsing."""

    def __init__(self):
        log_cookie_status_at_startup()
        self.cookies_path = get_youtube_cookie_file()
        self.last_error: Optional[str] = None

    def get_po_token_status(self) -> str:
        po_tok = os.getenv("YTDLP_PO_TOKEN") or os.getenv("PO_TOKEN")
        if po_tok:
            return "configured"
        return "unavailable"

    def _get_attempt_configs(self) -> List[Dict[str, Any]]:
        """
        Returns sequential yt-dlp configurations to handle YouTube anti-bot / PO token mechanisms.
        Includes Node.js JS runtime engine for signature decryption.
        """
        base_headers_desktop = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        base_headers_mobile = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
            "Accept-Language": "en-US,en;q=0.9",
        }

        configs = [
            # Attempt 1: Standard web client with Node JS runtime
            {
                "name": "standard_web",
                "opts": {
                    "format": "bestaudio/best",
                    "noplaylist": True,
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "socket_timeout": 8,
                    "logger": YtDlpQuietLogger(),
                    "js_runtimes": {"node": {}},
                    "http_headers": base_headers_desktop,
                },
            },
            # Attempt 2: Mobile / Android web client
            {
                "name": "mobile_web",
                "opts": {
                    "format": "bestaudio/best",
                    "noplaylist": True,
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "socket_timeout": 8,
                    "logger": YtDlpQuietLogger(),
                    "js_runtimes": {"node": {}},
                    "http_headers": base_headers_mobile,
                },
            },
        ]

        po_token = os.getenv("YTDLP_PO_TOKEN") or os.getenv("PO_TOKEN")
        if po_token:
            configs.append({
                "name": "po_token_provider",
                "opts": {
                    "format": "bestaudio/best",
                    "noplaylist": True,
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                    "socket_timeout": 8,
                    "logger": YtDlpQuietLogger(),
                    "js_runtimes": {"node": {}},
                    "extractor_args": {
                        "youtube": {
                            "po_token": [po_token],
                        }
                    },
                    "http_headers": base_headers_desktop,
                },
            })

        if self.cookies_path and os.path.exists(self.cookies_path):
            for c in configs:
                c["opts"]["cookiefile"] = self.cookies_path

        return configs

    def _extract_ytdlp(
        self, target: str, is_url: bool, requester_id: int, requester_name: str
    ) -> Optional[Track]:
        """Extracts direct audio stream URL from YouTube using yt-dlp with multi-attempt client strategy."""
        try:
            import yt_dlp
        except ImportError:
            logger.warning("[PROVIDER-YOUTUBE] yt_dlp is not installed!")
            self.last_error = "YTDLP_NOT_INSTALLED"
            return None

        search_query = target if is_url else f"ytsearch1:{target}"
        configs = self._get_attempt_configs()
        has_cookies = bool(self.cookies_path and os.path.exists(self.cookies_path))

        logger.info("[YTDLP] cookies_configured=%s cookiefile_configured=%s", str(has_cookies).lower(), str(has_cookies).lower())

        for idx, cfg in enumerate(configs, 1):
            cfg_name = cfg["name"]
            opts = cfg["opts"]
            logger.info("[YOUTUBE] extraction_started target=\"%s\" attempt=%d config=\"%s\"", sanitize_text(target, 40), idx, cfg_name)

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(search_query, download=False)
                    if not info:
                        logger.info("[YOUTUBE] extraction_result=empty_info")
                        continue

                    entries = list(info.get("entries") or [info]) if "entries" in info else [info]
                    entry = entries[0] if entries else None
                    if not entry:
                        logger.info("[YOUTUBE] extraction_result=empty_entry")
                        continue

                    video_id = entry.get("id") or "unknown"
                    webpage_url = entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id != "unknown" else target)

                    stream_url, total_fmt, audio_fmt = parse_audio_stream_from_entry(entry)

                    logger.info(
                        "[YTDLP] video_id=%s total_formats=%d audio_formats=%d direct_url_present=%s",
                        video_id,
                        total_fmt,
                        audio_fmt,
                        str(bool(stream_url)).lower(),
                    )

                    if not stream_url:
                        logger.info("[YTDLP] direct_stream_present=false reason=NO_AUDIO_FORMAT")
                        self.last_error = "NO_AUDIO_FORMAT"
                        continue

                    logger.info("[YTDLP] direct_stream_present=true direct_stream_is_webpage=false")

                    title = sanitize_text(entry.get("title") or target, 80)
                    artist = (
                        sanitize_text(entry.get("artist") or entry.get("uploader") or entry.get("channel"), 60)
                        or "YouTube Artist"
                    )
                    duration = int(entry.get("duration") or 180)
                    thumbnail = entry.get("thumbnail") or DEFAULT_THUMBNAIL

                    return Track(
                        track_id=f"yt_{video_id}",
                        title=title,
                        artist=artist,
                        duration=duration,
                        thumbnail=thumbnail,
                        source_url=webpage_url,
                        stream_url=stream_url,
                        requester_user_id=requester_id,
                        requester_name=requester_name,
                    )
            except Exception as e:
                err_type = classify_youtube_exception(e)
                self.last_error = err_type
                logger.info("[YTDLP] extraction_failed type=%s reason=\"%s\"", type(e).__name__, err_type)
                logger.debug("[YOUTUBE] Attempt %d (%s) exception: %s", idx, cfg_name, str(e))

        return None

    def _get_video_ydl_opts(self) -> Dict[str, Any]:
        opts = {
            "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 12,
            "logger": YtDlpQuietLogger(),
            "extractor_args": {
                "youtube": {
                    "player_client": ["ios", "android", "tv_embedded", "mweb"],
                    "player_skip": ["webpage", "configs"],
                }
            },
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        if self.cookies_path and os.path.exists(self.cookies_path):
            opts["cookiefile"] = self.cookies_path
        return opts

    def _extract_video_ytdlp(
        self, target: str, is_url: bool, requester_id: int, requester_name: str
    ) -> Optional[Track]:
        """Extracts direct video stream URL from YouTube using yt-dlp for /vplay."""
        try:
            import yt_dlp
        except ImportError:
            logger.warning("[PROVIDER-YOUTUBE] yt_dlp is not installed!")
            return None

        search_query = target if is_url else f"ytsearch5:{target}"
        opts = self._get_video_ydl_opts()
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                if not info:
                    return None

                entries = list(info.get("entries") or [info]) if "entries" in info else [info]
                valid_entry = None
                for ent in entries:
                    if not ent:
                        continue
                    url = ent.get("url")
                    if not url or "youtube.com/watch" in url or "youtu.be/" in url:
                        continue
                    dur = int(ent.get("duration") or 0)
                    t = ent.get("title", "").lower()
                    # Skip shorts unless explicitly requested
                    if dur < 60 or "#short" in t or "/shorts/" in (ent.get("webpage_url") or ""):
                        continue
                    valid_entry = ent
                    break

                if not valid_entry and entries:
                    # Fallback to first non-empty entry with direct url
                    for ent in entries:
                        if ent and ent.get("url") and "youtube.com/watch" not in ent.get("url") and "youtu.be/" not in ent.get("url"):
                            valid_entry = ent
                            break

                if not valid_entry:
                    logger.warning("[PROVIDER-YOUTUBE] No valid direct video stream found for target '%s'", target)
                    return None

                stream_url = valid_entry.get("url")
                title = sanitize_text(valid_entry.get("title") or target, 80)
                artist = sanitize_text(valid_entry.get("artist") or valid_entry.get("uploader") or valid_entry.get("channel"), 60) or "YouTube Video"
                duration = int(valid_entry.get("duration") or 240)
                thumbnail = valid_entry.get("thumbnail") or DEFAULT_THUMBNAIL
                source_url = valid_entry.get("webpage_url") or target

                return Track(
                    track_id=f"ytv_{valid_entry.get('id') or uuid.uuid4().hex[:8]}",
                    title=title,
                    artist=artist,
                    duration=duration,
                    thumbnail=thumbnail,
                    source_url=source_url,
                    stream_url=stream_url,
                    requester_user_id=requester_id,
                    requester_name=requester_name,
                    is_video=True,
                    media_type="video",
                )
        except Exception as e:
            logger.warning("[PROVIDER-YOUTUBE] Video extraction error for %s: %s", target, str(e))
            return None

    def search(
        self, query: str, limit: int = 5, requester_id: int = 0, requester_name: str = ""
    ) -> List[Track]:
        """Searches YouTube using scraper + API v3 as fallback."""
        if not query or not query.strip():
            return []

        # 1. First try ytInitialData scraper search (fast, zero API key)
        tracks = self._search_youtube_ytinitialdata(query, limit, requester_id, requester_name)
        if tracks:
            return tracks

        # 2. Fallback to API v3 search
        tracks = self._search_youtube_api_v3(query, limit, requester_id, requester_name)
        return tracks

    def _search_youtube_ytinitialdata(
        self, query: str, limit: int, requester_id: int, requester_name: str
    ) -> List[Track]:
        tracks = []
        try:
            encoded_query = urllib.parse.quote(query)
            url = f"https://www.youtube.com/results?search_query={encoded_query}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                match = re.search(r'var ytInitialData = ({.*?});</script>', html) or re.search(r'window\[\"ytInitialData\"\] = ({.*?});', html)
                if match:
                    data = json.loads(match.group(1))
                    sections = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get("primaryContents", {}).get("sectionListRenderer", {}).get("contents", [])
                    contents = []
                    for s in sections:
                        contents.extend(s.get("itemSectionRenderer", {}).get("contents", []))

                    for c in contents:
                        v = c.get("videoRenderer")
                        if not v:
                            continue
                        vid_id = v.get("videoId")
                        if not vid_id:
                            continue

                        title_runs = v.get("title", {}).get("runs", [{}])
                        title = sanitize_text(title_runs[0].get("text") if title_runs else query, 80)

                        owner_runs = v.get("ownerText", {}).get("runs", [{}])
                        channel = sanitize_text(owner_runs[0].get("text") if owner_runs else "YouTube", 60)

                        dur_str = v.get("lengthText", {}).get("simpleText", "3:30")
                        duration = 210
                        if dur_str and ":" in dur_str:
                            parts = dur_str.split(":")
                            try:
                                if len(parts) == 2:
                                    duration = int(parts[0]) * 60 + int(parts[1])
                                elif len(parts) == 3:
                                    duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                            except ValueError:
                                pass

                        # Skip Shorts and short snippets (<60s) unless explicitly queried
                        is_clip_query = any(w in query.lower() for w in ["short", "shorts", "clip", "snippet", "teaser", "status", "ringtone"])
                        if not is_clip_query:
                            if duration < 60:
                                continue
                            if "#short" in title.lower() or "#shorts" in title.lower():
                                continue

                        thumb = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                        source_url = f"https://www.youtube.com/watch?v={vid_id}"

                        tracks.append(
                            Track(
                                track_id=f"yt_{vid_id}",
                                title=title,
                                artist=channel,
                                duration=duration,
                                thumbnail=thumb,
                                source_url=source_url,
                                stream_url=None,  # Do not pre-fill stream_url with watch page!
                                requester_user_id=requester_id,
                                requester_name=requester_name,
                            )
                        )
                        if len(tracks) >= limit:
                            break
        except Exception as e:
            logger.debug("[PROVIDER-YOUTUBE] Scraping search error: %s", str(e))
        return tracks

    def _search_youtube_api_v3(
        self, query: str, limit: int, requester_id: int, requester_name: str
    ) -> List[Track]:
        api_key = os.getenv("YOUTUBE_API_KEY") or os.getenv("YT_API_KEY")
        if not api_key:
            return []

        tracks = []
        try:
            encoded_query = urllib.parse.quote(query)
            api_url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults={limit}&q={encoded_query}&key={api_key.strip()}"
            req = urllib.request.Request(
                api_url,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    items = data.get("items", [])
                    for item in items:
                        vid_id = item.get("id", {}).get("videoId")
                        if not vid_id:
                            continue
                        snippet = item.get("snippet", {})
                        title = sanitize_text(snippet.get("title") or query, 80)
                        channel = sanitize_text(snippet.get("channelTitle") or "YouTube", 60)
                        thumbs = snippet.get("thumbnails", {})
                        thumb = thumbs.get("high", {}).get("url") or thumbs.get("default", {}).get("url") or f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"

                        source_url = f"https://www.youtube.com/watch?v={vid_id}"
                        tracks.append(
                            Track(
                                track_id=f"yt_{vid_id}",
                                title=title,
                                artist=channel,
                                duration=210,
                                thumbnail=thumb,
                                source_url=source_url,
                                stream_url=None,  # Do not pre-fill stream_url with watch page!
                                requester_user_id=requester_id,
                                requester_name=requester_name,
                            )
                        )
        except Exception as e:
            logger.debug("[PROVIDER-YOUTUBE] API v3 search error: %s", str(e))
        return tracks

    def get_track(
        self, track_id: str, requester_id: int = 0, requester_name: str = ""
    ) -> Optional[Track]:
        """Resolves metadata and stream URL for a given YouTube video ID."""
        clean_id = track_id.replace("yt_", "")
        url = f"https://www.youtube.com/watch?v={clean_id}"
        return self._extract_ytdlp(url, is_url=True, requester_id=requester_id, requester_name=requester_name)


youtube_provider = YouTubeProvider()
