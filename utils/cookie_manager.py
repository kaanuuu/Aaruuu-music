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
                try:
                    os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
                except Exception:
                    # Fallback to local cache directory if /tmp is completely blocked or read-only
                    tmp_path = "cache/ytdlp_cookies.txt"
                    os.makedirs("cache", exist_ok=True)

                with open(tmp_path, "w", encoding="utf-8") as f:
                    # Ensure standard Netscape header if missing
                    if "# Netscape" not in cookies_text:
                        f.write("# Netscape HTTP Cookie File\n")
                    f.write(cookies_text.strip() + "\n")

                # Set restrictive 0o600 permissions
                try:
                    os.chmod(tmp_path, 0o600)
                except Exception:
                    pass
                _CACHED_COOKIE_FILE = tmp_path

                # Register shutdown cleanup
                atexit.register(cleanup_cookie_files)
                return tmp_path
            except Exception as e:
                logger.warning("[YOUTUBE] Failed to write inline cookie file: %s", str(e))
        else:
            logger.warning("[YOUTUBE] cookies_configured=invalid (provided YTDLP_COOKIES_TEXT is not in Netscape format)")

    # Priority 2: Cookie file path via YOUTUBE_COOKIES_FILE, YTDLP_COOKIES, or COOKIES
    file_path = os.getenv("YOUTUBE_COOKIES_FILE") or os.getenv("YTDLP_COOKIES") or os.getenv("COOKIES") or "cache/cookies.txt"
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


def get_cookie_diagnostic_status() -> str:
    """
    Evaluates cookie configuration and returns diagnostic status code:
    - COOKIES_ENV_MISSING
    - COOKIES_EMPTY
    - COOKIES_INVALID
    - COOKIES_READY
    """
    raw_env = os.getenv("YTDLP_COOKIES_TEXT")
    if raw_env is None:
        raw_env_alt = os.getenv("COOKIES_TEXT") or os.getenv("YOUTUBE_COOKIES_FILE") or os.getenv("YTDLP_COOKIES") or os.getenv("COOKIES")
        if raw_env_alt is None:
            return "COOKIES_ENV_MISSING"
        raw_env = raw_env_alt

    if not raw_env or not raw_env.strip():
        return "COOKIES_EMPTY"

    cfile = get_youtube_cookie_file()
    if cfile and os.path.exists(cfile) and os.path.getsize(cfile) > 0:
        return "COOKIES_READY"

    return "COOKIES_INVALID"


def log_cookie_status_at_startup() -> None:
    """Logs safe diagnostic status at startup without leaking secrets."""
    global _LOGGED_STARTUP_STATUS
    if _LOGGED_STARTUP_STATUS:
        return
    _LOGGED_STARTUP_STATUS = True

    try:
        import yt_dlp
        import yt_dlp.version
        ytdlp_ver = getattr(yt_dlp.version, "__version__", None) or getattr(yt_dlp, "__version__", None) or "unknown"
    except Exception as e:
        ytdlp_ver = f"ImportError: {str(e)}"

    status_code = get_cookie_diagnostic_status()
    cfile = get_youtube_cookie_file()
    has_cookies = (status_code == "COOKIES_READY")
    cookie_size = os.path.getsize(cfile) if (cfile and os.path.exists(cfile)) else 0
    cookie_format = "valid" if has_cookies else "invalid" if status_code == "COOKIES_INVALID" else "none"

    logger.info("[YTDLP] version=%s", ytdlp_ver)
    logger.info("[YTDLP] cookies_status=%s", "COOKIES_READY" if has_cookies else "UNAVAILABLE")
    logger.info("[YTDLP] cookie_file_created=%s", "yes" if cfile and os.path.exists(cfile) else "no")
    logger.info("[YTDLP] cookie_file_size=%d", cookie_size)
    logger.info("[YTDLP] cookie_file_format=%s", cookie_format)
    logger.info("[YTDLP] cookie_file_usable=unknown")


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
