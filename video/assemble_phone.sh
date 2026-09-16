#!/usr/bin/env bash
# Full build with the phone footage: narration timing -> picture -> audio -> overlay -> mux.
#   PHONE=phone.mp4 ./assemble_phone.sh            (set RENDER=0 to reuse anim.mp4)
set -euo pipefail
cd "$(dirname "$0")"
PHONE="${PHONE:-phone.mp4}"
python3 phone_timing.py "$PHONE" | tail -2
T0=$(python3 -c "import json;print(json.load(open('phone.json'))['start'])")
if [ "${RENDER:-1}" = "1" ]; then
  FPS=60 OUT=anim.mp4 node render.mjs > render.log 2>&1
  tail -2 render.log
fi
MUSIC="${MUSIC:-mus_Inspired.mp3}" bash build-audio.sh
# phone: drop the status bar, scale to 980 high, round the corners, place on the right from T0
ffmpeg -hide_banner -loglevel error -y -i anim.mp4 -i "$PHONE" -i audio.wav -filter_complex "\
 [1:v]crop=iw:ih-66:0:66,scale=-2:980,format=rgba,\
 geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lt(min(X,W-1-X),44)*lt(min(Y,H-1-Y),44)*gt(hypot(min(X,W-1-X)-44,min(Y,H-1-Y)-44),44),0,255)',\
 setpts=PTS+${T0}/TB[ph]; [0:v][ph]overlay=x=1300:y=50:eof_action=pass:format=auto[v]" \
 -map "[v]" -map 2:a -c:v libx264 -preset medium -crf 17 -pix_fmt yuv420p -c:a aac -b:a 192k -ar 48000 \
 -shortest -movflags +faststart demo-video.mp4
ffprobe -v error -show_entries format=duration:stream=width,height,r_frame_rate -of default=nw=1 demo-video.mp4
