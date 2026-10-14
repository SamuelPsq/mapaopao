"""规划层：16 周计划生成 pipeline（7 步，纯 CPU，幂等）。

① 护栏前置（劝退判定，在任何计算之前）
② 能力参数化：成绩 → VDOT → 5 档配速区间
③ 课次上限收敛（SESSION_CAP 唯一来源在 knowledge.rules）
④ 周跑量基线与周期展开（3 递增 + 1 减量，15-16 减量周）
⑤ 生成即过护栏（不是事后检查）
⑥ 幂等：同 profile 哈希 → 同计划
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta

from server.guard import check_with_patch
from server.knowledge.rules import (MAX_WEEKLY_GAIN, TEMPLATE, check_8020, check_spike,
                                    long_run_cap, pace_zones, phase_of, session_cap,
                                    vdot_from_recent_race)

SESSION_TYPES = {
    # type: (pace_zone, 时长分钟估计 per km 系数用于展示)
    "easy_run": "easy",
    "long_run": "marathon",
    "threshold": "threshold",
    "interval": "interval",
}


@dataclass
class Reject:
    reason: str
    suggest: str


@dataclass
class Profile:
    recent_km: float
    recent_sec: float
    avail_days: int
    goal_race: str = "full"          # full / half
    ability: str = "B_base"          # A_zero_base / B_base / C_advanced
    injury_flag: str = "N"           # N / M（3 个月内大伤）
    major_injury_in_3m: bool = False
    city: str = "上海"
    lat: float = 31.23
    lon: float = 121.47
    max_duration: int = 90           # 单次最长分钟


def validate_intake(p: Profile) -> Reject | None:
    """准入门槛：护栏前置，在任何计算之前。命中返回 200 + Reject（产品结果，不是错误码）。"""
    if p.avail_days < 2:
        return Reject("CAPACITY_OR_INJURY", "每周可用时间不足 2 天，建议先养成跑步习惯，或把目标改为半马/健康跑")
    if p.injury_flag == "M" or p.major_injury_in_3m:
        return Reject("CAPACITY_OR_INJURY", "3 个月内有较重伤痛，请先就医/康复，恢复后再来生成计划")
    if p.goal_race == "full" and p.avail_days < 3:
        return Reject("CAPACITY_OR_INJURY", "全马至少需要每周 3 天可用时间，建议改为半马目标或延期")
    return None


def estimate_base_km(vdot: int, cap: int, max_duration: int) -> float:
    """周跑量基线：能力越强、可练天数越多，基线越高。首周从保守值开始。"""
    base = 14.0 + (vdot - 30) * 0.55 + (cap - 3) * 2.5
    return min(base, max_duration / 7 * cap * 1.6)   # 被单次时长约束


def ramp_factor(week_no: int) -> float:
    """3 周递增 + 1 周减量的展开系数（相对基线）。"""
    cycle = (week_no - 1) % 4
    return [1.00, 1.10, 1.21, 0.85][cycle]


def _layout_sessions(week_no: int, week_km: float, zones: dict, cap: int,
                     phase: str, goal_race: str) -> list[dict]:
    """把周跑量切成 cap 节课：关键课固定 2 次（质量课 + 长距离），其余为辅助轻松跑。

    返回 [{seq, offset_days, type, pace_zone, target_km, is_key, target_pace}]
    """
    key_km = round(week_km * 0.30, 1)          # 长距离 ≈ 30%
    quality_km = round(week_km * 0.20, 1)      # 质量课 ≈ 20%
    if phase == "taper":
        key_km = round(key_km * 0.6, 1)
        quality_km = round(quality_km * 0.6, 1)
    aux_n = max(0, cap - 2)
    aux_total = max(0.0, round(week_km - key_km - quality_km, 1))
    aux_km = round(aux_total / aux_n, 1) if aux_n else 0.0

    # 峰值期（week 9-12 附近的 load 第三周）质量课换间歇
    cycle_pos = (week_no - 1) % 4
    quality_type = "interval" if (phase == "load" and cycle_pos == 2 and week_no >= 5) else "threshold"

    # taper 周没有间歇
    if phase == "taper":
        quality_type = "marathon"

    def pace_of(zone: str) -> str:
        lo, hi = zones[zone]
        from server.knowledge.rules import sec_to_pace
        return f"{sec_to_pace(lo)}–{sec_to_pace(hi)}" if lo != hi else sec_to_pace(lo)

    sessions = [
        {"seq": 1, "offset": 1, "type": quality_type, "pace_zone": quality_type,
         "target_km": quality_km, "is_key": True,
         "target_pace": pace_of("interval" if quality_type == "interval" else "threshold")},
        {"seq": 2, "offset": 4, "type": "easy_run", "pace_zone": "easy",
         "target_km": aux_km, "is_key": False, "target_pace": pace_of("easy")},
        {"seq": 3, "offset": 5, "type": "long_run", "pace_zone": "marathon",
         "target_km": key_km, "is_key": True, "target_pace": pace_of("marathon")},
    ]
    # 第 4、5 节课（cap=4/5 时）：轻松跑，插在周三、周日
    extra_offsets = [2, 6]
    for i in range(min(aux_n - 1, 2)):
        sessions.append({"seq": 4 + i, "offset": extra_offsets[i], "type": "easy_run",
                         "pace_zone": "easy", "target_km": aux_km, "is_key": False,
                         "target_pace": pace_of("easy")})
    sessions.sort(key=lambda s: s["offset"])
    for i, s in enumerate(sessions, 1):
        s["seq"] = i
    return sessions


def generate_plan(p: Profile) -> dict | Reject:
    """链路 A：一次性生成 16 周计划。P95 < 50ms，纯 CPU，无网络。"""
    # ① 护栏前置
    rej = validate_intake(p)
    if rej:
        return rej
    # ② 能力参数化
    vdot = vdot_from_recent_race(p.recent_km, p.recent_sec, p.ability)
    zones = pace_zones(vdot)
    # ③ 课次上限收敛
    cap = session_cap(p.avail_days)
    # ④ 周跑量基线与周期展开
    base_km = estimate_base_km(vdot, cap, p.max_duration)
    target_weeks = 14 if p.goal_race == "half" else 16
    weeks: list[dict] = []
    prev_km = 0.0
    prev_phase: str | None = None
    prev_load_km = 0.0      # 最近一个负荷周（非 cut/taper）的跑量
    for w in range(1, target_weeks + 1):
        phase = phase_of(w) if p.goal_race == "full" else ("taper" if w >= target_weeks - 1 else phase_of(w))
        km = base_km * ramp_factor(w)
        if phase == "cut":
            km *= TEMPLATE["cut_ratio"]
        if phase == "taper":
            km *= [0.6, 0.4][min(w - 15, 1)] if p.goal_race == "full" else 0.5
        if weeks:
            # 周增幅红线以「上一个负荷周」为基准：cut/taper 是主动恢复，
            # 不能让它把后续周永久锁死在低跑量（否则计划从第 5 周起整体塌缩）
            baseline = prev_load_km if prev_phase in ("cut", "taper") else prev_km
            km = min(km, baseline * (1 + MAX_WEEKLY_GAIN))
        km = round(km, 1)
        sessions = _layout_sessions(w, km, zones, cap, phase, p.goal_race)
        # 80/20 不通过则回填轻松跑（保持低强度占比）
        v8020 = check_8020(sessions)
        if not v8020.ok:
            for s in sessions:
                if s["pace_zone"] in ("interval", "threshold"):
                    s["target_km"] = round(s["target_km"] * 0.8, 1)
        weeks.append({"week_no": w, "phase": phase, "planned_km": km, "is_dropped": False,
                      "sessions": sessions})
        if phase not in ("cut", "taper"):
            prev_load_km = km
        prev_phase = phase
        prev_km = km
    # ⑤ 生成即过护栏
    anchor = date(2024, 1, 1)          # 合成周一锚点，只为给模板课注入日期供红线判定
    for idx, w in enumerate(weeks):
        probe = [dict(s, date=(anchor + timedelta(days=7 * (w["week_no"] - 1) + s["offset"] - 1)).isoformat())
                 for s in w["sessions"]]
        prev = weeks[idx - 1]["planned_km"] if idx > 0 else 0.0
        if idx > 0 and weeks[idx - 1]["phase"] in ("cut", "taper"):
            # 与生成循环同口径：上一周是主动恢复周时，增幅基准回溯到最近的负荷周
            for pidx in range(idx - 1, -1, -1):
                if weeks[pidx]["phase"] not in ("cut", "taper"):
                    prev = weeks[pidx]["planned_km"]
                    break
        v, patched_km, patched = check_with_patch(
            w["planned_km"], probe,
            {"prev_week_km": prev,
             "injury_reports_14d": 0})
        if v.action == "patch" and v.hit_rule == "WEEKLY_GAIN":
            w["planned_km"] = patched_km
            for orig, new in zip(w["sessions"], patched):
                orig["target_km"] = new["target_km"]
    # ⑥ 幂等
    profile_hash = hashlib.sha1(json.dumps(p.__dict__, sort_keys=True).encode()).hexdigest()[:8]
    return {"vdot": vdot, "cap": cap, "profile_hash": profile_hash, "weeks": weeks,
            "goal_race": p.goal_race, "target_weeks": target_weeks}


def should_suggest_degrade(weeks: list[dict], recent_weeks: int = 3) -> bool:
    """反增长设计：连续 3 周关键课完成率 <50% → 建议降目标。只弹卡片，决定权在用户。"""
    recent = weeks[-recent_weeks:]
    if len(recent) < recent_weeks:
        return False
    done = sum(w.get("key_done", 0) for w in recent)
    plan = sum(w.get("key_plan", 0) for w in recent)
    return plan > 0 and done / plan < 0.50
