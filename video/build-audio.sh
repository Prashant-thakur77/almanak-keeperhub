#!/usr/bin/env bash
# music bed + narration -> audio.wav (narration ducks the music automatically)
set -euo pipefail
cd "$(dirname "$0")"
D=$(python3 -c "import json;print(round(json.load(open('timings.json'))['total']+1.4,2))")
FO=$(python3 -c "print(round($D-4,2))")
lfo(){ echo "volume='$1*(0.78+0.22*sin(2*PI*$2*t+$3))':eval=frame"; }

# MUSIC=<file> uses a real track (trimmed, faded, levelled); unset synthesises the pad.
if [ -n "${MUSIC:-}" ]; then
  # -stream_loop: a track shorter than the video repeats (the crossfade is the track's own fade-out into its intro)
  ffmpeg -hide_banner -loglevel error -y -stream_loop -1 -i "$MUSIC" -t "$D" -af "\
    afade=t=in:st=0:d=1.5,afade=t=out:st=$FO:d=4,loudnorm=I=-20:TP=-2:LRA=9" \
    -ar 48000 -ac 2 -c:a pcm_s16le music.wav
else
  ffmpeg -hide_banner -loglevel error -y \
   -f lavfi -i "sine=frequency=55:duration=$D"     -f lavfi -i "sine=frequency=82.41:duration=$D" \
   -f lavfi -i "sine=frequency=164.81:duration=$D" -f lavfi -i "sine=frequency=220:duration=$D" \
   -f lavfi -i "sine=frequency=261.63:duration=$D" -f lavfi -i "sine=frequency=329.63:duration=$D" \
   -f lavfi -i "sine=frequency=392:duration=$D"    -f lavfi -i "sine=frequency=493.88:duration=$D" \
   -filter_complex "\
   [0]$(lfo 0.30 0.045 0)[a0]; [1]$(lfo 0.15 0.061 1.1)[a1]; \
   [2]$(lfo 0.11 0.037 2.0)[a2]; [3]$(lfo 0.13 0.052 0.6)[a3]; \
   [4]$(lfo 0.10 0.029 3.1)[a4]; [5]$(lfo 0.085 0.043 1.7)[a5]; \
   [6]$(lfo 0.065 0.033 2.6)[a6]; [7]$(lfo 0.045 0.025 0.3)[a7]; \
   [a0][a1][a2][a3][a4][a5][a6][a7]amix=inputs=8:normalize=0[mix]; \
   [mix]lowpass=f=2200,aecho=0.8:0.88:900|1700:0.30|0.18,\
   afade=t=in:st=0:d=3,afade=t=out:st=$FO:d=4,loudnorm=I=-23:TP=-2:LRA=7[out]" \
   -map "[out]" -ar 48000 -ac 2 -c:a pcm_s16le music.wav
fi

bash build_vo.sh

ffmpeg -hide_banner -loglevel error -y -i music.wav -i vo.wav -filter_complex "\
 [1:a]apad=whole_dur=$D,asplit=2[vo_a][vo_b]; [0:a]volume=2.0[mus]; \
 [mus][vo_b]sidechaincompress=threshold=0.035:ratio=12:attack=15:release=550:makeup=1[duck]; \
 [duck][vo_a]amix=inputs=2:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=9[out]" \
 -map "[out]" -ar 48000 -ac 2 -c:a pcm_s16le audio.wav
echo "wrote audio.wav"
