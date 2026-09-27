FROM python:3.12-slim

# ffmpeg: required to decode/transcode audio for voice-chat playback.
# nodejs: required by yt-dlp's js_runtimes={"node": {}} option used throughout this
#         codebase to solve YouTube's signature/nsig JS challenges during extraction.
#         Without Node.js, format resolution silently degrades and shows up as
#         "download failed" or bot-check errors even when cookies are configured.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    npm \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x start.sh

CMD ["bash", "start.sh"]
