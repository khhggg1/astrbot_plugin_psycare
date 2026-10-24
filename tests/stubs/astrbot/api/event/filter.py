"""filter 模块桩：装饰器保持函数原样，只记录注册信息。"""

from __future__ import annotations

import enum
from typing import Any, Callable


class EventMessageType(enum.Enum):
    ALL = "all"
    PRIVATE_MESSAGE = "private"
    GROUP_MESSAGE = "group"


class PermissionType(enum.Enum):
    ADMIN = "admin"
    MEMBER = "member"


class PlatformAdapterType(enum.Flag):
    ALL = enum.auto()
    AIOCQHTTP = enum.auto()
    QQOFFICIAL = enum.auto()


def _mark(fn: Callable, key: str, value: Any) -> Callable:
    meta = getattr(fn, "__astrbot_filters__", {})
    meta = dict(meta)
    meta[key] = value
    fn.__astrbot_filters__ = meta  # type: ignore[attr-defined]
    return fn


def command(name: str | None = None, alias: set[str] | None = None, priority: int = 0):
    def decorator(fn):
        return _mark(fn, "command", {"name": name or fn.__name__, "alias": alias, "priority": priority})

    return decorator


def command_group(name: str):
    def decorator(fn):
        class _Group:
            def __init__(self, name: str, fn):
                self.name = name
                self.fn = fn

            def command(self, cname: str | None = None, **kwargs):
                return command(cname or self.fn.__name__, **kwargs)

            def group(self, gname: str):
                def deco(f):
                    return _Group(gname, f)

                return deco

        return _Group(name, fn)

    return decorator


def event_message_type(message_type: EventMessageType, priority: int = 0):
    def decorator(fn):
        return _mark(fn, "event_message_type", message_type)

    return decorator


def permission_type(permission: PermissionType):
    def decorator(fn):
        return _mark(fn, "permission_type", permission)

    return decorator


def platform_adapter_type(adapter: PlatformAdapterType):
    def decorator(fn):
        return _mark(fn, "platform_adapter_type", adapter)

    return decorator


def llm_tool(name: str | None = None):
    def decorator(fn):
        return _mark(fn, "llm_tool", name or fn.__name__)

    return decorator


def on_astrbot_loaded():
    def decorator(fn):
        return _mark(fn, "on_astrbot_loaded", True)

    return decorator


def on_llm_request():
    def decorator(fn):
        return _mark(fn, "on_llm_request", True)

    return decorator
