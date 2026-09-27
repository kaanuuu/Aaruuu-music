FROM python:3.12-slim

# ffmpeg: required to decode/transcode audio for voice-chat playback.
# nodejs: required by yt-dlp's js_runtimes={"node": {}} option used throughout this
#         codebase to solve YouTube's signature/nsig JS challenges during extraction.
# libc6-dev: provides C standard library headers (stdint.h etc.) needed to compile
#            tgcrypto from source -- without it, pip install fails with
#            "fatal error: stdint.h: No such file or directory"
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    npm \
    gcc \
    libc6-dev \
    git \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x start.sh

CMD ["bash", "start.sh"]
