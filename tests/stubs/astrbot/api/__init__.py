"""astrbot.api 桩：logger / AstrBotConfig。"""

from __future__ import annotations

import logging
from typing import Any, Mapping

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("astrbot")


class AstrBotConfig(dict):
    """真实实现是带 save_config() 的 dict 子类，这里只要满足 dict 语义。"""

    def __init__(self, data: Mapping[str, Any] | None = None, *args, **kwargs):
        super().__init__(data or {}, *args, **kwargs)

    def save_config(self, *args, **kwargs) -> None:  # pragma: no cover
        return None
