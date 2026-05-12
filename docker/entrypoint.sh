#!/usr/bin/env bash
set -e

echo "[entrypoint] Vidadi AI Voice Userbot starting..."
echo "[entrypoint] ffmpeg: $(ffmpeg -version 2>/dev/null | head -1)"

# Ensure data dir
mkdir -p /app/data /app/.cache

cd /app
exec python -u main.py
