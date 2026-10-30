"""插件自测：用 AstrBot 桩跑通采集 → 分析 → 报告 → 提醒的整条链路。

运行：python tests/test_plugin.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUBS = ROOT / "tests" / "stubs"
for path in (str(ROOT), str(STUBS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import astrbot_stub_setup  # noqa: F401,E402

from astrbot.api.event import MessageEventResult  # noqa: E402
from astrbot.api.star import Context  # noqa: E402

import main as plugin_main  # noqa: E402
from core import analyzer, lexicon  # noqa: E402
from core.config import Settings  # noqa: E402
from core.store import Store  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name} {detail}")


class FakeContext(Context):
    """记录发出去的消息，并返回固定的模型输出。"""

    def __init__(self, llm_reply: str | None = None):
        super().__init__()
        self.llm_reply = llm_reply
        self.llm_calls: list[str] = []

    async def get_current_chat_provider_id(self, umo: str = "") -> str:
        return "fake-provider" if self.llm_reply is not None else ""

    async def llm_generate(self, chat_provider_id: str = "", prompt: str = "", **kwargs):
        self.llm_calls.append(prompt)
        reply = DAILY_JSON if "整体氛围" in prompt or "群氛围" in prompt else self.llm_reply

        class _Resp:
            completion_text = reply

        return _Resp()


LLM_JSON = json.dumps(    {
        "level": 3,
        "crisis": False,
        "dimensions": {
            "mood": 3,
            "anxiety": 2,
            "worth": 2,
            "sleep": 3,
            "withdraw": 1,
            "stress": 2,
            "crisis": 0,
        },
        "summary": "持续表达疲惫与失眠，自我评价偏低。",
        "advice": "先私聊确认最近作息，别急着给建议。",
        "evidence": ["最近好累", "睡不着"],
    },
    ensure_ascii=False,
)

DAILY_JSON = json.dumps(
    {
        "label": "略有疲惫",
        "mood": 2,
        "keywords": ["加班", "失眠", "开黑"],
        "summary": "白天大多在聊工作和熬夜，晚上气氛松了一点。",
        "care_tip": "早点睡，别熬太晚。",
    },
    ensure_ascii=False,
)


def make_event(**kwargs) -> plugin_main.AstrMessageEvent:
    return plugin_main.AstrMessageEvent(**kwargs)


def make_plugin(tmp: Path, config: dict | None = None) -> plugin_main.PsyCarePlugin:
    os.environ["ASTRBOT_DATA_PATH"] = str(tmp)
    cfg = {
        "llm_min_new_messages": 2,
        "llm_cooldown_minutes": 1,
        "alert_threshold": 60,
        "window_days": 14,
        "image_report": False,
        "weather_enabled": False,
    }
    cfg.update(config or {})
    ctx = FakeContext(LLM_JSON)
    return plugin_main.PsyCarePlugin(ctx, cfg)


async def drain(agen) -> list[MessageEventResult]:
    return [item async for item in agen]


def chain_texts(sent) -> list[str]:
    """把 ctx._sent 里所有纯文本段收集起来（图片段没有 text 属性）。"""
    texts = []
    for _, chain in sent:
        for comp in getattr(chain, "chain", []) or []:
            text = getattr(comp, "text", None)
            if text:
                texts.append(text)
    return texts


def chain_images(sent) -> list:
    images = []
    for _, chain in sent:
        for comp in getattr(chain, "chain", []) or []:
            if comp.__class__.__name__ == "Image":
                images.append(comp)
    return images


async def run() -> None:
    tmp = ROOT / "build" / f"test-{os.getpid()}-{int(time.time())}"
    tmp.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- 词库
    print("\n[词库与规则]")
    s = analyzer.analyze_text("最近真的好累，什么都不想做，晚上也睡不着")
    check("解释性消息有分数", s.intensity >= 3, f"intensity={s.intensity}")
    check("命中多个维度", {"mood", "sleep"} <= set(s.dims), str(s.dims))

    s = analyzer.analyze_text("我不想活了")
    check("危机表达被识别", s.crisis and s.intensity >= 4, f"{s.intensity}")

    s = analyzer.analyze_text("我不会自杀的，别担心")
    check("否定语境不误报", not s.crisis and s.intensity == 0, f"{s.intensity}")

    s = analyzer.analyze_text("笑死我了哈哈哈哈")
    check("玩梗不误报", s.intensity == 0, f"{s.intensity} {s.dims}")

    s = analyzer.analyze_text("今天好多了，谢谢大家陪我")
    check("保护性表达不产生风险", s.intensity == 0 and not s.crisis, f"{s.intensity}")

    s = analyzer.analyze_text("我是不是挺没用的")
    check("反问式自我怀疑不误判成否定", s.intensity >= 2 and "worth" in s.dims, f"{s.intensity} {s.dims}")

    s = analyzer.analyze_text("不太想说话")
    check("弱化说法也能命中", s.intensity >= 2 and "withdraw" in s.dims, f"{s.intensity} {s.dims}")

    check("词库规模合理", lexicon.TERM_COUNT >= 120, str(lexicon.TERM_COUNT))

    # ------------------------------------------------------------- 配置兜底
    print("\n[配置]")
    st = Settings({"enabled": "false", "alert_threshold": "80", "group_whitelist": "123"})
    check("字符串布尔值被纠正", st.bool("enabled") is False)
    check("字符串数字被纠正", st.int("alert_threshold") == 80)
    check("单值白名单转列表", st.get("group_whitelist") == ["123"])

    # ------------------------------------------------------------- 采集
    print("\n[采集与画像]")
    plugin = make_plugin(tmp)
    gid, uid = "1001", "200"
    ctx = plugin.context

    # 未激活的群不应被动采集
    await plugin._collect(
        make_event(
            text="今天上班好累啊",
            group_id=gid,
            sender_id=uid,
            sender_name="小A",
            umo=f"aiocqhttp:GroupMessage:{gid}",
        )
    )
    check("未激活时不采集", plugin.store.user(gid, uid) is None)

    out = await drain(
        plugin.cmd_toggle(
            make_event(
                text="/心理开关 on",
                group_id=gid,
                sender_id="900",
                admin=True,
                umo=f"aiocqhttp:GroupMessage:{gid}",
            )
        )
    )
    check("管理员可激活群", "开启" in out[0].text)

    for text in [
        "今天上班好累啊，感觉自己什么都做不好",
        "昨晚又失眠了，到三点才睡着",
        "不想说话，想一个人待着",
        "我觉得自己挺没用的",
        "谢谢你们，其实还好啦",
        "今天吃了顿好的，开心",
    ]:
        await plugin._collect(
            make_event(
                text=text,
                group_id=gid,
                sender_id=uid,
                sender_name="小A",
                umo=f"aiocqhttp:GroupMessage:{gid}",
            )
        )
    await asyncio.sleep(0.05)

    user = plugin.store.user(gid, uid)
    check("消息已入库", bool(user) and len(user["messages"]) == 6)
    profile = analyzer.build_profile(gid, uid, "小A", plugin.store.messages(gid, uid))
    check("画像统计正确", profile.n_messages == 6 and profile.active_days == 1)
    check("规则分在合理区间", 0 < profile.rule_score < 100, f"{profile.rule_score}")
    check("维度有值", "mood" in profile.dims and profile.dims["mood"] > 0, str(profile.dims))
    check("心情曲线长度", len(profile.mood_strip) == 7, profile.mood_strip)
    check("AI 复核只触发一次", len(ctx.llm_calls) == 1, str(len(ctx.llm_calls)))

    score = analyzer.combine_score(profile, plugin.store.get_analysis(gid, uid)["llm"])
    check("融合分有值", 0 < score <= 100, f"{score}")
    _, key, label, _ = analyzer.level_of(score)
    check("能映射到等级", key in {"stable", "mild", "watch", "high", "crisis"}, f"{label}")

    # ------------------------------------------------------------- 命令
    print("\n[命令]")
    out = await drain(plugin.cmd_help(make_event(text="/心理帮助", group_id=gid, sender_id=uid)))
    check("帮助可输出", "心理退出" in out[0].text)

    out = await drain(plugin.cmd_status(make_event(text="/心理状态", group_id=gid, sender_id=uid)))
    check("状态可输出", "条消息" in out[0].text, out[0].text[:40])

    # 绑定 + 报告
    bind = make_event(
        text="/心理绑定",
        sender_id="900",
        umo="aiocqhttp:FriendMessage:900",
        is_private=True,
    )
    out = await drain(plugin.cmd_bind(bind))
    check("私聊绑定成功", plugin.store.get_setting("alert_umo") == "aiocqhttp:FriendMessage:900")

    report = make_event(
        text="/心理报告",
        group_id=gid,
        sender_id="900",
        admin=True,
        at_targets=[uid],
        umo=f"aiocqhttp:GroupMessage:{gid}",
    )
    out = await drain(plugin.cmd_report(report))
    check("群内报告被转到私聊", "已发送" in out[0].text, out[0].text[:40])
    sent_texts = chain_texts(ctx._sent)
    check(
        "报告内容完整",
        any("心理关怀报告" in t and "小A" in t for t in sent_texts),
        str(sent_texts[:1])[:200],
    )

    # 概览
    out = await drain(
        plugin.cmd_overview(
            make_event(
                text="/心理概览",
                group_id=gid,
                sender_id="900",
                admin=True,
                umo=f"aiocqhttp:GroupMessage:{gid}",
            )
        )
    )
    check("概览可输出", "概览" in out[0].text or "已发送" in out[0].text, out[0].text[:40])

    # ------------------------------------------------------------- 危机提醒
    print("\n[危机提醒]")
    ctx._sent.clear()
    await plugin._collect(
        make_event(
            text="我真的不想活了，感觉没有未来",
            group_id=gid,
            sender_id="201",
            sender_name="小B",
            umo=f"aiocqhttp:GroupMessage:{gid}",
        )
    )
    check("危机事件写入待办", len(plugin.store.alerts(gid)) >= 1)
    alerts = chain_texts([(u, c) for u, c in ctx._sent if "FriendMessage:900" in u])
    check("提醒发到绑定私聊", any("心理关怀提醒" in t for t in alerts), str(len(alerts)))
    check("提醒里不含诊断词", all("诊断" not in t or "非诊断" in t for t in alerts))

    # 冷却：立刻再发一次不应重复提醒
    before = len(plugin.store.alerts(gid))
    await plugin._collect(
        make_event(
            text="想死",
            group_id=gid,
            sender_id="201",
            sender_name="小B",
            umo=f"aiocqhttp:GroupMessage:{gid}",
        )
    )
    check("提醒有冷却", len(plugin.store.alerts(gid)) == before, f"{before}->{len(plugin.store.alerts(gid))}")

    # ------------------------------------------------------------- 日报
    print("\n[每日群氛围日报]")
    from core import daily as daily_mod

    gid2 = "3001"
    out = await drain(
        plugin.cmd_toggle(
            make_event(
                text="/心理开关 on",
                group_id=gid2,
                sender_id="900",
                admin=True,
                umo=f"aiocqhttp:GroupMessage:{gid2}",
            )
        )
    )
    seed = [
        ("310", "小C", "今天加班到十点，好累"),
        ("310", "小C", "感觉最近什么都不想做"),
        ("310", "小C", "昨晚又失眠了"),
        ("311", "小D", "哈哈哈哈这个表情包绝了"),
        ("311", "小D", "今晚开黑吗"),
        ("312", "小E", "我不太想说话"),
        ("312", "小E", "我是不是挺没用的"),
    ]
    for uid_, name_, text_ in seed:
        await plugin._collect(
            make_event(
                text=text_,
                group_id=gid2,
                sender_id=uid_,
                sender_name=name_,
                umo=f"aiocqhttp:GroupMessage:{gid2}",
            )
        )

    check("日报时间解析", plugin._parse_daily_time("9:05") == (9, 5))
    check("日报时间非法回落", plugin._parse_daily_time("25:99") == (21, 30))

    from datetime import datetime as _dt

    plugin2 = make_plugin(tmp / "daily2", {"daily_time": "09:00"})
    check("日报未到点不跑", plugin2._should_run_daily(_dt(2026, 1, 2, 8, 0)) is False)
    check("日报到点会跑", plugin2._should_run_daily(_dt(2026, 1, 2, 10, 0)) is True)
    plugin2.store.set_setting("daily_last_date", "2026-01-02")
    check("同一天不重复跑", plugin2._should_run_daily(_dt(2026, 1, 2, 10, 30)) is False)
    check("错过太久不补跑", plugin2._should_run_daily(_dt(2026, 1, 3, 23, 0)) is False)
    await plugin2.terminate()

    result = await plugin._run_daily(gid2, deliver=False)
    check("能生成日报", bool(result) and bool(result.public) and bool(result.private))
    public, private = (result.public, result.private) if result else ("", "")
    check("公开版含氛围", "群氛围日报" in public and "整体气氛" in public, public[:60])
    check("公开版不含人名", "小C" not in public and "310" not in public)
    check("公开版不含个体判断", "关注度" not in public and "心理问题" not in public)
    check("私密版含管理员信息", "管理员" in private and "群聊" in private, private[:60])

    today = time.strftime("%Y-%m-%d")
    report = plugin.store.get_daily(gid2, today)
    check("日报已入库", bool(report) and report["messages"] >= 7)
    check("日报历史可查", len(plugin.store.daily_reports(gid2)) >= 1)

    ctx._sent.clear()
    out = await drain(
        plugin.cmd_daily(
            make_event(
                text="/心理日报",
                group_id=gid2,
                sender_id="900",
                admin=True,
                umo=f"aiocqhttp:GroupMessage:{gid2}",
            )
        )
    )
    daily_texts = chain_texts(ctx._sent)
    check("日报推送到私聊", any("群关怀日报" in t for t in daily_texts), str(len(daily_texts)))

    out = await drain(
        plugin.cmd_history(
            make_event(
                text="/心理历史",
                group_id=gid2,
                sender_id="900",
                admin=True,
                umo=f"aiocqhttp:GroupMessage:{gid2}",
            )
        )
    )
    check("历史命令可输出", "日报" in out[0].text or "已发送" in out[0].text, out[0].text[:40])

    check("日报 JSON 解析", daily_mod.parse_daily_json('{"mood": 4, "label": "紧张"}')["mood"] == 4)
    check("日报 JSON 越界裁剪", daily_mod.parse_daily_json('{"mood": 99}')["mood"] == 5)

    # --------------------------------------------------- 图片报告 / 面板
    print("\n[图片报告与 WebUI 面板]")
    import re as _re

    from core import render as render_mod

    tpl_daily = render_mod.load_template("daily_card.html")
    tpl_report = render_mod.load_template("report_card.html")
    check("卡片模板存在", bool(tpl_daily) and bool(tpl_report))

    def _check_template(tag: str, tpl: str, ctx_data: dict, loop_vars: set[str]) -> None:
        names = set(_re.findall(r"{{\s*([a-zA-Z_][\w]*)", tpl))
        missing = {n for n in names if n not in ctx_data and n not in loop_vars}
        check(f"模板 {tag} 变量齐全", not missing, str(missing))
        check(
            f"模板 {tag} 块配对",
            tpl.count("{% if") == tpl.count("{% endif")
            and tpl.count("{% for") == tpl.count("{% endfor"),
        )

    daily_ctx = render_mod.daily_context(
        result.stats, result.mood, group_name="摸鱼小分队", private=True
    )
    _check_template("daily", tpl_daily, daily_ctx, {"d", "r", "k", "e"})
    check(
        "公开版上下文不含风险名单",
        not render_mod.daily_context(
            result.stats, result.mood, group_name="摸鱼小分队", private=False
        )["risks"],
    )

    profile_c = analyzer.build_profile(gid2, "310", "小C", plugin.store.messages(gid2, "310"))
    report_ctx = render_mod.report_context(
        profile_c, 66.0, None, group_name="摸鱼小分队", window_days=14
    )
    _check_template("report", tpl_report, report_ctx, {"d", "r", "k", "e", "b"})
    check("心情柱数量", len(report_ctx["mood_bars"]) == 7)

    plugin.settings._raw["image_report"] = True
    result2 = await plugin._run_daily(gid2, deliver=False)
    check(
        "日报能出图",
        bool(
            result2
            and result2.private_image
            and result2.private_image.startswith("data:image/png")
        ),
        str(result2.private_image)[:32] if result2 else "",
    )
    ctx._sent.clear()
    await drain(
        plugin.cmd_daily(
            make_event(
                text="/心理日报",
                group_id=gid2,
                sender_id="900",
                admin=True,
                umo=f"aiocqhttp:GroupMessage:{gid2}",
            )
        )
    )
    check("图片日报已发送", bool(chain_images(ctx._sent)), str(len(ctx._sent)))

    plugin.settings._raw["image_report"] = False
    result3 = await plugin._run_daily(gid2, deliver=False)
    check("关掉图片后回退文本", result3 is not None and result3.private_image is None)

    check("面板接口已注册", any("overview" in r for r in ctx.web_routes), str(list(ctx.web_routes)))
    overview = await plugin.api_overview()
    body = overview.get("data") if isinstance(overview, dict) else None
    check("面板概览可用", bool(body) and body["totals"]["groups"] >= 1, str(overview)[:80])
    check("概览含成员列表", any(g["members"] for g in body["groups"]))

    from astrbot.api.web import request as web_request

    web_request.payload = {"group_id": gid2}
    resp = await plugin.api_daily()
    check(
        "面板生成日报",
        resp.get("status") == "ok" and "日报" in resp["data"]["private"],
        str(resp)[:80],
    )

    web_request.payload = {"group_id": gid2, "enabled": False}
    resp = await plugin.api_toggle()
    check("面板可关闭群", resp["data"]["enabled"] is False)
    web_request.payload = {"group_id": gid2, "enabled": True}
    resp = await plugin.api_toggle()
    check("面板可开启群", resp["data"]["enabled"] is True)
    web_request.payload = {}

    print("\n[气象数据]")
    from core import weather as weather_mod

    w = weather_mod.Weather(date="2026-10-30", tmax=36.2, tmin=26.8, precip=0.0, code=0)
    check("气象摘要", "36℃" in w.summary() and "晴" in w.summary(), w.summary())
    check("高温给出提示", bool(w.tip()))
    pub = daily_mod.render_public(result.stats, result.mood, group_name="g", weather=w)
    check("公开版含气象行", "今日气象" in pub and "36℃" in pub)
    priv = daily_mod.render_private(result.stats, result.mood, group_name="g", weather=w)
    check("私密版含气象提示", "气象提示" in priv)
    ctx = render_mod.daily_context(
        result.stats, result.mood, group_name="g", private=False, weather=w
    )
    check("卡片上下文含气象", (ctx.get("weather") or {}).get("summary", "").startswith("36"))
    check(
        "无气象时不出行",
        "今日气象" not in daily_mod.render_public(result.stats, result.mood, group_name="g"),
    )
    check("低温提示", bool(weather_mod.Weather("d", 2, -3, 0, 0).tip()))

    # ------------------------------------------------------------- 退出
    print("\n[退出与删除]")
    out = await drain(
        plugin.cmd_opt_out(make_event(text="/心理退出", group_id=gid, sender_id=uid))
    )
    check("退出提示", "已停止记录" in out[0].text)
    check("退出后清空", plugin.store.messages(gid, uid) == [])
    await plugin._collect(
        make_event(
            text="我又来了，好难受",
            group_id=gid,
            sender_id=uid,
            umo=f"aiocqhttp:GroupMessage:{gid}",
        )
    )
    check("退出后不再采集", plugin.store.messages(gid, uid) == [])

    # 开关
    out = await drain(
        plugin.cmd_toggle(
            make_event(
                text="/心理开关 off",
                group_id=gid,
                sender_id="900",
                admin=True,
                umo=f"aiocqhttp:GroupMessage:{gid}",
            )
        )
    )
    check("开关可关闭", "关闭" in out[0].text)
    await plugin._collect(
        make_event(
            text="关闭后这条不该被记录",
            group_id=gid,
            sender_id="202",
            umo=f"aiocqhttp:GroupMessage:{gid}",
        )
    )
    check("关闭后停止采集", plugin.store.user(gid, "202") is None)

    # ------------------------------------------------------------- 解析与存储
    print("\n[解析与持久化]")
    parsed = analyzer.parse_llm_json('```json\n{"level": 4, "crisis": true, "summary": "x"}\n```')
    check("能解析代码块 JSON", bool(parsed) and parsed["level"] == 4 and parsed["crisis"])
    check("非法输出返回 None", analyzer.parse_llm_json("模型今天不想说 JSON") is None)
    check("越界值被裁剪", analyzer.parse_llm_json('{"level": 99, "dimensions": {"mood": 9}}')["level"] == 4)

    await plugin.terminate()
    store_file = tmp / "plugin_data" / "astrbot_plugin_psycare" / "store.json"
    check("数据已落盘", store_file.exists())
    raw = json.loads(store_file.read_text(encoding="utf-8"))
    check("落盘结构完整", "groups" in raw and gid in raw["groups"])

    reloaded = Store(store_file.parent, retention_days=30, max_per_user=300)
    reloaded.load()
    check("重新载入成功", bool(reloaded.groups()))

    # 保留期清理
    old = int(time.time()) - 90 * 86400
    group = reloaded.ensure_group("999")
    group["users"]["1"] = {"nick": "老人", "messages": [{"t": old, "x": "旧消息", "i": 0, "d": [], "c": False}], "last_seen": old}
    removed = reloaded.purge()
    check("过期消息被清理", removed == 1, str(removed))

    shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    asyncio.run(run())
    print(f"\n通过 {PASSED} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败项：" + "、".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
