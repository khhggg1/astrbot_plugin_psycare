"""消息段桩。"""

from __future__ import annotations


class Plain:
    def __init__(self, text: str = ""):
        self.text = text

    def __repr__(self) -> str:
        return f"Plain({self.text!r})"


class At:
    def __init__(self, qq: str = ""):
        self.qq = str(qq)


class Image:
    def __init__(self, file: str = "", url: str = ""):
        self.file = file
        self.url = url

    @classmethod
    def fromURL(cls, url: str) -> "Image":
        return cls(url=url)

    @classmethod
    def fromBase64(cls, data: str) -> "Image":
        return cls(file="base64://" + data)

    @classmethod
    def fromFileSystem(cls, path: str) -> "Image":
        return cls(file=path)

    def __repr__(self) -> str:
        return f"Image(file={self.file[:32]!r}, url={self.url[:32]!r})"
