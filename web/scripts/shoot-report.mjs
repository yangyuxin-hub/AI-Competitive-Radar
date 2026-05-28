import { chromium } from "playwright";
import { mkdirSync } from "fs";

const OUT = "shots";
mkdirSync(OUT, { recursive: true });
const id = process.argv[2];
if (!id) throw new Error("usage: node shoot-report.mjs <report_id>");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.on("console", (m) => m.type() === "error" && console.log("  [err]", m.text()));
page.on("pageerror", (e) => console.log("  [pageerror]", e.message));

console.log("→ open report", id);
await page.goto(`http://localhost:3000/report/${id}`, { waitUntil: "networkidle" });
await page.getByText("功能对比矩阵").waitFor({ timeout: 20000 });
await page.waitForTimeout(800);

// viewport shot (top, readable) + full page
await page.screenshot({ path: `${OUT}/m2-report-top.png` });
await page.screenshot({ path: `${OUT}/m2-report-full.png`, fullPage: true });
console.log("shot: report top + full");

// click first chip → evidence panel
const chip = page.locator("table button").first();
if (await chip.count()) {
  await chip.click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/m2-evidence.png` });
  console.log("shot: evidence panel");
}

await browser.close();
console.log("DONE");
