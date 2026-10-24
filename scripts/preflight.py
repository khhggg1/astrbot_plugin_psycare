"""发布前自检：占位符、元数据、必需文件、配置 schema、logo。

用法：python scripts/preflight.py
不联网、不需要 token，只读本地文件。全部通过返回 0。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "astrbot_plugin_psycare"
REQUIRED_FILES = [
    "main.py",
    "metadata.yaml",
    "_conf_schema.json",
    "requirements.txt",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
]
PLACEHOLDER = "your-github-name"

problems: list[str] = []
warnings: list[str] = []


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def bad(msg: str) -> None:
    problems.append(msg)
    print(f"  ✗ {msg}")


def warn(msg: str) -> None:
    warnings.append(msg)
    print(f"  ! {msg}")


def parse_metadata(text: str) -> dict[str, str]:
    """只解析顶层 `key: value`，支持 `|` 块（够用且不依赖 PyYAML）。"""
    data: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.startswith((" ", "\t")):
            i += 1
            continue
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not match:
            i += 1
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in ("|", ">", "|-", ">-"):
            block: list[str] = []
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            data[key] = "\n".join(block).strip()
            continue
        data[key] = value.strip("\"'")
        i += 1
    return data


def check_files() -> None:
    print("\n[必需文件]")
    for name in REQUIRED_FILES:
        if (ROOT / name).exists():
            ok(name)
        else:
            bad(f"缺少 {name}")
    if not (ROOT / "logo.png").exists():
        warn("没有 logo.png（市场卡片会没有图标）")


def check_metadata() -> dict[str, str]:
    print("\n[metadata.yaml]")
    path = ROOT / "metadata.yaml"
    if not path.exists():
        bad("metadata.yaml 不存在")
        return {}
    meta = parse_metadata(path.read_text(encoding="utf-8"))
    for key in ("name", "display_name", "desc", "version", "author", "repo"):
        if meta.get(key):
            ok(f"{key}: {meta[key][:60]}")
        else:
            bad(f"缺少 {key}")

    if meta.get("name") and meta["name"] != PLUGIN_NAME:
        bad(f"name={meta['name']} 与预期 {PLUGIN_NAME} 不一致")

    version = meta.get("version", "")
    if version.startswith("v"):
        bad("version 不要带 v 前缀（市场要求纯语义化版本）")
    elif not re.fullmatch(r"\d+\.\d+\.\d+", version):
        bad(f"version={version} 不是 x.y.z 形式")
    else:
        ok(f"version 格式正确：{version}")

    author = meta.get("author", "")
    if "/" in author:
        bad("author 不能包含 /")
    if PLACEHOLDER in author:
        bad(f"author 还是占位符 {PLACEHOLDER}")

    repo = meta.get("repo", "")
    if not re.fullmatch(r"https://github\.com/[\w-]+/[\w-]+(\.git)?", repo):
        bad(f"repo 不是合法的 GitHub 仓库地址：{repo}")

    if meta.get("astrbot_version"):
        ok(f"astrbot_version: {meta['astrbot_version']}")

    platforms = re.findall(r"^\s*-\s*(\w+)\s*$", _block(ROOT / "metadata.yaml", "support_platforms"))
    if platforms:
        ok(f"support_platforms: {', '.join(platforms)}")
    return meta


def _block(path: Path, key: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    active = False
    for line in lines:
        if re.match(rf"^{key}:\s*$", line):
            active = True
            continue
        if active:
            if line.startswith("  - ") or line.startswith("- "):
                out.append(line)
            elif line.strip():
                break
    return "\n".join(out)


def check_placeholders() -> None:
    print("\n[占位符]")
    hits = []
    for path in (ROOT / "metadata.yaml", ROOT / "LICENSE"):
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PLACEHOLDER in line:
                hits.append(f"{path.name}:{i}")
    if hits:
        bad(f"仍在使用 {PLACEHOLDER}：{', '.join(hits)}")
    else:
        ok("没有残留占位符")


def check_schema() -> None:
    print("\n[_conf_schema.json]")
    path = ROOT / "_conf_schema.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        bad(f"JSON 解析失败：{exc}")
        return
    if not isinstance(data, dict) or not data:
        bad("配置项为空")
        return
    missing_type = [k for k, v in data.items() if not isinstance(v, dict) or "type" not in v]
    if missing_type:
        bad(f"这些配置项缺少 type：{missing_type}")
    else:
        ok(f"{len(data)} 个配置项，全部带 type")


def check_versions_consistent() -> None:
    print("\n[版本一致性]")
    meta = parse_metadata((ROOT / "metadata.yaml").read_text(encoding="utf-8"))
    version = meta.get("version", "")
    readme = (ROOT / "README.md").read_text(encoding="utf-8") if (ROOT / "README.md").exists() else ""
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8") if (ROOT / "CHANGELOG.md").exists() else ""
    if version and f"version-{version}-" in readme:
        ok(f"README 版本徽章是 {version}")
    elif version:
        warn(f"README 里没找到版本 {version} 的徽章，记得同步")
    if version and f"## [{version}]" in changelog:
        ok(f"CHANGELOG 有 {version} 段")
    elif version:
        warn(f"CHANGELOG 缺少 [{version}] 段")


def check_logo() -> None:
    path = ROOT / "logo.png"
    if not path.exists():
        return
    print("\n[logo]")
    try:
        from PIL import Image  # 仅用于读尺寸，没装就跳过
    except ImportError:
        warn("没装 Pillow，跳过 logo 尺寸检查")
        return
    with Image.open(path) as im:
        if im.size == (256, 256):
            ok("256×256")
        else:
            warn(f"尺寸 {im.size[0]}×{im.size[1]}，市场推荐 256×256")


def main() -> int:
    print(f"发布前自检：{ROOT}")
    check_files()
    meta = check_metadata()
    check_placeholders()
    check_schema()
    check_versions_consistent()
    check_logo()

    print("\n" + "=" * 44)
    if problems:
        print(f"未通过：{len(problems)} 项需要处理")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("全部通过，可以发布。" + (f"（{len(warnings)} 条提醒）" if warnings else ""))
    if meta.get("repo"):
        print(f"下一步：git push 到 {meta['repo']}，再到 https://cloud.astrbot.app/publish 提交")
    return 0


if __name__ == "__main__":
    sys.exit(main())
