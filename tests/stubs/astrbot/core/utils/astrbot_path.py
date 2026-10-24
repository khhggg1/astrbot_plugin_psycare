"""astrbot.core.utils.astrbot_path 桩。"""

from __future__ import annotations

import os


def get_astrbot_data_path() -> str:
    return os.environ.get("ASTRBOT_DATA_PATH", "data")
