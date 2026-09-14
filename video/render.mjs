/* Renders scene.html frame by frame at a fixed timestep and pipes PNGs straight
   into ffmpeg, so the encode is exactly smooth and nothing touches the disk. */
import puppeteer from "puppeteer-core";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

const FPS = Number(process.env.FPS || 30);
const W = 1920, H = 1080;
const OUT = process.env.OUT || "anim.mp4";
const CHROME = process.env.CHROME || "/snap/bin/chromium";
const here = path.resolve(".");

const timings = JSON.parse(readFileSync("timings.json", "utf8"));
const DURATION = Number(process.env.DURATION || (timings.total + 1.2));
const total = Math.ceil(DURATION * FPS);
console.log(`rendering ${DURATION.toFixed(1)}s @ ${FPS}fps = ${total} frames -> ${OUT}`);

const ff = spawn("ffmpeg", [
  "-y", "-f", "image2pipe", "-framerate", String(FPS), "-i", "-",
  "-c:v", "libx264", "-preset", "medium", "-crf", "17",
  "-pix_fmt", "yuv420p", "-movflags", "+faststart", OUT,
], { stdio: ["pipe", "ignore", "inherit"] });

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  args: [
    "--no-sandbox", "--disable-dev-shm-usage",
    "--enable-unsafe-swiftshader",          // deterministic software WebGL
    "--hide-scrollbars", "--force-device-scale-factor=1",
    `--window-size=${W},${H}`,
  ],
});
const page = await browser.newPage();
await page.setViewport({ width: W, height: H, deviceScaleFactor: 1 });
page.on("pageerror", (e) => console.error("PAGE ERROR:", e.message));
page.on("console", (m) => { if (m.type() === "error") console.error("console:", m.text()); });

await page.evaluateOnNewDocument((t) => { window.__TIMINGS = t; }, timings);
await page.goto(`file://${here}/scene.html`, { waitUntil: "networkidle0" });
await page.waitForFunction("window.__ready !== undefined", { timeout: 30000 });
await page.evaluate("window.__ready");
console.log("scene ready");

const t0 = Date.now();
for (let i = 0; i < total; i++) {
  await page.evaluate((t) => window.__renderAt(t), i / FPS);
  const buf = await page.screenshot({ type: "png", optimizeForSpeed: true });
  if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once("drain", r));
  if (i % (FPS * 5) === 0) {
    const el = (Date.now() - t0) / 1000;
    const pct = ((i / total) * 100).toFixed(0);
    const eta = i ? ((el / i) * (total - i)).toFixed(0) : "?";
    process.stdout.write(`\r  ${pct}%  frame ${i}/${total}  ${el.toFixed(0)}s elapsed, ~${eta}s left   `);
  }
}
ff.stdin.end();
await browser.close();
await new Promise((r) => ff.on("close", r));
console.log(`\ndone in ${((Date.now() - t0) / 1000).toFixed(0)}s -> ${OUT}`);
