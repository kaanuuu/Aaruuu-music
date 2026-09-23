FROM python:3.11-slim

# Install OS libraries and ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    gcc \
    python3-dev \
    curl \
    bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create virtual environment at /opt/venv
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Create symlinks so python is accessible from /opt/venv, /app/.venv, and system PATH
RUN mkdir -p /app/.venv/bin && \
    ln -sf /opt/venv/bin/python /app/.venv/bin/python && \
    ln -sf /opt/venv/bin/pip /app/.venv/bin/pip && \
    chmod +x start.sh

CMD ["bash", "start.sh"]
