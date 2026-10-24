"""把 Jinja2 卡片模板渲染成 HTML，方便在浏览器里预览 / 截图。

AstrBot 运行时用真正的 Jinja2 渲染；这里只实现模板里实际用到的几种语法
（if / for / 变量 / length / or / 行内 if），目的是能在没有网络的环境里
先把排版看一眼。
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import analyzer, daily  # noqa: E402
from core import render as render_mod  # noqa: E402

TOKEN_RE = re.compile(r"({{.*?}}|{%.*?%})", re.S)


def tokenize(tpl: str):
    tokens = []
    for part in TOKEN_RE.split(tpl):
        if not part:
            continue
        if part.startswith("{{"):
            tokens.append(("var", part[2:-2].strip()))
        elif part.startswith("{%"):
            tokens.append(("tag", part[2:-2].strip()))
        else:
            tokens.append(("text", part))
    return tokens


def eval_expr(expr: str, ctx: dict):
    expr = expr.strip()
    # a if cond else b
    m = re.fullmatch(r"(.+?)\s+if\s+(.+?)\s+else\s+(.+)", expr)
    if m:
        return eval_expr(m.group(1), ctx) if eval_expr(m.group(2), ctx) else eval_expr(m.group(3), ctx)
    # a | length
    if "|" in expr:
        base, _, filt = expr.partition("|")
        value = eval_expr(base, ctx)
        if filt.strip() == "length":
            return len(value or [])
        return value
    # a and b
    if " and " in expr:
        return all(eval_expr(part, ctx) for part in expr.split(" and "))
    # a or b
    if " or " in expr:
        left, _, right = expr.partition(" or ")
        return eval_expr(left, ctx) or eval_expr(right, ctx)
    if expr.startswith("not "):
        return not eval_expr(expr[4:], ctx)
    if (expr.startswith('"') and expr.endswith('"')) or (expr.startswith("'") and expr.endswith("'")):
        return expr[1:-1]
    if re.fullmatch(r"-?\d+", expr):
        return int(expr)
    # dotted lookup
    value = ctx
    for part in expr.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            value = getattr(value, part, None)
    return value


def _collect(tokens, i, end_tag):
    """收集到匹配的 end 标签，返回 (body_tokens, next_index, else_index)。"""
    depth = 0
    body = []
    else_at = None
    while i < len(tokens):
        kind, text = tokens[i]
        if kind == "tag":
            if text.startswith(("if ", "for ")):
                depth += 1
            elif text in ("endif", "endfor"):
                if depth == 0:
                    return body, i + 1, else_at
                depth -= 1
            elif text == "else" and depth == 0:
                else_at = len(body)
        body.append((kind, text))
        i += 1
    return body, i, else_at


def render(tpl: str, ctx: dict) -> str:
    tokens = tokenize(tpl)
    out = []
    i = 0
    while i < len(tokens):
        kind, text = tokens[i]
        if kind == "text":
            out.append(text)
            i += 1
        elif kind == "var":
            value = eval_expr(text, ctx)
            out.append("" if value is None else str(value))
            i += 1
        else:
            if text.startswith("if "):
                body, nxt, else_at = _collect(tokens, i + 1, "endif")
                cond = bool(eval_expr(text[3:], ctx))
                if cond:
                    chunk = body if else_at is None else body[:else_at]
                else:
                    chunk = [] if else_at is None else body[else_at + 1 :]
                out.append(render_tokens(chunk, ctx))
                i = nxt
            elif text.startswith("for "):
                m = re.match(r"for\s+(\w+)\s+in\s+(.+)", text)
                body, nxt, _ = _collect(tokens, i + 1, "endfor")
                for item in eval_expr(m.group(2), ctx) or []:
                    sub = dict(ctx)
                    sub[m.group(1)] = item
                    out.append(render_tokens(body, sub))
                i = nxt
            else:
                i += 1
    return "".join(out)


def render_tokens(tokens, ctx) -> str:
    tpl = "".join(
        ("{{" + t + "}}") if k == "var" else ("{%" + t + "%}") if k == "tag" else t
        for k, t in tokens
    )
    return render(tpl, ctx)


def build_demo():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = int(today.timestamp())
    end = start + 86400
    chat = {
        "10001": ("小满", [
            (6, 9, "今天加班到十点，好累"),
            (6, 9, "感觉最近什么事都提不起劲"),
            (4, 21, "昨晚又失眠了，三点多才睡着"),
            (2, 21, "我是不是挺没用的"),
            (0, 22, "感觉没有未来"),
            (0, 22, "真的撑不下去了"),
        ]),
        "10002": ("阿澈", [(0, 10, "哈哈哈哈这个表情包绝了"), (0, 20, "今晚开黑吗"), (0, 20, "我请客奶茶")]),
        "10003": ("木木", [(0, 12, "论文改到崩溃"), (0, 19, "不想说话"), (0, 19, "谢谢你们陪我")]),
        "10004": ("小K", [(0, 8, "早八人已死"), (0, 18, "下班了！"), (0, 22, "晚安")]),
    }
    users, first_user_msgs = {}, None
    for uid, (nick, lines) in chat.items():
        msgs = []
        for day, hour, text in lines:
            ts = int(today.replace(hour=hour, minute=5).timestamp()) - day * 86400
            sig = analyzer.analyze_text(text, ts)
            msgs.append({"t": ts, "x": text, "i": sig.intensity, "d": sig.dims, "c": sig.crisis})
        users[uid] = {"nick": nick, "messages": msgs, "opt_out": False}
        if uid == "10001":
            first_user_msgs = msgs

    stats = daily.collect(users, "8888", start_ts=start, end_ts=end + 1)
    mood = daily.parse_daily_json(
        """{"label": "略有疲惫", "mood": 2, "keywords": ["加班", "论文", "失眠", "开黑"],
            "summary": "白天聊工作和论文的多，普遍在喊累；晚上话题转到游戏和宵夜，气氛松快了些。",
            "care_tip": "期末周大家都辛苦，早点睡比多刷一小时有用。"}"""
    ) or daily.fallback_mood(stats)

    profile = analyzer.build_profile(
        "8888", "10001", "小满", first_user_msgs, window_days=14, now=end
    )
    llm = {
        "summary": "近两周持续表达疲惫、失眠与自我否定，社交意愿下降，尚未见明确危险计划。",
        "advice": "先私下关心作息和最近的烦心事，少给建议多倾听；若持续两周以上，建议陪同寻求专业帮助。",
        "evidence": ["感觉最近什么事都提不起劲", "我是不是挺没用的"],
    }
    score = analyzer.combine_score(profile, llm)
    return stats, mood, profile, llm, score


def main() -> None:
    stats, mood, profile, llm, score = build_demo()
    out_dir = ROOT / "build" / "preview"
    out_dir.mkdir(parents=True, exist_ok=True)

    tpl_daily = render_mod.load_template("daily_card.html")
    tpl_report = render_mod.load_template("report_card.html")

    public_ctx = render_mod.daily_context(stats, mood, group_name="摸鱼小分队", private=False)
    private_ctx = render_mod.daily_context(stats, mood, group_name="摸鱼小分队", private=True)
    report_ctx = render_mod.report_context(
        profile, score, llm, group_name="摸鱼小分队", window_days=14
    )

    (out_dir / "daily_public.html").write_text(render(tpl_daily, public_ctx), encoding="utf-8")
    (out_dir / "daily_private.html").write_text(render(tpl_daily, private_ctx), encoding="utf-8")
    (out_dir / "report.html").write_text(render(tpl_report, report_ctx), encoding="utf-8")
    print("written to", out_dir)
    print(f"score={score} level={analyzer.level_of(score)[2]}")


if __name__ == "__main__":
    main()
