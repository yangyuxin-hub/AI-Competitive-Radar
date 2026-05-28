import { chromium } from "playwright";
import { mkdirSync } from "fs";

const OUT = "shots";
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("console", (m) => console.log("  [console]", m.type(), m.text()));

async function shot(name) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  console.log("shot:", name);
}

console.log("→ landing");
await page.goto("http://localhost:3000", { waitUntil: "networkidle" });
await page.waitForTimeout(800);
await shot("1-landing");

console.log("→ fill input");
await page.locator("textarea").fill("分析 Cursor 和 Windsurf 在代码补全体验上的差距");
await page.getByRole("button", { name: /确认分析配置/ }).click();

console.log("→ clarify (waiting for questions)");
await page.getByText("Agent 已根据你的描述预填").waitFor({ timeout: 60000 });
await page.waitForTimeout(500);
await shot("2-clarify");

console.log("→ run");
await page.getByRole("button", { name: "开始分析" }).click();

console.log("→ theater (capturing mid-run)");
await page.getByText("多 Agent 协作中").waitFor({ timeout: 15000 });
await page.waitForTimeout(4000);
await shot("3-theater");

console.log("→ waiting for report (real Doubao, up to 6min)…");
await page.getByRole("button", { name: "开始新的分析" }).waitFor({ timeout: 360000 });
await page.waitForTimeout(1000);
await shot("4-report");

console.log("→ click a chip to open evidence panel");
const chip = page.locator("article button").first();
if (await chip.count()) {
  await chip.click();
  await page.waitForTimeout(800);
  await shot("5-evidence");
}

await browser.close();
console.log("DONE");
