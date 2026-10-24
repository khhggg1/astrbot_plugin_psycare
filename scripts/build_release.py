"""打包发布用的 zip（不含测试与开发脚本）。

用法：python scripts/build_release.py
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "astrbot_plugin_psycare"
MAX_BYTES = 16 * 1024 * 1024

INCLUDE = [
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "logo.png",
    "core",
    "templates",
    "views",
    "docs",
    ".astrbot-plugin",
]
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", "build", "dist", "tests", "scripts", ".github"}
SKIP_SUFFIX = {".pyc", ".pyo", ".zip", ".log"}


def metadata_text() -> str:
    return (ROOT / "metadata.yaml").read_text(encoding="utf-8")


def version_of(text: str) -> str:
    match = re.search(r"(?m)^version:\s*([^\s#]+)", text)
    if not match:
        raise SystemExit("metadata.yaml 里没有 version 字段")
    return match.group(1).strip()


def name_of(text: str) -> str:
    match = re.search(r"(?m)^name:\s*([^\s#]+)", text)
    return match.group(1).strip() if match else ""


def collect() -> list[Path]:
    files: list[Path] = []
    for item in INCLUDE:
        path = ROOT / item
        if not path.exists():
            print(f"  ! 缺少 {item}（跳过）")
            continue
        if path.is_file():
            files.append(path)
            continue
        for child in path.rglob("*"):
            if not child.is_file():
                continue
            if any(part in SKIP_DIRS for part in child.relative_to(ROOT).parts):
                continue
            if child.suffix.lower() in SKIP_SUFFIX:
                continue
            files.append(child)
    return sorted(files)


def main() -> int:
    text = metadata_text()
    version = version_of(text)
    name = name_of(text)
    if name != PLUGIN:
        print(f"  ! metadata.name={name!r} 与目录名 {PLUGIN!r} 不一致")
    if "your-github-name" in text:
        print("  ! metadata.yaml 里还有占位符 your-github-name，发布前请替换")

    files = collect()
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / f"{PLUGIN}-{version}.zip"

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            arcname = f"{PLUGIN}/{path.relative_to(ROOT).as_posix()}"
            zf.write(path, arcname)

    size = target.stat().st_size
    print(f"\n打包完成：{target}")
    print(f"  版本：{version}")
    print(f"  文件数：{len(files)}")
    print(f"  体积：{size / 1024:.1f} KB（上限 16 MB）")
    if size > MAX_BYTES:
        print("  ! 超过 16MB，插件市场会拒绝，请压缩 docs/ 里的图片")
        return 1
    required = {"main.py", "metadata.yaml", "_conf_schema.json"}
    missing = required - {p.name for p in files}
    if missing:
        print(f"  ! 缺少必需文件：{sorted(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
