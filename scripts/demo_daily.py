"""渲染一份「群氛围日报」：公开版（群里）与私密版（管理员）。

运行：python scripts/demo_daily.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import analyzer, daily  # noqa: E402

TODAY = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
START = int(TODAY.timestamp())
END = START + 86400  # 模拟当天已结束

CHAT = {
    "10001": (
        "小满",
        [
            (9, "今天加班到十点，好累"),
            (9, "感觉最近什么事都提不起劲"),
            (21, "昨晚又失眠了，三点多才睡着"),
            (21, "我是不是挺没用的"),
            (22, "感觉没有未来"),
            (22, "真的撑不下去了"),
        ],
    ),
    "10002": ("阿澈", [(10, "哈哈哈哈这个表情包绝了"), (20, "今晚开黑吗"), (20, "我请客奶茶")]),
    "10003": ("木木", [(12, "论文改到崩溃"), (19, "不想说话"), (19, "谢谢你们陪我")]),
    "10004": ("小K", [(8, "早八人已死"), (18, "下班了！"), (22, "晚安")]),
}

users = {}
for uid, (nick, lines) in CHAT.items():
    msgs = []
    for hour, text in lines:
        ts = int(TODAY.replace(hour=hour, minute=5).timestamp())
        signal = analyzer.analyze_text(text, ts)
        msgs.append({"t": ts, "x": text, "i": signal.intensity, "d": signal.dims, "c": signal.crisis})
    users[uid] = {"nick": nick, "messages": msgs, "opt_out": False}

stats = daily.collect(users, "8888", start_ts=START, end_ts=END + 1)

mood = daily.parse_daily_json(
    """{"label": "略有疲惫", "mood": 2, "keywords": ["加班", "论文", "开黑"],
        "summary": "白天聊工作和论文的多，普遍在喊累；晚上话题转到游戏和宵夜，气氛松快了不少。",
        "care_tip": "期末周大家都辛苦，早点睡比多刷一小时有用。"}"""
) or daily.fallback_mood(stats)

print("=" * 46)
print("群里看到的版本")
print("=" * 46)
print(daily.render_public(stats, mood, group_name="摸鱼小分队"))
print()
print("=" * 46)
print("管理员私聊收到的版本")
print("=" * 46)
print(daily.render_private(stats, mood, group_name="摸鱼小分队"))
print()
print("[统计] 消息", stats.messages, "人", stats.users, "平均强度", stats.avg_intensity,
      "高峰", f"{stats.peak_hour:02d}:00")
