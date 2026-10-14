"""护栏层：三条红线，一个不可绕过的纯函数。

工程约束：本包不允许 import 任何优化/重排相关代码（CI 静态检查强制）。
护栏的目标函数是「这个方案会不会伤人」，与重排的目标函数（这周怎么排最合理）会冲突，
必须物理隔离。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import floor
from typing import Optional

from server.knowledge.rules import HARD_ZONES, MAX_WEEKLY_GAIN, Verdict, check_spike


@dataclass
class GuardVerdict:
    ok: bool
    hit_rule: str = ""
    action: str = "pass"          # pass / patch / block
    reason_user: str = ""         # 必须能生成一句用户读得懂的人话
    detail: str = ""


# ---------------------------------------------------------------- 三条红线
def R_WEEKLY_GAIN(plan_km: float, prev_week_km: float) -> Verdict:
    """红线一：周增幅 ≤10%。书籍里最硬的一条，v3 就栽在这。"""
    return check_spike(prev_week_km, plan_km)


def R_CONSEC_HARD(sessions: list[dict]) -> Verdict:
    """红线二：无连续 3 天高强度。受伤最主要的近因是高强度堆叠。"""
    by_date: dict[date, list[dict]] = {}
    for s in sessions:
        d = s["date"] if isinstance(s["date"], date) else date.fromisoformat(s["date"])
        by_date.setdefault(d, []).append(s)
    days = sorted(by_date)
    for i in range(len(days) - 2):
        tri = [by_date[days[i + k]] for k in range(3)]
        # 连续 3 个日历日，每天都含高强度课
        if all(any(s2["pace_zone"] in HARD_ZONES for s2 in day) for day in tri):
            return Verdict(False, "CONSECUTIVE_HARD",
                           f"{days[i]} 起连续 3 天排了高强度课，必须拆开")
    return Verdict(True)


def R_INJURY_WINDOW(injury_reports_14d: int, sessions: list[dict] | None = None,
                    plan_km: float = 0.0, prev_week_km: float = 0.0) -> Verdict:
    """红线三：14 天内伤痛上报 ≥2 次 → 本周只允许降载形态。

    降载形态 = 全部课都是轻松跑，且周跑量不超上周 +10%。
    已是降载形态则放行（pass），否则 block —— 不允许 patch 绕过。
    """
    if injury_reports_14d < 2:
        return Verdict(True)
    if sessions is not None:
        planned = [s for s in sessions if s.get("status", "planned") == "planned"]
        all_easy = all(s["pace_zone"] == "easy" for s in planned) if planned else True
        gain_ok = prev_week_km <= 0 or plan_km <= prev_week_km * (1 + MAX_WEEKLY_GAIN)
        if all_easy and gain_ok:
            return Verdict(True)
    return Verdict(False, "INJURY_WINDOW",
                   "近 14 天伤痛上报 ≥2 次，本周强制降载（只保留轻松跑，且不允许补偿）")


_RULE_TEXT = {
    "WEEKLY_GAIN": "本周跑量相比上周涨得太快（超过 10%），受伤风险高，已自动压回安全上限",
    "CONSECUTIVE_HARD": "连续 3 天高强度课会把身体堆垮，已自动拆开",
    "INJURY_WINDOW": "你最近上报的伤痛有点集中，本周先降到轻松跑养一养",
}


def check(plan_km: float,
          sessions: list[dict],
          ctx: dict,
          ) -> GuardVerdict:
    """所有候选（包括「什么都不做」）都必须过它，一次都不允许例外。

    ctx 需要：prev_week_km、injury_reports_14d
    """
    checks = [
        R_WEEKLY_GAIN(plan_km, ctx.get("prev_week_km", 0.0)),
        R_CONSEC_HARD(sessions),
        R_INJURY_WINDOW(ctx.get("injury_reports_14d", 0), sessions, plan_km,
                        ctx.get("prev_week_km", 0.0)),
    ]
    for v in checks:
        if not v.ok:
            if v.rule == "INJURY_WINDOW":
                return GuardVerdict(ok=False, hit_rule=v.rule, action="block",
                                    reason_user=_RULE_TEXT[v.rule], detail=v.detail)
            return GuardVerdict(ok=False, hit_rule=v.rule, action="patch",
                                reason_user=_RULE_TEXT[v.rule], detail=v.detail)
    return GuardVerdict(ok=True)


def check_with_patch(plan_km: float, sessions: list[dict], ctx: dict) -> tuple[GuardVerdict, float, list[dict]]:
    """护栏 + patch 动作：可修的规则自动修（压跑量），block 类直接拒绝。

    返回 (verdict, patched_km, patched_sessions)。
    """
    v = check(plan_km, sessions, ctx)
    if v.ok:
        return v, plan_km, sessions
    if v.action == "block":
        return v, plan_km, sessions
    # patch：WEEKLY_GAIN → 压回上限；CONSEC_HARD → 把最后一天的高强度降为 easy
    patched_km, patched = plan_km, [dict(s) for s in sessions]
    if v.hit_rule == "WEEKLY_GAIN":
        prev = ctx.get("prev_week_km", 0.0)
        if prev > 0:
            limit = prev * (1 + MAX_WEEKLY_GAIN)
            ratio = limit / plan_km
            for s in patched:
                s["target_km"] = s["target_km"] * ratio      # 中间不取整，避免 0.1 精度吃掉压量
            total = sum(s["target_km"] for s in patched)
            if total > limit:                                 # 双保险：仍超限则等比缩
                ratio2 = limit / total
                for s in patched:
                    s["target_km"] *= ratio2
            # 输出向下取整到 0.1：只舍不入，取整后总量仍不超上限；显示层也不再出现长浮点
            for s in patched:
                s["target_km"] = floor(s["target_km"] * 10) / 10
            patched_km = round(sum(s["target_km"] for s in patched), 1)
        return GuardVerdict(ok=True, hit_rule="WEEKLY_GAIN", action="patch",
                            reason_user=v.reason_user, detail=v.detail), patched_km, patched
    if v.hit_rule == "CONSECUTIVE_HARD":
        by_date: dict[date, list[dict]] = {}
        for s in patched:
            d = s["date"] if isinstance(s["date"], date) else date.fromisoformat(s["date"])
            by_date.setdefault(d, []).append(s)
        days = sorted(by_date)
        for i in range(len(days) - 2):
            tri_days = days[i:i + 3]
            if all(any(s2["pace_zone"] in HARD_ZONES for s2 in by_date[d]) for d in tri_days):
                # 把 tri 最后一天的高强度课降为 easy
                for s in by_date[tri_days[-1]]:
                    if s["pace_zone"] in HARD_ZONES and not s.get("is_key"):
                        s["pace_zone"] = "easy"
        return GuardVerdict(ok=True, hit_rule="CONSECUTIVE_HARD", action="patch",
                            reason_user=v.reason_user, detail=v.detail), patched_km, patched
    return v, plan_km, sessions
