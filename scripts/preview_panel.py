"""生成 WebUI 面板的静态预览（注入假 bridge + 样例数据）。

运行：python scripts/preview_panel.py
然后用浏览器打开 build/preview/panel/index.html。
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "pages" / "dashboard"
OUT = ROOT / "build" / "preview" / "panel"


def sample_payload() -> dict:
    now = int(time.time())
    groups = [
        {
            "id": "8888",
            "name": "摸鱼小分队",
            "activated": True,
            "enabled": True,
            "today_messages": 186,
            "member_total": 12,
            "risk_total": 2,
            "last_daily": {
                "date": time.strftime("%Y-%m-%d"),
                "messages": 186,
                "users": 12,
                "mood": {"label": "略有疲惫", "mood": 2},
                "private": "🌙 群氛围日报 · 今天\n消息 186 条 · 活跃 12 人\n整体气氛：🟡 略有疲惫",
                "public": "🌙 群氛围日报 · 今天",
            },
            "members": [
                {"uid": "10001", "nick": "小满", "score": 80.0, "level": "high", "level_label": "高度关注", "messages": 42, "today": 9, "last_ts": now - 300},
                {"uid": "10003", "nick": "木木", "score": 61.0, "level": "watch", "level_label": "值得关注", "messages": 28, "today": 6, "last_ts": now - 900},
                {"uid": "10002", "nick": "阿澈", "score": 34.0, "level": "mild", "level_label": "轻度波动", "messages": 51, "today": 14, "last_ts": now - 120},
                {"uid": "10004", "nick": "小K", "score": 8.0, "level": "stable", "level_label": "相对平稳", "messages": 33, "today": 11, "last_ts": now - 60},
            ],
        },
        {
            "id": "9999",
            "name": "考研自习室",
            "activated": True,
            "enabled": True,
            "today_messages": 74,
            "member_total": 5,
            "risk_total": 0,
            "last_daily": None,
            "members": [
                {"uid": "20001", "nick": "阿岚", "score": 22.0, "level": "mild", "level_label": "轻度波动", "messages": 18, "today": 4, "last_ts": now - 3600},
            ],
        },
        {
            "id": "7777",
            "name": "老同学群",
            "activated": False,
            "enabled": False,
            "today_messages": 0,
            "member_total": 0,
            "risk_total": 0,
            "last_daily": None,
            "members": [],
        },
    ]
    return {
        "generated_at": now,
        "window_days": 14,
        "alert_bound": True,
        "totals": {
            "groups": 3,
            "members": 17,
            "today_messages": 260,
            "risk_total": 2,
            "daily_reports": 6,
        },
        "groups": groups,
        "alerts": [],
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("style.css", "app.js"):
        shutil.copyfile(VIEW / name, OUT / name)

    html = (VIEW / "index.html").read_text(encoding="utf-8")
    payload = json.dumps(sample_payload(), ensure_ascii=False)
    mock = f"""
  <script>
    const SAMPLE = {payload};
    window.AstrBotPluginView = window.AstrBotPluginPage = {{
      ready: async () => ({{ displayName: "心理关怀", isDark: false }}),
      getContext: () => ({{ isDark: false }}),
      getLocale: () => "zh-CN",
      t: (key, fallback) => fallback,
      onContext: () => () => {{}},
      apiGet: async (endpoint) =>
        endpoint === "report"
          ? {{ text: "🧭 心理关怀报告（预览）\\n综合关注度：🔴 高度关注 · 80/100" }}
          : SAMPLE,
      apiPost: async () => ({{ ok: true }}),
    }};
  </script>
  <script type="module" src="./app.js"></script>"""
    html = html.replace('<script type="module" src="./app.js"></script>', mock)
    (OUT / "index.html").write_text(html, encoding="utf-8")
    print("written to", OUT)


if __name__ == "__main__":
    sys.exit(main())
