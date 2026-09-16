"""Cut the phone recording: keep every reply at normal speed, fast-forward the waits and scrolls.

    python phone_cut.py ~/Downloads/ALM.mp4 phone1.mp4 "6-8 8-13 13-19x5 ..."
Each token is start-end[xSpeed]; speed 1 when omitted. Prints the resulting duration and the
output-time at which each segment starts, for narration timing.
"""
import subprocess, sys

src, out, plan = sys.argv[1], sys.argv[2], sys.argv[3].split()
segs = []
for tok in plan:
    rng, _, k = tok.partition("x")
    a, b = map(float, rng.split("-"))
    segs.append((a, b, float(k) if k else 1.0))
parts, t = [], 0.0
for i, (a, b, k) in enumerate(segs):
    parts.append(f"[0:v]trim=start={a}:end={b},setpts=(PTS-STARTPTS)/{k}[v{i}]")
    print(f"seg {i:2d} src {a:6.1f}-{b:6.1f} x{k:<3g} -> out {t:6.2f}")
    t += (b - a) / k
print(f"output length {t:.2f}s")
fc = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[v]"
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-filter_complex", fc, "-map", "[v]",
                "-r", "30", "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p", out], check=True)
