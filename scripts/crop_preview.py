"""把截图裁到内容高度（去掉视口多出来的空白）。

用法：python scripts/crop_preview.py build/preview/*.png
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops


def crop(path: Path, pad: int = 40) -> None:
    """只裁底部空白，保持卡片整体宽度（否则右侧圆角会被切掉）。"""
    image = Image.open(path).convert("RGB")
    background = Image.new("RGB", image.size, image.getpixel((2, image.height - 2)))
    bbox = ImageChops.difference(image, background).getbbox()
    if not bbox:
        return
    bottom = min(image.height, bbox[3] + pad)
    image.crop((0, 0, image.width, bottom)).save(path)


def main() -> None:
    for raw in sys.argv[1:]:
        path = Path(raw)
        if path.suffix.lower() == ".png":
            crop(path)
            print("cropped", path)


if __name__ == "__main__":
    main()
