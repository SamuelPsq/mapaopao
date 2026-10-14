"""规则引擎：VDOT 查表、配速区间、成绩折算、增幅约束、80/20 校验。

纯函数、零 IO、零随机 —— 这是能被 pytest 覆盖评测集的前提。
LLM 绝不参与本模块的任何数值计算。
"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

DATA_DIR = Path(__file__).parent

# ---------------------------------------------------------------- 常量
MAX_WEEKLY_GAIN = 0.10                     # 周增幅 ≤10%，全书最硬的一条安全规则
ZERO_BASE_CONSERVATIVE_FACTOR = 0.92       # A 类（零基础）用户的初始 VDOT 保守系数
VDOT_MIN, VDOT_MAX = 30, 70                # clamp 区间（本表覆盖 30–70）

INTENSITY_FACTOR = {
    "easy": 1.00,
    "marathon": 1.15,
    "threshold": 1.25,
    "interval": 1.40,
    "repetition": 1.50,
}
HARD_ZONES = ("threshold", "interval", "repetition")   # 高强度档（连续 3 天红线用）
MARATHON_PACE_TOLERANCE_SEC = 6          # 马拉松/阈值配速给 ±6s 区间

# 标准距离成绩 → VDOT 反查表（丹尼尔斯方程式核心行，t 单位：秒/km 越低越强）
# 用 10km 成绩的每公里配速反查。
@lru_cache(maxsize=1)
def _load_template() -> dict:
    with open(DATA_DIR / "template_16w.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)

TEMPLATE = _load_template()


# ---------------------------------------------------------------- 配速工具
def pace_to_sec(pace: str) -> int:
    """'5:24' -> 324 秒"""
    m, s = pace.split(":")
    return int(m) * 60 + int(s)


def sec_to_pace(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


# ---------------------------------------------------------------- VDOT 表
@lru_cache(maxsize=1)
def vdot_table() -> dict[int, dict]:
    table: dict[int, dict] = {}
    with open(DATA_DIR / "vdot_table.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = int(row["vdot"])
            table[v] = {k: pace_to_sec(row[k]) for k in
                        ("easy_lo", "easy_hi", "marathon", "threshold", "interval", "repetition")}
    return table


def clamp(vdot: float) -> int:
    """越界直接 clamp，不抛异常。"""
    return max(VDOT_MIN, min(VDOT_MAX, int(round(vdot))))


def pace_zones(vdot: float) -> dict:
    """VDOT → 5 档配速区间（返回秒）。"""
    row = vdot_table()[clamp(vdot)]
    return {
        "easy": (row["easy_lo"], row["easy_hi"]),
        "marathon": (row["marathon"] - MARATHON_PACE_TOLERANCE_SEC,
                     row["marathon"] + MARATHON_PACE_TOLERANCE_SEC),
        "threshold": (row["threshold"] - MARATHON_PACE_TOLERANCE_SEC,
                      row["threshold"] + MARATHON_PACE_TOLERANCE_SEC),
        "interval": (row["interval"], row["interval"]),
        "repetition": (row["repetition"], row["repetition"]),
    }


def pace_zones_display(vdot: float) -> dict:
    """5 档配速区间（'m:ss' 字符串，给前端展示用）。"""
    zones = pace_zones(vdot)
    out = {}
    for k, (lo, hi) in zones.items():
        out[k] = f"{sec_to_pace(lo)}–{sec_to_pace(hi)}" if lo != hi else sec_to_pace(lo)
    return out


# ---------------------------------------------------------------- 成绩 → VDOT
def predict_time(t1: float, d1: float, d2: float, k: float = 1.06) -> float:
    """Riegel 公式：由距离 d1 的成绩 t1 预测距离 d2 的成绩。"""
    return t1 * (d2 / d1) ** k


# 10km 配速（s/km）→ VDOT：在查表内找配速最接近的行（配速随 VDOT 单调递减）
@lru_cache(maxsize=1)
def _pace_to_vdot_lookup() -> list[tuple[int, int]]:
    return sorted(((row["marathon"], v) for v, row in vdot_table().items()), reverse=True)


def lookup_vdot_by_10k_pace(pace_sec_per_km: float) -> int:
    table = vdot_table()
    best_v, best_d = VDOT_MIN, float("inf")
    for v in sorted(table):
        d = abs(table[v]["marathon"] - pace_sec_per_km)
        if d < best_d:
            best_v, best_d = v, d
    return best_v


def vdot_from_recent_race(recent_km: float, recent_sec: float, ability: str) -> int:
    """最近一次跑步 → 折算 10km → 反查 VDOT；A 类乘 0.92 保守系数。"""
    t_10k = predict_time(recent_sec, recent_km, 10.0)
    pace_10k = t_10k / 10.0
    v = lookup_vdot_by_10k_pace(pace_10k)
    if ability == "A_zero_base":
        v *= ZERO_BASE_CONSERVATIVE_FACTOR
    return max(VDOT_MIN, min(60, int(round(v))))


def implied_vdot(actual_km: float, actual_sec: float) -> int:
    """由一次已完成的跑步反推 VDOT（能力重估用）。"""
    return lookup_vdot_by_10k_pace(predict_time(actual_sec, actual_km, 10.0) / 10.0)


# ---------------------------------------------------------------- 护栏共用判定
@dataclass
class Verdict:
    ok: bool
    rule: str = ""
    detail: str = ""

    def __bool__(self):
        return self.ok


def check_spike(prev_km: float, cur_km: float) -> Verdict:
    """周增幅 ≤10%。首周 prev_km=0 直接 PASS。"""
    if prev_km <= 0:
        return Verdict(True)
    gain = (cur_km - prev_km) / prev_km
    if gain > MAX_WEEKLY_GAIN:
        return Verdict(False, "WEEKLY_GAIN",
                       f"周增幅 {gain:.0%} 超过上限 10%（上限 {prev_km * (1 + MAX_WEEKLY_GAIN):.1f} km）")
    return Verdict(True)


def check_8020(sessions: list[dict]) -> Verdict:
    """80/20 强度分布：低强度（easy/marathon）跑量占比 ≥ 80%。"""
    total = sum(s["target_km"] for s in sessions)
    if total <= 0:
        return Verdict(True)
    low = sum(s["target_km"] for s in sessions if s["pace_zone"] in ("easy", "marathon"))
    if low / total >= 0.80:
        return Verdict(True)
    return Verdict(False, "INTENSITY_8020", f"低强度占比 {low / total:.0%} < 80%")


# ---------------------------------------------------------------- 课次上限
def session_cap(avail_days: int) -> int:
    """SESSION_CAP 唯一来源：生成器和重排引擎都从这里读，不允许各自硬编码。"""
    cap_map = TEMPLATE["session_cap_by_avail_days"]
    return cap_map.get(min(avail_days, max(cap_map)), min(avail_days, 3))


def phase_of(week_no: int) -> str:
    if week_no in TEMPLATE["taper_weeks"]:
        return "taper"
    return TEMPLATE["mesocycle"][(week_no - 1) % len(TEMPLATE["mesocycle"])]


def long_run_cap(week_km: float) -> float:
    return week_km * TEMPLATE["long_run_cap_of_weekly_km"]
