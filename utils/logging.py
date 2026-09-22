"""
Aaruu Music - Logging Utilities
Provides safe, structured logging with automatic secret masking.
"""

import logging
import os
import re
from typing import Any

# Patterns to mask in logs
_SENSITIVE_PATTERNS = [
    re.compile(r"(\b\d{8,12}:[A-Za-z0-9_-]{35}\b)"),  # Bot Token format
    re.compile(r"(bot[0-9]{8,12}:[A-Za-z0-9_-]{35})", re.IGNORECASE),
    re.compile(r"(session[=:][A-Za-z0-9+/=_-]{20,})", re.IGNORECASE),
    re.compile(r"(token[=:][A-Za-z0-9+/=_-]{10,})", re.IGNORECASE),
]


class SecretMaskingFilter(logging.Filter):
    """Filter that strips out bot tokens, passwords, and sensitive sessions."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.mask_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._mask_val(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._mask_val(v) for v in record.args)
        return True

    def _mask_val(self, val: Any) -> Any:
        if isinstance(val, str):
            return self.mask_secrets(val)
        return val

    @staticmethod
    def mask_secrets(text: str) -> str:
        for pattern in _SENSITIVE_PATTERNS:
            text = pattern.sub("[REDACTED_SECRET]", text)
        return text


def setup_logger(name: str = "aaruu_music") -> logging.Logger:
    """Configures and returns the central structured application logger."""
    log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level_str, logging.INFO)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.addFilter(SecretMaskingFilter())
        logger.addHandler(handler)

    return logger


logger = setup_logger("aaruu_music")
