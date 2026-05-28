import { chromium } from "playwright";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1280, height: 1000 } });
p.on("pageerror", (e) => console.log("[pageerror]", e.message));
await p.goto("http://localhost:3000", { waitUntil: "networkidle" });
await p.locator("textarea").fill("分析 Cursor 和 Windsurf 的代码补全体验");
await p.getByRole("button", { name: /确认分析配置/ }).click();
await p.getByText("Agent 已根据你的描述预填").waitFor({ timeout: 60000 });
await p.waitForTimeout(400);
// 在「竞品」多选里手填一个自定义项
const compInput = p.locator("input[placeholder='其他…']").nth(1);
await compInput.fill("Amazon Q Developer");
await compInput.press("Enter");
await p.waitForTimeout(400);
await p.screenshot({ path: "shots/m5-custom-input.png", fullPage: true });
console.log("DONE");
await b.close();
