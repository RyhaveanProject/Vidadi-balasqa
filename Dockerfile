FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# System deps: ffmpeg for audio, build essentials for native wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    build-essential \
    libsndfile1 \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt

# Install emergentintegrations from custom index, then rest
RUN pip install --upgrade pip \
 && pip install emergentintegrations --extra-index-url https://d33sy5i8bnduwe.cloudfront.net/simple/ \
 && pip install -r requirements.txt

COPY . /app

# Persist sqlite/whisper cache
RUN mkdir -p /app/data /app/.cache

ENV WHISPER_CACHE_DIR=/app/.cache/whisper

CMD ["bash", "/app/docker/entrypoint.sh"]
