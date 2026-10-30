from pathlib import Path

p = Path(r"D:\1\心理\scripts\preview_cards.py")
s = p.read_text(encoding="utf-8")

if "from core import weather" not in s:
    s = s.replace(
        "from core import analyzer, daily  # noqa: E402\nfrom core import render as render_mod  # noqa: E402",
        "from core import analyzer, daily, weather  # noqa: E402\nfrom core import render as render_mod  # noqa: E402",
    )

if "w = weather.fetch" not in s:
    s = s.replace(
        "    public_ctx = render_mod.daily_context(stats, mood, group_name=\"摸鱼小分队\", private=False)",
        "    try:\n"
        "        w = weather.fetch(32.06, 118.80)\n"
        "    except Exception:\n"
        "        w = None\n"
        "    public_ctx = render_mod.daily_context(\n"
        "        stats, mood, group_name=\"摸鱼小分队\", private=False, weather=w\n"
        "    )",
    )
    s = s.replace(
        "    private_ctx = render_mod.daily_context(stats, mood, group_name=\"摸鱼小分队\", private=True)",
        "    private_ctx = render_mod.daily_context(\n"
        "        stats, mood, group_name=\"摸鱼小分队\", private=True, weather=w\n"
        "    )",
    )

p.write_text(s, encoding="utf-8")
print("preview patched")
