# Demo video

Renders `demo-video.mp4`: a 1080p60 explainer built around the real Base Sepolia
run, narrated with Chatterbox and scored with a synthesised ambient bed.

Everything on screen is real: the console screenshot is `docs/img/console.png`
from the recorded run, and the transaction hashes, execution ids and benchmark
figures come from `docs/receipts.json`, `docs/benchmark.md` and the strategy's
own `keeperhub-receipts.json`.

## Rebuild

```bash
# 1. narration (needs the pyenv 3.10 environment that has chatterbox + torch)
/home/prashant/.pyenv/versions/3.10.13/bin/python gen_missing.py

# 2. picture: deterministic frame-by-frame render straight into ffmpeg
FPS=60 OUT=anim.mp4 node render.mjs

# 3. music bed + narration mix -> audio.wav   (see build-audio.sh)
./build-audio.sh

# 4. mux
./assemble.sh
```

Editing the script: change `script.json`, delete the affected `vo/<id>.wav`,
then rerun step 1. `gen_missing.py` only regenerates what is missing and
rewrites `timings.json`, and the animation is timed off that file, so the
picture re-cuts itself to the new narration automatically.

## Files

| file | what it is |
|---|---|
| `script.json` | the narration, one line per beat |
| `vo/*.wav` | Chatterbox output, one file per line |
| `timings.json` | generated: start and duration of every line, drives the animation |
| `scene.html` | the DOM layer (all typography) |
| `timeline.js` | the three.js scene and the whole animation as a function of time |
| `render.mjs` | headless Chromium, one screenshot per frame, piped to ffmpeg |
| `console.png` | copy of `docs/img/console.png`, the real console |

## Swapping in a live screen recording

The demo section runs from the start of line `s4a` to the end of `s4d`
(see `timings.json`). To use real screen capture there instead of the
screenshot pan, record that many seconds and overlay it:

```bash
ffmpeg -i demo-video.mp4 -i screencap.mp4 -filter_complex \
  "[1:v]scale=1920:1080,setpts=PTS-STARTPTS+<s4a_start>/TB[ov]; \
   [0:v][ov]overlay=enable='between(t,<s4a_start>,<s4d_end>)'" \
  -c:a copy demo-video-live.mp4
```
