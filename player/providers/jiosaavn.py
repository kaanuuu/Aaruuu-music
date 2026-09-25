"""
Aaruu Music - JioSaavn Music Provider
Integrates JioSaavn as a first-class music source with search, track normalization,
direct streaming source discovery, and album/playlist resolution.
"""

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional
import uuid
from player.models import Track
from utils.escaping import sanitize_text
from utils.logging import logger

DEFAULT_THUMBNAIL = (
    "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
)

class JioSaavnProvider:
    """
    Dedicated JioSaavn music provider. Supports searching, direct track details retrieval,
    and album/playlist expansion using configured endpoints with high reliability fallbacks.
    """

    def __init__(self):
        # Allow custom API base URL configuration via environment variables
        self.api_base = os.getenv("JIOSAAVN_API_BASE_URL") or os.getenv("SAAVN_API_URL") or "https://saavn.dev"
        self.api_base = self.api_base.rstrip("/")
        self.timeout = 2.5

    def _request_api(self, endpoint: str) -> Optional[Any]:
        """Performs a safe HTTP request to the JioSaavn API with dynamic endpoint fallbacks on DNS/network errors."""
        bases = [self.api_base]
        fallbacks = [
            "https://saavn-api.vercel.app",
            "https://saavn.me",
            "https://jio-saavn-api.vercel.app",
            "https://saavn-api-beta.vercel.app",
            "https://saavn.dev"
        ]
        for f in fallbacks:
            clean_f = f.rstrip("/")
            if clean_f not in bases:
                bases.append(clean_f)

        for base in bases:
            url = f"{base}{endpoint}"
            logger.debug("[JIOSAAVN] Querying endpoint: %s", url)
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        if data:
                            # Cache the successful host to optimize subsequent tracks
                            self.api_base = base
                            return data
            except Exception as e:
                logger.debug("[JIOSAAVN] Endpoint note '%s': %s", url, str(e))
        return None

    def _normalize_track(self, item: Dict[str, Any], requester_id: int = 0, requester_name: str = "Aaruu Music") -> Optional[Track]:
        """Converts JioSaavn song dictionary into normalized Aaruu Track model."""
        if not item or not isinstance(item, dict):
            return None

        track_id = str(item.get("id") or uuid.uuid4().hex[:8])
        title = sanitize_text(item.get("name") or item.get("title") or "Unknown JioSaavn Song", 80)
        
        # Support various formats for artist name
        artists_data = item.get("artists") or {}
        primary_artists = ""
        if isinstance(artists_data, dict):
            primary_artists_list = artists_data.get("primary", [])
            if isinstance(primary_artists_list, list):
                primary_artists = ", ".join([a.get("name") for a in primary_artists_list if isinstance(a, dict) and a.get("name")])
        if not primary_artists:
            primary_artists = item.get("primaryArtists") or item.get("artist") or item.get("singers") or "JioSaavn"
        
        artist = sanitize_text(primary_artists, 60)
        duration = int(item.get("duration") or 210)

        # Get highest quality thumbnail
        images = item.get("image") or item.get("images") or []
        thumb = DEFAULT_THUMBNAIL
        if isinstance(images, list) and images:
            last_img = images[-1]
            if isinstance(last_img, dict):
                thumb = last_img.get("url") or last_img.get("link") or DEFAULT_THUMBNAIL
            elif isinstance(last_img, str):
                thumb = last_img
        elif isinstance(images, str) and images:
            thumb = images

        # Extract direct play URL
        stream_url = self._extract_stream_url(item)
        page_url = item.get("url") or item.get("perma_url") or f"https://www.jiosaavn.com/song/{urllib.parse.quote(title)}"

        if not stream_url:
            return None

        return Track(
            track_id=f"saavn_{track_id}",
            title=title,
            artist=artist,
            duration=duration,
            thumbnail=thumb,
            source_url=page_url,
            stream_url=stream_url,
            requester_user_id=requester_id,
            requester_name=requester_name
        )

    def _extract_stream_url(self, item: Dict[str, Any]) -> Optional[str]:
        """Extracts direct high-quality audio stream CDN URL."""
        download_urls = item.get("downloadUrl") or item.get("download_url") or item.get("download_urls") or []
        if isinstance(download_urls, list) and download_urls:
            # Prefer highest quality available (usually listed last in the API response array)
            for d in reversed(download_urls):
                if isinstance(d, dict):
                    url = d.get("url") or d.get("link")
                    if url and isinstance(url, str) and url.startswith("http"):
                        return url
                elif isinstance(d, str) and d.startswith("http"):
                    return d

        # Check direct media keys (NEVER use preview_url for full tracks)
        for key in ("media_url", "encrypted_media_url", "stream_url", "media_path"):
            val = item.get(key)
            if val and isinstance(val, str) and val.startswith("http") and "jiosaavn.com/song/" not in val and "preview" not in val.lower():
                return val

        return None

    def search(self, query: str, limit: int = 5, requester_id: int = 0, requester_name: str = "") -> List[Track]:
        """Searches JioSaavn for tracks matching the query."""
        if not query or not query.strip():
            return []
        
        # We try both the standard /api/search/songs and fallback to legacy endpoints
        endpoint = f"/api/search/songs?query={urllib.parse.quote(query.strip())}&limit={limit}"
        data = self._request_api(endpoint)
        
        results = []
        if data and isinstance(data, dict):
            # Parse response format which can be wrapped in 'data'
            results = data.get("data", {}).get("results", []) or data.get("results", []) or data.get("data", [])
        
        if not results:
            # Fallback to general search if song search yielded empty
            endpoint_fallback = f"/api/search?query={urllib.parse.quote(query.strip())}"
            data_fb = self._request_api(endpoint_fallback)
            if data_fb and isinstance(data_fb, dict):
                results = data_fb.get("data", {}).get("songs", {}).get("results", []) or data_fb.get("songs", {}).get("results", [])
        
        tracks = []
        if isinstance(results, list):
            for item in results:
                track = self._normalize_track(item, requester_id, requester_name)
                if track:
                    tracks.append(track)
                    if len(tracks) >= limit:
                        break
        return tracks

    def get_track(self, track_id: str, requester_id: int = 0, requester_name: str = "") -> Optional[Track]:
        """Retrieves details for a specific JioSaavn song ID."""
        clean_id = track_id.replace("saavn_", "")
        data = self._request_api(f"/api/songs?id={clean_id}")
        if data and isinstance(data, dict):
            items = data.get("data", []) or data.get("results", [])
            if items and isinstance(items, list):
                return self._normalize_track(items[0], requester_id, requester_name)
        return None

    def get_album(self, album_id: str, requester_id: int = 0, requester_name: str = "") -> List[Track]:
        """Retrieves all tracks belonging to a JioSaavn album ID."""
        data = self._request_api(f"/api/albums?id={album_id}")
        tracks = []
        if data and isinstance(data, dict):
            album_data = data.get("data", {}) or data
            songs = album_data.get("songs", [])
            if isinstance(songs, list):
                for song in songs:
                    track = self._normalize_track(song, requester_id, requester_name)
                    if track:
                        tracks.append(track)
        return tracks

    def get_playlist(self, playlist_id: str, requester_id: int = 0, requester_name: str = "") -> List[Track]:
        """Retrieves all tracks belonging to a JioSaavn playlist ID."""
        data = self._request_api(f"/api/playlists?id={playlist_id}")
        tracks = []
        if data and isinstance(data, dict):
            playlist_data = data.get("data", {}) or data
            songs = playlist_data.get("songs", [])
            if isinstance(songs, list):
                for song in songs:
                    track = self._normalize_track(song, requester_id, requester_name)
                    if track:
                        tracks.append(track)
        return tracks

    def resolve_url(self, url: str, requester_id: int = 0, requester_name: str = "") -> List[Track]:
        """
        Attempts to resolve a direct JioSaavn URL (song, album, or playlist)
        into a list of normalized Track objects.
        """
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        
        # Check if URL represents song, album, or playlist
        if "song" in path:
            # Song details by link
            data = self._request_api(f"/api/songs?link={urllib.parse.quote(url)}")
            if data and isinstance(data, dict):
                items = data.get("data", []) or data.get("results", [])
                if items and isinstance(items, list):
                    track = self._normalize_track(items[0], requester_id, requester_name)
                    return [track] if track else []
        elif "album" in path:
            data = self._request_api(f"/api/albums?link={urllib.parse.quote(url)}")
            tracks = []
            if data and isinstance(data, dict):
                album_data = data.get("data", {}) or data
                songs = album_data.get("songs", [])
                if isinstance(songs, list):
                    for song in songs:
                        track = self._normalize_track(song, requester_id, requester_name)
                        if track:
                            tracks.append(track)
            return tracks
        elif "playlist" in path or "featured" in path:
            data = self._request_api(f"/api/playlists?link={urllib.parse.quote(url)}")
            tracks = []
            if data and isinstance(data, dict):
                playlist_data = data.get("data", {}) or data
                songs = playlist_data.get("songs", [])
                if isinstance(songs, list):
                    for song in songs:
                        track = self._normalize_track(song, requester_id, requester_name)
                        if track:
                            tracks.append(track)
            return tracks
            
        return []

jiosaavn_provider = JioSaavnProvider()
