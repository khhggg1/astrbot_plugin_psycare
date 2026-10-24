"""演示「全自动」链路：除了开局激活一次，之后不需要任何命令。

模拟平台把群消息逐条推给插件，打印每一步自动发生了什么：
规则打分 → 入库 → 累计到阈值后自动 LLM 复核 → 关注度超线自动私聊提醒。

运行：python scripts/demo_auto_flow.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "tests" / "stubs")):
    if path not in sys.path:
        sys.path.insert(0, path)

import astrbot_stub_setup  # noqa: F401,E402

from astrbot.api.star import Context  # noqa: E402

import main as plugin_main  # noqa: E402
from core import analyzer  # noqa: E402

LLM_JSON = json.dumps(
    {
        "level": 3,
        "crisis": False,
        "dimensions": {"mood": 3, "anxiety": 2, "worth": 2, "sleep": 3, "withdraw": 2, "stress": 2, "crisis": 0},
        "summary": "连续表达疲惫、失眠与自我否定，社交意愿下降。",
        "advice": "私聊问问最近怎么了，先听不急着给建议。",
        "evidence": ["感觉自己挺没用的"],
    },
    ensure_ascii=False,
)


class Ctx(Context):
    def __init__(self):
        super().__init__()
        self.calls = 0

    async def get_current_chat_provider_id(self, umo: str = "") -> str:
        return "demo-provider"

    async def llm_generate(self, chat_provider_id: str = "", prompt: str = "", **kwargs):
        self.calls += 1

        class _Resp:
            completion_text = LLM_JSON

        return _Resp()


def event(text: str, uid: str, name: str, gid: str = "8888"):
    return plugin_main.AstrMessageEvent(
        text=text,
        group_id=gid,
        sender_id=uid,
        sender_name=name,
        umo=f"aiocqhttp:GroupMessage:{gid}",
    )


async def drain(agen):
    return [x async for x in agen]


async def main() -> None:
    data = ROOT / "build" / "demo-auto"
    shutil.rmtree(data, ignore_errors=True)
    os.environ["ASTRBOT_DATA_PATH"] = str(data)
    ctx = Ctx()
    plugin = plugin_main.PsyCarePlugin(
        ctx,
        {"llm_min_new_messages": 8, "llm_cooldown_minutes": 1, "alert_threshold": 60},
    )
    gid = "8888"

    print("① 管理员私聊绑定一次接收会话（一次性设置）")
    await drain(
        plugin.cmd_bind(
            plugin_main.AstrMessageEvent(
                text="/心理绑定",
                sender_id="900",
                umo="aiocqhttp:FriendMessage:900",
                is_private=True,
            )
        )
    )
    print("   → 已绑定 aiocqhttp:FriendMessage:900\n")

    print("② 管理员在群里发一次 /心理开关 on（唯一的手动开关）")
    out = await drain(
        plugin.cmd_toggle(
            plugin_main.AstrMessageEvent(
                text="/心理开关 on",
                group_id=gid,
                sender_id="900",
                admin=True,
                umo=f"aiocqhttp:GroupMessage:{gid}",
            )
        )
    )
    print("   →", out[0].text.splitlines()[0], "\n")

    print("③ 之后平台每推来一条群消息，插件自动处理：")
    chat = [
        ("200", "小A", "今天加班到十点，好累"),
        ("201", "小B", "哈哈哈哈这个表情包绝了"),
        ("200", "小A", "感觉最近什么都不想做"),
        ("201", "小B", "今晚开黑吗"),
        ("200", "小A", "昨晚又失眠了"),
        ("200", "小A", "不太想说话"),
        ("201", "小B", "我先去吃饭了"),
        ("200", "小A", "我是不是挺没用的"),
        ("200", "小A", "什么都做不好"),
        ("200", "小A", "压力好大，喘不过气"),
        ("201", "小B", "明天见"),
        ("200", "小A", "睡不着，脑子里全是事"),
        ("200", "小A", "谢谢你们陪我"),
        ("200", "小A", "还是觉得挺没意思的"),
    ]
    for uid, name, text in chat:
        await plugin._collect(event(text, uid, name))
        info = analyzer.analyze_text(text)
        stored = len(plugin.store.messages(gid, uid))
        print(
            f"   {name}: {text:<16} 规则 {info.intensity:.0f} 分 "
            f"{'/'.join(info.dims) or '—':<18} 已入库 {stored} 条"
        )

    await asyncio.sleep(0.1)
    print("\n④ 后台自动完成的事：")
    print(f"   调用模型复核 {ctx.calls} 次（累计新消息达到阈值才调用）")
    for uid, name in (("200", "小A"), ("201", "小B")):
        data_i = plugin.store.get_analysis(gid, uid) or {}
        p = analyzer.build_profile(gid, uid, name, plugin.store.messages(gid, uid))
        score = analyzer.combine_score(p, data_i.get("llm"))
        _, _, label, mark = analyzer.level_of(score)
        print(f"   {name}: 关注度 {score:.0f} · {mark} {label} · {p.n_messages} 条消息")

    print("\n⑤ 推出了哪些消息（都在私聊，群里一条没发）：")
    for umo, chain in ctx._sent:
        text = chain.chain[0].text if chain.chain else ""
        print(f"   → {umo}\n     " + text.replace("\n", "\n     ")[:300])

    print("\n⑥ 管理员随时可以拉详情（这一步是手动查，不是自动推送）：")
    out = await drain(
        plugin.cmd_report(
            plugin_main.AstrMessageEvent(
                text="/心理报告",
                group_id=gid,
                sender_id="900",
                admin=True,
                at_targets=["200"],
                umo=f"aiocqhttp:GroupMessage:{gid}",
            )
        )
    )
    print("   →", out[0].text)

    await plugin.terminate()
    shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
