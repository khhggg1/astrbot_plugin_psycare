"""AstrBot 插件：群聊心理关怀（PsyCare）。

被动收集群消息 → 词频规则打分 → 可选的大模型语义复核 → 只把结论推给
管理员私聊，不在群里公开任何人的状态。

命令一览（详见 /心理帮助）：
  /心理状态   /心理退出   /心理加入   /心理遗忘   /心理绑定   /心理解绑
  /心理报告   /心理概览   /心理待办   /心理开关   /心理帮助
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star

try:  # AstrBot >= 4.x
    from astrbot.api import AstrBotConfig
except ImportError:  # pragma: no cover
    AstrBotConfig = dict  # type: ignore[assignment,misc]

try:  # 以包形式被加载（data.plugins.<name>.main）
    from .core import analyzer, daily, lexicon, render
    from .core.config import DISPLAY_NAME, PLUGIN_NAME, Settings
    from .core.store import Store
except ImportError:  # 退化：插件目录可能不在 sys.path 上
    import sys as _sys

    _here = str(Path(__file__).resolve().parent)
    if _here not in _sys.path:
        _sys.path.insert(0, _here)
    from core import analyzer, daily, lexicon, render  # type: ignore[no-redef]
    from core.config import DISPLAY_NAME, PLUGIN_NAME, Settings  # type: ignore[no-redef]
    from core.store import Store  # type: ignore[no-redef]

try:
    import astrbot.api.message_components as Comp
except ImportError:  # pragma: no cover
    Comp = None  # type: ignore[assignment]

try:  # 插件可视化视图的后端接口（AstrBot 4.16+）
    from astrbot.api.web import error_response, json_response, request
except Exception:  # pragma: no cover
    error_response = json_response = request = None  # type: ignore[assignment]

try:
    from astrbot.api.event import MessageChain
except ImportError:  # pragma: no cover
    MessageChain = None  # type: ignore[assignment]

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # pragma: no cover

    def get_astrbot_data_path() -> str:
        return "data"


HELP_TEXT = f"""🧭 {DISPLAY_NAME} · 群聊心理关怀

我在后台安静地看群消息，只做「值得关心的线索」归纳，不做诊断，
也不会在群里公开任何人的状态。

【所有人可用】
/心理状态 —— 看看我记录了你多少消息、当前关注度
/心理退出 —— 停止记录我，并删除已有数据
/心理加入 —— 重新允许记录
/心理遗忘 —— 立刻删除我的全部数据

【管理员可用】
/心理报告 @某人 —— 单人详细报告（发到管理员私聊）
/心理报告 12345678 —— 用 QQ 号查询
/心理概览 —— 本群关注度排行
/心理日报 —— 立刻生成今天的群氛围日报
/心理历史 —— 最近 7 天的日报列表
/心理待办 —— 最近的提醒记录
/心理开关 on|off —— 开启/关闭本群记录（首次使用要先发一次 on）

【接收提醒】
私聊我发送 /心理绑定 ，之后高危提醒和报告都会发到那个私聊会话。

⚠️ 结果来自文本信号，不构成医学诊断。有自伤风险时请直接联系本人、
学校/单位心理老师，或拨打全国心理援助热线 12356。"""


def _split_text(text: str, limit: int = 1200) -> list[str]:
    """按段落切分长文本，避免超过 QQ 单条消息长度限制。"""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks or [text]


class PsyCarePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.settings = Settings(config)
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.store = Store(
            self.data_dir,
            retention_days=self.settings.int("retention_days", minimum=1, maximum=3650),
            max_per_user=self.settings.int("max_messages_per_user", minimum=10, maximum=5000),
        )
        self.store.load()

        self._bg_task: asyncio.Task | None = None
        self._analysis_tasks: set[asyncio.Task] = set()
        self._pending: dict[tuple[str, str], int] = {}
        self._last_llm: dict[tuple[str, str], float] = {}
        self._running: set[tuple[str, str]] = set()
        self._care_sent: dict[tuple[str, str], float] = {}
        self._provider_warned = False
        self._daily_warned = False
        self._t2i_warned = False
        logger.info(
            f"[{PLUGIN_NAME}] 已加载：词条 {lexicon.TERM_COUNT} 条，"
            f"数据目录 {self.data_dir}"
        )
        self._register_web_api()
        self._ensure_bg()

    # ------------------------------------------------------------------
    # 生命周期与后台任务
    # ------------------------------------------------------------------
    def _ensure_bg(self) -> None:
        if self._bg_task and not self._bg_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # 不在事件循环里（例如单元测试同步调用）
            return
        self._bg_task = loop.create_task(self._bg_loop())

    async def _bg_loop(self) -> None:
        # 启动后先等一小会儿再跑，避开 AstrBot 初始化
        first = True
        while True:
            try:
                await asyncio.sleep(20 if first else 60)
                first = False
                removed = self.store.purge()
                if removed:
                    logger.info(f"[{PLUGIN_NAME}] 清理过期消息 {removed} 条")
                await self._maybe_daily()
                await self.store.save()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 后台任务不能因为一次异常就死掉
                logger.warning(f"[{PLUGIN_NAME}] 后台任务异常：{exc}")

    async def terminate(self) -> None:
        for task in list(self._analysis_tasks):
            task.cancel()
        self._analysis_tasks.clear()
        if self._bg_task:
            self._bg_task.cancel()
            self._bg_task = None
        try:
            await self.store.save(force=True)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[{PLUGIN_NAME}] 退出时保存失败：{exc}")

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    @staticmethod
    def _call(event: AstrMessageEvent, name: str, default: Any = None) -> Any:
        fn = getattr(event, name, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                return default
        return getattr(event, name, default)

    def _is_private(self, event: AstrMessageEvent) -> bool:
        fn = getattr(event, "is_private_chat", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                pass
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        return "friend" in umo.lower() or "private" in umo.lower()

    @staticmethod
    def _looks_like_command(text: str) -> bool:
        return bool(
            re.match(r"^\s*[/／!！]", text)
            or re.match(
                r"^\s*心理(报告|概览|状态|退出|加入|遗忘|绑定|解绑|帮助|待办|开关)",
                text,
            )
        )

    def _group_name(self, group_id: str) -> str:
        group = self.store.group(group_id) or {}
        return str(group.get("name") or group_id)

    async def _send_text(self, umo: str, text: str) -> bool:
        if not umo:
            return False
        try:
            for chunk in _split_text(text):
                if MessageChain is None:
                    await self.context.send_message(umo, chunk)
                else:
                    await self.context.send_message(umo, MessageChain().message(chunk))
            return True
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 发送消息到 {umo} 失败：{exc}")
            return False

    def _alert_umo(self) -> str:
        return str(self.store.get_setting("alert_umo") or "")

    # --- 图片卡片 -------------------------------------------------------
    def _image_component(self, url: str):
        """把 html_render 的返回值转成 Image 组件（兼容 URL / data URI / 本地路径）。"""
        if not url or Comp is None:
            return None
        try:
            if url.startswith(("http://", "https://")):
                return Comp.Image.fromURL(url)
            if url.startswith("data:image/"):
                return Comp.Image.fromBase64(url.split(",", 1)[1])
            if url.startswith("file://"):
                return Comp.Image.fromFileSystem(url[7:])
            return Comp.Image.fromFileSystem(url)
        except Exception as exc:
            logger.info(f"[{PLUGIN_NAME}] 图片组件构造失败：{exc}")
            return None

    async def _render_card(self, template: str, data: dict) -> str | None:
        """用 AstrBot 的 T2I 把模板渲染成图片；不可用时静默返回 None。"""
        if not self.settings.bool("image_report"):
            return None
        tmpl = render.load_template(template)
        if not tmpl:
            return None
        fn = getattr(self, "html_render", None)
        if not callable(fn):
            fn = getattr(self.context, "html_render", None)
        if not callable(fn):
            return None
        try:
            result = await fn(
                tmpl,
                data,
                options={"type": "png", "full_page": True, "timeout": 30000},
            )
        except Exception as exc:
            if not self._t2i_warned:
                logger.info(
                    f"[{PLUGIN_NAME}] 图片渲染不可用，报告将退回文本（{exc}）。"
                    "如需图片，请在 AstrBot 配置里启用文转图 / T2I。"
                )
                self._t2i_warned = True
            return None
        return str(result) if result else None

    async def _send_rich(self, umo: str, text: str, image_url: str | None = None) -> bool:
        """优先发图片，失败则发文本。"""
        if not umo:
            return False
        comp = self._image_component(image_url) if image_url else None
        if comp is not None:
            try:
                chain = MessageChain([comp]) if MessageChain is not None else [comp]
                await self.context.send_message(umo, chain)
                return True
            except Exception as exc:
                logger.warning(f"[{PLUGIN_NAME}] 图片发送失败，改发文本：{exc}")
        return await self._send_text(umo, text)

    async def _deliver_rich(
        self, event: AstrMessageEvent, text: str, image_url: str | None = None
    ) -> tuple[str, Any]:
        """敏感内容分发；返回 ("text"|"image", 载荷)。"""
        comp = self._image_component(image_url) if image_url else None
        if self._is_private(event) or self.settings.bool("allow_group_report"):
            return ("image", comp) if comp is not None else ("text", text)
        umo = self._alert_umo()
        if umo:
            ok = await self._send_rich(umo, text, image_url)
            return (
                "text",
                "📨 报告已发送到你绑定的私聊会话。"
                if ok
                else "⚠️ 发送到绑定会话失败，可能是会话已失效，请重新 /心理绑定。",
            )
        return (
            "text",
            "🔒 为保护群友隐私，报告不在这里展开。\n"
            "请私聊我发送 /心理绑定 绑定接收会话，再回来查询。",
        )

    async def _notify_admin(self, text: str) -> bool:
        umo = self._alert_umo()
        if not umo:
            logger.info(
                f"[{PLUGIN_NAME}] 未绑定提醒接收人，本条提醒只写入本地记录。"
                "管理员可私聊机器人发送 /心理绑定 来接收。"
            )
            return False
        return await self._send_text(umo, text)

    async def _deliver_sensitive(self, event: AstrMessageEvent, text: str) -> str:
        """敏感内容：私聊、或管理员绑定的会话；否则只回提示。"""
        if self._is_private(event):
            return text
        if self.settings.bool("allow_group_report"):
            return text
        umo = self._alert_umo()
        if umo:
            ok = await self._send_text(umo, text)
            return (
                "📨 报告已发送到你绑定的私聊会话。"
                if ok
                else "⚠️ 发送到绑定会话失败，可能是会话已失效，请重新 /心理绑定。"
            )
        return (
            "🔒 为保护群友隐私，报告不在这里展开。\n"
            "请私聊我发送 /心理绑定 绑定接收会话，再回来查询。"
        )

    def _guess_group(self, user_id: str) -> str:
        candidates = []
        for gid, group in self.store.groups().items():
            user = (group.get("users") or {}).get(user_id)
            if isinstance(user, dict):
                candidates.append((int(user.get("last_seen", 0)), gid))
        if candidates:
            return max(candidates)[1]
        groups = list(self.store.groups().keys())
        return groups[0] if len(groups) == 1 else ""

    def _resolve_target(self, event: AstrMessageEvent) -> tuple[str, str]:
        """从 @ 或消息里的 QQ 号解析目标；默认是发送者自己。"""
        self_id = str(self._call(event, "get_self_id", "") or "")
        try:
            messages = event.get_messages()
        except Exception:
            messages = []
        for seg in messages or []:
            qq = getattr(seg, "qq", None)
            if qq is None or seg.__class__.__name__ != "At":
                continue
            qq_s = str(qq)
            if qq_s in {"", "0", "all", self_id}:
                continue
            return qq_s, "群友"
        match = re.search(r"(?<!\d)(\d{5,12})(?!\d)", str(getattr(event, "message_str", "") or ""))
        if match:
            return match.group(1), "群友"
        sender = str(self._call(event, "get_sender_id", "") or "")
        return sender, str(self._call(event, "get_sender_name", "") or "自己")

    def _admin_hint(self) -> str:
        return (
            "这个命令需要机器人管理员权限。\n"
            "如果你只是想知道自己的情况，可以用 /心理状态。"
        )

    # ------------------------------------------------------------------
    # 被动采集
    # ------------------------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        try:
            await self._collect(event)
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 采集消息失败：{exc}")

    async def _collect(self, event: AstrMessageEvent) -> None:
        settings = self.settings
        if not settings.bool("enabled"):
            return

        text = str(getattr(event, "message_str", "") or "").strip()
        if len(text) < settings.int("min_message_length", minimum=1, maximum=50):
            return
        if self._looks_like_command(text):
            return

        group_id = str(self._call(event, "get_group_id", "") or "")
        user_id = str(self._call(event, "get_sender_id", "") or "")
        if not group_id or not user_id:
            return
        self_id = str(self._call(event, "get_self_id", "") or "")
        if self_id and user_id == self_id:
            return

        if settings.bool("group_whitelist_enabled"):
            allowed = [str(x).strip() for x in (settings.get("group_whitelist") or [])]
            if group_id not in allowed:
                return

        umo = str(getattr(event, "unified_msg_origin", "") or "")
        group = self.store.ensure_group(group_id, umo)
        if not group.get("enabled", True):
            return
        # 默认要求群管理员先执行一次 /心理开关 on，避免机器人一装上就全群静默分析
        if settings.bool("require_group_activation") and not group.get("activated"):
            return

        nickname = str(self._call(event, "get_sender_name", "") or user_id)
        user = self.store.ensure_user(group_id, user_id, nickname)
        if user.get("opt_out"):
            return
        if settings.bool("require_opt_in") and user.get("opt_in") is not True:
            return

        if settings.bool("keyword_analysis"):
            signal = analyzer.analyze_text(text)
        else:
            # 只靠大模型审查时，规则层不参与打分，但仍保留原始文本供模型阅读
            signal = analyzer.Signal(
                ts=int(time.time()), text=text[:300], intensity=0.0
            )
        self.store.record(group_id, user_id, nickname, signal)
        self._ensure_bg()

        if signal.crisis:
            await self._crisis_alert(group_id, user_id, nickname)

        if settings.bool("llm_analysis"):
            await self._maybe_analyze(group_id, user_id)

    # ------------------------------------------------------------------
    # 分析调度
    # ------------------------------------------------------------------
    async def _maybe_analyze(self, group_id: str, user_id: str, *, force: bool = False) -> None:
        key = (group_id, user_id)
        self._pending[key] = self._pending.get(key, 0) + 1
        if not force:
            if self._pending[key] < self.settings.int("llm_min_new_messages", minimum=1, maximum=200):
                return
            cooldown = self.settings.int("llm_cooldown_minutes", minimum=1, maximum=10080) * 60
            if time.time() - self._last_llm.get(key, 0) < cooldown:
                return
        if key in self._running:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        # 先占位再建任务：任务真正开始执行前，后面几条消息仍会进到这里，
        # 不占位就会给同一个人并发排好几个分析任务。
        self._running.add(key)
        self._pending[key] = 0
        task = loop.create_task(self._run_analysis(group_id, user_id, force=force))
        self._analysis_tasks.add(task)
        task.add_done_callback(self._analysis_tasks.discard)
        task.add_done_callback(lambda _t: self._running.discard(key))

    async def _run_analysis(self, group_id: str, user_id: str, *, force: bool = False) -> None:
        key = (group_id, user_id)
        try:
            user = self.store.user(group_id, user_id) or {}
            nickname = str(user.get("nick") or user_id)
            messages = self.store.messages(group_id, user_id)
            profile = analyzer.build_profile(
                group_id,
                user_id,
                nickname,
                messages,
                window_days=self.settings.int("window_days", minimum=1, maximum=365),
                include_quotes=self.settings.bool("include_quotes"),
            )
            if profile.n_messages < 3:
                return
            llm, ok = await self._llm_analyze(profile, messages)
            score = analyzer.combine_score(profile, llm)
            self.store.set_analysis(
                group_id,
                user_id,
                {
                    "ts": int(time.time()),
                    "score": score,
                    "level": analyzer.level_of(score)[1],
                    "llm": llm,
                    "llm_ok": ok,
                },
            )
            await self.store.save()
            if llm is None and not ok and profile.rule_score < 1:
                return
            threshold = self.settings.int("alert_threshold", minimum=0, maximum=100)
            if self.settings.bool("alert_enabled") and score >= threshold:
                reason = (
                    "综合关注度超过阈值"
                    if llm
                    else "规则指标超过阈值（AI 复核未启用或不可用）"
                )
                await self._raise_alert(group_id, user_id, score, reason, profile, llm)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 分析 {user_id} 失败：{exc}")
        finally:
            # 不在这里清 _pending：分析期间新到的消息要计入下一轮
            self._last_llm[key] = time.time()

    async def _llm_analyze(self, profile, messages: list[dict]) -> tuple[dict | None, bool]:
        """返回 (解析后的结果, 是否成功调用)。"""
        if not self.settings.bool("llm_analysis"):
            return None, False
        group = self.store.group(profile.group_id) or {}
        umo = str(group.get("umo") or "")
        provider_id = ""
        try:
            provider_id = await self.context.get_current_chat_provider_id(umo=umo) or ""
        except Exception as exc:
            if not self._provider_warned:
                logger.info(f"[{PLUGIN_NAME}] 未能获取模型提供商，退回规则模式：{exc}")
                self._provider_warned = True
            return None, False
        if not provider_id:
            return None, False

        prompt = (
            analyzer.SYSTEM_RULES
            + "\n\n"
            + analyzer.build_llm_prompt(
                profile,
                messages,
                self.settings.int("window_days", minimum=1, maximum=365),
            )
        )
        try:
            resp = await asyncio.wait_for(
                self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt),
                timeout=90,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{PLUGIN_NAME}] 模型响应超时（{profile.user_id}）")
            return None, False
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 模型调用失败：{exc}")
            return None, False

        raw = getattr(resp, "completion_text", "") or ""
        parsed = analyzer.parse_llm_json(raw)
        if parsed is None:
            logger.info(f"[{PLUGIN_NAME}] 模型输出无法解析为 JSON，忽略本次结果")
            return None, False
        return parsed, True

    # ------------------------------------------------------------------
    # 提醒
    # ------------------------------------------------------------------
    async def _crisis_alert(self, group_id: str, user_id: str, nickname: str) -> None:
        if not self.settings.bool("alert_enabled"):
            return
        if self.store.recent_alert(group_id, user_id, 6 * 3600):
            return
        profile = analyzer.build_profile(
            group_id,
            user_id,
            nickname,
            self.store.messages(group_id, user_id),
            window_days=self.settings.int("window_days", minimum=1, maximum=365),
        )
        score = max(92.0, analyzer.combine_score(profile, None))
        await self._raise_alert(group_id, user_id, score, "出现危机类表达（规则命中）", profile, None)
        await self._care_message(group_id, user_id, nickname)

    async def _care_message(self, group_id: str, user_id: str, nickname: str) -> None:
        if not self.settings.bool("care_message_enabled"):
            return
        key = (group_id, user_id)
        if time.time() - self._care_sent.get(key, 0) < 24 * 3600:
            return
        group = self.store.group(group_id) or {}
        umo = str(group.get("umo") or "")
        if not umo:
            return
        body = self.settings.text("care_message").strip()
        if not body:
            return
        self._care_sent[key] = time.time()
        await self._send_text(umo, f"{nickname}，{body}")

    async def _raise_alert(
        self,
        group_id: str,
        user_id: str,
        score: float,
        reason: str,
        profile,
        llm: dict | None,
    ) -> None:
        cooldown = self.settings.int("alert_cooldown_hours", minimum=1, maximum=720) * 3600
        if self.store.recent_alert(group_id, user_id, cooldown):
            return
        text = analyzer.render_alert(
            profile,
            score,
            llm,
            group_name=self._group_name(group_id),
            reason=reason,
        )
        self.store.add_alert(
            {
                "ts": int(time.time()),
                "gid": group_id,
                "uid": user_id,
                "nick": profile.nickname,
                "score": round(score, 1),
                "level": analyzer.level_of(score)[1],
                "reason": reason,
            }
        )
        await self.store.save()
        await self._notify_admin(text)

    # ------------------------------------------------------------------
    # 每日群氛围日报
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_daily_time(raw: str) -> tuple[int, int]:
        match = re.fullmatch(r"\s*(\d{1,2})\s*[:：]\s*(\d{1,2})\s*", raw or "")
        if not match:
            return 21, 30
        hour, minute = int(match.group(1)), int(match.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return 21, 30
        return hour, minute

    def _daily_window(self) -> tuple[int, int, str]:
        """当天 00:00 到现在。"""
        now = datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp()), int(time.time()), now.strftime("%Y-%m-%d")

    def _should_run_daily(self, now: datetime) -> bool:
        """到点了吗、今天跑过了吗。抽出来是为了能脱离真实时钟测试。"""
        if not self.settings.bool("daily_enabled"):
            return False
        hour, minute = self._parse_daily_time(self.settings.text("daily_time"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < target:
            return False
        # 机器人重启/离线后补跑，但只补 6 小时内的，避免半夜突然刷一堆日报
        if (now - target).total_seconds() > 6 * 3600:
            return False
        return self.store.get_setting("daily_last_date") != now.strftime("%Y-%m-%d")

    async def _maybe_daily(self) -> None:
        """后台每分钟调用一次：到点、且今天没跑过，就跑一遍所有已开启的群。"""
        now = datetime.now()
        if not self._should_run_daily(now):
            return
        self.store.set_setting("daily_last_date", now.strftime("%Y-%m-%d"))
        await self.store.save()

        for group_id, group in list(self.store.groups().items()):
            if not group.get("activated") or not group.get("enabled", True):
                continue
            if not group.get("umo"):
                continue
            try:
                await self._run_daily(group_id)
            except Exception as exc:
                logger.warning(f"[{PLUGIN_NAME}] 生成日报失败（{group_id}）：{exc}")

    async def _run_daily(
        self, group_id: str, *, deliver: bool = True, with_image: bool = True
    ) -> daily.DailyResult | None:
        """生成一天的日报；数据不足或没绑定接收人时返回 None。"""
        group = self.store.group(group_id) or {}
        start, end, date = self._daily_window()
        target_mode = self.settings.text("daily_target") or "private"
        need_private = target_mode in {"private", "both"}
        need_group = target_mode in {"group", "both"}

        if need_private and not self._alert_umo():
            if not self._daily_warned:
                logger.info(
                    f"[{PLUGIN_NAME}] 日报已开启但还没有绑定接收人，"
                    "先私聊机器人发送 /心理绑定。"
                )
                self._daily_warned = True
            return None

        stats = daily.collect(
            self.store.users(group_id),
            group_id,
            start_ts=start,
            end_ts=end + 1,
        )
        if not stats.has_data:
            return None
        if stats.messages < self.settings.int("daily_min_messages", minimum=1, maximum=1000):
            return None

        name = self._group_name(group_id)
        mood = None
        if self.settings.bool("llm_analysis") and stats.samples:
            mood = await self._daily_llm(group, stats, name)
        if mood is None:
            mood = daily.fallback_mood(stats)

        public = daily.render_public(stats, mood, group_name=name)
        private = daily.render_private(stats, mood, group_name=name)
        result = daily.DailyResult(public=public, private=private, stats=stats, mood=mood)

        if with_image and self.settings.bool("image_report"):
            result.public_image = await self._render_card(
                "daily_card.html",
                render.daily_context(stats, mood, group_name=name, private=False),
            )
            result.private_image = await self._render_card(
                "daily_card.html",
                render.daily_context(stats, mood, group_name=name, private=True),
            )

        self.store.set_daily(
            group_id,
            date,
            {
                "date": date,
                "ts": int(time.time()),
                "messages": stats.messages,
                "users": stats.users,
                "avg_intensity": stats.avg_intensity,
                "mood": mood,
                "public": public,
                "private": private,
                "risks": [
                    {"uid": r.user_id, "nick": r.nickname, "score": round(r.score, 1)}
                    for r in stats.risks[:10]
                ],
            },
        )
        await self.store.save()

        if deliver:
            group_umo = str(group.get("umo") or "")
            if need_group and group_umo:
                await self._send_rich(group_umo, public, result.public_image)
            if need_private:
                await self._send_rich(self._alert_umo(), private, result.private_image)
        return result

    async def _daily_llm(self, group: dict, stats, group_name: str) -> dict | None:
        umo = str(group.get("umo") or "")
        try:
            provider_id = await self.context.get_current_chat_provider_id(umo=umo) or ""
        except Exception:
            return None
        if not provider_id:
            return None
        prompt = daily.DAILY_SYSTEM + "\n\n" + daily.build_prompt(stats, group_name)
        try:
            resp = await asyncio.wait_for(
                self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt),
                timeout=90,
            )
        except Exception as exc:  # 超时、网络错误都走兜底文案
            logger.warning(f"[{PLUGIN_NAME}] 日报模型调用失败：{exc}")
            return None
        return daily.parse_daily_json(getattr(resp, "completion_text", "") or "")

    # ------------------------------------------------------------------
    # WebUI 面板接口（views/dashboard）
    # ------------------------------------------------------------------
    def _register_web_api(self) -> None:
        register = getattr(self.context, "register_web_api", None)
        if not callable(register) or json_response is None:
            logger.info(
                f"[{PLUGIN_NAME}] 当前 AstrBot 不支持插件 Web API，跳过面板注册"
            )
            return
        routes = [
            (f"/{PLUGIN_NAME}/overview", self.api_overview, ["GET"], "心理关怀面板概览"),
            (f"/{PLUGIN_NAME}/report", self.api_report, ["GET"], "单人报告"),
            (f"/{PLUGIN_NAME}/daily", self.api_daily, ["POST"], "生成日报"),
            (f"/{PLUGIN_NAME}/toggle", self.api_toggle, ["POST"], "开关群记录"),
        ]
        for route, handler, methods, desc in routes:
            try:
                register(route, handler, methods, desc)
            except Exception as exc:  # 旧版本 AstrBot 没有这个接口
                logger.warning(f"[{PLUGIN_NAME}] 注册面板接口 {route} 失败：{exc}")

    def _panel_payload(self) -> dict:
        window = self.settings.int("window_days", minimum=1, maximum=365)
        day_start = int(
            datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        groups = []
        for group_id, group in self.store.groups().items():
            users = group.get("users") or {}
            members = []
            today_messages = 0
            risky = 0
            for uid, user in users.items():
                if not isinstance(user, dict) or user.get("opt_out"):
                    continue
                msgs = user.get("messages") or []
                today = sum(1 for m in msgs if int(m.get("t", 0)) >= day_start)
                today_messages += today
                if not msgs:
                    continue
                profile = analyzer.build_profile(
                    group_id,
                    uid,
                    str(user.get("nick") or uid),
                    msgs,
                    window_days=window,
                    include_quotes=False,
                )
                if profile.n_messages == 0:
                    continue
                stored = self.store.get_analysis(group_id, uid) or {}
                score = analyzer.combine_score(profile, stored.get("llm"))
                _, key, label, _ = analyzer.level_of(score)
                if score >= 50:
                    risky += 1
                members.append(
                    {
                        "uid": uid,
                        "nick": str(user.get("nick") or uid),
                        "score": round(score, 1),
                        "level": key,
                        "level_label": label,
                        "messages": profile.n_messages,
                        "today": today,
                        "last_ts": profile.last_ts,
                    }
                )
            members.sort(key=lambda m: -m["score"])
            last = self.store.daily_reports(group_id, limit=1)
            groups.append(
                {
                    "id": group_id,
                    "name": self._group_name(group_id),
                    "activated": bool(group.get("activated")),
                    "enabled": bool(group.get("enabled", True)),
                    "today_messages": today_messages,
                    "member_total": len(members),
                    "risk_total": risky,
                    "members": members[:30],
                    "last_daily": last[0] if last else None,
                }
            )
        groups.sort(key=lambda g: -g["today_messages"])

        recent_alerts = self.store.alerts(None, limit=10)
        return {
            "generated_at": int(time.time()),
            "window_days": window,
            "alert_bound": bool(self._alert_umo()),
            "totals": {
                "groups": len(groups),
                "members": sum(g["member_total"] for g in groups),
                "today_messages": sum(g["today_messages"] for g in groups),
                "risk_total": sum(g["risk_total"] for g in groups),
                "daily_reports": sum(
                    len(self.store.daily_reports(g["id"], limit=99)) for g in groups
                ),
            },
            "groups": groups,
            "alerts": [
                {
                    "ts": int(a.get("ts", 0)),
                    "gid": a.get("gid"),
                    "uid": a.get("uid"),
                    "nick": a.get("nick"),
                    "score": a.get("score"),
                    "level": a.get("level"),
                    "reason": a.get("reason"),
                }
                for a in recent_alerts
            ],
        }

    async def api_overview(self):
        if json_response is None:
            return None
        try:
            return json_response(self._panel_payload())
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 面板概览失败：{exc}")
            return error_response(f"生成概览失败：{exc}", status_code=500)

    async def api_report(self):
        if json_response is None or request is None:
            return None
        group_id = str(request.query.get("group_id", ""))
        uid = str(request.query.get("uid", ""))
        if not group_id or not uid:
            return error_response("缺少 group_id 或 uid", status_code=400)
        user = self.store.user(group_id, uid)
        if not user:
            return error_response("没有找到该成员的记录", status_code=404)
        window = self.settings.int("window_days", minimum=1, maximum=365)
        profile = analyzer.build_profile(
            group_id,
            uid,
            str(user.get("nick") or uid),
            self.store.messages(group_id, uid),
            window_days=window,
            include_quotes=self.settings.bool("include_quotes"),
        )
        stored = self.store.get_analysis(group_id, uid) or {}
        score = analyzer.combine_score(profile, stored.get("llm"))
        text = analyzer.render_report(
            profile,
            score,
            stored.get("llm"),
            group_name=self._group_name(group_id),
            window_days=window,
        )
        return json_response(
            {
                "text": text,
                "score": round(score, 1),
                "level": analyzer.level_of(score)[2],
                "uid": uid,
                "nick": str(user.get("nick") or uid),
            }
        )

    async def api_daily(self):
        if json_response is None or request is None:
            return None
        payload = await request.json(default={})
        group_id = str((payload or {}).get("group_id") or "")
        if not group_id or not self.store.group(group_id):
            return error_response("未知的群", status_code=400)
        result = await self._run_daily(group_id, deliver=False, with_image=False)
        if result is None:
            return error_response(
                "今天的数据不足，或还没有绑定接收人（/心理绑定）", status_code=409
            )
        return json_response(
            {
                "public": result.public,
                "private": result.private,
                "date": result.stats.date if result.stats else "",
                "mood": result.mood,
            }
        )

    async def api_toggle(self):
        if json_response is None or request is None:
            return None
        payload = await request.json(default={})
        group_id = str((payload or {}).get("group_id") or "")
        enabled = bool((payload or {}).get("enabled"))
        if not group_id:
            return error_response("缺少 group_id", status_code=400)
        group = self.store.group(group_id) or self.store.ensure_group(group_id)
        if enabled:
            group["activated"] = True
            group["enabled"] = True
        else:
            group["enabled"] = False
        self.store.mark_dirty()
        await self.store.save(force=True)
        return json_response(
            {"ok": True, "group_id": group_id, "enabled": bool(group.get("enabled"))}
        )

    # ------------------------------------------------------------------
    # 命令：所有人
    # ------------------------------------------------------------------
    @filter.command("心理帮助", alias={"心理", "心理指令"})
    async def cmd_help(self, event: AstrMessageEvent):
        """查看心理关怀插件的用法。"""
        yield event.plain_result(HELP_TEXT)

    @filter.command("心理状态")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看自己被记录的数据量和当前关注度。"""
        group_id = str(self._call(event, "get_group_id", "") or "")
        user_id = str(self._call(event, "get_sender_id", "") or "")
        if not user_id:
            yield event.plain_result("没能识别到你的账号信息。")
            return
        if not group_id:
            group_id = self._guess_group(user_id)
        user = self.store.user(group_id, user_id) if group_id else None
        if not user:
            yield event.plain_result("我还没有记录过你的消息。")
            return
        if user.get("opt_out"):
            yield event.plain_result("你已经退出记录，我没有保存你的任何消息。")
            return
        profile = analyzer.build_profile(
            group_id,
            user_id,
            str(user.get("nick") or user_id),
            self.store.messages(group_id, user_id),
            window_days=self.settings.int("window_days", minimum=1, maximum=365),
            include_quotes=False,
        )
        score = analyzer.combine_score(profile, None)
        _, _, label, mark = analyzer.level_of(score)
        text = (
            f"🧭 你的记录情况（{self._group_name(group_id)}）\n"
            f"近 {self.settings.int('window_days')} 天：{profile.n_messages} 条消息，"
            f"活跃 {profile.active_days} 天\n"
            f"关注度：{mark} {label} · {score:.0f}/100（仅供参考）\n"
            f"数据保存在机器人本地，可用 /心理退出 停止记录并删除。"
        )
        yield event.plain_result(text)

    @filter.command("心理退出")
    async def cmd_opt_out(self, event: AstrMessageEvent):
        """停止记录我，并删除已保存的消息。"""
        group_id = str(self._call(event, "get_group_id", "") or "")
        user_id = str(self._call(event, "get_sender_id", "") or "")
        if not group_id:
            group_id = self._guess_group(user_id)
        if not group_id or not user_id:
            yield event.plain_result("没能识别到群聊或账号信息。")
            return
        self.store.ensure_user(group_id, user_id, str(self._call(event, "get_sender_name", "") or ""))
        self.store.set_opt_out(group_id, user_id, True)
        await self.store.save()
        yield event.plain_result(
            "✅ 已停止记录你，并清空了已保存的消息。\n再次使用 /心理加入 可以恢复。"
        )

    @filter.command("心理加入")
    async def cmd_opt_in(self, event: AstrMessageEvent):
        """恢复记录。"""
        group_id = str(self._call(event, "get_group_id", "") or "")
        user_id = str(self._call(event, "get_sender_id", "") or "")
        if not group_id:
            group_id = self._guess_group(user_id)
        if not group_id or not user_id:
            yield event.plain_result("没能识别到群聊或账号信息。")
            return
        self.store.ensure_user(group_id, user_id, str(self._call(event, "get_sender_name", "") or ""))
        self.store.set_opt_in(group_id, user_id, True)
        await self.store.save()
        yield event.plain_result("✅ 已恢复记录。你仍然可以随时用 /心理退出 关闭。")

    @filter.command("心理遗忘")
    async def cmd_forget(self, event: AstrMessageEvent):
        """删除自己的全部数据（管理员可指定他人）。"""
        group_id = str(self._call(event, "get_group_id", "") or "")
        sender = str(self._call(event, "get_sender_id", "") or "")
        target, _ = self._resolve_target(event)
        is_admin = False
        try:
            is_admin = bool(await self._is_admin(event))
        except Exception:
            is_admin = False
        if target and target != sender and not is_admin:
            yield event.plain_result("只有管理员可以删除他人的数据。")
            return
        if not group_id:
            group_id = self._guess_group(target)
        if not group_id or not target:
            yield event.plain_result("没能识别到要删除的记录。")
            return
        removed = self.store.delete_user(group_id, target)
        await self.store.save(force=True)
        if removed:
            yield event.plain_result(f"🗑 已删除 {target} 在本群的记录。")
        else:
            yield event.plain_result("没有找到对应的记录。")

    async def _is_admin(self, event: AstrMessageEvent) -> bool:
        fn = getattr(event, "is_admin", None)
        if callable(fn):
            try:
                result = fn()
                if asyncio.iscoroutine(result):
                    result = await result
                return bool(result)
            except Exception:
                return False
        return False

    # ------------------------------------------------------------------
    # 命令：接收人绑定
    # ------------------------------------------------------------------
    @filter.command("心理绑定")
    async def cmd_bind(self, event: AstrMessageEvent):
        """在私聊中绑定接收提醒与报告的会话。"""
        if not self._is_private(event):
            yield event.plain_result("请在私聊里发送 /心理绑定 ，不要把提醒发到群里。")
            return
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        if not umo:
            yield event.plain_result("没能识别到会话信息。")
            return
        self.store.set_setting("alert_umo", umo)
        await self.store.save(force=True)
        yield event.plain_result(
            "✅ 已绑定。之后的高危提醒和 /心理报告 都会发到这个私聊。\n"
            "取消绑定：/心理解绑"
        )

    @filter.command("心理解绑")
    async def cmd_unbind(self, event: AstrMessageEvent):
        """取消接收提醒。"""
        if not self._is_private(event):
            yield event.plain_result("请在私聊里操作。")
            return
        self.store.set_setting("alert_umo", "")
        await self.store.save(force=True)
        yield event.plain_result("已解绑，不会再向你推送提醒。")

    # ------------------------------------------------------------------
    # 命令：管理员
    # ------------------------------------------------------------------
    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("心理报告")
    async def cmd_report(self, event: AstrMessageEvent):
        """生成某位群友的详细关注度报告（管理员）。"""
        group_id = str(self._call(event, "get_group_id", "") or "")
        target, _ = self._resolve_target(event)
        if not target:
            yield event.plain_result("用法：/心理报告 @某人 或 /心理报告 QQ号")
            return
        if not group_id:
            group_id = self._guess_group(target)
        if not group_id:
            yield event.plain_result("无法确定要查询的群，请在群内使用，或先让管理员绑定。")
            return

        user = self.store.user(group_id, target)
        if not user:
            yield event.plain_result(f"没有找到 {target} 在本群的记录。")
            return
        messages = self.store.messages(group_id, target)
        profile = analyzer.build_profile(
            group_id,
            target,
            str(user.get("nick") or target),
            messages,
            window_days=self.settings.int("window_days", minimum=1, maximum=365),
            include_quotes=self.settings.bool("include_quotes"),
        )
        llm, llm_ok = (None, False)
        if self.settings.bool("llm_analysis") and profile.n_messages >= 3:
            llm, llm_ok = await self._llm_analyze(profile, messages)
        score = analyzer.combine_score(profile, llm)
        report = analyzer.render_report(
            profile,
            score,
            llm,
            group_name=self._group_name(group_id),
            window_days=self.settings.int("window_days"),
            llm_failed=not llm_ok and self.settings.bool("llm_analysis"),
        )
        image_url = None
        if profile.n_messages:
            image_url = await self._render_card(
                "report_card.html",
                render.report_context(
                    profile,
                    score,
                    llm,
                    group_name=self._group_name(group_id),
                    window_days=self.settings.int("window_days"),
                ),
            )
        kind, payload = await self._deliver_rich(event, report, image_url)
        if kind == "image":
            yield event.chain_result([payload])
        else:
            yield event.plain_result(payload)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("心理概览")
    async def cmd_overview(self, event: AstrMessageEvent):
        """本群关注度排行（管理员）。"""
        group_id = str(self._call(event, "get_group_id", "") or "")
        if not group_id:
            group_id = self._guess_group(str(self._call(event, "get_sender_id", "") or ""))
        if not group_id:
            yield event.plain_result("请在群内使用这个命令。")
            return
        window = self.settings.int("window_days", minimum=1, maximum=365)
        rows = []
        for uid, user in self.store.users(group_id).items():
            if user.get("opt_out"):
                continue
            profile = analyzer.build_profile(
                group_id,
                uid,
                str(user.get("nick") or uid),
                user.get("messages") or [],
                window_days=window,
                include_quotes=False,
            )
            if profile.n_messages == 0:
                continue
            rows.append((profile, analyzer.combine_score(profile, (self.store.get_analysis(group_id, uid) or {}).get("llm"))))
        text = analyzer.render_overview(
            self._group_name(group_id),
            sorted(rows, key=lambda x: x[1], reverse=True),
            window_days=window,
            total_messages=self.store.message_count(group_id),
        )
        yield event.plain_result(await self._deliver_sensitive(event, text))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("心理日报")
    async def cmd_daily(self, event: AstrMessageEvent):
        """立刻生成今天的群氛围日报（管理员）。"""
        group_id = str(self._call(event, "get_group_id", "") or "")
        if not group_id:
            group_id = self._guess_group(str(self._call(event, "get_sender_id", "") or ""))
        if not group_id:
            yield event.plain_result("请在群内使用，或先在群里发过言。")
            return

        result = await self._run_daily(group_id, deliver=False)
        if result is None:
            yield event.plain_result(
                "今天的数据还不够生成日报。\n"
                "可能原因：消息太少、本群未开启、或还没绑定接收会话（/心理绑定）。"
            )
            return
        mode = self.settings.text("daily_target") or "private"
        if mode in {"group", "both"} and self._call(event, "get_group_id", ""):
            group_umo = str((self.store.group(group_id) or {}).get("umo") or "")
            if group_umo:
                await self._send_rich(group_umo, result.public, result.public_image)
        kind, payload = await self._deliver_rich(event, result.private, result.private_image)
        if kind == "image":
            yield event.chain_result([payload])
        else:
            yield event.plain_result(payload)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("心理历史")
    async def cmd_history(self, event: AstrMessageEvent):
        """最近 7 天的日报列表（管理员）。"""
        group_id = str(self._call(event, "get_group_id", "") or "")
        if not group_id:
            group_id = self._guess_group(str(self._call(event, "get_sender_id", "") or ""))
        if not group_id:
            yield event.plain_result("请在群内使用这个命令。")
            return
        reports = self.store.daily_reports(group_id, limit=7)
        if not reports:
            yield event.plain_result("还没有日报记录。/心理日报 可以立刻生成一份。")
            return
        lines = [f"🗂 最近的日报（{self._group_name(group_id)}）"]
        for report in reports:
            mood = report.get("mood") or {}
            mark = daily.MOOD_MARKS[int(mood.get("mood", 1))] if mood else "🟢"
            lines.append(
                f"{mark} {report.get('date', '?')} · {mood.get('label', '未知')} · "
                f"{report.get('messages', 0)} 条 / {report.get('users', 0)} 人"
            )
            if report.get("risks"):
                lines.append(f"   需留意 {len(report['risks'])} 人")
        lines.append("")
        lines.append("完整内容保存在插件数据目录的 store.json 里。")
        yield event.plain_result(await self._deliver_sensitive(event, "\n".join(lines)))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("心理待办")
    async def cmd_alerts(self, event: AstrMessageEvent):
        """最近的提醒记录（管理员）。"""
        group_id = str(self._call(event, "get_group_id", "") or "")
        items = self.store.alerts(group_id or None, limit=10)
        if not items:
            yield event.plain_result("最近没有触发提醒。")
            return
        lines = ["🔔 最近的提醒"]
        for item in items:
            ts = time.strftime("%m-%d %H:%M", time.localtime(int(item.get("ts", 0))))
            _, _, label, mark = analyzer.level_of(float(item.get("score", 0)))
            lines.append(
                f"{mark} {ts} {item.get('nick') or '群友'}（{item.get('uid')}）"
                f" {float(item.get('score', 0)):.0f}分 · {label}"
            )
        lines.append("\n用 /心理报告 QQ号 查看详情。")
        yield event.plain_result(await self._deliver_sensitive(event, "\n".join(lines)))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("心理开关")
    async def cmd_toggle(self, event: AstrMessageEvent):
        """打开或关闭本群的记录（管理员）。"""
        group_id = str(self._call(event, "get_group_id", "") or "")
        if not group_id:
            yield event.plain_result("请在群内使用这个命令。")
            return
        parts = str(getattr(event, "message_str", "") or "").strip().split()
        arg = parts[-1].lower() if len(parts) > 1 else ""
        on = arg in {"on", "开", "开启", "enable", "true", "1"}
        off = arg in {"off", "关", "关闭", "disable", "false", "0"}
        group = self.store.ensure_group(group_id)
        if not on and not off:
            active = bool(group.get("activated")) and bool(group.get("enabled", True))
            yield event.plain_result(
                f"本群当前状态：{'分析中' if active else '未开启'}\n"
                "用法：/心理开关 on 或 /心理开关 off"
            )
            return
        if on:
            group["activated"] = True
            group["enabled"] = True
        else:
            group["enabled"] = False
        self.store.mark_dirty()
        await self.store.save(force=True)
        yield event.plain_result(
            f"本群记录已{'开启' if on else '关闭'}。"
            + (
                "接下来会开始记录本群消息，成员可随时用 /心理退出 退出。"
                if on
                else "已有数据会在保留期后自动清理。"
            )
        )
