#!/usr/bin/env bash
# Install ffmpeg + ffprobe on the Actions runner.
# Ubuntu's apt mirror for GitHub runners has repeatedly stalled for 10+ minutes, so prefer the
# static BtbN build hosted on GitHub Releases (fast from runners) and fall back to apt.
set -euo pipefail

URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
DEST=/opt/ffmpeg

if curl -fsSL --retry 3 --connect-timeout 20 --max-time 240 "$URL" -o /tmp/ffmpeg.tar.xz; then
  sudo mkdir -p "$DEST"
  sudo tar -xJf /tmp/ffmpeg.tar.xz -C "$DEST" --strip-components=1
  echo "$DEST/bin" >> "$GITHUB_PATH"
  "$DEST/bin/ffmpeg" -version | head -1
else
  echo "Static ffmpeg download failed; falling back to apt."
  sudo apt-get update && sudo apt-get install -y ffmpeg
fi
