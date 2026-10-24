"""测试用的 AstrBot 最小桩实现。

只实现插件真正用到的那部分接口，让 main.py 能在没有 AstrBot 的环境里
被导入并按真实调用顺序跑一遍。
"""

from __future__ import annotations

import sys
from pathlib import Path


def _install() -> None:
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))


_install()
