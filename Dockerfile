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

# Copy requirements
COPY requirements.txt .

# 1. Install dependencies into system Python directly
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 2. Also create /opt/venv with system packages included
RUN python3 -m venv --system-site-packages /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# 3. Create /app/.venv as well
RUN python3 -m venv --system-site-packages /app/.venv && \
    /app/.venv/bin/pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Ensure start.sh has executable permissions
RUN chmod +x start.sh

ENV PATH="/opt/venv/bin:/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app:/opt/venv/lib/python3.11/site-packages:/app/.venv/lib/python3.11/site-packages:/usr/local/lib/python3.11/site-packages"

CMD ["bash", "start.sh"]
