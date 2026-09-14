#!/usr/bin/env bash
# Muxes the rendered animation with the narration+music mix.
#   ./assemble.sh            -> demo-video.mp4
# Rebuild the parts first with:
#   python gen_missing.py    (narration, needs the pyenv 3.10 with chatterbox)
#   node render.mjs          (picture; FPS=60 OUT=anim.mp4)
set -euo pipefail
cd "$(dirname "$0")"
OUT="${1:-demo-video.mp4}"

ffmpeg -hide_banner -loglevel error -y \
  -i anim.mp4 -i audio.wav \
  -c:v copy -c:a aac -b:a 192k -ar 48000 \
  -shortest -movflags +faststart "$OUT"

echo "wrote $OUT"
ffprobe -v error -show_entries format=duration:stream=codec_type,width,height,r_frame_rate \
  -of default=noprint_wrappers=1 "$OUT"
