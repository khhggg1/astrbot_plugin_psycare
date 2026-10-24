"""生成一份示例报告，用来检查文案和排版。

运行：python scripts/demo_report.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import analyzer  # noqa: E402

NOW = int(time.time())

SAMPLES = [
    (9, "今天加班到十点，回家路上突然觉得好累"),
    (9, "感觉最近什么事都提不起劲"),
    (8, "昨晚又失眠了，三点多才睡着"),
    (7, "群里好热闹啊，不过我不太想说话"),
    (7, "我是不是挺没用的，什么都做不好"),
    (6, "今天吃了顿火锅，开心了一点"),
    (5, "谢谢你们陪我聊天"),
    (4, "最近压力好大，喘不过气"),
    (4, "我妈又催我找对象，烦死了"),
    (3, "睡不着，脑子里全是工作的事"),
    (3, "有时候觉得自己挺多余的"),
    (2, "什么都不想做，只想躺着"),
    (1, "感觉没有未来"),
    (1, "你们别管我了"),
]

LLM = {
    "level": 3,
    "crisis": False,
    "dimensions": {"mood": 3, "anxiety": 2, "worth": 3, "sleep": 3, "withdraw": 2, "stress": 2, "crisis": 0},
    "summary": "近两周持续表达疲惫、失眠与自我否定，社交意愿下降，尚未见明确危险计划。",
    "advice": "先私下关心作息和最近的烦心事，少给建议多倾听；若持续两周以上，建议陪同寻求学校或医院心理科帮助。",
    "evidence": ["感觉最近什么事都提不起劲", "我是不是挺没用的"],
}

messages = []
for days_ago, text in SAMPLES:
    for _ in range(2):
        messages.append(
            {
                "t": NOW - days_ago * 86400,
                "x": text,
                "i": analyzer.analyze_text(text, NOW - days_ago * 86400).intensity,
                "d": analyzer.analyze_text(text, NOW - days_ago * 86400).dims,
                "c": analyzer.analyze_text(text, NOW - days_ago * 86400).crisis,
            }
        )

profile = analyzer.build_profile("123456", "10001", "小满", messages, window_days=14, now=NOW)
score = analyzer.combine_score(profile, LLM)

print(analyzer.render_report(profile, score, LLM, group_name="摸鱼小分队", window_days=14, now=NOW))
print("\n" + "=" * 46 + "\n")
rows = [(profile, score)]
print(analyzer.render_overview("摸鱼小分队", rows, window_days=14, total_messages=len(messages), now=NOW))
print("\n" + "=" * 46 + "\n")
print(analyzer.render_alert(profile, score, LLM, group_name="摸鱼小分队", reason="综合关注度超过阈值"))

print("\n\n[规则画像] rule=%.1f severity=%.1f persistence=%.2f recency=%.1f conf=%.2f"
      % (profile.rule_score, profile.severity, profile.persistence, profile.recency, profile.confidence))
print("[维度]", {k: v for k, v in sorted(profile.dims.items(), key=lambda kv: -kv[1])})
