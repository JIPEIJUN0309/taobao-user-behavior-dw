"""演示页冒烟测试：用 Streamlit 官方 AppTest 在进程内跑一遍脚本。

它不点按钮（点「编译并执行」会把 DuckDB 数仓替身建出来，约 30 秒），
只保证：页面能起来、三个页签在、没有异常、侧边栏引擎可切换。
"""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def test_app_boots_without_exception() -> None:
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()

    assert not at.exception, [str(e.value) for e in at.exception]
    assert len(at.tabs) == 3
    assert at.title[0].value.startswith("淘宝用户行为数仓")
    assert at.sidebar.radio[0].options == ["duckdb", "pg"]
