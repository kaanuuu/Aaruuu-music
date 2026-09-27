FROM python:3.12-slim

# ffmpeg: required to decode/transcode audio for voice-chat playback.
# nodejs: required by yt-dlp's js_runtimes={"node": {}} option used throughout this
#         codebase to solve YouTube's signature/nsig JS challenges during extraction.
# Both installed with --no-install-recommends to keep the build fast and avoid
# pulling in large unrelated dependency trees (this was causing slow/timed-out builds).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    npm \
    gcc \
    libc6-dev \
    git \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && python3 -c "import yt_dlp; v=getattr(yt_dlp, '__version__', None); print('yt-dlp version:', v); assert v, 'yt-dlp failed to install correctly (no __version__)'"

COPY . .

RUN chmod +x start.sh

CMD ["bash", "start.sh"]
