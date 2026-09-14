"""Narration for the demo video, one wav per line so the animation can be cut to it."""
import json, pathlib, torch, torchaudio as ta
from chatterbox.tts import ChatterboxTTS

OUT = pathlib.Path("vo"); OUT.mkdir(exist_ok=True)
lines = json.load(open("script.json"))["lines"]

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"loading chatterbox on {device}", flush=True)
model = ChatterboxTTS.from_pretrained(device=device)

timings, t = [], 0.0
for i, line in enumerate(lines, 1):
    # Measured pacing: a demo narration read, not an advertisement.
    wav = model.generate(line["text"], exaggeration=0.45, cfg_weight=0.4)
    path = OUT / f"{line['id']}.wav"
    ta.save(str(path), wav.cpu(), model.sr)
    dur = wav.shape[-1] / model.sr
    timings.append({**line, "file": str(path), "start": round(t, 3), "dur": round(dur, 3)})
    t += dur + 0.45  # beat between lines
    print(f"[{i}/{len(lines)}] {line['id']} {dur:5.2f}s  {line['text'][:58]}...", flush=True)

json.dump({"sr": model.sr, "total": round(t, 3), "lines": timings},
          open("timings.json", "w"), indent=2)
print(f"\nTOTAL NARRATION {t:.1f}s ({t/60:.1f} min)")
