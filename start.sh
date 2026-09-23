#!/usr/bin/env bash
set -e

# Prioritize virtual environment python if present
if [ -f "/app/.venv/bin/python" ]; then
    exec /app/.venv/bin/python main.py
elif [ -f "/opt/venv/bin/python" ]; then
    exec /opt/venv/bin/python main.py
elif [ -f ".venv/bin/python" ]; then
    exec .venv/bin/python main.py
elif command -v python3 >/dev/null 2>&1; then
    exec python3 main.py
else
    exec python main.py
fi
