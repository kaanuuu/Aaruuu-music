FROM python:3.12-slim

# ffmpeg: required to decode/transcode audio for voice-chat playback.
# nodejs: required by yt-dlp's js_runtimes={"node": {}} option used throughout this
#         codebase to solve YouTube's signature/nsig JS challenges during extraction.
#         Installed via NodeSource (not apt's default nodejs package) because the
#         Debian nodejs package pulls in ~150 unrelated dev-tool dependencies
#         (webpack, jest, terser, etc.) that bloat and slow down the build a lot.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    gcc \
    git \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x start.sh

CMD ["bash", "start.sh"]
