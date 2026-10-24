"""报告卡片的数据准备（Jinja2 上下文）。

只做「数据 → 模板变量」的转换，不负责渲染：真正把 HTML 变成图片的是
AstrBot 的 `html_render`，放在 main.py 里调用，方便在没装 Playwright 时退回文本。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import analyzer, lexicon

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# 等级 → 环的颜色
RING_COLORS = {
    "stable": "#3fae7a",
    "mild": "#d9a220",
    "watch": "#e08a3c",
    "high": "#d8624b",
    "crisis": "#c0392b",
}

TREND_TEXT = {-1: "↓ 缓解", 0: "→ 持平", 1: "↑ 加重"}


def load_template(name: str) -> str | None:
    path = TEMPLATES_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _dims(profile, limit: int = 5) -> list[dict]:
    items = sorted(profile.dims.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [
        {
            "label": lexicon.DIMENSIONS[dim][0],
            "value": int(round(value)),
            "width": max(3, min(100, int(round(value)))),
        }
        for dim, value in items
        if value >= 5
    ]


def daily_context(stats, mood: dict, *, group_name: str, private: bool, now: int | None = None) -> dict:
    now = int(now or datetime.now().timestamp())
    mood_index = int(mood.get("mood", 1))
    dims = sorted(stats.dims.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "date": datetime.fromtimestamp(now).strftime("%Y-%m-%d"),
        "group": group_name or stats.group_id,
        "private": private,
        "messages": stats.messages,
        "users": stats.users,
        "peak": f"{stats.peak_hour:02d}:00",
        "mood_mark": ["🟢", "🟢", "🟡", "🟠", "🟠", "🔴"][max(0, min(5, mood_index))],
        "mood_label": mood.get("label") or "平稳",
        "keywords": list(mood.get("keywords") or [])[:5],
        "summary": mood.get("summary") or "",
        "care_tip": mood.get("care_tip") or "",
        "dims": [
            {
                "label": lexicon.DIMENSIONS[dim][0],
                "value": int(round(value)),
                "width": max(3, min(100, int(round(value)))),
            }
            for dim, value in dims
            # 「高危表达」属于个人风险，单独列在「建议留意」里，
            # 不放进群氛围构成，避免整张卡片被一个词条带偏
            if value >= 5 and dim != "crisis"
        ],
        "risks": (
            [
                {
                    "nick": r.nickname,
                    "uid": r.user_id,
                    "score": int(round(r.score)),
                    "label": r.label,
                    "dims": "、".join(r.dims),
                }
                for r in stats.risks[:5]
            ]
            if private
            else []
        ),
    }


def report_context(
    profile,
    score: float,
    llm: dict | None,
    *,
    group_name: str,
    window_days: int,
    now: int | None = None,
) -> dict:
    now = int(now or datetime.now().timestamp())
    _, key, label, mark = analyzer.level_of(score)
    nick = profile.nickname or profile.user_id
    evidence = list(llm.get("evidence") or []) if llm else list(profile.evidence)
    warning = ""
    if profile.crisis_recent:
        warning = "最近 72 小时出现危机类表达。请尽快私聊确认安全，不要公开讨论，也不要替对方下结论。"
    elif profile.escalated:
        warning = "近 72 小时多次出现高强度负性表达，建议尽快人工确认。"
    return {
        "nick": nick,
        "uid": profile.user_id,
        "initial": (nick[:1] or "?").upper(),
        "group": group_name or profile.group_id,
        "range": f"近 {window_days} 天",
        "messages": profile.n_messages,
        "active_days": profile.active_days,
        "n_risk": profile.n_risk,
        "score": int(round(score)),
        "ring_color": RING_COLORS.get(key, "#7c869c"),
        "ring_deg": max(6, min(360, int(round(score * 3.6)))),
        "level_mark": mark,
        "level_label": label,
        "trend_text": TREND_TEXT.get(profile.trend, TREND_TEXT[0]),
        "confidence_text": (
            "高" if profile.confidence >= 0.7 else ("中" if profile.confidence >= 0.35 else "低")
        ),
        "dims": _dims(profile),
        "mood_strip": profile.mood_strip,
        "mood_bars": [
            {
                "h": 8 if value is None else int(8 + min(1.0, value / 5.5) * 34),
                "empty": value is None,
            }
            for value in profile.mood_series
        ],
        "summary": (llm.get("summary") if llm else "") or "",
        "advice": (llm.get("advice") if llm else "") or "",
        "evidence": evidence[:3],
        "warning": warning,
    }
