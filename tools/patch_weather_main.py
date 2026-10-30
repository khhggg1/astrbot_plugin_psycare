from pathlib import Path

p = Path(r"D:\1\心理\main.py")
s = p.read_text(encoding="utf-8")

s = s.replace(
    "    from .core import analyzer, daily, lexicon, render",
    "    from .core import analyzer, daily, lexicon, render, weather",
)
s = s.replace(
    "    from core import analyzer, daily, lexicon, render  # type: ignore[no-redef]",
    "    from core import analyzer, daily, lexicon, render, weather  # type: ignore[no-redef]",
)

s = s.replace(
    "        public = daily.render_public(stats, mood, group_name=name)\n"
    "        private = daily.render_private(stats, mood, group_name=name)",
    "        weather_today = await self._today_weather()\n"
    "        public = daily.render_public(stats, mood, group_name=name, weather=weather_today)\n"
    "        private = daily.render_private(stats, mood, group_name=name, weather=weather_today)",
)

s = s.replace(
    'render.daily_context(stats, mood, group_name=name, private=False),',
    'render.daily_context(stats, mood, group_name=name, private=False, weather=weather_today),',
)
s = s.replace(
    'render.daily_context(stats, mood, group_name=name, private=True),',
    'render.daily_context(stats, mood, group_name=name, private=True, weather=weather_today),',
)

anchor = "    async def _daily_llm(self, group: dict, stats, group_name: str) -> dict | None:"
method = '''    async def _today_weather(self):
        """取当日气象（3 小时缓存）；失败静默返回 None，不影响日报。"""
        if not self.settings.bool("weather_enabled"):
            return None
        today = time.strftime("%Y-%m-%d")
        cache = self.store.get_setting("weather_cache") or {}
        if cache.get("date") == today and time.time() - float(cache.get("ts", 0)) < 3 * 3600:
            try:
                return weather.Weather.from_dict(cache.get("data") or {})
            except Exception:
                pass
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    weather.fetch,
                    float(self.settings.get("weather_lat") or 32.06),
                    float(self.settings.get("weather_lon") or 118.80),
                ),
                timeout=10,
            )
        except Exception as exc:
            logger.info(f"[{PLUGIN_NAME}] 气象数据不可用，日报跳过气象行：{exc}")
            return None
        self.store.set_setting(
            "weather_cache",
            {"date": result.date, "ts": int(time.time()), "data": result.to_dict()},
        )
        return result

'''
if "_today_weather" not in s:
    s = s.replace(anchor, method + anchor)

p.write_text(s, encoding="utf-8")
print("main patched")
