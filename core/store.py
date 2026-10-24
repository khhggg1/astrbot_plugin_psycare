"""本地数据存储。

只存一份 JSON：`data/plugin_data/astrbot_plugin_psycare/store.json`。
- 消息文本截断到 300 字，够做分析，也避免体积失控；
- 每个成员最多保留 N 条、整体按 retention_days 过期；
- 写入使用「临时文件 + os.replace」，避免进程被杀时留下半个文件；
- 加锁串行化落盘，避免并发写。

数据不出本机；只有开启 LLM 复核时，抽取的片段才会发给模型提供商。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from astrbot.api import logger

from .config import PLUGIN_NAME

_EMPTY = {
    "version": 1,
    "updated_at": 0,
    "groups": {},
    "analysis": {},
    "daily": {},
    "alerts": [],
    "settings": {},
}


class Store:
    def __init__(
        self,
        data_dir: Path,
        *,
        retention_days: int = 30,
        max_per_user: int = 300,
    ):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "store.json"
        self.retention_days = max(1, int(retention_days))
        self.max_per_user = max(10, int(max_per_user))
        self._data = json.loads(json.dumps(_EMPTY))
        self._dirty = False
        self._lock = asyncio.Lock()
        self._last_save = 0.0

    # --- 载入 / 落盘 ---------------------------------------------------
    def load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"[{PLUGIN_NAME}] 读取数据文件失败，已忽略：{exc}")
            return
        if not isinstance(data, dict):
            return
        base = json.loads(json.dumps(_EMPTY))
        for key in base:
            if key in data:
                base[key] = data[key]
        if not isinstance(base["groups"], dict):
            base["groups"] = {}
        self._data = base

    async def save(self, force: bool = False) -> None:
        if not self._dirty and not force:
            return
        async with self._lock:
            payload = json.dumps(
                self._data, ensure_ascii=False, separators=(",", ":")
            )
            tmp = self.path.with_suffix(".json.tmp")
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, self.path)
                self._dirty = False
                self._last_save = time.time()
            except OSError as exc:
                logger.warning(f"[{PLUGIN_NAME}] 保存数据失败：{exc}")

    def mark_dirty(self) -> None:
        self._dirty = True

    @property
    def dirty(self) -> bool:
        return self._dirty

    # --- 群 / 成员 -----------------------------------------------------
    def ensure_group(self, group_id: str, umo: str = "", name: str = "") -> dict:
        groups = self._data["groups"]
        group = groups.get(group_id)
        if not isinstance(group, dict):
            group = {
                "umo": umo,
                "name": name,
                "enabled": True,
                "activated": False,
                "created_at": int(time.time()),
                "users": {},
            }
            groups[group_id] = group
            self._dirty = True
        else:
            if umo and group.get("umo") != umo:
                group["umo"] = umo
                self._dirty = True
            if name and group.get("name") != name:
                group["name"] = name
                self._dirty = True
        group.setdefault("users", {})
        return group

    def group(self, group_id: str) -> dict | None:
        group = self._data["groups"].get(group_id)
        return group if isinstance(group, dict) else None

    def groups(self) -> dict[str, dict]:
        return {
            gid: g
            for gid, g in self._data["groups"].items()
            if isinstance(g, dict)
        }

    def ensure_user(self, group_id: str, user_id: str, nickname: str = "") -> dict:
        group = self.ensure_group(group_id)
        users = group.setdefault("users", {})
        user = users.get(user_id)
        now = int(time.time())
        if not isinstance(user, dict):
            user = {
                "nick": nickname,
                "opt_in": None,
                "opt_out": False,
                "first_seen": now,
                "last_seen": now,
                "messages": [],
            }
            users[user_id] = user
            self._dirty = True
        else:
            if nickname and user.get("nick") != nickname:
                user["nick"] = nickname
                self._dirty = True
            user["last_seen"] = now
            user.setdefault("messages", [])
        return user

    def user(self, group_id: str, user_id: str) -> dict | None:
        group = self.group(group_id)
        if not group:
            return None
        user = (group.get("users") or {}).get(user_id)
        return user if isinstance(user, dict) else None

    def users(self, group_id: str) -> dict[str, dict]:
        group = self.group(group_id)
        if not group:
            return {}
        return {
            uid: u
            for uid, u in (group.get("users") or {}).items()
            if isinstance(u, dict)
        }

    # --- 消息 -----------------------------------------------------------
    def record(self, group_id: str, user_id: str, nickname: str, signal) -> dict:
        user = self.ensure_user(group_id, user_id, nickname)
        msgs = user.setdefault("messages", [])
        item = {
            "t": int(signal.ts),
            "x": signal.text[:300],
            "i": round(float(signal.intensity), 1),
            "d": list(signal.dims),
            "c": bool(signal.crisis),
        }
        msgs.append(item)
        if len(msgs) > self.max_per_user:
            del msgs[: len(msgs) - self.max_per_user]
        user["last_seen"] = int(time.time())
        self._dirty = True
        return item

    def messages(self, group_id: str, user_id: str) -> list[dict]:
        user = self.user(group_id, user_id)
        if not user:
            return []
        return [m for m in (user.get("messages") or []) if isinstance(m, dict)]

    def message_count(self, group_id: str) -> int:
        total = 0
        for user in self.users(group_id).values():
            total += len(user.get("messages") or [])
        return total

    # --- 选择 / 删除 ----------------------------------------------------
    def set_opt_out(self, group_id: str, user_id: str, value: bool) -> None:
        user = self.ensure_user(group_id, user_id)
        user["opt_out"] = bool(value)
        user["opt_in"] = None if value else True
        if value:
            user["messages"] = []
        self._dirty = True

    def set_opt_in(self, group_id: str, user_id: str, value: bool) -> None:
        user = self.ensure_user(group_id, user_id)
        user["opt_in"] = bool(value)
        if value:
            user["opt_out"] = False
        self._dirty = True

    def delete_user(self, group_id: str, user_id: str) -> bool:
        group = self.group(group_id)
        if not group:
            return False
        users = group.get("users") or {}
        removed = users.pop(user_id, None) is not None
        self._data["analysis"].pop(f"{group_id}:{user_id}", None)
        if removed:
            self._dirty = True
        return removed

    # --- 分析结果 -------------------------------------------------------
    def get_analysis(self, group_id: str, user_id: str) -> dict | None:
        item = self._data["analysis"].get(f"{group_id}:{user_id}")
        return item if isinstance(item, dict) else None

    def set_analysis(self, group_id: str, user_id: str, payload: dict) -> None:
        self._data["analysis"][f"{group_id}:{user_id}"] = payload
        self._dirty = True

    # --- 日报 -----------------------------------------------------------
    def set_daily(self, group_id: str, date: str, payload: dict) -> None:
        self._data.setdefault("daily", {})[f"{group_id}:{date}"] = payload
        self._dirty = True

    def get_daily(self, group_id: str, date: str) -> dict | None:
        item = self._data.get("daily", {}).get(f"{group_id}:{date}")
        return item if isinstance(item, dict) else None

    def daily_reports(self, group_id: str, limit: int = 7) -> list[dict]:
        items = []
        for key, item in (self._data.get("daily") or {}).items():
            if not isinstance(item, dict):
                continue
            if group_id and not key.startswith(f"{group_id}:"):
                continue
            items.append(item)
        items.sort(key=lambda r: str(r.get("date", "")), reverse=True)
        return items[:limit]

    # --- 提醒 -----------------------------------------------------------
    def add_alert(self, alert: dict) -> None:
        alerts = self._data.setdefault("alerts", [])
        alerts.append(alert)
        if len(alerts) > 300:
            del alerts[: len(alerts) - 300]
        self._dirty = True

    def alerts(self, group_id: str | None = None, limit: int = 10) -> list[dict]:
        items = [a for a in self._data.get("alerts", []) if isinstance(a, dict)]
        if group_id:
            items = [a for a in items if a.get("gid") == group_id]
        return items[-limit:][::-1]

    def recent_alert(self, group_id: str, user_id: str, within: int) -> dict | None:
        cutoff = int(time.time()) - within
        for alert in reversed(self._data.get("alerts", [])):
            if (
                isinstance(alert, dict)
                and alert.get("gid") == group_id
                and alert.get("uid") == user_id
                and int(alert.get("ts", 0)) >= cutoff
            ):
                return alert
        return None

    # --- 全局设置 -------------------------------------------------------
    def get_setting(self, key: str, default=None):
        return (self._data.get("settings") or {}).get(key, default)

    def set_setting(self, key: str, value) -> None:
        self._data.setdefault("settings", {})[key] = value
        self._dirty = True

    # --- 过期清理 -------------------------------------------------------
    def purge(self, now: int | None = None) -> int:
        now = int(now or time.time())
        cutoff = now - self.retention_days * 86400
        removed = 0
        for gid, group in self.groups().items():
            users = group.get("users") or {}
            for uid in list(users.keys()):
                user = users.get(uid) or {}
                msgs = user.get("messages") or []
                keep = [m for m in msgs if int(m.get("t", 0)) >= cutoff]
                removed += len(msgs) - len(keep)
                user["messages"] = keep
                if not keep and now - int(user.get("last_seen", now)) > 7 * 86400:
                    users.pop(uid, None)
                    self._data["analysis"].pop(f"{gid}:{uid}", None)
        if removed:
            self._dirty = True
        return removed
