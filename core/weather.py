"""气象数据接入（Open-Meteo 公开预报，无需 API Key）。

气象在这里的作用是「情境解释 + 关怀提示」，不参与风险打分：
没有经过验证的数据，不应该用一个气温去加重对某个人的判断。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date

API = "https://api.open-meteo.com/v1/forecast"

# WMO 天气代码 -> 中文
_WMO = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨",
    56: "冻毛毛雨", 57: "强冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "米雪",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "大阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷暴伴冰雹",
}


@dataclass
class Weather:
    date: str
    tmax: float
    tmin: float
    precip: float
    code: int

    @property
    def label(self) -> str:
        return _WMO.get(self.code, "未知天气")

    def summary(self) -> str:
        return f"{self.tmax:.0f}℃ / {self.tmin:.0f}℃ · {self.label}"

    def tip(self) -> str | None:
        """只在明显天气下给一句关怀提示，避免每天刷存在感。"""
        if self.tmax >= 35:
            return "高温天容易烦躁、睡不好，群里如果有人情绪起伏，可以先问一句。"
        if self.tmin <= 0:
            return "降温明显，户外活动变少，容易闷着；留意最近不太说话的人。"
        if self.precip >= 10:
            return "雨比较大，出行和心情都容易受影响，提醒大家注意安全。"
        if self.code >= 95:
            return "雷暴天气，注意安全；如果群里有人焦虑，可以先安抚。"
        return None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Weather":
        return cls(
            date=str(data.get("date", "")),
            tmax=float(data.get("tmax", 0)),
            tmin=float(data.get("tmin", 0)),
            precip=float(data.get("precip", 0)),
            code=int(data.get("code", 0)),
        )


def fetch(lat: float, lon: float, timeout: float = 8.0) -> Weather:
    """同步请求当日预报；失败时抛异常，由调用方静默降级。"""
    query = urllib.parse.urlencode(
        {
            "latitude": lat,
            "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weathercode",
            "timezone": "Asia/Shanghai",
            "forecast_days": 1,
        }
    )
    req = urllib.request.Request(f"{API}?{query}", headers={"User-Agent": "psycare/1.3"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.load(resp)
    daily = payload.get("daily") or {}
    return Weather(
        date=(daily.get("time") or [date.today().isoformat()])[0],
        tmax=float((daily.get("temperature_2m_max") or [0])[0]),
        tmin=float((daily.get("temperature_2m_min") or [0])[0]),
        precip=float((daily.get("precipitation_sum") or [0])[0]),
        code=int((daily.get("weathercode") or [0])[0]),
    )
