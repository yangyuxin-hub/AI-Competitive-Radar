import { chromium } from "playwright";
import { mkdirSync } from "fs";

mkdirSync("shots", { recursive: true });
const id = process.argv[2];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
page.on("pageerror", (e) => console.log("  [pageerror]", e.message));

await page.goto(`http://localhost:3000/report/${id}`, { waitUntil: "networkidle" });
await page.getByText("功能对比矩阵").waitFor({ timeout: 20000 });
await page.waitForTimeout(1200); // 等 diff 计算

const btn = page.getByRole("button", { name: /对比上一份/ });
if (await btn.count()) {
  await btn.click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: "shots/m3-diff.png" });
  console.log("shot: m3-diff");
} else {
  console.log("NO diff button (no previous report?)");
}
await browser.close();
console.log("DONE");
