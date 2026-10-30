"""配置读取与默认值。

AstrBot 会把 _conf_schema.json 解析出来的配置以 dict 形式传给插件，
这里做一层轻量包装：缺失项回落到默认值，类型错误自动纠正，
避免用户把 "false" 填成字符串导致插件行为异常。
"""

from __future__ import annotations

from typing import Any, Mapping

PLUGIN_NAME = "astrbot_plugin_psycare"
DISPLAY_NAME = "心理关怀"

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "group_whitelist_enabled": False,
    "group_whitelist": [],
    "require_group_activation": True,
    "min_message_length": 2,
    "window_days": 14,
    "retention_days": 30,
    "max_messages_per_user": 300,
    "keyword_analysis": True,
    "llm_analysis": True,
    "llm_min_new_messages": 12,
    "llm_cooldown_minutes": 120,
    "alert_enabled": True,
    "alert_threshold": 75,
    "alert_cooldown_hours": 24,
    "require_opt_in": False,
    "allow_group_report": False,
    "care_message_enabled": False,
    "care_message": (
        "如果最近真的很难受，可以说出来，我们都在。\n"
        "如果需要专业帮助，可以拨打全国心理援助热线 12356。"
    ),
    "include_quotes": True,
    "daily_enabled": True,
    "daily_time": "21:30",
    "daily_target": "private",
    "daily_min_messages": 5,
    "image_report": True,
    "weather_enabled": True,
    "weather_city": "南京",
    "weather_lat": 32.06,
    "weather_lon": 118.80,
}

_TRUTHY = {"1", "true", "yes", "on", "y", "是", "开", "开启", "启用"}


class Settings:
    """对 AstrBotConfig 的只读包装，带默认值与类型兜底。"""

    def __init__(self, config: Mapping[str, Any] | None = None):
        self._raw: dict[str, Any] = dict(config) if config else {}

    # --- 基础访问 -----------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        base = DEFAULTS.get(key, default)
        if key in self._raw and self._raw[key] is not None:
            value = self._raw[key]
        else:
            value = base
        try:
            if isinstance(base, bool):
                if isinstance(value, bool):
                    return value
                if isinstance(value, str):
                    return value.strip().lower() in _TRUTHY
                return bool(value)
            if isinstance(base, int) and not isinstance(base, bool):
                return int(value)
            if isinstance(base, float):
                return float(value)
            if isinstance(base, list) and not isinstance(value, list):
                return [value] if value not in (None, "") else []
        except (TypeError, ValueError):
            return base
        return value

    def bool(self, key: str) -> bool:
        return bool(self.get(key))

    def int(self, key: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
        value = int(self.get(key))
        if minimum is not None:
            value = max(minimum, value)
        if maximum is not None:
            value = min(maximum, value)
        return value

    def text(self, key: str) -> str:
        value = self.get(key)
        return value if isinstance(value, str) else str(value or "")

    def raw(self) -> dict[str, Any]:
        return dict(self._raw)
