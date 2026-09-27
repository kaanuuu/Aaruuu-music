#!/usr/bin/env bash
set -e

# NOTE: this project is deployed via the Dockerfile (see railway.toml), which installs
# all dependencies straight into the image's system site-packages with a single
# `pip install -r requirements.txt`. There is no /opt/venv or /app/.venv in that image,
# so we must NOT inject those paths into PYTHONPATH -- doing so risked Python resolving
# imports (like yt_dlp) against stale/empty paths ahead of the real install location,
# which showed up as "[YTDLP] version=unknown" and broken downloads at runtime.
exec python3 main.py
