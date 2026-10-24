"""astrbot.api.star 桩。"""

from __future__ import annotations

import base64


class Context:
    def __init__(self):
        self._sent: list[tuple[str, object]] = []
        self.web_routes: dict[str, tuple] = {}

    async def send_message(self, umo: str, chain) -> None:
        self._sent.append((umo, chain))

    async def get_current_chat_provider_id(self, umo: str = "") -> str:
        return "fake-provider"

    async def llm_generate(self, chat_provider_id: str = "", prompt: str = "", **kwargs):
        class _Resp:
            completion_text = "{}"

        return _Resp()

    def register_web_api(self, route, handler, methods, desc) -> None:
        self.web_routes[route] = (handler, methods, desc)


class Star:
    def __init__(self, context: Context):
        self.context = context

    async def html_render(self, tmpl: str, data: dict | None = None, options: dict | None = None):
        """假的 T2I：返回一个 data URI，方便断言「确实走了图片分支」。"""
        payload = (tmpl[:40] + str(sorted((data or {}).keys()))).encode("utf-8")
        return "data:image/png;base64," + base64.b64encode(payload).decode()

    async def terminate(self) -> None:
        return None
