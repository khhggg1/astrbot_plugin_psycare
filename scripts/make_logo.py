"""生成 256x256 的插件 logo（渐变圆角 + 心形）。

用法：python scripts/make_logo.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SIZE = 256
SS = 4  # 超采样，边缘更干净


def gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    pixels = img.load()
    start, end = (0x5B, 0x7C, 0xFA), (0xB0, 0x6A, 0xE0)
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size - 2)
            pixels[x, y] = tuple(
                int(start[i] + (end[i] - start[i]) * t) for i in range(3)
            )
    return img


def heart_points(cx: float, cy: float, scale: float) -> list[tuple[float, float]]:
    points = []
    for deg in range(0, 360, 2):
        t = math.radians(deg)
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        points.append((cx + x * scale, cy - y * scale))
    return points


def main() -> None:
    size = SIZE * SS
    img = gradient(size).convert("RGBA")

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=255)
    img.putalpha(mask)

    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.polygon(heart_points(size / 2, size * 0.53, size / 48), fill=(255, 255, 255, 235))
    img = Image.alpha_composite(img, layer)

    img.resize((SIZE, SIZE), Image.LANCZOS).save(ROOT / "logo.png", optimize=True)
    print("written", ROOT / "logo.png")


if __name__ == "__main__":
    main()
