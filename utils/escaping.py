"""
Aaruu Music - Escaping and Sanitization Utilities
Safely escapes user input to prevent markup injection in Telegram Rich Messages and HTML.
"""

import html
import re


def escape_html(text: str | None) -> str:
    """Escapes raw strings for safe inclusion in Telegram HTML messages."""
    if not text:
        return ""
    return html.escape(str(text), quote=True)


def sanitize_text(text: str | None, max_length: int = 128) -> str:
    """Sanitizes plain user input: strips control characters, collapses whitespace, and limits length."""
    if not text:
        return ""
    clean = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", str(text))
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > max_length:
        clean = clean[: max_length - 1] + "…"
    return clean


def sanitize_url(url: str | None) -> str | None:
    """Validates that a URL is a legitimate HTTP/HTTPS URL."""
    if not url:
        return None
    url = url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        # Remove any unsafe characters
        if re.match(r"^https?://[^\s\"'<>]+$", url):
            return url
    return None
