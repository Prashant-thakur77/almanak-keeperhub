import puppeteer from "puppeteer-core";
import { readFileSync } from "node:fs";
import path from "node:path";
const timings = JSON.parse(readFileSync("timings.json","utf8"));
const times = process.argv.slice(2).map(Number);
const browser = await puppeteer.launch({ executablePath:"/snap/bin/chromium", headless:"new",
  args:["--no-sandbox","--disable-dev-shm-usage","--enable-unsafe-swiftshader","--hide-scrollbars","--force-device-scale-factor=1","--window-size=1920,1080"]});
const page = await browser.newPage();
await page.setViewport({width:1920,height:1080,deviceScaleFactor:1});
page.on("pageerror",e=>console.error("PAGE ERROR:",e.message));
await page.evaluateOnNewDocument((t)=>{window.__TIMINGS=t;}, timings);
await page.goto(`file://${path.resolve(".")}/scene.html`,{waitUntil:"networkidle0"});
await page.evaluate("window.__ready");
for (const t of times){
  await page.evaluate((x)=>window.__renderAt(x), t);
  await page.screenshot({path:`grab_${String(t).replace(".","_")}.png`});
  console.log("grabbed", t);
}
await browser.close();
