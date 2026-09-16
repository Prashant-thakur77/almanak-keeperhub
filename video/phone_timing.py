"""Re-time the narration around the phone footage and regenerate build_vo.sh.

gen_missing.py lays lines end to end. The phone scene instead pins each line to the moment
its step appears in the cut phone clip (offsets from phone_cut.py), and everything after
the scene shifts by the clip's length. Writes timings.json, phone.json and build_vo.sh.
"""
import json, subprocess, sys

PHONE_CLIP = sys.argv[1] if len(sys.argv) > 1 else "phone.mp4"
OFFSETS = json.load(open("phone_offsets.json"))  # line id -> seconds into the phone clip
BEAT = 0.45

t = json.load(open("timings.json"))
lines = t["lines"]
phone_len = float(subprocess.check_output(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", PHONE_CLIP]).decode().strip())

first_phone = next(i for i, l in enumerate(lines) if l["id"] in OFFSETS)
last_phone = max(i for i, l in enumerate(lines) if l["id"] in OFFSETS)
prev = lines[first_phone - 1]
t0 = round(prev["start"] + prev["dur"] + BEAT + 0.6, 3)   # the phone clip starts here
for l in lines[first_phone:last_phone + 1]:
    l["start"] = round(t0 + OFFSETS[l["id"]], 3)
cursor = t0 + phone_len + 0.8
for l in lines[last_phone + 1:]:
    l["start"] = round(cursor, 3)
    cursor += l["dur"] + BEAT
t["total"] = round(cursor - BEAT, 3)
t["phone"] = {"start": t0, "length": round(phone_len, 3), "clip": PHONE_CLIP}
json.dump(t, open("timings.json", "w"), indent=2)
json.dump(t["phone"], open("phone.json", "w"), indent=2)

inputs = " ".join(f"-i {l['file']}" for l in lines)
chains = "; ".join(
    f"[{i}]aresample=48000,aformat=channel_layouts=stereo,adelay={int(l['start']*1000)}|{int(l['start']*1000)}[v{i}]"
    for i, l in enumerate(lines))
maps = "".join(f"[v{i}]" for i in range(len(lines)))
open("build_vo.sh", "w").write(
    "ffmpeg -hide_banner -loglevel error -y \\\n " + inputs + " \\\n"
    f' -filter_complex "{chains}; {maps}amix=inputs={len(lines)}:normalize=0,volume=2.2[vo]"'
    f' -map "[vo]" -t {t["total"] + 1.4:.2f} -ar 48000 -ac 2 -c:a pcm_s16le vo.wav\n')
for l in lines:
    print(f"{l['id']:4s} {l['start']:7.2f} +{l['dur']:5.2f}  {l['text'][:60]}")
print(f"phone clip {phone_len:.2f}s at {t0:.2f}; total narration {t['total']:.2f}s")
