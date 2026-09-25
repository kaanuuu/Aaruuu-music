"""
Aaruu Music - YouTube Cookie & Session Manager
Handles secure parsing, validation, file creation, permission setting,
and cleanup for YouTube Netscape cookies without leaking sensitive details.
"""

import atexit
import os
import re
from typing import Optional
from utils.logging import logger

_CACHED_COOKIE_FILE: Optional[str] = None
_LOGGED_STARTUP_STATUS: bool = False


def validate_netscape_cookies_text(cookies_text: str) -> bool:
    """
    Validates if string follows standard Netscape cookie format.
    Must contain domain entries (e.g. .youtube.com) or tab-delimited columns.
    """
    if not cookies_text or not isinstance(cookies_text, str):
        return False

    clean_text = cookies_text.strip()
    if not clean_text:
        return False

    # Check for header or common cookie domains
    if "# Netscape HTTP Cookie File" in clean_text:
        return True

    lines = [line.strip() for line in clean_text.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines:
        return False

    valid_lines = 0
    for line in lines:
        parts = re.split(r"\s+", line)
        if len(parts) >= 6:
            valid_lines += 1

    return valid_lines > 0


def get_youtube_cookie_file() -> Optional[str]:
    """
    Retrieves or generates a secure, restrictive-permission Netscape cookies file
    from YTDLP_COOKIES_TEXT or YOUTUBE_COOKIES_FILE environment variables.
    Reuses the generated cookie file across requests.
    """
    global _CACHED_COOKIE_FILE

    if _CACHED_COOKIE_FILE and os.path.exists(_CACHED_COOKIE_FILE) and os.path.getsize(_CACHED_COOKIE_FILE) > 0:
        return _CACHED_COOKIE_FILE

    # Priority 1: Inline cookie text via YTDLP_COOKIES_TEXT or COOKIES_TEXT
    cookies_text = os.getenv("YTDLP_COOKIES_TEXT") or os.getenv("COOKIES_TEXT")
    if cookies_text:
        if validate_netscape_cookies_text(cookies_text):
            try:
                tmp_path = "/tmp/ytdlp_cookies.txt"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    # Ensure standard Netscape header if missing
                    if "# Netscape" not in cookies_text:
                        f.write("# Netscape HTTP Cookie File\n")
                    f.write(cookies_text.strip() + "\n")

                # Set restrictive 0o600 permissions
                os.chmod(tmp_path, 0o600)
                _CACHED_COOKIE_FILE = tmp_path

                # Register shutdown cleanup
                atexit.register(cleanup_cookie_files)
                return tmp_path
            except Exception as e:
                logger.warning("[YOUTUBE] Failed to write inline cookie file: %s", str(e))
        else:
            logger.warning("[YOUTUBE] cookies_configured=invalid (provided YTDLP_COOKIES_TEXT is not in Netscape format)")

    # Priority 2: Cookie file path via YOUTUBE_COOKIES_FILE, YTDLP_COOKIES, or COOKIES
    file_path = os.getenv("YOUTUBE_COOKIES_FILE") or os.getenv("YTDLP_COOKIES") or os.getenv("COOKIES") or "/tmp/cookies.txt"
    if file_path and os.path.exists(file_path) and os.path.isfile(file_path) and os.path.getsize(file_path) > 0:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(512)
            if validate_netscape_cookies_text(content):
                _CACHED_COOKIE_FILE = file_path
                return file_path
            else:
                logger.warning("[YOUTUBE] cookies_configured=invalid (file '%s' is not in Netscape format)", file_path)
        except Exception as e:
            logger.warning("[YOUTUBE] Failed reading cookie file '%s': %s", file_path, str(e))

    return None


def log_cookie_status_at_startup() -> None:
    """Logs safe diagnostic status at startup without leaking secrets."""
    global _LOGGED_STARTUP_STATUS
    if _LOGGED_STARTUP_STATUS:
        return
    _LOGGED_STARTUP_STATUS = True

    cfile = get_youtube_cookie_file()
    if cfile:
        logger.info("[YOUTUBE] cookies_configured=true file_path=%s", cfile)
    else:
        logger.info("[YOUTUBE] cookies_configured=false")


def cleanup_cookie_files() -> None:
    """Deletes temporary cookie files on clean shutdown."""
    global _CACHED_COOKIE_FILE
    if _CACHED_COOKIE_FILE and _CACHED_COOKIE_FILE.startswith("/tmp/") and os.path.exists(_CACHED_COOKIE_FILE):
        try:
            os.remove(_CACHED_COOKIE_FILE)
            logger.info("[YOUTUBE] Cleaned up temporary cookie file: %s", _CACHED_COOKIE_FILE)
        except Exception:
            pass
    _CACHED_COOKIE_FILE = None
