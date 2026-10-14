"""天气子系统：Open-Meteo + 体感温度（NOAA Heat Index）+ 缓存与降级。

工程约束：绝不因为天气服务挂掉而阻塞重排。
硬约束与软调节分离；confidence=low 时硬约束仍生效，软调节不触发（避免误伤）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from typing import Optional

import httpx

API_URL = "https://api.open-meteo.com/v1/forecast"
THUNDER_CODES = {95, 96, 99}          # 雷电类 weather_code
HARD_PRECIP_MM = 10.0                  # 降水量 ≥10mm 视为硬约束
HOT_APPARENT_C = 30.0                  # 体感 ≥30℃ 触发软调节
AQI_HARD_LIMIT = 150                   # AQI >150 硬约束

# 简单进程内缓存（演示环境替代 Redis；key = wx:{lat},{lon}:{date}）
_cache: dict[str, tuple[float, "WeatherDay"]] = {}
TTL_FORECAST = 6 * 3600                # 未来 7 天 TTL 6h
TTL_HISTORY = 30 * 24 * 3600           # 历史日期 TTL 30d


@dataclass
class WeatherDay:
    date: str
    t_max: float = 0.0
    rh_mean: float = 50.0
    precip: float = 0.0
    weather_code: int = 0
    aqi: Optional[int] = None
    apparent_max: float = 0.0
    source: str = "live"               # live / cache / STALE
    confidence: str = "high"           # high / low

    @property
    def is_thunder(self) -> bool:
        return self.weather_code in THUNDER_CODES

    @property
    def hard_constraint(self) -> bool:
        """硬约束：暴雨/雷电/AQI>150。confidence=low 时仍生效（保守）。"""
        if self.precip >= HARD_PRECIP_MM or self.is_thunder:
            return True
        if self.aqi is not None and self.aqi > AQI_HARD_LIMIT:
            return True
        return False

    @property
    def hot_soft(self) -> bool:
        """软调节：体感 ≥30℃。confidence=low 时不触发（避免误伤）。"""
        return self.confidence == "high" and self.apparent_max >= HOT_APPARENT_C

    def to_dict(self) -> dict:
        return asdict(self) | {"hard_constraint": self.hard_constraint, "hot_soft": self.hot_soft}


def apparent_max(t_c: float, rh: float) -> float:
    """体感温度：Rothfusz 回归式的摄氏版本（NOAA Heat Index），rh 为百分比。"""
    t, r = t_c, rh
    return (-8.784695 + 1.61139411 * t + 2.338549 * r - 0.14611605 * t * r
            - 1.2308094e-2 * t * t - 1.6424828e-2 * r * r
            + 2.211732e-3 * t * t * r + 7.2546e-4 * t * r * r
            - 3.582e-6 * t * t * r * r)


def _empty_day(d: date, source: str = "STALE", confidence: str = "low") -> WeatherDay:
    return WeatherDay(date=d.isoformat(), source=source, confidence=confidence)


def _fetch_7d(lat: float, lon: float, timeout: float = 3.0) -> dict[str, WeatherDay]:
    """一次请求拉取 7 天预报（+一次 AQI），返回 {date_str: WeatherDay}。"""
    params = {
        "latitude": lat, "longitude": lon,
        "daily": "temperature_2m_max,relative_humidity_2m_mean,precipitation_sum,weathercode",
        "timezone": "auto", "forecast_days": 7,
    }
    with httpx.Client(timeout=timeout) as client:
        r = client.get(API_URL, params=params)
        r.raise_for_status()
        j = r.json()
    daily = j["daily"]
    times = daily["time"]
    aqi_by_day: dict[str, int] = {}
    try:
        with httpx.Client(timeout=timeout) as client:
            r2 = client.get("https://air-quality-api.open-meteo.com/v1/air-quality",
                            params={"latitude": lat, "longitude": lon,
                                    "hourly": "us_aqi", "timezone": "auto", "forecast_days": 7})
            if r2.status_code == 200:
                arr = r2.json()["hourly"]["us_aqi"]
                for i, t in enumerate(times):
                    vals = [v for v in arr[i * 24:(i + 1) * 24] if v is not None]
                    if vals:
                        aqi_by_day[t] = max(vals)
    except Exception:
        pass                                              # AQI 失败不阻塞主链路
    out: dict[str, WeatherDay] = {}
    for i, t in enumerate(times):
        t_max = daily["temperature_2m_max"][i]
        rh = daily["relative_humidity_2m_mean"][i] or 50.0
        out[t] = WeatherDay(
            date=t, t_max=t_max, rh_mean=rh,
            precip=daily["precipitation_sum"][i] or 0.0,
            weather_code=daily["weathercode"][i],
            aqi=aqi_by_day.get(t),
            apparent_max=round(apparent_max(t_max, rh), 1),
            source="live", confidence="high",
        )
    return out


_week_cache: dict[str, tuple[float, dict[str, WeatherDay]]] = {}


def _week_forecast(lat: float, lon: float) -> dict[str, WeatherDay]:
    """取（并缓存 6h）未来 7 天预报。任何失败都返回 {}，绝不抛异常。"""
    key = f"wx7:{lat:.2f},{lon:.2f}"
    now = time.time()
    if key in _week_cache and now - _week_cache[key][0] < TTL_FORECAST:
        return _week_cache[key][1]
    try:
        data = _fetch_7d(lat, lon)
        _week_cache[key] = (now, data)
        return data
    except Exception:
        return {}


def get_weather(lat: float, lon: float, target: date) -> WeatherDay:
    """带缓存与降级的天气查询：失败返回 STALE + confidence=low，绝不抛异常。"""
    data = _week_forecast(lat, lon)
    day = data.get(target.isoformat())
    if day is not None:
        return day
    # 降级：低置信骨架（硬约束不生效、软调节不触发，避免误伤）
    return _empty_day(target)


def get_week_weather(lat: float, lon: float, start: date, days: int = 7) -> dict[str, WeatherDay]:
    data = _week_forecast(lat, lon)
    out: dict[str, WeatherDay] = {}
    for i in range(days):
        d = start + timedelta(days=i)
        out[d.isoformat()] = data.get(d.isoformat(), _empty_day(d))
    return out
