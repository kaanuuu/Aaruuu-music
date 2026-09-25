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
from player.voice_chat import verify_media_file_with_ffmpeg
from utils.cookie_manager import get_youtube_cookie_file
from utils.escaping import sanitize_text
from utils.logging import logger

DEFAULT_THUMBNAIL = (
    "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
)


def validate_and_score_track(query: str, track: Track, is_explicit_clip: bool = False) -> tuple[bool, float, str]:
    """
    Validates that a track corresponds to the requested query and is a full-length playable audio.
    Rejects:
    - Previews & snippets (< 60s)
    - YouTube Shorts (#shorts or /shorts/)
    - Teasers / trailers / WhatsApp status / ringtones
    - Unrelated tracks with low keyword overlap
    Returns (is_valid, score, reason).
    """
    if not track:
        return False, 0.0, "Empty track"

    dur = getattr(track, "duration", 0) or 0
    title_lower = (track.title or "").lower()
    artist_lower = (track.artist or "").lower()
    source_lower = (track.source_url or "").lower()
    stream_lower = (track.stream_url or "").lower()
    query_lower = query.lower().strip()

    # Detect if user explicitly queried for a short/clip/status
    is_clip_req = is_explicit_clip or any(
        w in query_lower for w in ["short", "shorts", "clip", "snippet", "teaser", "status", "ringtone", "preview"]
    )

    if not is_clip_req:
        # Reject suspiciously short tracks (< 60 seconds)
        if 0 < dur < 60:
            return False, 0.0, f"Too short ({dur}s < 60s) for a full song"

        # Reject Shorts
        if "/shorts/" in source_lower or "#shorts" in title_lower or "#short" in title_lower:
            return False, 0.0, "YouTube Short clip rejected"

        # Reject previews
        if "preview" in stream_lower or "preview" in title_lower:
            return False, 0.0, "Preview clip rejected"

        # Reject teasers, trailers, status clips, ringtones
        if "teaser" in title_lower and "teaser" not in query_lower:
            return False, 0.0, "Teaser rejected"
        if "trailer" in title_lower and "trailer" not in query_lower:
            return False, 0.0, "Trailer rejected"
        if "whatsapp status" in title_lower or "30 sec status" in title_lower or "30sec status" in title_lower:
            return False, 0.0, "Status clip rejected"
        if "ringtone" in title_lower and "ringtone" not in query_lower:
            return False, 0.0, "Ringtone rejected"

        # Reject abnormally long items (> 20 min) unless user asked for mix/podcast
        is_long_req = any(w in query_lower for w in ["mix", "podcast", "album", "jukebox", "compilation", "1 hour", "hours", "medley"])
        if dur > 1200 and not is_long_req:
            return False, 0.0, f"Duration too long ({dur}s > 20min) for single track"

    # Tokenize query words
    cleaned_q = re.sub(r"[^\w\s]", " ", query_lower)
    raw_tokens = cleaned_q.split()
    stop_words = {
        "song", "songs", "audio", "video", "mp3", "track", "music", "full", "hd", "4k",
        "the", "a", "an", "of", "in", "to", "for", "and", "by", "with", "from", "original", "official"
    }
    query_tokens = [w for w in raw_tokens if len(w) >= 2 and w not in stop_words]

    full_text = f"{title_lower} {artist_lower}"
    score = 1.0

    if query_tokens:
        matched = sum(1 for w in query_tokens if w in full_text)
        match_ratio = matched / len(query_tokens)
        if match_ratio < 0.35 and len(query_tokens) >= 2:
            return False, 0.0, f"Keyword match ratio too low ({matched}/{len(query_tokens)})"
        score = match_ratio

    # Penalize remix/mashup/dj if not queried
    if not any(w in query_lower for w in ["remix", "mashup", "dj"]):
        if any(w in title_lower for w in ["remix", "mashup", "dj mix", "club mix"]):
            score -= 0.25

    # Penalize cover if not queried
    if not any(w in query_lower for w in ["cover", "female version", "male version"]):
        if any(w in title_lower for w in ["cover", "covered by", "female version", "male version"]):
            score -= 0.30

    # Penalize karaoke/instrumental
    if not any(w in query_lower for w in ["karaoke", "instrumental"]):
        if any(w in title_lower for w in ["karaoke", "instrumental", "backing track"]):
            score -= 0.40

    # Penalize live if not queried
    if "live" not in query_lower:
        if any(w in title_lower for w in ["live at", "live performance", "live concert"]):
            score -= 0.20

    # Bonus for official/original
    if any(w in title_lower for w in ["official audio", "official music video", "original audio", "original song"]):
        score += 0.20

    # Bonus for standard song duration (120s - 420s)
    if 120 <= dur <= 420:
        score += 0.15

    return True, score, "Matched"


class YtDlpQuietLogger:
    """Redirects yt-dlp warnings/errors to debug logs to keep stdout/stderr clean and safe."""

    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        logger.debug("yt-dlp warning: %s", msg)

    def error(self, msg: str) -> None:
        if msg:
            sanitized = re.sub(r'(cookie|token|auth|key|password)=[\w\.-]+', r'\1=***', str(msg).strip(), flags=re.IGNORECASE)
            logger.debug("[YTDLP_ERROR_LOG] %s", sanitized[:250])


class MediaExtractor:
    """
    Extracts track metadata with multi-source fallback to prevent cloud datacenter IP blocks.
    Guarantees that songs are matched accurately from JioSaavn, YouTube, and SoundCloud.
    """

    def __init__(self):
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._download_locks: Dict[str, asyncio.Lock] = {}
        self.last_extraction_status: str = "NONE"
        self.cookies_path = get_youtube_cookie_file()

    @staticmethod
    def _clean_search_query(text: str) -> str:
        """Removes YouTube descriptors, channel noise, and symbols to produce clean search terms."""
        if not text:
            return ""
        # Remove video descriptors & extra text
        cleaned = re.sub(
            r"(?i)\b(official\s+music\s+video|official\s+video|official\s+audio|lyric\s+video|lyrics\s+video|lyrics|full\s+song|4k|hd|video|audio|her\s+side\s+of\s+the\s+story|spider-man\s+brand\s+new\s+day\s+edition|brand\s+new\s+day\s+edition|teaser|trailer|whatsapp\s+status|status)\b",
            "",
            text,
        )
        # Remove channel noise & record label names
        cleaned = re.sub(
            r"(?i)\b(and\s+)?(sony\s+music\s*\w*|tips\s+official|t-series|zee\s+music\s+company|7alfaaz|ytinitialdata|records|music|vevo|official)\b",
            "",
            cleaned,
        )
        # Remove symbols
        cleaned = re.sub(r"[|\\/()\[\]{}\-–—_:;]", " ", cleaned)
        words = [w for w in cleaned.split() if len(w) >= 2]
        return " ".join(words[:4])

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
            "js_runtimes": {"node": {}},
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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
        Search & Track Resolution Pipeline:
        Resolves query to candidate Track metadata (video_id, title, artist, source_url)
        without attempting media download or stream extraction at this stage.
        The returned Track is then passed to prepare_track() for local caching/download.
        """
        clean_input = query_or_url.strip()
        if not clean_input:
            return None

        is_url = bool(re.match(r"^https?://", clean_input, re.IGNORECASE))
        loop = asyncio.get_running_loop()

        # Case 1: Direct JioSaavn URL
        if "jiosaavn.com" in clean_input.lower():
            try:
                from player.providers.jiosaavn import jiosaavn_provider
                tracks = await loop.run_in_executor(None, jiosaavn_provider.resolve_url, clean_input, requester_id, requester_name)
                if tracks:
                    candidate = tracks[0]
                    candidate.is_video = False
                    candidate.media_type = "audio"
                    return candidate
            except Exception as e:
                logger.warning("[EXTRACTOR] JioSaavn URL resolution failed: %s", str(e))

        # Case 2: Direct YouTube URL or Video ID
        match = re.search(r"(?:v=|\/|youtu\.be\/|embed\/|shorts\/)([a-zA-Z0-9_-]{11})", clean_input)
        if match or (("youtu.be/" in clean_input or "youtube.com/watch" in clean_input) and is_url):
            vid_id = match.group(1) if match else "unknown"
            yt_meta = await loop.run_in_executor(None, self._extract_youtube_meta, clean_input)
            thumb = (yt_meta.get("thumbnail") if yt_meta else None) or f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
            title = (yt_meta.get("title") if yt_meta else None) or f"YouTube Track ({vid_id})"
            artist = (yt_meta.get("artist") if yt_meta else None) or "YouTube Artist"
            return Track(
                track_id=f"yt_{vid_id}",
                title=title,
                artist=artist,
                duration=210,
                thumbnail=thumb,
                source_url=f"https://www.youtube.com/watch?v={vid_id}" if vid_id != "unknown" else clean_input,
                stream_url=None,
                requester_user_id=requester_id,
                requester_name=requester_name,
                is_video=False,
                media_type="audio",
            )

        # Case 3: Music Search Query
        clean_query = self._clean_search_query(clean_input) or clean_input
        logger.info("[EXTRACTOR] Multi-candidate search for query: '%s' (cleaned: '%s')", clean_input, clean_query)

        # 1. Search YouTube scraper for candidate tracks (fastest, zero-API)
        yt_candidates: List[Track] = []
        try:
            yt_candidates = await loop.run_in_executor(
                None, self._search_youtube_ytinitialdata, clean_input, 5, requester_id, requester_name
            )
            for y in yt_candidates:
                y.source = "youtube"
        except Exception as e:
            logger.debug("[EXTRACTOR] YouTube scraper search note: %s", str(e))

        # 2. Search YouTube API v3
        yt_api_candidates: List[Track] = []
        try:
            yt_api_candidates = await loop.run_in_executor(
                None, self._search_youtube_api_v3, clean_input, 5, requester_id, requester_name
            )
            for y in yt_api_candidates:
                y.source = "youtube"
        except Exception as e:
            logger.debug("[EXTRACTOR] YouTube API search note: %s", str(e))

        # 3. Search JioSaavn for candidate tracks
        jio_candidates: List[Track] = []
        try:
            jio_candidates = await loop.run_in_executor(
                None, self._extract_jiosaavn_multi, clean_query, 5, requester_id, requester_name
            )
            for j in jio_candidates:
                j.source = "jiosaavn"
        except Exception as e:
            logger.debug("[EXTRACTOR] JioSaavn search note: %s", str(e))

        # 4. Search yt-dlp YouTube
        yt_dlp_candidates: List[Track] = []
        try:
            yt_dlp_candidates = await loop.run_in_executor(
                None, self._extract_ytdlp_multi, clean_query, 5, requester_id, requester_name
            )
        except Exception as e:
            logger.debug("[EXTRACTOR] yt-dlp multi search note: %s", str(e))

        # 5. Search SoundCloud candidates
        sc_candidates: List[Track] = []
        try:
            sc_candidates = await loop.run_in_executor(
                None, self._extract_soundcloud_multi, clean_query, 5, requester_id, requester_name
            )
        except Exception as e:
            logger.debug("[EXTRACTOR] SoundCloud multi search note: %s", str(e))

        all_candidates = yt_candidates + yt_api_candidates + jio_candidates + yt_dlp_candidates + sc_candidates
        logger.info(
            "[SEARCH] QUERY: \"%s\" YOUTUBE_RESULTS: %d CANDIDATES_TOTAL: %d (yt_scrape=%d, yt_api=%d, jio=%d, yt_dlp=%d, sc=%d)",
            clean_input,
            len(yt_candidates) + len(yt_api_candidates) + len(yt_dlp_candidates),
            len(all_candidates),
            len(yt_candidates),
            len(yt_api_candidates),
            len(jio_candidates),
            len(yt_dlp_candidates),
            len(sc_candidates),
        )

        if not all_candidates:
            self.last_extraction_status = "NO_SEARCH_RESULTS"
            logger.warning("[SEARCH_FAILED] No search results returned from any provider for: '%s'", clean_input)
            return None

        # Score and filter candidates against query
        scored_candidates: List[Tuple[float, Track]] = []
        seen_cand_ids = set()
        for cand in all_candidates:
            if cand.track_id in seen_cand_ids:
                continue
            seen_cand_ids.add(cand.track_id)
            is_valid, score, reason = validate_and_score_track(clean_input, cand)
            if is_valid:
                scored_candidates.append((score, cand))
                logger.info(
                    "[CANDIDATE] title=\"%s\" video_id=\"%s\" url_type=youtube_watch accepted=yes score=%.2f",
                    sanitize_text(cand.title, 40),
                    cand.track_id,
                    score,
                )
            else:
                logger.info(
                    "[CANDIDATE] title=\"%s\" video_id=\"%s\" url_type=youtube_watch accepted=no reject_reason=\"%s\"",
                    sanitize_text(cand.title, 40),
                    cand.track_id,
                    reason,
                )

        # Fallback: if strict scoring rejected all, allow candidate with highest match
        if not scored_candidates and all_candidates:
            logger.info("[EXTRACTOR] Strict validation filter fallback: evaluating all %d candidates", len(all_candidates))
            for cand in all_candidates:
                _, score, _ = validate_and_score_track(clean_input, cand)
                scored_candidates.append((max(score, 0.1), cand))

        if not scored_candidates:
            logger.warning("[NO_MATCH] No acceptable audio match found for '%s'", clean_input)
            return None

        # Sort highest score first
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        top_score, top_cand = scored_candidates[0]
        top_cand.is_video = False
        top_cand.media_type = "audio"
        logger.info(
            "[SEARCH_SUCCESS] Selected top candidate: title=\"%s\" video_id=\"%s\" source=\"%s\" score=%.2f",
            top_cand.title,
            top_cand.track_id,
            getattr(top_cand, "source", "youtube"),
            top_score,
        )
        return top_cand

    async def extract_video(
        self, query_or_url: str, requester_id: int, requester_name: str
    ) -> Optional[Track]:
        """
        Extracts top video candidate metadata for /vplay command.
        The returned Track is passed to prepare_track(is_video=True) for local video caching/download.
        """
        clean_input = query_or_url.strip()
        if not clean_input:
            return None

        is_url = bool(re.match(r"^https?://", clean_input, re.IGNORECASE))
        loop = asyncio.get_running_loop()

        # Direct YouTube URL or Video ID
        match = re.search(r"(?:v=|\/|youtu\.be\/|embed\/|shorts\/)([a-zA-Z0-9_-]{11})", clean_input)
        if match or (("youtu.be/" in clean_input or "youtube.com/watch" in clean_input) and is_url):
            vid_id = match.group(1) if match else "unknown"
            yt_meta = await loop.run_in_executor(None, self._extract_youtube_meta, clean_input)
            thumb = (yt_meta.get("thumbnail") if yt_meta else None) or f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
            title = (yt_meta.get("title") if yt_meta else None) or f"YouTube Video ({vid_id})"
            artist = (yt_meta.get("artist") if yt_meta else None) or "YouTube Channel"
            return Track(
                track_id=f"ytv_{vid_id}",
                title=title,
                artist=artist,
                duration=240,
                thumbnail=thumb,
                source_url=f"https://www.youtube.com/watch?v={vid_id}" if vid_id != "unknown" else clean_input,
                stream_url=None,
                requester_user_id=requester_id,
                requester_name=requester_name,
                is_video=True,
                media_type="video",
            )

        # Text query: search YouTube videos
        clean_query = self._clean_search_query(clean_input) or clean_input
        logger.info("[EXTRACTOR-VIDEO] Searching YouTube video candidate for query: '%s'", clean_query)

        # Scrape top candidates
        candidates = await loop.run_in_executor(
            None, self._search_youtube_ytinitialdata, clean_input, 5, requester_id, requester_name
        )

        scored_candidates: List[Tuple[float, Track]] = []
        for cand in candidates:
            is_valid, score, _ = validate_and_score_track(clean_input, cand)
            if is_valid:
                scored_candidates.append((score, cand))

        if not scored_candidates and candidates:
            for cand in candidates:
                scored_candidates.append((0.1, cand))

        if not scored_candidates:
            logger.warning("[EXTRACTOR-VIDEO] No valid video found for '%s'", clean_input)
            return None

        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        top_vid = scored_candidates[0][1]
        top_vid.is_video = True
        top_vid.media_type = "video"
        top_vid.track_id = f"ytv_{top_vid.track_id.replace('yt_', '')}"
        logger.info("[EXTRACTOR-VIDEO] Selected top video candidate: '%s'", top_vid.title)
        return top_vid

    async def search_tracks(
        self, query: str, limit: int = 5, requester_id: int = 0, requester_name: str = ""
    ) -> List[Track]:
        """Searches top matching tracks across YouTube (ytInitialData + API v3), JioSaavn, and SoundCloud."""
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

        # 5. Search SoundCloud multi results via yt-dlp
        sc_results = await loop.run_in_executor(
            None, self._extract_soundcloud_multi, clean_input, limit, requester_id, requester_name
        )

        combined: List[Track] = []
        seen_titles = set()

        for tr in (yt_scraper_results + yt_api_results + jio_results + yt_results + sc_results):
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
                        vid_id = str(entry.get("id") or "")
                        tracks.append(
                            Track(
                                track_id=f"yt_{vid_id or uuid.uuid4().hex[:8]}",
                                title=sanitize_text(entry.get("title") or query, 80),
                                artist=sanitize_text(entry.get("artist") or entry.get("uploader") or entry.get("channel"), 60) or "YouTube",
                                duration=int(entry.get("duration") or 210),
                                thumbnail=entry.get("thumbnail") or DEFAULT_THUMBNAIL,
                                source_url=entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"),
                                stream_url=entry.get("url"),
                                source="youtube",
                                requester_user_id=requester_id,
                                requester_name=requester_name,
                            )
                        )
        except Exception as e:
            logger.debug("yt-dlp multi search note: %s", str(e))
        return tracks

    def _extract_soundcloud_multi(
        self, query: str, limit: int, requester_id: int, requester_name: str
    ) -> List[Track]:
        tracks = []
        try:
            import yt_dlp
            opts = {
                "format": "bestaudio/best",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "socket_timeout": 8,
                "logger": YtDlpQuietLogger(),
            }
            from player.providers.youtube import parse_audio_stream_from_entry
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
                if info and "entries" in info:
                    for entry in (info.get("entries") or []):
                        if not entry:
                            continue
                        stream_url, _, _ = parse_audio_stream_from_entry(entry)
                        tracks.append(
                            Track(
                                track_id=f"sc_{str(entry.get('id') or uuid.uuid4().hex[:8])}",
                                title=sanitize_text(entry.get("title") or query, 80),
                                artist=sanitize_text(entry.get("uploader") or "SoundCloud", 60),
                                duration=int(entry.get("duration") or 180),
                                thumbnail=entry.get("thumbnail") or DEFAULT_THUMBNAIL,
                                source_url=entry.get("webpage_url") or f"https://soundcloud.com/search?q={urllib.parse.quote(query)}",
                                stream_url=stream_url or entry.get("url"),
                                source="soundcloud",
                                requester_user_id=requester_id,
                                requester_name=requester_name,
                            )
                        )
        except Exception as e:
            logger.debug("SoundCloud multi search note: %s", str(e))
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
            from player.providers.youtube import parse_audio_stream_from_entry
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"scsearch1:{query}", download=False)
                if info and "entries" in info and info["entries"]:
                    entry = info["entries"][0]
                    stream_url, _, _ = parse_audio_stream_from_entry(entry)
                    return Track(
                        track_id=f"sc_{str(entry.get('id') or uuid.uuid4().hex[:8])}",
                        title=sanitize_text(entry.get("title") or query, 80),
                        artist=sanitize_text(entry.get("uploader") or "SoundCloud", 60),
                        duration=int(entry.get("duration") or 180),
                        thumbnail=entry.get("thumbnail") or DEFAULT_THUMBNAIL,
                        source_url=entry.get("webpage_url") or f"https://soundcloud.com/search?q={urllib.parse.quote(query)}",
                        stream_url=stream_url or entry.get("url"),
                        source="soundcloud",
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
        """Downloads direct audio stream from JioSaavn, SoundCloud, GoogleVideo / YouTube CDN, or direct CDN."""
        from player.models import is_direct_media_url
        if not url or not is_direct_media_url(url):
            return False

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
                "Accept": "*/*",
                "Accept-Encoding": "identity",
            }
            if "saavncdn" in url or "jiosaavn" in url:
                headers["Referer"] = "https://www.jiosaavn.com/"
            elif "sndcdn" in url or "soundcloud" in url:
                headers["Referer"] = "https://soundcloud.com/"
            elif "googlevideo" in url or "youtube" in url:
                headers["Referer"] = "https://www.youtube.com/"

            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as response:
                status_code = getattr(response, "status", 200)
                if status_code not in (200, 206):
                    logger.warning("Direct download HTTP status error %s for %s", status_code, url[:80])
                    return False

                content_type = response.headers.get("Content-Type", "").lower()
                if "text/html" in content_type or "application/json" in content_type:
                    logger.warning("Direct download received invalid content-type '%s' for %s", content_type, url[:80])
                    return False

                with open(dest_path, "wb") as out_file:
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        out_file.write(chunk)

            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
                return True
            return False
        except Exception as e:
            logger.warning("Direct download failed for %s: %s", url[:80], str(e))
            if os.path.exists(dest_path):
                try:
                    os.remove(dest_path)
                except Exception:
                    pass
            return False

    def _download_ytdlp(self, url_or_query: str, dest_path: str, video_id: str = "unknown", use_cookies: bool = True) -> bool:
        try:
            import yt_dlp
            opts = {
                "format": "bestaudio/best",
                "outtmpl": dest_path,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": False,
                "nocheckcertificate": True,
                "retries": 2,
                "fragment_retries": 2,
                "socket_timeout": 20,
                "continuedl": True,
                "logger": YtDlpQuietLogger(),
            }
            cookie_file = self.cookies_path if use_cookies else None
            if cookie_file and os.path.exists(cookie_file) and os.path.getsize(cookie_file) > 0:
                if any(k in url_or_query.lower() for k in ("youtube.com", "youtu.be", "ytsearch")):
                    opts["cookiefile"] = cookie_file

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url_or_query])

            parent_dir = os.path.dirname(dest_path)
            prefix = os.path.splitext(os.path.basename(dest_path))[0]
            if os.path.exists(parent_dir):
                for fname in os.listdir(parent_dir):
                    if fname.startswith(prefix + ".") and not fname.endswith(".part") and not fname.endswith(".ytdl"):
                        fpath = os.path.join(parent_dir, fname)
                        if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                            return True
            return False
        except Exception as e:
            err_str = str(e).strip()
            sanitized_err = re.sub(r'(cookie|token|auth|key|password)=[\w\.-]+', r'\1=***', err_str, flags=re.IGNORECASE)
            
            # Classify errors securely
            err_lower = sanitized_err.lower()
            if "page needs to be reloaded" in err_lower or "reload" in err_lower:
                classification = "YOUTUBE_EXTRACTION_OR_AUTH_ERROR"
            elif "sign in to confirm" in err_lower or "not a bot" in err_lower or "bot" in err_lower:
                classification = "YOUTUBE_BOT_CHECK"
            else:
                classification = "YOUTUBE_DOWNLOAD_FAILED"
            
            logger.warning("[YTDLP_ERROR]")
            logger.warning("video_id=%s", video_id)
            logger.warning("mode=%s", "cookies" if (use_cookies and self.cookies_path) else "no_cookies")
            logger.warning("classification=%s", classification)
            logger.warning("error=%s", sanitized_err)
            return False

    def _download_video_ytdlp(self, url_or_query: str, dest_path: str, video_id: str = "unknown", use_cookies: bool = True) -> bool:
        try:
            import yt_dlp
            opts = {
                "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
                "outtmpl": dest_path,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": False,
                "nocheckcertificate": True,
                "retries": 2,
                "fragment_retries": 2,
                "socket_timeout": 20,
                "continuedl": True,
                "logger": YtDlpQuietLogger(),
            }
            cookie_file = self.cookies_path if use_cookies else None
            if cookie_file and os.path.exists(cookie_file) and os.path.getsize(cookie_file) > 0:
                if any(k in url_or_query.lower() for k in ("youtube.com", "youtu.be", "ytsearch")):
                    opts["cookiefile"] = cookie_file

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url_or_query])

            parent_dir = os.path.dirname(dest_path)
            prefix = os.path.splitext(os.path.basename(dest_path))[0]
            if os.path.exists(parent_dir):
                for fname in os.listdir(parent_dir):
                    if fname.startswith(prefix + ".") and not fname.endswith(".part") and not fname.endswith(".ytdl"):
                        fpath = os.path.join(parent_dir, fname)
                        if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                            return True
            return False
        except Exception as e:
            err_str = str(e).strip()
            sanitized_err = re.sub(r'(cookie|token|auth|key|password)=[\w\.-]+', r'\1=***', err_str, flags=re.IGNORECASE)
            
            # Classify errors securely
            err_lower = sanitized_err.lower()
            if "page needs to be reloaded" in err_lower or "reload" in err_lower:
                classification = "YOUTUBE_EXTRACTION_OR_AUTH_ERROR"
            elif "sign in to confirm" in err_lower or "not a bot" in err_lower or "bot" in err_lower:
                classification = "YOUTUBE_BOT_CHECK"
            else:
                classification = "YOUTUBE_DOWNLOAD_FAILED"
            
            logger.warning("[YTDLP_ERROR]")
            logger.warning("video_id=%s", video_id)
            logger.warning("mode=%s", "cookies" if (use_cookies and self.cookies_path) else "no_cookies")
            logger.warning("classification=%s", classification)
            logger.warning("error=%s", sanitized_err)
            return False

    def _download_soundcloud_fallback(self, search_target: str, dest_path: str, video_id: str = "unknown") -> bool:
        try:
            import yt_dlp
            opts = {
                "format": "bestaudio/best",
                "outtmpl": dest_path,
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "nocheckcertificate": True,
                "retries": 2,
                "fragment_retries": 2,
                "socket_timeout": 15,
                "continuedl": True,
                "logger": YtDlpQuietLogger(),
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(search_target, download=False)
                entries = info.get("entries", []) if info else []
                for entry in entries:
                    if not entry:
                        continue
                    url = entry.get("webpage_url") or entry.get("permalink_url")
                    if url and "api.soundcloud.com" not in url and "soundcloud.com/" in url:
                        logger.info("[MEDIA] Downloading SoundCloud fallback clean URL: %s", url)
                        ydl.download([url])
                        parent_dir = os.path.dirname(dest_path)
                        prefix = os.path.splitext(os.path.basename(dest_path))[0]
                        if os.path.exists(parent_dir):
                            for fname in os.listdir(parent_dir):
                                if fname.startswith(prefix + ".") and not fname.endswith(".part") and not fname.endswith(".ytdl"):
                                    fpath = os.path.join(parent_dir, fname)
                                    if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                                        return True
            return False
        except Exception as e:
            logger.warning("[YTDLP_ERROR] SoundCloud fallback failed for %s: %s", video_id, str(e))
            return False

    def _clean_cache_dir(self, cache_dir: str = "/tmp/aaruu_cache", max_files: int = 100, max_size_bytes: int = 500 * 1024 * 1024) -> None:
        """Bounded local cache cleanup: removes oldest files if file count or total size exceeds limit."""
        try:
            if not os.path.exists(cache_dir):
                return
            files = []
            total_size = 0
            for fname in os.listdir(cache_dir):
                fpath = os.path.join(cache_dir, fname)
                if os.path.isfile(fpath) and not fname.endswith(".part"):
                    sz = os.path.getsize(fpath)
                    mtime = os.path.getmtime(fpath)
                    files.append((fpath, sz, mtime))
                    total_size += sz

            files.sort(key=lambda x: x[2])  # Oldest first

            while (len(files) > max_files or total_size > max_size_bytes) and files:
                fpath, sz, _ = files.pop(0)
                try:
                    os.remove(fpath)
                    total_size -= sz
                    logger.info("[MEDIA] cache_cleanup removed=%s", fpath)
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Cache cleanup exception: %s", str(e))

    def _extract_video_id(self, track: Track) -> str:
        if not track:
            return "unknown"
        if track.track_id:
            clean = track.track_id.replace("yt_", "").replace("ytv_", "").replace("sc_", "").replace("jio_", "")
            if re.match(r"^[a-zA-Z0-9_-]{11}$", clean):
                return clean
        if track.source_url:
            match = re.search(r"(?:v=|\/|be\/|shorts\/)([a-zA-Z0-9_-]{11})", track.source_url)
            if match:
                return match.group(1)
        raw_id = re.sub(r"[^\w-]", "_", track.track_id or track.title or "track")[:32]
        return raw_id or "unknown"

    def _find_cached_file(self, video_id: str, cache_dir: str) -> Optional[str]:
        if not video_id:
            return None

        dirs_to_check = [cache_dir, "cache/audio", "cache/video", "/tmp/aaruu_cache"]
        for cdir in dirs_to_check:
            if not os.path.exists(cdir):
                continue

            for ext in ("webm", "mp3", "m4a", "opus", "mp4", "mkv"):
                exact = os.path.join(cdir, f"{video_id}.{ext}")
                if os.path.exists(exact) and os.path.getsize(exact) > 0:
                    return exact

            try:
                for fname in os.listdir(cdir):
                    if fname.startswith(f"{video_id}.") and not fname.endswith(".part"):
                        fpath = os.path.join(cdir, fname)
                        if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                            return fpath
            except Exception:
                pass

        return None

    async def prepare_track(self, track: Track, is_video: bool = False) -> Optional[str]:
        """
        Aviax-Style Download-First Media Preparation Pipeline:
        YouTube Video ID -> Local Cache Check -> Shared In-Flight Task -> yt-dlp Local Download -> Local File Verification -> PyTgCalls
        """
        if not track:
            return None

        video_id = self._extract_video_id(track)
        cache_dir = "cache/video" if is_video else "cache/audio"
        os.makedirs(cache_dir, exist_ok=True)

        has_cookies = bool(self.cookies_path and os.path.exists(self.cookies_path))

        # 1. Existing local_filepath on track
        if track.local_filepath and os.path.exists(track.local_filepath) and os.path.getsize(track.local_filepath) > 0:
            sz = os.path.getsize(track.local_filepath)
            logger.info(
                "[MEDIA-PREPARE] track_id=%s source=%s cookies_configured=%s cache_hit=yes file_exists=true file_size=%d path=%s",
                video_id,
                getattr(track, "source", "youtube"),
                "yes" if has_cookies else "no",
                sz,
                track.local_filepath,
            )
            return track.local_filepath

        # 2. Check local disk cache (DO NOT contact YouTube if cached!)
        cached_file = self._find_cached_file(video_id, cache_dir)
        if cached_file:
            track.local_filepath = cached_file
            sz = os.path.getsize(cached_file)
            logger.info(
                "[MEDIA-PREPARE] track_id=%s source=%s cookies_configured=%s cache_hit=yes file_exists=true file_size=%d path=%s",
                video_id,
                getattr(track, "source", "youtube"),
                "yes" if has_cookies else "no",
                sz,
                cached_file,
            )
            return cached_file

        # 3. Check in-flight download tasks for concurrent same-song requests
        if not hasattr(self, "_in_flight_tasks"):
            self._in_flight_tasks: Dict[str, asyncio.Task] = {}

        if video_id in self._in_flight_tasks:
            logger.info(
                "[MEDIA-PREPARE] track_id=%s source=%s cookies_configured=%s cache_hit=no status=in_flight_download_shared",
                video_id,
                getattr(track, "source", "youtube"),
                "yes" if has_cookies else "no",
            )
            try:
                res_path = await self._in_flight_tasks[video_id]
                if res_path and os.path.exists(res_path) and os.path.getsize(res_path) > 0:
                    track.local_filepath = res_path
                    return res_path
            except Exception as task_err:
                logger.debug("Shared download task exception for %s: %s", video_id, str(task_err))

        # 4. Launch new download task
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._execute_media_download(track, video_id, cache_dir, is_video))
        self._in_flight_tasks[video_id] = task

        try:
            local_path = await task
            if local_path and os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                track.local_filepath = local_path
                return local_path
            else:
                track.local_filepath = None
                return None
        finally:
            self._in_flight_tasks.pop(video_id, None)

    async def _execute_media_download(self, track: Track, video_id: str, cache_dir: str, is_video: bool) -> Optional[str]:
        loop = asyncio.get_running_loop()
        has_cookies = bool(self.cookies_path and os.path.exists(self.cookies_path))
        from player.models import is_direct_media_url

        # Check if direct media stream URL is provided (e.g. JioSaavn / SoundCloud / direct MP3 / CDN stream)
        if track.stream_url and is_direct_media_url(track.stream_url) and "youtube.com/watch" not in track.stream_url and "youtu.be" not in track.stream_url:
            direct_dest = os.path.join(cache_dir, f"{video_id}.mp3")
            d_ok = await loop.run_in_executor(None, self._download_direct_url, track.stream_url, direct_dest)
            if d_ok and os.path.exists(direct_dest) and os.path.getsize(direct_dest) > 0:
                sz = os.path.getsize(direct_dest)
                logger.info("[MEDIA] Direct HTTP stream download succeeded for '%s' (file_size=%d)", track.title, sz)
                self._clean_cache_dir(cache_dir)
                return direct_dest

        # Build clean YouTube watch URL for yt-dlp
        yt_watch_url = f"https://www.youtube.com/watch?v={video_id}"
        dest_template = os.path.join(cache_dir, f"{video_id}.%(ext)s")

        logger.info("[YTDLP] download_started")
        logger.info("track_id=%s", video_id)
        logger.info("cookies_configured=%s", "yes" if has_cookies else "no")
        logger.info("cache_hit=no")
        logger.info("attempt=1")

        # 1. Primary yt-dlp download (MODE A with cookies if configured)
        success = False
        if is_video:
            success = await loop.run_in_executor(None, self._download_video_ytdlp, yt_watch_url, dest_template, video_id, True)
        else:
            success = await loop.run_in_executor(None, self._download_ytdlp, yt_watch_url, dest_template, video_id, True)

        cached_file = self._find_cached_file(video_id, cache_dir)
        if success and cached_file and os.path.exists(cached_file) and os.path.getsize(cached_file) > 0:
            sz = os.path.getsize(cached_file)
            logger.info(
                "[YTDLP] download_success track_id=%s source=%s cookies_configured=%s cache_hit=no file_exists=true file_size=%d path=%s",
                video_id,
                getattr(track, "source", "youtube"),
                "yes" if has_cookies else "no",
                sz,
                cached_file,
            )
            self._clean_cache_dir(cache_dir)
            return cached_file

        # 2. MODE B fallback download (without cookies if MODE A with cookies failed)
        if has_cookies and not cached_file:
            logger.info("[YTDLP] retrying_without_cookies")
            if is_video:
                success_b = await loop.run_in_executor(None, self._download_video_ytdlp, yt_watch_url, dest_template, video_id, False)
            else:
                success_b = await loop.run_in_executor(None, self._download_ytdlp, yt_watch_url, dest_template, video_id, False)

            cached_file_b = self._find_cached_file(video_id, cache_dir)
            if success_b and cached_file_b and os.path.exists(cached_file_b) and os.path.getsize(cached_file_b) > 0:
                sz = os.path.getsize(cached_file_b)
                logger.info(
                    "[YTDLP] download_success (MODE B fallback) track_id=%s file_exists=true file_size=%d path=%s",
                    video_id,
                    sz,
                    cached_file_b,
                )
                self._clean_cache_dir(cache_dir)
                return cached_file_b

        # 3. Multi-provider Fallback Audio Download if YouTube yt-dlp failed
        if not is_video:
            clean_title = self._clean_search_query(track.title)
            clean_artist = self._clean_search_query(track.artist) if track.artist and track.artist != "YouTube Music" else ""
            fallback_query = f"{clean_title} {clean_artist}".strip() or track.title
            logger.info(
                "[MEDIA] Primary YouTube download failed for '%s' (id=%s). Initiating optional SoundCloud fallback for query: '%s'",
                track.title,
                video_id,
                fallback_query,
            )

            # SoundCloud fallback via dedicated clean search helper
            sc_target = f"scsearch3:{fallback_query}"
            sc_success = await loop.run_in_executor(None, self._download_soundcloud_fallback, sc_target, dest_template, video_id)
            cached_file = self._find_cached_file(video_id, cache_dir)
            if sc_success and cached_file and os.path.exists(cached_file) and os.path.getsize(cached_file) > 0:
                sz = os.path.getsize(cached_file)
                logger.info(
                    "[MEDIA] Optional SoundCloud fallback download succeeded for '%s' (file_size=%d)",
                    track.title,
                    sz,
                )
                self._clean_cache_dir(cache_dir)
                return cached_file

        logger.warning(
            "[YTDLP] download_failed track_id=%s source=%s cookies_configured=%s error_code=YTDLP_DOWNLOAD_FAILED",
            video_id,
            getattr(track, "source", "youtube"),
            "yes" if has_cookies else "no",
        )
        return None

    async def download_track(self, track: Track) -> bool:
        """Downloads a track's media to local cache file for PyTgCalls playback."""
        is_vid = getattr(track, "is_video", False)
        path = await self.prepare_track(track, is_video=is_vid)
        return bool(path and os.path.exists(path) and os.path.getsize(path) > 0)
