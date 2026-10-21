"""用 Playwright 驱动本机 Chrome 给演示页截图（给 README 用）。

    python tools/capture_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs"
URL = "http://127.0.0.1:8501/"


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else "每天的浏览购买转化率"
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("text=淘宝用户行为数仓 · 指标语义层问数", timeout=60000)
        page.wait_for_timeout(2000)
        page.screenshot(path=str(OUT / "demo_home.png"), full_page=False)

        # 问一句，等结果真的出来（首次会先建 DuckDB 替身，约 30 秒）
        box = page.get_by_label("问一句")
        box.fill(question)
        page.get_by_role("button", name="提问").click()
        page.get_by_text("已编译", exact=False).wait_for(timeout=180000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "demo_answer.png"), full_page=False)

        # DSL 直编页签
        page.get_by_role("tab", name="DSL 直编").click()
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="编译并执行").click()
        page.get_by_text("SELECT", exact=False).first.wait_for(timeout=120000)
        page.wait_for_timeout(3000)
        page.screenshot(path=str(OUT / "demo_dsl.png"), full_page=False)

        browser.close()
    for name in ("demo_home.png", "demo_answer.png", "demo_dsl.png"):
        path = OUT / name
        print(f"{name}: {path.stat().st_size} bytes" if path.exists() else f"{name}: MISSING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
