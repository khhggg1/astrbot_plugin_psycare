"""事件对象桩：只提供插件会调用的方法。"""

from __future__ import annotations

from typing import Any


class AstrMessageEvent:
    def __init__(
        self,
        *,
        text: str = "",
        group_id: str = "",
        sender_id: str = "",
        sender_name: str = "",
        umo: str = "",
        self_id: str = "bot",
        at_targets: list[str] | None = None,
        is_private: bool = False,
        admin: bool = False,
    ):
        self.message_str = text
        self.unified_msg_origin = umo
        self._group_id = group_id
        self._sender_id = sender_id
        self._sender_name = sender_name
        self._self_id = self_id
        self._at_targets = at_targets or []
        self._is_private = is_private
        self._admin = admin
        self._results: list[Any] = []

    # --- 平台信息 -----------------------------------------------------
    def get_group_id(self) -> str:
        return self._group_id

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def get_self_id(self) -> str:
        return self._self_id

    def is_private_chat(self) -> bool:
        return self._is_private

    def is_admin(self) -> bool:
        return self._admin

    def get_messages(self) -> list[Any]:
        from astrbot.api.message_components import At

        return [At(qq=qq) for qq in self._at_targets]

    # --- 回复 ---------------------------------------------------------
    def plain_result(self, text: str):
        from .message import MessageEventResult

        result = MessageEventResult(text)
        self._results.append(result)
        return result

    def chain_result(self, chain):
        from .message import MessageEventResult

        result = MessageEventResult("")
        result.chain = list(chain)
        self._results.append(result)
        return result
