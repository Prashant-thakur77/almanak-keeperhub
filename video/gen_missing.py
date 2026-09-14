"""Generate only the lines that have no wav yet, then rebuild timings.json."""
import json, pathlib, torch, torchaudio as ta
from chatterbox.tts import ChatterboxTTS

OUT = pathlib.Path("vo"); OUT.mkdir(exist_ok=True)
lines = json.load(open("script.json"))["lines"]
missing = [l for l in lines if not (OUT / f"{l['id']}.wav").exists()]
print("missing:", [l["id"] for l in missing], flush=True)

sr = 24000
if missing:
    model = ChatterboxTTS.from_pretrained(device="cuda" if torch.cuda.is_available() else "cpu")
    sr = model.sr
    for l in missing:
        wav = model.generate(l["text"], exaggeration=0.45, cfg_weight=0.4)
        ta.save(str(OUT / f"{l['id']}.wav"), wav.cpu(), sr)
        print("ok", l["id"], round(wav.shape[-1]/sr, 2), "s", flush=True)
else:
    info = ta.info(str(OUT / f"{lines[0]['id']}.wav")); sr = info.sample_rate

timings, t = [], 0.0
for l in lines:
    p = OUT / f"{l['id']}.wav"
    info = ta.info(str(p))
    dur = info.num_frames / info.sample_rate
    timings.append({**l, "file": str(p), "start": round(t,3), "dur": round(dur,3)})
    t += dur + 0.45
json.dump({"sr": sr, "total": round(t,3), "lines": timings}, open("timings.json","w"), indent=2)
print(f"TOTAL {t:.1f}s")
