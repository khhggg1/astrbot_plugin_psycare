from pathlib import Path

p = Path(r"D:\1\心理\tests\test_plugin.py")
s = p.read_text(encoding="utf-8")

if '"weather_enabled": False' not in s:
    s = s.replace(
        '        "image_report": False,',
        '        "image_report": False,\n        "weather_enabled": False,',
    )

block = '''    print("\\n[气象数据]")
    from core import weather as weather_mod

    w = weather_mod.Weather(date="2026-10-30", tmax=36.2, tmin=26.8, precip=0.0, code=0)
    check("气象摘要", "36℃" in w.summary() and "晴" in w.summary(), w.summary())
    check("高温给出提示", bool(w.tip()))
    pub = daily_mod.render_public(result.stats, result.mood, group_name="g", weather=w)
    check("公开版含气象行", "今日气象" in pub and "36℃" in pub)
    priv = daily_mod.render_private(result.stats, result.mood, group_name="g", weather=w)
    check("私密版含气象提示", "气象提示" in priv)
    ctx = render_mod.daily_context(
        result.stats, result.mood, group_name="g", private=False, weather=w
    )
    check("卡片上下文含气象", (ctx.get("weather") or {}).get("summary", "").startswith("36"))
    check(
        "无气象时不出行",
        "今日气象" not in daily_mod.render_public(result.stats, result.mood, group_name="g"),
    )
    check("低温提示", bool(weather_mod.Weather("d", 2, -3, 0, 0).tip()))

'''
anchor = "    # ------------------------------------------------------------- 退出"
if "[气象数据]" not in s:
    s = s.replace(anchor, block + anchor)

p.write_text(s, encoding="utf-8")
print("tests patched")
