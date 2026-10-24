"""astrbot.api.web 桩：内存里的伪请求与响应。"""

from __future__ import annotations

from typing import Any


class _Query:
    def __init__(self):
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None, type=None):  # noqa: A002
        value = self._data.get(key, default)
        if type is not None and value is not None:
            try:
                return type(value)
            except (TypeError, ValueError):
                return default
        return value

    def getlist(self, key: str):
        value = self._data.get(key)
        return value if isinstance(value, list) else ([] if value is None else [value])


class _Request:
    def __init__(self):
        self.query = _Query()
        self.payload: dict[str, Any] = {}
        self.username = "admin"

    async def json(self, default: Any = None) -> Any:
        return self.payload if self.payload is not None else default


request = _Request()


def json_response(data: Any, status_code: int = 200) -> dict:
    return {"status": "ok", "data": data, "code": status_code}


def error_response(message: str, status_code: int = 400) -> dict:
    return {"status": "error", "message": message, "code": status_code}


def file_response(*args, **kwargs):  # pragma: no cover
    return None


def stream_response(*args, **kwargs):  # pragma: no cover
    return None
