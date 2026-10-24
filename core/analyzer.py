"""信号分析、聚合与报告渲染。

三层结构：
1. `analyze_text` —— 单条消息的规则打分（毫秒级，随消息流实时执行）；
2. `build_profile` —— 把窗口内的消息聚合成一个人的「关注度画像」；
3. `llm_*` —— 可选的语义复核，把画像和原始片段交给大模型，要求它只做
   「值得关心的线索归纳」，不做诊断。

所有分数都是启发式的 0-100「关注度」，不是量表分，也不是诊断结论。
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import lexicon

# 关注度分级：(下限, 键, 名称, 标记)
LEVELS: list[tuple[int, str, str, str]] = [
    (90, "crisis", "危机信号", "🚨"),
    (75, "high", "高度关注", "🔴"),
    (50, "watch", "值得关注", "🟠"),
    (30, "mild", "轻度波动", "🟡"),
    (0, "stable", "相对平稳", "🟢"),
]

TREND_ARROW = {-1: "↓ 较前期缓解", 0: "→ 与前期持平", 1: "↑ 较前期加重"}


# ---------------------------------------------------------------------------
# 单条消息
# ---------------------------------------------------------------------------
@dataclass
class Signal:
    ts: int
    text: str
    intensity: float
    dims: list[str] = field(default_factory=list)
    crisis: bool = False
    hits: list[str] = field(default_factory=list)


def analyze_text(text: str, ts: int | None = None) -> Signal:
    """对单条消息做规则打分。"""
    text = (text or "").strip()
    now = int(ts if ts is not None else time.time())
    if not text:
        return Signal(ts=now, text="", intensity=0.0)

    hits = lexicon.match_terms(text)
    crisis = any(h.crisis for h in hits)
    raw = sum(h.weight for h in hits)
    if not hits:
        raw = -lexicon.protective_score(text)
    intensity = max(0.0, min(9.0, float(raw)))
    dims = sorted({h.dim for h in hits})
    return Signal(
        ts=now,
        text=text[:300],
        intensity=intensity,
        dims=dims,
        crisis=crisis,
        hits=[h.pattern.pattern for h in hits][:8],
    )


# ---------------------------------------------------------------------------
# 聚合画像
# ---------------------------------------------------------------------------
@dataclass
class UserProfile:
    group_id: str
    user_id: str
    nickname: str
    n_messages: int = 0
    n_risk: int = 0
    active_days: int = 0
    risk_days: int = 0
    severity: float = 0.0
    persistence: float = 0.0
    recency: float = 0.0
    peak: float = 0.0
    rule_score: float = 0.0
    dims: dict[str, float] = field(default_factory=dict)
    mood_strip: str = ""
    mood_series: list[float | None] = field(default_factory=list)
    crisis_recent: bool = False
    escalated: bool = False
    trend: int = 0
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    last_ts: int = 0
    first_ts: int = 0

    @property
    def has_data(self) -> bool:
        return self.n_messages > 0


def _day_key(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _clip(text: str, limit: int = 36) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


_MOOD_BLOCKS = "▁▂▃▄▅▆▇█"


def mood_series(messages: list[dict], now: int, days: int = 7) -> list[float | None]:
    """每天的平均强度；当天没有发言则为 None。"""
    sums = [0.0] * days
    counts = [0] * days
    for m in messages:
        idx = int((now - int(m.get("t", 0))) // 86400)
        if 0 <= idx < days:
            slot = days - 1 - idx
            sums[slot] += float(m.get("i", 0))
            counts[slot] += 1
    return [(sums[i] / counts[i]) if counts[i] else None for i in range(days)]


def mood_strip(messages: list[dict], now: int, days: int = 7) -> str:
    """最近 N 天的心情曲线（纯文本版，群里用）：八档方块，· 表示没发言。"""
    out = []
    for avg in mood_series(messages, now, days):
        if avg is None:
            out.append("·")
        else:
            out.append(_MOOD_BLOCKS[min(len(_MOOD_BLOCKS) - 1, int(avg / 6.0 * 8))])
    return "".join(out)


def build_profile(
    group_id: str,
    user_id: str,
    nickname: str,
    messages: list[dict],
    *,
    window_days: int = 14,
    now: int | None = None,
    include_quotes: bool = True,
) -> UserProfile:
    """把窗口内的消息聚合成画像。"""
    now = int(now if now is not None else time.time())
    start = now - window_days * 86400
    msgs = sorted(
        (m for m in messages if isinstance(m, dict) and m.get("t", 0) >= start),
        key=lambda m: m.get("t", 0),
    )
    profile = UserProfile(group_id=group_id, user_id=user_id, nickname=nickname)
    if not msgs:
        return profile

    profile.n_messages = len(msgs)
    profile.first_ts = int(msgs[0]["t"])
    profile.last_ts = int(msgs[-1]["t"])
    days = {_day_key(int(m["t"])) for m in msgs}
    profile.active_days = len(days)

    risk = [m for m in msgs if float(m.get("i", 0)) >= 2]
    profile.n_risk = len(risk)
    risk_days = {_day_key(int(m["t"])) for m in risk}
    profile.risk_days = len(risk_days)

    # 单条消息强度理论上限 9，但实际命中 5 分（如「想死」）就已很重，
    # 因此用 5.5 归一化，避免所有分数都挤在 40 分以下。
    top = sorted((float(m.get("i", 0)) for m in risk), reverse=True)[:5]
    profile.severity = (sum(top) / len(top) / 5.5 * 100.0) if top else 0.0

    # 只有一天记录时 risk_days/active_days 恒为 1，会把分数拉爆；
    # 乘一个活跃天数的置信系数，至少积累 4 天才算「持续」。
    sustained = profile.risk_days / max(1, profile.active_days)
    profile.persistence = sustained * min(1.0, profile.active_days / 4.0)
    profile.peak = min(100.0, max((float(m.get("i", 0)) for m in msgs), default=0.0) / 5.5 * 100.0)

    recent = [m for m in risk if int(m["t"]) >= now - 48 * 3600]
    profile.recency = (
        sum(float(m.get("i", 0)) for m in recent) / len(recent) / 5.5 * 100.0
        if recent
        else 0.0
    )
    profile.recency = min(100.0, profile.recency)

    profile.rule_score = (
        0.45 * profile.severity
        + 0.25 * profile.persistence * 100.0
        + 0.20 * profile.recency
        + 0.10 * profile.peak
    )

    # 维度累计：一条消息的强度按命中维度均摊
    totals: dict[str, float] = {}
    for m in msgs:
        dims = m.get("d") or []
        if not dims:
            continue
        share = float(m.get("i", 0)) / len(dims)
        for dim in dims:
            totals[dim] = totals.get(dim, 0.0) + share
    profile.dims = {
        dim: round(100.0 * (1.0 - math.exp(-total / 8.0)), 1)
        for dim, total in totals.items()
        if dim in lexicon.DIMENSIONS
    }
    profile.mood_series = mood_series(msgs, now)
    profile.mood_strip = "".join(
        "·"
        if value is None
        else _MOOD_BLOCKS[
            min(len(_MOOD_BLOCKS) - 1, int(value / 6.0 * 8))
        ]
        for value in profile.mood_series
    )

    profile.crisis_recent = any(
        bool(m.get("c")) and int(m["t"]) >= now - 72 * 3600 for m in msgs
    )
    # 72 小时内反复出现高强度表达（≥4 分，如「撑不下去」「没有未来」「我不配」），
    # 即使没有出现明确的自伤词，也升级为高度关注，交给人工确认。
    severe_recent = [
        m for m in msgs
        if float(m.get("i", 0)) >= 4 and int(m["t"]) >= now - 72 * 3600
    ]
    profile.escalated = len(severe_recent) >= 2

    # 趋势：近 3 天 vs 之前 11 天（按日均强度比较）
    cut = now - 3 * 86400
    recent_i = [float(m.get("i", 0)) for m in msgs if int(m["t"]) >= cut]
    older_i = [float(m.get("i", 0)) for m in msgs if int(m["t"]) < cut]
    r_avg = sum(recent_i) / max(1, len(recent_i))
    o_avg = sum(older_i) / max(1, len(older_i))
    if recent_i and r_avg > o_avg * 1.25 + 0.4:
        profile.trend = 1
    elif older_i and r_avg < o_avg * 0.75 - 0.2:
        profile.trend = -1
    else:
        profile.trend = 0

    profile.confidence = max(0.15, min(1.0, profile.n_messages / 40.0))

    if include_quotes:
        picks = sorted(risk, key=lambda m: float(m.get("i", 0)), reverse=True)[:6]
        seen: set[str] = set()
        for m in picks:
            text = _clip(str(m.get("x", "")))
            if text and text not in seen:
                seen.add(text)
                profile.evidence.append(text)
            if len(profile.evidence) >= 3:
                break
    return profile


# ---------------------------------------------------------------------------
# 分数合成与分级
# ---------------------------------------------------------------------------
def level_of(score: float) -> tuple[int, str, str, str]:
    for threshold, key, label, mark in LEVELS:
        if score >= threshold:
            return threshold, key, label, mark
    return 0, "stable", "相对平稳", "🟢"


def llm_level_score(level: int, crisis: bool = False) -> float:
    mapping = {0: 10.0, 1: 35.0, 2: 55.0, 3: 78.0, 4: 97.0}
    return 100.0 if crisis else mapping.get(int(level), 20.0)


def combine_score(profile: UserProfile, llm: dict | None) -> float:
    """规则分与大模型分融合；没有 LLM 结果时退回规则分。"""
    base = profile.rule_score
    if llm:
        base = 0.40 * base + 0.60 * llm_level_score(
            llm.get("level", 0), bool(llm.get("crisis"))
        )
    base *= 0.90 + 0.10 * profile.confidence
    if profile.crisis_recent or (llm and llm.get("crisis")):
        base = max(base, 92.0)
    elif profile.escalated:
        base = max(base, 80.0)
    return round(max(0.0, min(100.0, base)), 1)


def merged_dims(profile: UserProfile, llm: dict | None) -> dict[str, float]:
    dims = dict(profile.dims)
    if not llm:
        return dims
    for dim, value in (llm.get("dimensions") or {}).items():
        if dim not in lexicon.DIMENSIONS:
            continue
        try:
            score = float(value) / 3.0 * 100.0
        except (TypeError, ValueError):
            continue
        dims[dim] = round(max(dims.get(dim, 0.0), 0.65 * score + 0.35 * dims.get(dim, 0.0)), 1)
    return dims


# ---------------------------------------------------------------------------
# LLM 提示词与解析
# ---------------------------------------------------------------------------
SYSTEM_RULES = """你是一名谨慎的心理健康观察助手，只做「值得关心的线索归纳」，不做诊断。
必须遵守：
1. 只依据给出的聊天片段判断，不要臆测、不要编造不存在的内容；
2. 日常吐槽、玩梗、开玩笑不要当成风险；证据不足时给低分；
3. 不输出疾病名称的诊断结论，不使用「患有抑郁症」这类表述；
4. 简短、克制、可执行。"""

PROMPT_TEMPLATE = """以下是 QQ 群成员「{nickname}」在最近 {days} 天的部分发言（共采样 {count} 条，已按时间排序）：

{messages}

请判断这些发言里是否包含值得关心的心理风险线索，并只输出一个 JSON 对象，不要输出其他内容：
{{
  "level": 0-4 的整数（0 无明显信号，1 轻微，2 明显，3 较强，4 强烈/危机）,
  "crisis": true 或 false（是否出现自伤、自杀、告别等危险表达）,
  "dimensions": {{
    "mood": 0-3, "anxiety": 0-3, "worth": 0-3,
    "sleep": 0-3, "withdraw": 0-3, "stress": 0-3, "crisis": 0-3
  }},
  "summary": "不超过 60 字，客观描述观察到的状态变化",
  "advice": "不超过 80 字，给关心TA的人的具体行动建议",
  "evidence": ["不超过 3 条原文片段，每条不超过 30 字"]
}}
如果没有足够线索，level 填 0，summary 写「暂无明显风险信号」。"""


def build_llm_prompt(profile: UserProfile, messages: list[dict], days: int) -> str:
    sampled = _sample_messages(messages, limit=40)
    lines = []
    for m in sampled:
        ts = int(m.get("t", 0))
        when = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
        text = str(m.get("x", "")).strip().replace("\n", " ")
        if len(text) > 160:
            text = text[:159] + "…"
        lines.append(f"[{when}] {text}")
    return PROMPT_TEMPLATE.format(
        nickname=profile.nickname or "该成员",
        days=days,
        count=len(sampled),
        messages="\n".join(lines) if lines else "（无）",
    )


def _sample_messages(messages: list[dict], limit: int = 40) -> list[dict]:
    """按时间均匀采样，保证覆盖整个窗口，同时保留风险较高的消息。"""
    msgs = [m for m in messages if str(m.get("x", "")).strip()]
    if len(msgs) <= limit:
        return msgs
    risky = [m for m in msgs if float(m.get("i", 0)) >= 3]
    keep = {id(m) for m in risky[: limit // 2]}
    kept = [m for m in msgs if id(m) in keep]
    rest = [m for m in msgs if id(m) not in keep]
    room = max(1, limit - len(kept))
    step = max(1, len(rest) // room)
    sampled = rest[::step][:room]
    return sorted(kept + sampled, key=lambda m: m.get("t", 0))


def parse_llm_json(raw: str) -> dict | None:
    """从模型输出里抠出 JSON，宽松解析并做边界裁剪。"""
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        try:
            data = json.loads(text[start : end + 1].replace("，", ","))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None

    def _int(value, low, high, default=0):
        try:
            return max(low, min(high, int(float(value))))
        except (TypeError, ValueError):
            return default

    dims_raw = data.get("dimensions") if isinstance(data.get("dimensions"), dict) else {}
    dims = {
        key: _int(dims_raw.get(key, 0), 0, 3)
        for key in ("mood", "anxiety", "worth", "sleep", "withdraw", "stress", "crisis")
    }
    evidence = data.get("evidence") if isinstance(data.get("evidence"), list) else []
    return {
        "level": _int(data.get("level", 0), 0, 4),
        "crisis": bool(data.get("crisis", False)),
        "dimensions": dims,
        "summary": str(data.get("summary", ""))[:160],
        "advice": str(data.get("advice", ""))[:200],
        "evidence": [str(e)[:40] for e in evidence[:3]],
        "ts": int(time.time()),
    }


# ---------------------------------------------------------------------------
# 文本渲染
# ---------------------------------------------------------------------------
def render_report(
    profile: UserProfile,
    score: float,
    llm: dict | None,
    *,
    group_name: str,
    window_days: int,
    now: int | None = None,
    llm_failed: bool = False,
) -> str:
    now = int(now if now is not None else time.time())
    _, _, label, mark = level_of(score)
    dims = merged_dims(profile, llm)
    top_dims = sorted(dims.items(), key=lambda kv: kv[1], reverse=True)[:4]

    lines = [
        "🧭 心理关怀报告（非诊断）",
        "━━━━━━━━━━━━━━",
        f"群聊：{group_name or '未知群聊'}",
        f"成员：{profile.nickname or '群友'}（{profile.user_id}）",
        f"区间：近 {window_days} 天 · {profile.n_messages} 条消息 · 活跃 {profile.active_days} 天",
        f"生成：{datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M')}",
        "",
        f"综合关注度：{mark} {label} · {score:.0f}/100",
        f"趋势：{TREND_ARROW.get(profile.trend, TREND_ARROW[0])}",
    ]
    if profile.mood_strip:
        lines.append(f"近 7 天强度：{profile.mood_strip}（· = 当天没说话）")

    if profile.escalated and not profile.crisis_recent:
        lines.append("注意：近 72 小时多次出现高强度负性表达，建议尽快人工确认。")

    if profile.n_messages == 0:
        lines += ["", "这个窗口内没有该成员的消息，无法给出任何判断。"]
        return "\n".join(lines)

    if profile.confidence < 0.35:
        lines.append("置信度：偏低（发言样本较少，结论仅供参考）")

    if top_dims:
        lines += ["", "📌 主要信号"]
        for dim, value in top_dims:
            if value < 8:
                continue
            dim_label = lexicon.DIMENSIONS[dim][0]
            bar = "▇" * max(1, round(value / 20))
            lines.append(f"· {dim_label} {value:.0f}/100 {bar}")
        if all(v < 8 for _, v in top_dims):
            lines.append("· 暂无明显集中信号")

    if llm:
        if llm.get("summary"):
            lines += ["", "🔍 语义观察", f"· {llm['summary']}"]
        if llm.get("advice"):
            lines += ["", "💡 建议做法", f"· {llm['advice']}"]
    elif llm_failed:
        lines += ["", "🔍 语义观察", "· 本次未能完成 AI 复核，以上仅为词频规则结果。"]

    if profile.evidence:
        lines += ["", "🗣 原文片段（已截断）"]
        lines += [f"· 「{e}」" for e in profile.evidence]

    if profile.crisis_recent:
        lines += [
            "",
            "🚨 最近 72 小时出现危机类表达。请尽快私聊确认安全，",
            "   不要公开讨论，也不要替对方下结论。",
        ]

    lines += [
        "",
        "⚠️ 本报告由群消息文本自动归纳，只提示「值得关心」的线索，",
        "   不构成任何医学诊断，也不能替代专业评估。",
        "   如有自伤风险：全国心理援助热线 12356。",
    ]
    return "\n".join(lines)


def render_overview(
    group_name: str,
    rows: list[tuple[UserProfile, float]],
    *,
    window_days: int,
    total_messages: int,
    now: int | None = None,
) -> str:
    now = int(now if now is not None else time.time())
    lines = [
        "📊 群心理关怀概览（非诊断）",
        "━━━━━━━━━━━━━━",
        f"群聊：{group_name or '未知群聊'}",
        f"区间：近 {window_days} 天 · 记录 {len(rows)} 人 / {total_messages} 条消息",
        f"生成：{datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if not rows:
        lines.append("窗口内还没有任何记录。")
        return "\n".join(lines)

    buckets: dict[str, list[tuple[UserProfile, float]]] = {}
    for profile, score in rows:
        _, key, label, _ = level_of(score)
        buckets.setdefault(key, []).append((profile, score))

    for key, label, mark in (
        ("crisis", "危机信号", "🚨"),
        ("high", "高度关注", "🔴"),
        ("watch", "值得关注", "🟠"),
        ("mild", "轻度波动", "🟡"),
    ):
        items = buckets.get(key) or []
        if not items:
            continue
        lines.append(f"{mark} {label} {len(items)} 人")
        for profile, score in sorted(items, key=lambda x: x[1], reverse=True)[:10]:
            arrow = TREND_ARROW.get(profile.trend, "")[:1]
            lines.append(
                f"· {profile.nickname or '群友'}（{profile.user_id}）"
                f" {score:.0f}分 {arrow} · {profile.n_messages}条消息"
            )
        lines.append("")

    lines += [
        "用 /心理报告 @某人 查看单人详情。",
        "⚠️ 关注度由词频与语义信号估算，不是诊断。",
    ]
    return "\n".join(lines)


def render_alert(
    profile: UserProfile,
    score: float,
    llm: dict | None,
    *,
    group_name: str,
    reason: str,
) -> str:
    _, _, label, mark = level_of(score)
    dims = merged_dims(profile, llm)
    top = sorted(dims.items(), key=lambda kv: kv[1], reverse=True)[:3]
    names = "、".join(lexicon.DIMENSIONS[d][0] for d, v in top if v >= 8) or "综合情绪表达"
    lines = [
        f"{mark} 心理关怀提醒：{label}（{score:.0f}/100）",
        f"群聊：{group_name or '未知群聊'}",
        f"成员：{profile.nickname or '群友'}（{profile.user_id}）",
        f"触发：{reason}",
        f"信号：{names}",
    ]
    if profile.evidence:
        lines.append(f"片段：「{profile.evidence[0]}」")
    if llm and llm.get("advice"):
        lines.append(f"建议：{llm['advice']}")
    lines += [
        "",
        "处理建议：私聊关心，先问「最近还好吗」，不要公开点名。",
        "查看详情：/心理报告 " + profile.user_id,
        "（自动分析，非诊断）",
    ]
    return "\n".join(lines)
