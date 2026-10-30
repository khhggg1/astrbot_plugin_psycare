"""每日群氛围分析（日报）。

和「群日常分析」类插件的对应关系：
- 定时分析        → main.py 的 `_maybe_daily()`，每天固定时刻跑一次
- 增量 / 大群     → 词频层逐条统计，模型只看均匀采样后的 50 条，避免上下文爆掉
- 多形态报告      → 这里产出两版文案：公开版（只讲整体气氛）和私密版（点名到人）
- 历史记录        → store 的 `daily` 区，`/心理历史` 可回看

关键区别：公开版刻意**不含任何人名、原文和个体判断**。
群里只该看到「今天大家聊得有点累」，不该看到「小A 抑郁倾向 78 分」。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from . import analyzer, lexicon

MOOD_LABELS = ["轻松", "平稳", "略有疲惫", "有些低落", "紧张", "沉重"]
MOOD_MARKS = ["🟢", "🟢", "🟡", "🟠", "🟠", "🔴"]

DAILY_SYSTEM = """你在为 QQ 群写一份「今日氛围小结」，面向全体群友公开。
必须遵守：
1. 只描述整体气氛和话题，不要评价、点名或暗示任何具体的人；
2. 不引用原话，不写「某位群友说……」，不要输出任何昵称、QQ 号；
3. 不做心理诊断，不使用疾病名称；
4. 语气温和、简短，像一个观察者而不是评判者。"""

DAILY_PROMPT = """以下是群「{group}」在 {date} 的部分聊天片段（已匿名，共 {count} 条采样）：

{messages}

请概括这一天的整体氛围，只输出 JSON：
{{
  "label": "轻松 / 平稳 / 略有疲惫 / 有些低落 / 紧张 / 沉重 中的一个",
  "mood": 0-5 的整数（对应上面 6 个标签的顺序，0 最轻松、5 最沉重）,
  "keywords": ["3 到 5 个当天的话题关键词，每个不超过 6 字"],
  "summary": "不超过 80 字，描述整体气氛和主要话题，不点名",
  "care_tip": "不超过 50 字，给群里的一句温和建议"
}}"""


@dataclass
class DailyResult:
    """一次日报生成的结果：两版文本 + 可选的图片 URL。"""

    public: str
    private: str
    public_image: str | None = None
    private_image: str | None = None
    stats: DailyStats | None = None
    mood: dict | None = None


@dataclass
class MemberRisk:
    user_id: str
    nickname: str
    score: float
    label: str
    dims: list[str] = field(default_factory=list)
    messages: int = 0


@dataclass
class DailyStats:
    group_id: str
    date: str
    start_ts: int
    end_ts: int
    messages: int = 0
    users: int = 0
    avg_intensity: float = 0.0
    hour_hist: list[int] = field(default_factory=lambda: [0] * 24)
    dims: dict[str, float] = field(default_factory=dict)
    risks: list[MemberRisk] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)

    @property
    def peak_hour(self) -> int:
        return max(range(24), key=lambda h: self.hour_hist[h]) if self.messages else 0

    @property
    def has_data(self) -> bool:
        return self.messages > 0


def collect(
    users: dict[str, dict],
    group_id: str,
    *,
    start_ts: int,
    end_ts: int,
    sample_limit: int = 50,
) -> DailyStats:
    """把一天的原始消息聚合成日报数据（纯规则，不调模型）。"""
    stats = DailyStats(
        group_id=group_id,
        date=datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d"),
        start_ts=start_ts,
        end_ts=end_ts,
    )
    dim_totals: dict[str, float] = {}
    intensity_sum = 0.0
    pool: list[tuple[int, str]] = []

    for uid, user in users.items():
        if not isinstance(user, dict) or user.get("opt_out"):
            continue
        msgs = [
            m
            for m in (user.get("messages") or [])
            if isinstance(m, dict) and start_ts <= int(m.get("t", 0)) < end_ts
        ]
        if not msgs:
            continue
        stats.users += 1
        stats.messages += len(msgs)
        nick = str(user.get("nick") or uid)

        for m in msgs:
            hour = datetime.fromtimestamp(int(m["t"])).hour
            stats.hour_hist[hour] += 1
            intensity_sum += float(m.get("i", 0))
            if not str(m.get("x", "")).strip():
                continue
            pool.append((int(m.get("t", 0)), str(m["x"])))
            for dim in m.get("d") or []:
                dim_totals[dim] = dim_totals.get(dim, 0.0) + float(m.get("i", 0))

        profile = analyzer.build_profile(
            group_id,
            uid,
            nick,
            msgs,
            window_days=1,
            now=end_ts,
            include_quotes=False,
        )
        score = analyzer.combine_score(profile, None)
        if score >= 45 or profile.crisis_recent:
            stats.risks.append(
                MemberRisk(
                    user_id=uid,
                    nickname=nick,
                    score=score,
                    label=analyzer.level_of(score)[2],
                    dims=[
                        lexicon.DIMENSIONS[d][0]
                        for d, v in sorted(profile.dims.items(), key=lambda kv: -kv[1])[:2]
                        if v >= 10
                    ],
                    messages=len(msgs),
                )
            )

    if stats.messages:
        stats.avg_intensity = round(intensity_sum / stats.messages, 2)
    stats.dims = {
        dim: round(100.0 * (1.0 - math.exp(-total / 12.0)), 1)
        for dim, total in dim_totals.items()
        if dim in lexicon.DIMENSIONS
    }
    stats.risks.sort(key=lambda r: (-r.score, -r.messages))
    stats.samples = _sample(pool, sample_limit)
    return stats


def _sample(pool: list[tuple[int, str]], limit: int) -> list[str]:
    """按时间均匀采样，长消息截断，避免把整天的记录塞给模型。"""
    if not pool:
        return []
    pool.sort(key=lambda item: item[0])
    if len(pool) <= limit:
        picked = pool
    else:
        step = len(pool) / limit
        picked = [pool[min(len(pool) - 1, int(i * step))] for i in range(limit)]
    out = []
    for _, text in picked:
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append(text[:100])
    return out


def build_prompt(stats: DailyStats, group_name: str) -> str:
    return DAILY_PROMPT.format(
        group=group_name or "本群",
        date=stats.date,
        count=len(stats.samples),
        messages="\n".join(f"- {s}" for s in stats.samples) or "（无）",
    )


def parse_daily_json(raw: str) -> dict | None:
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        mood = max(0, min(5, int(float(data.get("mood", 1)))))
    except (TypeError, ValueError):
        mood = 1
    keywords = data.get("keywords") if isinstance(data.get("keywords"), list) else []
    return {
        "mood": mood,
        "label": str(data.get("label") or MOOD_LABELS[mood])[:8],
        "keywords": [str(k)[:8] for k in keywords[:5] if str(k).strip()],
        "summary": str(data.get("summary", ""))[:160],
        "care_tip": str(data.get("care_tip", ""))[:100],
    }


def fallback_mood(stats: DailyStats) -> dict:
    """没有模型时的兜底：用平均强度映射一个标签，关键词用维度名。"""
    level = min(5, int(stats.avg_intensity / 1.6))
    top = sorted(stats.dims.items(), key=lambda kv: -kv[1])[:3]
    return {
        "mood": level,
        "label": MOOD_LABELS[level],
        "keywords": [lexicon.DIMENSIONS[d][0] for d, v in top if v >= 10],
        "summary": "",
        "care_tip": "",
    }


def render_public(stats: DailyStats, mood: dict, *, group_name: str, weather=None) -> str:
    """公开版：只有整体气氛，没有任何人名和原话。"""
    mark = MOOD_MARKS[mood.get("mood", 1)]
    label = mood.get("label") or MOOD_LABELS[1]
    peak = stats.peak_hour
    keywords = " · ".join(mood.get("keywords") or []) or "—"
    lines = [
        f"🌙 群氛围日报 · {stats.date[5:]}",
        "━━━━━━━━━━━━━━",
        f"消息 {stats.messages} 条 · 活跃 {stats.users} 人 · 最热闹 {peak:02d}:00",
        f"整体气氛：{mark} {label}",
        f"今日话题：{keywords}",
    ]
    if weather is not None:
        lines.insert(4, f"今日气象：{weather.summary()}")
    if mood.get("summary"):
        lines += ["", f"📝 {mood['summary']}"]
    if mood.get("care_tip"):
        lines.append(f"💡 {mood['care_tip']}")
    if weather is not None and weather.tip():
        lines += ["", f"🌦 {weather.tip()}"]
    lines += [
        "",
        "（本报告只观察整体氛围，不评价任何个人；需要帮忙可以私聊管理员）",
        "⚠️ 由聊天文本自动归纳，不是心理诊断。",
    ]
    return "\n".join(lines)


def render_private(stats: DailyStats, mood: dict, *, group_name: str, weather=None) -> str:
    """私密版：给管理员，含需要留意的成员。"""
    mark = MOOD_MARKS[mood.get("mood", 1)]
    label = mood.get("label") or MOOD_LABELS[1]
    lines = [
        f"📋 群关怀日报（管理员）· {stats.date}",
        f"群聊：{group_name or stats.group_id}",
        f"消息 {stats.messages} 条 · 活跃 {stats.users} 人 · 平均强度 {stats.avg_intensity}",
        f"整体气氛：{mark} {label}",
    ]
    if weather is not None:
        lines.append(f"今日气象：{weather.summary()}")
    if weather is not None and weather.tip():
        lines.append(f"气象提示：{weather.tip()}")
    if mood.get("summary"):
        lines.append(f"观察：{mood['summary']}")
    if stats.risks:
        lines += ["", f"👤 建议留意（{len(stats.risks)} 人）"]
        for risk in stats.risks[:5]:
            dims = "、".join(risk.dims) or "综合表达"
            lines.append(
                f"· {risk.nickname}（{risk.user_id}）{risk.score:.0f}分 {risk.label} · {dims}"
            )
        lines.append("")
        lines.append("用 /心理报告 QQ号 查看单人详情；先私聊，不要公开点名。")
    else:
        lines += ["", "👤 今天没有需要特别留意的成员。"]
    if stats.risks and any(r.score >= 75 for r in stats.risks):
        lines += ["", "🚨 有人达到高度关注，建议今天内私聊确认一下。"]
    lines += ["", "⚠️ 自动归纳，非诊断。"]
    return "\n".join(lines)
