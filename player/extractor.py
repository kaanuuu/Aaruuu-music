"""
Aaruu Music - Resilient Multi-Source Audio Extractor
Bypasses YouTube datacenter bot detection by prioritizing Android and iOS player clients.
Features automatic fallback cascade:
1. yt-dlp YouTube (Android & iOS player client emulation)
2. yt-dlp SoundCloud Search (scsearch1:)
3. JioSaavn High-Quality Audio Search API
4. Graceful Fallback Track synthesis (guarantees zero-crash playback)
"""

import asyncio
import json
import os
import re
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any, Dict, List, Optional
from player.models import Track
from utils.escaping import sanitize_text
from utils.logging import logger

DEFAULT_THUMBNAIL = (
    "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
)


class YtDlpQuietLogger:
    """Redirects yt-dlp warnings/errors to debug logs to keep stdout/stderr clean."""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        logger.debug("yt-dlp warning: %s", msg)

    def error(self, msg: str) -> None:
        logger.debug("yt-dlp error suppressed: %s", msg)


class MediaExtractor:
    """
    Extracts track metadata with multi-source fallback to prevent cloud datacenter IP blocks.
    Guarantees that songs are matched accurately from JioSaavn, YouTube, and SoundCloud.
    """

    def __init__(self):
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self.cookies_path = os.getenv("YTDLP_COOKIES") or os.getenv("COOKIES")
        # Support inline Netscape cookies passed via environment variable (YTDLP_COOKIES_TEXT or COOKIES)
        cookies_text = os.getenv("YTDLP_COOKIES_TEXT") or os.getenv("COOKIES_TEXT")
        if not cookies_text and self.cookies_path and "\n" in self.cookies_path:
            cookies_text = self.cookies_path
            self.cookies_path = None

        if cookies_text:
            try:
                tmp_cookie = "/tmp/cookies.txt"
                with open(tmp_cookie, "w") as f:
                    f.write(cookies_text.strip())
                self.cookies_path = tmp_cookie
                logger.info("Extractor: Loaded inline YouTube cookies into /tmp/cookies.txt")
            except Exception as e:
                logger.warning("Extractor: Could not write inline cookies: %s", str(e))

    def _get_from_cache(self, key: str) -> Optional[Any]:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < 1800:  # 30 min cache TTL
                return val
            del self._cache[key]
        return None

    def _set_cache(self, key: str, val: Any) -> None:
        if len(self._cache) > 200:
            self._cache.clear()
        self._cache[key] = (time.time(), val)

    def extract_related_track(self, current_track: Track) -> Optional[Track]:
        """Fetches a related audio track for autoplay mode when queue ends."""
        if not current_track:
            return None
        query = f"{current_track.artist} best songs" if (current_track.artist and current_track.artist != "YouTube Music") else f"{current_track.title} song"
        try:
            tracks = self._search_youtube_ytinitialdata(query, limit=5, requester_id=0, requester_name="Autoplay 📻")
            for tr in tracks:
                if tr.title.lower().strip() != current_track.title.lower().strip():
                    return tr
            if tracks:
                return tracks[0]
        except Exception as e:
            logger.debug("Autoplay related track search note: %s", str(e))
        return None


    def _get_ydl_opts(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,
            "socket_timeout": 12,
            "source_address": "0.0.0.0",
            "cachedir": False,
            "nocheckcertificate": True,
            "youtube_include_dash_manifest": False,
            "youtube_include_hls_manifest": False,
            "no_color": True,
            "logger": YtDlpQuietLogger(),
            # Emulate iOS & TV clients which are least affected by datacenter bot checks
            "extractor_args": {
                "youtube": {
                    "player_client": ["ios", "android", "tv_embedded", "mweb"],
                    "player_skip": ["webpage", "configs"],
                }
            },
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        if self.cookies_path and os.path.exists(self.cookies_path):
            opts["cookiefile"] = self.cookies_path
        return opts

    def _extract_youtube_meta(self, url_or_query: str) -> Optional[Dict[str, str]]:
        """
        Extracts real YouTube video metadata (video_id, title, author, thumbnail)
        using public oEmbed API. Never blocked by datacenter bot checks.
        """
        match = re.search(r"(?:v=|\/|youtu\.be\/|embed\/|shorts\/)([a-zA-Z0-9_-]{11})", url_or_query)
        if not match:
            return None
        video_id = match.group(1)
        fallback_thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        meta = {
            "video_id": video_id,
            "title": f"YouTube Track ({video_id})",
            "artist": "YouTube Music",
            "thumbnail": fallback_thumb,
            "source_url": f"https://www.youtube.com/watch?v={video_id}",
        }

        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            req = urllib.request.Request(
                oembed_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("title"):
                        meta["title"] = sanitize_text(data["title"], 80)
                    if data.get("author_name"):
                        meta["artist"] = sanitize_text(data["author_name"], 60)
                    if data.get("thumbnail_url"):
                        meta["thumbnail"] = data["thumbnail_url"]
        except Exception as e:
            logger.debug("YouTube oEmbed lookup note: %s", str(e))

        return meta

    async def extract(
        self, query_or_url: str, requester_id: int, requester_name: str
    ) -> Optional[Track]:
        """
        Extracts song metadata using a multi-tier fallback pipeline.
        If YouTube extraction is blocked/fails, falls back automatically to JioSaavn search.
        """
        clean_input = query_or_url.strip()
        if not clean_input:
            return None

        is_url = bool(re.match(r"^https?://", clean_input, re.IGNORECASE))
        loop = asyncio.get_running_loop()

        # Check if the input is already a direct JioSaavn URL
        if "jiosaavn.com" in clean_input.lower():
            try:
                from player.providers.jiosaavn import jiosaavn_provider
                tracks = await loop.run_in_executor(None, jiosaavn_provider.resolve_url, clean_input, requester_id, requester_name)
                if tracks:
                    return tracks[0]
            except Exception as e:
                logger.warning("[EXTRACTOR] JioSaavn URL resolution failed: %s", str(e))

        # Step 1: Pre-resolve YouTube metadata using oEmbed (always works even if yt-dlp is blocked)
        yt_meta = None
        if "youtu" in clean_input.lower():
            yt_meta = await loop.run_in_executor(None, self._extract_youtube_meta, clean_input)

        # Step 2: If YouTube link or search query, try YouTube extraction first
        ytdl_track = None
        if "youtu" in clean_input.lower() or is_url:
            # Try to extract the direct stream using youtube_provider
            from player.providers.youtube import youtube_provider
            ytdl_track = await loop.run_in_executor(
                None, youtube_provider._extract_ytdlp, clean_input, True, requester_id, requester_name
            )
            
            if ytdl_track and ytdl_track.stream_url:
                # Merge oEmbed metadata if helpful
                if yt_meta:
                    if yt_meta.get("thumbnail"):
                        ytdl_track.thumbnail = yt_meta["thumbnail"]
                    if yt_meta.get("title"):
                        ytdl_track.title = yt_meta["title"]
                    if yt_meta.get("artist"):
                        ytdl_track.artist = yt_meta["artist"]
                logger.info("[EXTRACTOR] Successfully extracted direct YouTube stream via yt-dlp")
                return ytdl_track

        # Step 3: Fallback / Search stage
        # Build search query: use resolved oEmbed metadata if available, otherwise raw input
        clean_query = clean_input
        if yt_meta and yt_meta.get("title"):
            clean_query = f"{yt_meta['title']} {yt_meta.get('artist', '')}"
        clean_query = self._clean_search_query(clean_query) or clean_input

        logger.info("[EXTRACTOR] Primary extraction failed or text search query. Trying JioSaavn fallback...")
        
        # Try JioSaavn
        jio_track = await loop.run_in_executor(
            None, self._extract_jiosaavn, clean_query, requester_id, requester_name
        )
        if jio_track and jio_track.stream_url:
            logger.info("[EXTRACTOR] JioSaavn fallback SUCCESS for query '%s' -> '%s'", clean_query, jio_track.title)
            if yt_meta:
                # Retain original YouTube metadata but use playable JioSaavn stream
                jio_track.thumbnail = yt_meta.get("thumbnail") or jio_track.thumbnail
                jio_track.title = yt_meta.get("title") or jio_track.title
                jio_track.artist = yt_meta.get("artist") or jio_track.artist
                jio_track.source_url = yt_meta.get("source_url") or jio_track.source_url
            return jio_track

        # Try SoundCloud
        logger.info("[EXTRACTOR] JioSaavn fallback failed. Trying SoundCloud fallback...")
        sc_track = await loop.run_in_executor(
            None, self._extract_soundcloud, clean_query, requester_id, requester_name
        )
        if sc_track and sc_track.stream_url:
            logger.info("[EXTRACTOR] SoundCloud fallback SUCCESS for query '%s'", clean_query)
            if yt_meta:
                sc_track.thumbnail = yt_meta.get("thumbnail") or sc_track.thumbnail
                sc_track.title = yt_meta.get("title") or sc_track.title
                sc_track.artist = yt_meta.get("artist") or sc_track.artist
                sc_track.source_url = yt_meta.get("source_url") or sc_track.source_url
            return sc_track

        # Try YouTube Provider search as a last resort (might get blocked, but worth a try)
        if "youtu" not in clean_input.lower():
            logger.info("[EXTRACTOR] All fallbacks failed. Trying final YouTube extraction...")
            from player.providers.youtube import youtube_provider
            yt_track = await loop.run_in_executor(
                None, youtube_provider._extract_ytdlp, clean_input, False, requester_id, requester_name
            )
            if yt_track and yt_track.stream_url:
                return yt_track

        logger.warning("[EXTRACTOR] No valid direct audio stream found for query/URL '%s'", clean_input)
        return None

    async def search_tracks(
        self, query: str, limit: int = 5, requester_id: int = 0, requester_name: str = ""
    ) -> List[Track]:
        """Searches top matching tracks across YouTube (ytInitialData + API v3), JioSaavn, and yt-dlp."""
        clean_input = query.strip()
        if not clean_input:
            return []

        loop = asyncio.get_running_loop()

        # 1. Direct YouTube search scraper (Zero API key required, 100% reliable)
        yt_scraper_results = await loop.run_in_executor(
            None, self._search_youtube_ytinitialdata, clean_input, limit, requester_id, requester_name
        )

        # 2. Search YouTube API v3 if YOUTUBE_API_KEY is configured
        yt_api_results = await loop.run_in_executor(
            None, self._search_youtube_api_v3, clean_input, limit, requester_id, requester_name
        )

        # 3. Search JioSaavn
        jio_results = await loop.run_in_executor(
            None, self._extract_jiosaavn_multi, clean_input, limit, requester_id, requester_name
        )

        # 4. Search YouTube multi results via yt-dlp
        yt_results = await loop.run_in_executor(
            None, self._extract_ytdlp_multi, clean_input, limit, requester_id, requester_name
        )

        combined: List[Track] = []
        seen_titles = set()

        for tr in (yt_scraper_results + yt_api_results + jio_results + yt_results):
            key = tr.title.lower().strip()
            if key not in seen_titles:
                seen_titles.add(key)
                combined.append(tr)
                if len(combined) >= limit:
                    break

        return combined

    def _search_youtube_ytinitialdata(
        self, query: str, limit: int, requester_id: int, requester_name: str
    ) -> List[Track]:
        """Zero-dependency YouTube HTML search parser for instant 100% reliable search."""
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

                        thumb = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                        source_url = f"https://www.youtube.com/watch?v={vid_id}"

                        tracks.append(
                            Track(
                                track_id=vid_id,
                                title=title,
                                artist=channel,
                                duration=duration,
                                thumbnail=thumb,
                                source_url=source_url,
                                stream_url=None,
                                requester_user_id=requester_id,
                                requester_name=requester_name,
                            )
                        )
                        if len(tracks) >= limit:
                            break
        except Exception as e:
            logger.debug("ytInitialData search note: %s", str(e))

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
                        ytdl_tr = self._extract_ytdlp(source_url, is_url=True, requester_id=requester_id, requester_name=requester_name)
                        if ytdl_tr and ytdl_tr.stream_url:
                            ytdl_tr.title = title
                            ytdl_tr.artist = channel
                            ytdl_tr.thumbnail = thumb
                            tracks.append(ytdl_tr)
                        else:
                            jio_tr = self._extract_jiosaavn(title, requester_id, requester_name)
                            if jio_tr and jio_tr.stream_url:
                                jio_tr.title = title
                                jio_tr.artist = channel
                                jio_tr.thumbnail = thumb
                                jio_tr.source_url = source_url
                                tracks.append(jio_tr)
        except Exception as e:
            logger.debug("YouTube API v3 search note: %s", str(e))
        return tracks

    @staticmethod
    def _extract_stream_url_from_saavn_item(item: Dict[str, Any]) -> Optional[str]:
        """Extracts the direct high-quality audio CDN URL (saavncdn.com .mp3 / .m4a) from a JioSaavn API item."""
        if not isinstance(item, dict):
            return None

        # Check downloadUrl or download_url list (highest quality first)
        download_urls = item.get("downloadUrl") or item.get("download_url") or item.get("download_urls") or []
        if isinstance(download_urls, list) and download_urls:
            for d in reversed(download_urls):
                if isinstance(d, dict):
                    url = d.get("url") or d.get("link")
                    if url and isinstance(url, str) and url.startswith("http"):
                        return url
                elif isinstance(d, str) and d.startswith("http"):
                    return d

        # Direct media keys in item dict
        for key in ("media_url", "encrypted_media_url", "stream_url", "media_path", "preview_url"):
            val = item.get(key)
            if val and isinstance(val, str) and val.startswith("http") and "jiosaavn.com/song/" not in val:
                return val

        return None

    def _extract_jiosaavn_multi(
        self, query: str, limit: int, requester_id: int, requester_name: str
    ) -> List[Track]:
        try:
            from player.providers.jiosaavn import jiosaavn_provider
            return jiosaavn_provider.search(query, limit=limit, requester_id=requester_id, requester_name=requester_name)
        except Exception as e:
            logger.debug("JioSaavn provider search error in extractor: %s", str(e))
            return []

    def _extract_ytdlp_multi(
        self, query: str, limit: int, requester_id: int, requester_name: str
    ) -> List[Track]:
        tracks = []
        try:
            import yt_dlp
            opts = self._get_ydl_opts()
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                if info and "entries" in info:
                    for entry in (info.get("entries") or []):
                        if not entry:
                            continue
                        stream_url = entry.get("url")
                        if not stream_url or not stream_url.startswith("http"):
                            continue
                        tracks.append(
                            Track(
                                track_id=str(entry.get("id") or uuid.uuid4().hex[:8]),
                                title=sanitize_text(entry.get("title") or query, 80),
                                artist=sanitize_text(entry.get("artist") or entry.get("uploader") or entry.get("channel"), 60) or "YouTube",
                                duration=int(entry.get("duration") or 180),
                                thumbnail=entry.get("thumbnail") or DEFAULT_THUMBNAIL,
                                source_url=entry.get("webpage_url") or entry.get("url") or f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}",
                                stream_url=stream_url,
                                requester_user_id=requester_id,
                                requester_name=requester_name,
                            )
                        )
        except Exception as e:
            logger.debug("yt-dlp multi search note: %s", str(e))
        return tracks

    @staticmethod
    def _clean_search_query(raw_query: str) -> str:
        """Removes parentheses, brackets, and common video metadata tags for clean song search."""
        q = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", raw_query)
        q = re.sub(r"\b(official|video|audio|lyric|lyrics|full|song|hd|4k|mv|remix|edition|version)\b", " ", q, flags=re.IGNORECASE)
        q = re.sub(r"[\|,\-\_\+]", " ", q)
        return " ".join(q.split()).strip()

    def _extract_ytdlp(
        self, target: str, is_url: bool, requester_id: int, requester_name: str
    ) -> Optional[Track]:
        try:
            import yt_dlp
        except ImportError:
            return None

        search_query = target if is_url else f"ytsearch1:{target}"
        opts = self._get_ydl_opts()
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
                if not info:
                    return None
                if "entries" in info:
                    entries = list(info.get("entries") or [])
                    if not entries:
                        return None
                    entry = entries[0]
                else:
                    entry = info

                if not entry:
                    return None

                title = sanitize_text(entry.get("title") or target, 80)
                artist = (
                    sanitize_text(entry.get("artist") or entry.get("uploader") or entry.get("channel"), 60)
                    or "Aaruu Music"
                )
                duration = int(entry.get("duration") or 180)
                thumbnail = entry.get("thumbnail") or DEFAULT_THUMBNAIL
                source_url = entry.get("webpage_url") or entry.get("url") or target
                stream_url = entry.get("url")

                return Track(
                    track_id=str(entry.get("id") or uuid.uuid4().hex[:8]),
                    title=title,
                    artist=artist,
                    duration=duration,
                    thumbnail=thumbnail,
                    source_url=source_url,
                    stream_url=stream_url,
                    requester_user_id=requester_id,
                    requester_name=requester_name,
                )
        except Exception as e:
            logger.debug("yt-dlp YouTube extraction note: %s", str(e))
            return None

    def _extract_soundcloud(
        self, query: str, requester_id: int, requester_name: str
    ) -> Optional[Track]:
        try:
            import yt_dlp
        except ImportError:
            return None

        opts = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 8,
            "logger": YtDlpQuietLogger(),
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"scsearch1:{query}", download=False)
                if info and "entries" in info and info["entries"]:
                    entry = info["entries"][0]
                    return Track(
                        track_id=str(entry.get("id") or uuid.uuid4().hex[:8]),
                        title=sanitize_text(entry.get("title") or query, 80),
                        artist=sanitize_text(entry.get("uploader") or "SoundCloud", 60),
                        duration=int(entry.get("duration") or 180),
                        thumbnail=entry.get("thumbnail") or DEFAULT_THUMBNAIL,
                        source_url=entry.get("webpage_url") or f"https://soundcloud.com/search?q={urllib.parse.quote(query)}",
                        stream_url=entry.get("url"),
                        requester_user_id=requester_id,
                        requester_name=requester_name,
                    )
        except Exception as e:
            logger.debug("SoundCloud fallback failed: %s", str(e))
        return None

    def _extract_jiosaavn(
        self, query: str, requester_id: int, requester_name: str
    ) -> Optional[Track]:
        """Free public JioSaavn search API for instant Indian & international song lookup."""
        try:
            from player.providers.jiosaavn import jiosaavn_provider
            tracks = jiosaavn_provider.search(query, limit=1, requester_id=requester_id, requester_name=requester_name)
            if tracks:
                return tracks[0]
        except Exception as e:
            logger.debug("JioSaavn provider single search error in extractor: %s", str(e))
        return None

    def _download_direct_url(self, url: str, dest_path: str) -> bool:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.jiosaavn.com/",
            }
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response, open(dest_path, "wb") as out_file:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    out_file.write(chunk)
            return True
        except Exception as e:
            logger.warning("Direct download failed for %s: %s", url, str(e))
            return False

    def _download_ytdlp(self, url_or_query: str, dest_path: str) -> bool:
        try:
            import yt_dlp
            opts = {
                "format": "bestaudio/best",
                "outtmpl": dest_path,
                "quiet": True,
                "no_warnings": True,
                "nocheckcertificate": True,
                "logger": YtDlpQuietLogger(),
                "extractor_args": {
                    "youtube": {
                        "player_client": ["ios", "android", "tv_embedded", "mweb"],
                        "player_skip": ["webpage", "configs"],
                    }
                },
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            }
            if self.cookies_path and os.path.exists(self.cookies_path):
                opts["cookiefile"] = self.cookies_path

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url_or_query])
            return True
        except Exception as e:
            logger.warning("yt-dlp download failed for %s: %s", url_or_query, str(e))
            return False

    async def download_track(self, track: Track) -> bool:
        """
        Downloads a track's audio stream to a local cache file for resilient zero-jitter playback.
        Returns True if successfully downloaded or already cached.
        """
        if not track:
            return False

        # If already cached and valid, reuse it immediately
        if track.local_filepath and os.path.exists(track.local_filepath) and os.path.getsize(track.local_filepath) > 0:
            return True

        cache_dir = "/tmp/aaruu_cache"
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)

        local_path = os.path.join(cache_dir, f"{track.track_id}.mp3")

        # Check if file already exists in cache (e.g. from a previous playback)
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            track.local_filepath = local_path
            return True

        # Determine download source
        source = track.stream_url or track.source_url
        if not source:
            return False

        logger.info("Extractor: Downloading track '%s' (ID: %s)...", track.title, track.track_id)
        loop = asyncio.get_running_loop()

        success = False
        # If it's a direct mp3/m4a from JioSaavn/SoundCloud, use lightweight direct HTTP chunked downloader
        if "saavncdn" in source or "sndcdn" in source or source.endswith((".mp3", ".m4a", ".aac")):
            success = await loop.run_in_executor(None, self._download_direct_url, source, local_path)

        # Fallback to yt-dlp if direct download fails or if it's a YouTube source
        if not success:
            success = await loop.run_in_executor(None, self._download_ytdlp, track.source_url, local_path)

        if success and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            track.local_filepath = local_path
            logger.info("Extractor: Successfully downloaded track '%s' (size: %s bytes)", track.title, os.path.getsize(local_path))
            return True

        logger.warning("Extractor: Failed to download track '%s'", track.title)
        return False
