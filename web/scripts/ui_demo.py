"""驱动运行页,截图新的「Claude 式可折叠活动时间线」效果。
服务器需已在运行: 前端 :3000(NEXT_PUBLIC_API_BASE 指向后端 :8123)。"""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path("/tmp/ui")
OUT.mkdir(parents=True, exist_ok=True)
shots = []


def shot(page, name):
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p), full_page=True)
    shots.append(str(p))
    print(f"[shot] {p}")


with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 1700})
    page.goto("http://127.0.0.1:3210", wait_until="domcontentloaded")
    page.wait_for_timeout(3500)  # 等 React hydration,否则 onClick 未挂载
    shot(page, "01-input")

    # 选第一个场景卡 → 等「下一步」变可点(hydration 完成的可靠信号)
    nxt = page.get_by_role("button", name="下一步：确认分析配置")
    for _ in range(12):
        page.locator("button").nth(0).click()
        page.wait_for_timeout(600)
        if nxt.is_enabled():
            break
    nxt.click()

    # clarify 阶段:等「开始分析」出现
    page.get_by_role("button", name="开始分析", exact=True).wait_for(timeout=60000)
    page.wait_for_timeout(500)
    shot(page, "02-clarify")

    # 选快速档,开始
    try:
        page.locator("button", has_text="快速").first.click()
        page.wait_for_timeout(200)
    except Exception as e:
        print("profile click skip:", e)
    page.get_by_role("button", name="开始分析", exact=True).click()

    # 等运行页出现
    page.get_by_text("Agent 活动时间线").wait_for(timeout=30000)
    page.wait_for_timeout(6000)
    shot(page, "03-running-early")  # 采集进行中,时间线展开

    # 等到 analyzer 分组出现(采集应已折叠)
    analyzer_seen = False
    for _ in range(60):  # 最多 ~120s
        page.wait_for_timeout(2000)
        if page.get_by_text("分析结论").count() > 0:
            analyzer_seen = True
            break
    page.wait_for_timeout(1500)
    shot(page, "04-analyzer-running")  # 采集折叠成一行 + 分析展开(含实时结论)

    # 演示「点击已折叠的完成步骤 → 展开」
    try:
        page.locator("button", has_text="收集证据").first.click()
        page.wait_for_timeout(1000)
        shot(page, "05-expand-collapsed-collector")
    except Exception as e:
        print("expand collector skip:", e)

    print("ANALYZER_SEEN", analyzer_seen)
    print("SHOTS", "|".join(shots))
    browser.close()
