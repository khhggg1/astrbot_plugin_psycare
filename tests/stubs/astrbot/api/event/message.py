"""消息链 / 结果对象桩。"""

from __future__ import annotations


class MessageChain:
    def __init__(self, chain=None):
        self.chain = list(chain or [])

    def message(self, text: str) -> "MessageChain":
        from astrbot.api.message_components import Plain

        self.chain.append(Plain(text))
        return self


class MessageEventResult:
    def __init__(self, text: str = ""):
        self.text = text
        self.chain = []

    def __repr__(self) -> str:
        return f"MessageEventResult({self.text!r})"
