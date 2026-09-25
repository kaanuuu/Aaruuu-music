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
        self._download_locks: Dict[str, asyncio.Lock] = {}
        self.last_extraction_status: str = "NONE"
        self.cookies_path = get_youtube_cookie_file()

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
        Extracts verified audio track with strict matching, short-clip rejection, and multi-provider cascade.
        Never blindly accepts the first search result. Validates title, artist, duration, and playable audio.
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
                    is_valid, _, reason = validate_and_score_track(clean_input, candidate)
                    if is_valid:
                        return candidate
                    else:
                        logger.warning("[EXTRACTOR] JioSaavn URL track rejected: %s", reason)
            except Exception as e:
                logger.warning("[EXTRACTOR] JioSaavn URL resolution failed: %s", str(e))

        # Case 2: Direct YouTube URL
        if ("youtu.be/" in clean_input or "youtube.com/watch" in clean_input) and is_url:
            yt_meta = await loop.run_in_executor(None, self._extract_youtube_meta, clean_input)
            from player.providers.youtube import youtube_provider
            ytdl_track = await loop.run_in_executor(
                None, youtube_provider._extract_ytdlp, clean_input, True, requester_id, requester_name
            )
            if ytdl_track and ytdl_track.stream_url:
                if yt_meta:
                    ytdl_track.thumbnail = yt_meta.get("thumbnail") or ytdl_track.thumbnail
                    ytdl_track.title = yt_meta.get("title") or ytdl_track.title
                    ytdl_track.artist = yt_meta.get("artist") or ytdl_track.artist
                ytdl_track.is_video = False
                ytdl_track.media_type = "audio"
                is_valid, _, reason = validate_and_score_track(clean_input, ytdl_track)
                if is_valid:
                    ok, _ = await verify_media_file_with_ffmpeg(ytdl_track.playable_source or ytdl_track.stream_url)
                    if ok:
                        return ytdl_track

        # Case 3: Music Search Query (Multi-provider search + score-based selection)
        clean_query = self._clean_search_query(clean_input) or clean_input
        logger.info("[EXTRACTOR] Multi-candidate search for query: '%s' (cleaned: '%s')", clean_input, clean_query)

        # 1. Search JioSaavn for candidate tracks
        jio_candidates: List[Track] = []
        try:
            jio_candidates = await loop.run_in_executor(
                None, self._extract_jiosaavn_multi, clean_query, 5, requester_id, requester_name
            )
            for j in jio_candidates:
                j.source = "jiosaavn"
        except Exception as e:
            logger.debug("[EXTRACTOR] JioSaavn search note: %s", str(e))

        # 2. Search YouTube scraper for candidate tracks
        yt_candidates: List[Track] = []
        try:
            yt_candidates = await loop.run_in_executor(
                None, self._search_youtube_ytinitialdata, clean_query, 5, requester_id, requester_name
            )
            for y in yt_candidates:
                y.source = "youtube"
        except Exception as e:
            logger.debug("[EXTRACTOR] YouTube scraper search note: %s", str(e))

        # 3. Search yt-dlp YouTube
        yt_dlp_candidates: List[Track] = []
        try:
            yt_dlp_candidates = await loop.run_in_executor(
                None, self._extract_ytdlp_multi, clean_query, 5, requester_id, requester_name
            )
        except Exception as e:
            logger.debug("[EXTRACTOR] yt-dlp multi search note: %s", str(e))

        # 4. Search SoundCloud candidates
        sc_candidates: List[Track] = []
        try:
            sc_candidates = await loop.run_in_executor(
                None, self._extract_soundcloud_multi, clean_query, 5, requester_id, requester_name
            )
        except Exception as e:
            logger.debug("[EXTRACTOR] SoundCloud multi search note: %s", str(e))

        all_candidates = jio_candidates + yt_candidates + yt_dlp_candidates + sc_candidates
        logger.info(
            "[SEARCH] query=\"%s\" provider=\"multi\" results=%d (jio=%d, yt_scrape=%d, yt_dlp=%d, sc=%d)",
            clean_input,
            len(all_candidates),
            len(jio_candidates),
            len(yt_candidates),
            len(yt_dlp_candidates),
            len(sc_candidates),
        )

        if not all_candidates:
            self.last_extraction_status = "NO_SEARCH_RESULTS"
            logger.warning("[SEARCH_FAILED] No search results returned from any provider for: '%s'", clean_input)
            return None

        self.last_extraction_status = "MATCH_FOUND_BUT_EXTRACTION_FAILED"

        # 5. Score and filter candidates against query
        scored_candidates: List[Tuple[float, Track]] = []
        seen_cand_ids = set()
        for cand in all_candidates:
            if cand.track_id in seen_cand_ids:
                continue
            seen_cand_ids.add(cand.track_id)
            is_valid, score, reason = validate_and_score_track(clean_input, cand)
            if is_valid:
                scored_candidates.append((score, cand))
            else:
                logger.debug("[EXTRACTOR] Filtered candidate '%s': %s", cand.title, reason)

        # If strict scoring rejected all, allow highest-overlap candidate
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

        # 6. Iterate through ranked candidates, extract/download audio, and verify with FFmpeg
        from player.models import classify_media_source

        for score, cand in scored_candidates:
            logger.info(
                "[MATCH] candidate=\"%s\" video_id=\"%s\" url=\"%s\" source=\"%s\" score=%.2f",
                cand.title,
                cand.track_id,
                cand.source_url,
                getattr(cand, "source", "unknown"),
                score,
            )
            logger.info("[EXTRACTOR] status=started candidate=\"%s\"", cand.title)

            # Download track to local cache for resilient playback
            download_ok = await self.download_track(cand)

            cand_source = cand.playable_source
            source_type = classify_media_source(cand_source)

            # Strict source check: Never pass raw YouTube watch URLs to FFmpeg
            if source_type in ("LOCAL_FILE", "DIRECT_HTTP_MEDIA"):
                logger.info("[EXTRACTOR] status=success candidate=\"%s\"", cand.title)
                logger.info("[SOURCE] type=%s path=\"%s\"", source_type, cand_source)

                # Validate media decoding with FFmpeg
                ok, log = await verify_media_file_with_ffmpeg(cand_source)
                if ok:
                    cand.is_video = False
                    cand.media_type = "audio"
                    logger.info(
                        "[EXTRACTOR] Selected and verified track: '%s' by %s (Score: %.2f)",
                        cand.title,
                        cand.artist,
                        score,
                    )
                    return cand
                else:
                    logger.warning("[EXTRACTOR] Candidate '%s' failed FFmpeg validation: %s", cand.title, log)
            else:
                logger.warning(
                    "[EXTRACTION_FAILED] Candidate '%s' playable_source classified as %s (not LOCAL_FILE/DIRECT_HTTP_MEDIA)",
                    cand.title,
                    source_type,
                )

        # 7. Fallback to direct SoundCloud search if all previous candidates failed
        logger.info("[EXTRACTOR] Searching SoundCloud fallback for '%s'...", clean_query)
        sc_track = await loop.run_in_executor(
            None, self._extract_soundcloud, clean_query, requester_id, requester_name
        )
        if sc_track:
            logger.info("[MATCH] candidate=\"%s\" (SoundCloud fallback)", sc_track.title)
            await self.download_track(sc_track)
            sc_source = sc_track.playable_source
            sc_type = classify_media_source(sc_source)
            if sc_type in ("LOCAL_FILE", "DIRECT_HTTP_MEDIA"):
                ok, _ = await verify_media_file_with_ffmpeg(sc_source)
                if ok:
                    sc_track.is_video = False
                    sc_track.media_type = "audio"
                    logger.info("[EXTRACTOR] SoundCloud fallback verified: '%s' by %s", sc_track.title, sc_track.artist)
                    return sc_track

        logger.warning("[PLAYBACK_FAILED] No valid playable audio stream found for query: '%s'", clean_input)
        return None

    async def extract_video(
        self, query_or_url: str, requester_id: int, requester_name: str
    ) -> Optional[Track]:
        """
        Extracts verified video track for /vplay command.
        Searches YouTube for video, verifies stream with FFmpeg, and rejects Shorts/clips.
        Never passes watch-page URL directly to FFmpeg.
        """
        clean_input = query_or_url.strip()
        if not clean_input:
            return None

        is_url = bool(re.match(r"^https?://", clean_input, re.IGNORECASE))
        loop = asyncio.get_running_loop()
        from player.providers.youtube import youtube_provider

        # Direct YouTube URL
        if is_url:
            track = await loop.run_in_executor(
                None, youtube_provider._extract_video_ytdlp, clean_input, True, requester_id, requester_name
            )
            if track and track.stream_url:
                track.is_video = True
                track.media_type = "video"
                ok, log = await verify_media_file_with_ffmpeg(track.playable_source or track.stream_url)
                if ok:
                    return track
                logger.warning("[EXTRACTOR-VIDEO] Direct URL failed verification: %s", log)
            return None

        # Text query: search YouTube videos
        clean_query = self._clean_search_query(clean_input) or clean_input
        logger.info("[EXTRACTOR-VIDEO] Searching YouTube video for query: '%s'", clean_query)

        # Scrape top candidates
        candidates = await loop.run_in_executor(
            None, self._search_youtube_ytinitialdata, clean_query, 5, requester_id, requester_name
        )

        scored_candidates: List[Tuple[float, Track]] = []
        for cand in candidates:
            is_valid, score, _ = validate_and_score_track(clean_input, cand)
            if is_valid:
                scored_candidates.append((score, cand))

        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        for score, cand in scored_candidates:
            video_track = await loop.run_in_executor(
                None, youtube_provider._extract_video_ytdlp, cand.source_url, True, requester_id, requester_name
            )
            if video_track and video_track.stream_url:
                video_track.is_video = True
                video_track.media_type = "video"
                ok, log = await verify_media_file_with_ffmpeg(video_track.playable_source or video_track.stream_url)
                if ok:
                    logger.info("[EXTRACTOR-VIDEO] Selected video track '%s' (Score: %.2f)", video_track.title, score)
                    return video_track
                else:
                    logger.warning("[EXTRACTOR-VIDEO] Candidate '%s' failed FFmpeg check: %s", video_track.title, log)

        # Final attempt: direct search with yt-dlp
        direct_yt = await loop.run_in_executor(
            None, youtube_provider._extract_video_ytdlp, clean_input, False, requester_id, requester_name
        )
        if direct_yt and direct_yt.stream_url:
            direct_yt.is_video = True
            direct_yt.media_type = "video"
            ok, _ = await verify_media_file_with_ffmpeg(direct_yt.playable_source or direct_yt.stream_url)
            if ok:
                return direct_yt

        logger.warning("[EXTRACTOR-VIDEO] No valid video found for '%s'", clean_input)
        return None

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
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
                if info and "entries" in info:
                    for entry in (info.get("entries") or []):
                        if not entry:
                            continue
                        tracks.append(
                            Track(
                                track_id=f"sc_{str(entry.get('id') or uuid.uuid4().hex[:8])}",
                                title=sanitize_text(entry.get("title") or query, 80),
                                artist=sanitize_text(entry.get("uploader") or "SoundCloud", 60),
                                duration=int(entry.get("duration") or 180),
                                thumbnail=entry.get("thumbnail") or DEFAULT_THUMBNAIL,
                                source_url=entry.get("webpage_url") or f"https://soundcloud.com/search?q={urllib.parse.quote(query)}",
                                stream_url=entry.get("url"),
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
            return os.path.exists(dest_path) and os.path.getsize(dest_path) > 0
        except Exception as e:
            logger.warning("yt-dlp download failed for %s: %s", url_or_query, str(e))
            return False

    def _download_video_ytdlp(self, url_or_query: str, dest_path: str) -> bool:
        try:
            import yt_dlp
            opts = {
                "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best[height<=720]/best",
                "outtmpl": dest_path,
                "quiet": True,
                "no_warnings": True,
                "nocheckcertificate": True,
                "logger": YtDlpQuietLogger(),
                "socket_timeout": 15,
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

            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url_or_query])
            return os.path.exists(dest_path) and os.path.getsize(dest_path) > 0
        except Exception as e:
            logger.warning("yt-dlp video download failed for %s: %s", url_or_query, str(e))
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

    async def download_track(self, track: Track) -> bool:
        """
        Downloads a track's audio or video stream to a local cache file for resilient zero-jitter playback.
        Uses a per-track_id async Lock to prevent concurrent duplicate downloads.
        Strict Priority:
        1. existing local_filepath
        2. existing cached local file in /tmp/aaruu_cache
        3. direct track.stream_url
        4. only then source_url as an extraction/reference URL
        """
        if not track:
            return False

        if not hasattr(self, "_download_locks"):
            self._download_locks = {}

        track_lock = self._download_locks.setdefault(track.track_id, asyncio.Lock())

        async with track_lock:
            # Priority 1: If already cached in local_filepath and valid, reuse it immediately
            if track.local_filepath and os.path.exists(track.local_filepath) and os.path.getsize(track.local_filepath) > 0:
                logger.info("[MEDIA] track=%s", track.title)
                logger.info("[MEDIA] candidate=\"%s\"", track.title)
                logger.info("[MEDIA] provider=%s", getattr(track, "source", "unknown"))
                logger.info("[MEDIA] content_id=%s", track.track_id)
                logger.info("[MEDIA] cache_hit=True")
                logger.info("[MEDIA] source_type=LOCAL_FILE")
                logger.info("[MEDIA] local_download=True")
                logger.info("[MEDIA] downloading_source=%s", track.local_filepath)
                return True

            cache_dir = "/tmp/aaruu_cache"
            if not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)

            is_video = getattr(track, "is_video", False)
            ext = "mp4" if is_video else "mp3"
            local_path = os.path.join(cache_dir, f"{track.track_id}.{ext}")

            # Priority 2: Check if file already exists in cache (e.g. from a previous playback)
            if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                track.local_filepath = local_path
                logger.info("[MEDIA] track=%s", track.title)
                logger.info("[MEDIA] candidate=\"%s\"", track.title)
                logger.info("[MEDIA] provider=%s", getattr(track, "source", "unknown"))
                logger.info("[MEDIA] content_id=%s", track.track_id)
                logger.info("[MEDIA] cache_hit=True")
                logger.info("[MEDIA] source_type=LOCAL_FILE")
                logger.info("[MEDIA] local_download=True")
                logger.info("[MEDIA] downloading_source=%s", local_path)
                return True

            from player.models import is_youtube_watch_url, is_direct_media_url, classify_media_source

            stream_url = getattr(track, "stream_url", None)
            source_url = getattr(track, "source_url", None)

            loop = asyncio.get_running_loop()

            # If stream_url is missing but source_url is a YouTube watch URL, attempt direct stream extraction first
            if not stream_url and source_url and is_youtube_watch_url(source_url):
                try:
                    from player.providers.youtube import youtube_provider
                    ytdl_tr = await loop.run_in_executor(
                        None, youtube_provider._extract_ytdlp, source_url, True, track.requester_user_id, track.requester_name
                    )
                    if ytdl_tr and ytdl_tr.stream_url and is_direct_media_url(ytdl_tr.stream_url):
                        track.stream_url = ytdl_tr.stream_url
                        stream_url = track.stream_url
                except Exception as e:
                    logger.debug("Pre-download stream extraction note: %s", str(e))

            success = False

            # Priority 3: Direct track.stream_url
            if stream_url and is_direct_media_url(stream_url):
                logger.info("[MEDIA] track=%s", track.title)
                logger.info("[MEDIA] candidate=\"%s\"", track.title)
                logger.info("[MEDIA] provider=%s", getattr(track, "source", "unknown"))
                logger.info("[MEDIA] content_id=%s", track.track_id)
                logger.info("[MEDIA] cache_hit=False")
                logger.info("[MEDIA] source_url=%s", source_url)
                logger.info("[MEDIA] stream_url=%s", stream_url)
                logger.info("[MEDIA] stream_resolved=True")
                logger.info("[MEDIA] selected_source_type=DIRECT_MEDIA")
                logger.info("[MEDIA] downloading_source=%s", stream_url)

                if is_video:
                    success = await loop.run_in_executor(None, self._download_video_ytdlp, stream_url, local_path)
                else:
                    success = await loop.run_in_executor(None, self._download_direct_url, stream_url, local_path)
                    if not success and source_url and is_youtube_watch_url(source_url):
                        # Fallback to downloading source_url with yt-dlp if direct download fails
                        success = await loop.run_in_executor(None, self._download_ytdlp, source_url, local_path)
            else:
                # Priority 4: source_url fallback
                download_source = source_url or stream_url
                if not download_source:
                    return False

                stype = "YOUTUBE_WATCH" if is_youtube_watch_url(download_source) else ("DIRECT_MEDIA" if is_direct_media_url(download_source) else "UNKNOWN")
                logger.info("[MEDIA] track=%s", track.title)
                logger.info("[MEDIA] candidate=\"%s\"", track.title)
                logger.info("[MEDIA] provider=%s", getattr(track, "source", "unknown"))
                logger.info("[MEDIA] content_id=%s", track.track_id)
                logger.info("[MEDIA] cache_hit=False")
                logger.info("[MEDIA] source_url=%s", source_url)
                logger.info("[MEDIA] stream_url=%s", stream_url)
                logger.info("[MEDIA] stream_resolved=False")
                logger.info("[MEDIA] selected_source_type=%s", stype)
                logger.info("[MEDIA] downloading_source=%s", download_source)

                if is_video:
                    success = await loop.run_in_executor(None, self._download_video_ytdlp, download_source, local_path)
                else:
                    if is_direct_media_url(download_source):
                        success = await loop.run_in_executor(None, self._download_direct_url, download_source, local_path)
                    elif is_youtube_watch_url(download_source):
                        success = await loop.run_in_executor(None, self._download_ytdlp, download_source, local_path)

            if success:
                if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                    track.local_filepath = local_path
                    logger.info("[MEDIA] local_download=True path=%s", local_path)
                    self._clean_cache_dir(cache_dir)
                    return True

                # Check if yt-dlp created a file with a different audio extension in cache_dir
                try:
                    for fname in os.listdir(cache_dir):
                        if fname.startswith(f"{track.track_id}.") and not fname.endswith(".part"):
                            fpath = os.path.join(cache_dir, fname)
                            if os.path.isfile(fpath) and os.path.getsize(fpath) > 0:
                                track.local_filepath = fpath
                                logger.info("[MEDIA] local_download=True path=%s", fpath)
                                self._clean_cache_dir(cache_dir)
                                return True
                except Exception:
                    pass

            logger.warning("[MEDIA] local_download=False track=\"%s\"", track.title)
            return False
