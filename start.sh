#!/usr/bin/env bash
set -e

# Export all potential site-packages paths into PYTHONPATH
export PYTHONPATH="/app:/opt/venv/lib/python3.11/site-packages:/opt/venv/lib/python3.12/site-packages:/opt/venv/lib/python3.10/site-packages:/app/.venv/lib/python3.11/site-packages:/app/.venv/lib/python3.12/site-packages:/usr/local/lib/python3.11/site-packages:$PYTHONPATH"

# Run with prioritized virtual environment if present
if [ -f "/opt/venv/bin/python" ]; then
    exec /opt/venv/bin/python main.py
elif [ -f "/app/.venv/bin/python" ]; then
    exec /app/.venv/bin/python main.py
elif [ -f ".venv/bin/python" ]; then
    exec .venv/bin/python main.py
elif command -v python3 >/dev/null 2>&1; then
    exec python3 main.py
else
    exec python main.py
fi
