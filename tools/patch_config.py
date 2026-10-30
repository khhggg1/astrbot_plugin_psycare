from pathlib import Path

p = Path(r"D:\1\心理\core\config.py")
s = p.read_text(encoding="utf-8")
if "weather_enabled" not in s:
    s = s.replace(
        '    "image_report": True,',
        '    "image_report": True,\n'
        '    "weather_enabled": True,\n'
        '    "weather_city": "南京",\n'
        '    "weather_lat": 32.06,\n'
        '    "weather_lon": 118.80,',
    )
    p.write_text(s, encoding="utf-8")
    print("config patched")
else:
    print("config already patched")
