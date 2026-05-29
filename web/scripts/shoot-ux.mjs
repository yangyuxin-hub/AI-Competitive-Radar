import { chromium } from "playwright";
import { mkdirSync } from "fs";
mkdirSync("shots", { recursive: true });
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1280, height: 1100 } });
p.on("pageerror", (e) => console.log("[pageerror]", e.message));
const shot = async (n) => { await p.screenshot({ path: `shots/ux-${n}.png`, fullPage: true }); console.log("shot", n); };

await p.goto("http://localhost:3000", { waitUntil: "networkidle" });
await p.waitForTimeout(600); await shot("1-landing");

// 点第一个场景卡(domain_hint → intake 快)
await p.locator("button").filter({ hasText: "代码补全体验" }).first().click();
await p.getByRole("button", { name: /确认分析配置/ }).click();
await p.getByText(/预填的分析配置|Agent 的判断/).first().waitFor({ timeout: 60000 });
await p.waitForTimeout(500); await shot("2-clarify");

await p.getByRole("button", { name: "开始分析" }).click();
await p.getByText("多 Agent 协作中").waitFor({ timeout: 20000 });
await p.waitForTimeout(12000); await shot("3-theater-12s");
await p.waitForTimeout(28000); await shot("4-theater-40s");

// 报告阶段:用已有档案
await p.goto("http://localhost:3000/report/CR-20260528-001-160414", { waitUntil: "networkidle" });
await p.getByText("功能对比矩阵").first().waitFor({ timeout: 20000 });
await p.waitForTimeout(600); await shot("5-report");
await b.close(); console.log("DONE");
