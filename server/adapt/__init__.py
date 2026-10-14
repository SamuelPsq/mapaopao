"""适应层：周级重排决策引擎。整个产品的核心。

设计要点：不是写死 if-else 链，而是「候选生成 → 护栏过滤（全量留痕）→ 打分排序」。
新策略只需加一个 generator；被拦的方案有留痕；排序可解释。
打分：0.55×本周交付概率 + 0.30×关键训练刺激保留率 + 0.15×安全余量。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from server.guard import check_with_patch
from server.infra.weather import WeatherDay
from server.knowledge.rules import INTENSITY_FACTOR, session_cap

# 配速降档顺序：interval → threshold → marathon → easy
DOWNGRADE_LADDER = {"interval": "threshold", "threshold": "marathon", "marathon": "easy"}


@dataclass
class Candidate:
    strategy: str                                  # replace / downgrade / compress / postpone / noop
    sessions: list[dict]                           # 本周排期（含被改的）
    description: str                               # 给用户看的改动说明
    delta_km: float = 0.0
    guard_action: str = "pass"                     # pass / patch / block
    guard_note: str = ""
    is_noop: bool = False


@dataclass
class RearrangeContext:
    week_no: int
    remaining: list[dict]          # 本周尚未执行的课（含 is_key 标记）
    avail_days_left: int           # 本周剩余可训练日
    weather: dict[str, WeatherDay] # date_str -> WeatherDay
    soreness_today: bool = False
    prev_week_km: float = 0.0
    planned_week_km: float = 0.0
    injury_reports_14d: int = 0
    trigger_src: str = ""          # weather / body / schedule / manual

    def context_hash(self) -> str:
        payload = {
            "week_no": self.week_no,
            "sessions": [(s["session_id"], s["date"], s["status"]) for s in self.remaining],
            "avail": self.avail_days_left,
            "sore": self.soreness_today,
            "inj": self.injury_reports_14d,
            "trigger": self.trigger_src,
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- 打分函数
def p_complete(ctx: RearrangeContext, sessions: list[dict]) -> float:
    """本周还能不能交付 —— 逐日累乘存活概率（base 0.18 + 天气惩罚 + 酸痛惩罚）。"""
    p = 1.0
    for s in sessions:
        d = ctx.weather.get(s["date"])
        pen = 0.0
        if d is not None:
            pen = 0.35 if d.hard_constraint else (0.15 if d.apparent_max >= 30 else 0.0)
        pen += 0.20 if ctx.soreness_today else 0.0
        pen += 0.12 if s.get("postponed") else 0.0   # 顺延的课再被打断就没有缓冲日（PRD 10.2）
        p *= (1 - min(max(0.18 + pen, 0.03), 0.75))
    return p


def key_stimulus_kept(ctx: RearrangeContext, sessions: list[dict]) -> float:
    """关键训练刺激保留率 —— 按强度当量算，不是按公里算。"""
    src = sum(s["target_km"] * INTENSITY_FACTOR.get(s["pace_zone"], 1.0)
              for s in ctx.remaining if s.get("is_key"))
    kept = sum(s["target_km"] * INTENSITY_FACTOR.get(s["pace_zone"], 1.0)
               for s in sessions if s.get("is_key"))
    return kept / src if src else 1.0


def safety_margin(ctx: RearrangeContext, sessions: list[dict]) -> float:
    """安全余量：跑量增幅越激进余量越低；相邻训练日堆叠（连练）再扣分。"""
    km = sum(s["target_km"] for s in sessions)
    if ctx.prev_week_km > 0:
        gain = max(0.0, (km - ctx.prev_week_km) / ctx.prev_week_km)
        margin = 1 - min(gain / 0.30, 1.0)
    else:
        margin = 1.0
    days = sorted({s["date"] for s in sessions if s["status"] == "planned"})
    adj = sum(1 for a, b in zip(days, days[1:])
              if (date.fromisoformat(b) - date.fromisoformat(a)).days == 1)
    return max(0.0, margin - 0.08 * adj)


def score(ctx: RearrangeContext, sessions: list[dict]) -> float:
    return (0.55 * p_complete(ctx, sessions)
            + 0.30 * key_stimulus_kept(ctx, sessions)
            + 0.15 * safety_margin(ctx, sessions))


# ---------------------------------------------------------------- 候选生成器
def _week_dates(sessions: list[dict]) -> list[str]:
    return sorted({s["date"] for s in sessions})


def gen_replace(ctx: RearrangeContext) -> Optional[Candidate]:
    """替换：硬约束导致户外不可行 → 改室内交叉训练 40min（保强度、避雨天）。"""
    replaced = copy.deepcopy(ctx.remaining)
    changed = []
    for s in replaced:
        d = ctx.weather.get(s["date"])
        if d and d.hard_constraint and s["type"] != "cross_train":
            old = f"{s['type']} {s['target_km']}km"
            s.update(type="cross_train", pace_zone="easy", target_km=0.0,
                     note=f"室内交叉训练 40min（原 {old}）")
            changed.append(s)
    if not changed:
        return None
    delta = sum(c.get("_orig_km", 0) for c in [])  # 里程让位给时长，不计 delta
    desc = "；".join(f"{_dow(c['date'])}{c.get('note', '')}" for c in changed)
    return Candidate("replace", replaced, f"把 {desc}，避开坏天气", delta_km=0.0)


def gen_downgrade(ctx: RearrangeContext) -> Optional[Candidate]:
    """降级：状态偏差 / 高温 → 降强度不改课型（间歇→节奏，节奏→轻松），高温日时长削 20%。"""
    if not (ctx.soreness_today or any(w.hot_soft for w in ctx.weather.values())):
        return None
    out = copy.deepcopy(ctx.remaining)
    changed = []
    for s in out:
        d = ctx.weather.get(s["date"])
        hot = bool(d and d.hot_soft)
        if ctx.soreness_today and s["pace_zone"] in ("interval", "threshold"):
            old_zone = s["pace_zone"]
            s["pace_zone"] = DOWNGRADE_LADDER[old_zone]
            s["note"] = f"{_zone_cn(old_zone)}→{_zone_cn(s['pace_zone'])}"
            changed.append(s)
        elif hot and not s.get("is_key"):
            s["target_km"] = round(s["target_km"] * 0.8, 1)
            s["note"] = "高温，时长削 20%"
            changed.append(s)
        elif hot and s["pace_zone"] in ("interval", "threshold"):
            old_zone = s["pace_zone"]
            s["pace_zone"] = DOWNGRADE_LADDER[old_zone]
            s["note"] = f"高温，{_zone_cn(old_zone)}→{_zone_cn(s['pace_zone'])}"
            changed.append(s)
    if not changed:
        return None
    orig_km = sum(s["target_km"] for s in ctx.remaining)
    new_km = sum(s["target_km"] for s in out)
    desc = "；".join(f"{_dow(c['date'])}{c.get('note', '')}" for c in changed)
    return Candidate("downgrade", out, f"{desc}", delta_km=round(orig_km - new_km, 1))


def gen_compress(ctx: RearrangeContext) -> Optional[Candidate]:
    """压缩：砍辅助课保关键课（牺牲轻松跑，绝不牺牲长距离与质量课）。

    可跑天数 = 剩余可用日 - 受硬约束的天气日；装不下时砍辅助课。
    """
    pending = [s for s in ctx.remaining if s["status"] == "planned"]
    blocked_days = {d for d, w in ctx.weather.items() if w.hard_constraint}
    good_days = max(1, ctx.avail_days_left - len(blocked_days & {s["date"] for s in pending}))
    if len(pending) <= good_days:
        return None
    out = copy.deepcopy(ctx.remaining)
    # 砍课顺序：非关键 easy → 非关键 marathon → 其余非关键；关键课一个不动
    drop_order = sorted(
        (s for s in out if s["status"] == "planned" and not s.get("is_key")),
        key=lambda s: (INTENSITY_FACTOR.get(s["pace_zone"], 1.0), -s["target_km"]),
        reverse=True,
    )
    to_drop: list[dict] = []
    for s in drop_order:
        if len(pending) - len(to_drop) <= good_days:
            break
        to_drop.append(s)
    if not to_drop:
        return None
    drop_ids = {id(s) for s in to_drop}
    kept, dropped = [], []
    for s in out:
        if id(s) in drop_ids:
            s["status"] = "cancelled_by_rearrange"
            dropped.append(s)
        else:
            kept.append(s)
    delta = sum(s["target_km"] for s in dropped)
    desc = "删" + "、".join(f"{_dow(s['date'])}{_zone_cn(s['pace_zone'])} {s['target_km']}km" for s in dropped)
    return Candidate("compress", kept, desc, delta_km=round(delta, 1))


def gen_postpone(ctx: RearrangeContext) -> Optional[Candidate]:
    """顺延：本周内换一天。仅当前三条都不适用时使用 —— 打分函数会自然把它压到最后。"""
    pending = [s for s in ctx.remaining if s["status"] == "planned"]
    if not pending:
        return None
    out = copy.deepcopy(ctx.remaining)
    week = _week_dates(ctx.remaining)
    if not week:
        return None
    week_start = date.fromisoformat(week[0])
    occupied = {s["date"] for s in out if s["status"] == "planned"}
    moved = []
    for s in sorted(out, key=lambda x: (not x.get("is_key"), x["date"])):
        d = ctx.weather.get(s["date"])
        blocked = bool(d and d.hard_constraint)
        if not blocked and s["date"] in occupied:
            continue
        # 找一个本周内更晚的空闲且不受硬约束的日子
        for off in range(1, 8):
            nd = (week_start + timedelta(days=off)).isoformat()
            if nd >= s["date"] and nd not in occupied and nd <= week[-1]:
                nd_w = ctx.weather.get(nd)
                if nd_w and nd_w.hard_constraint:
                    continue
                occupied.discard(s["date"])
                occupied.add(nd)
                old = s["date"]
                s["date"] = nd
                s["postponed"] = True
                moved.append((old, nd, s))
                break
    if not moved:
        return None
    desc = "；".join(f"{_dow(o)}→{_dow(n)} {_zone_cn(s['pace_zone'])}" for o, n, s in moved)
    return Candidate("postpone", out, f"顺延：{desc}", delta_km=0.0)


def gen_deload(ctx: RearrangeContext) -> Optional[Candidate]:
    """强制降载：14 天内伤痛 ≥2 次 → 本周只保留轻松跑（红线三的唯一放行形态）。

    已是降载形态（全部 easy）则不重复生成，避免接受方案时二次压量。
    """
    if ctx.injury_reports_14d < 2:
        return None
    pending = [s for s in ctx.remaining if s["status"] == "planned"]
    if not pending:
        return None
    if all(s["pace_zone"] == "easy" for s in pending):
        return None                                   # 本周已在降载形态
    out = copy.deepcopy(ctx.remaining)
    orig_km = 0.0
    new_km = 0.0
    for s in out:
        if s["status"] != "planned":
            continue
        orig_km += s["target_km"]
        s["type"] = "easy_run"
        s["pace_zone"] = "easy"
        s["target_km"] = round(s["target_km"] * 0.6, 1)   # 降载：量也压下来
        s["note"] = "伤痛预警，强制降载"
        new_km += s["target_km"]
    return Candidate("deload", out, "本周强制降载：强度课全部取消，只保留轻松跑",
                     delta_km=round(orig_km - new_km, 1))


def gen_noop(ctx: RearrangeContext) -> Candidate:
    """永远保留的兜底：什么都不做 / 标记降载。缺口是沉没成本，不补。"""
    return Candidate("noop", copy.deepcopy(ctx.remaining), "保持原计划不变", is_noop=True)


GENERATORS = [gen_replace, gen_downgrade, gen_compress, gen_deload, gen_postpone]


# ---------------------------------------------------------------- 主入口
def rearrange(ctx: RearrangeContext) -> tuple[Optional[Candidate], Optional[Candidate], list[dict]]:
    """返回 (主方案, 备选, 护栏审计记录)。所有候选全量过护栏，连 pass 也留痕。"""
    audit: list[dict] = []
    candidates: list[Candidate] = []
    guard_ctx = {"prev_week_km": ctx.prev_week_km, "injury_reports_14d": ctx.injury_reports_14d}
    for gen in GENERATORS:
        c = gen(ctx)
        if c is None:
            continue
        plan_km = sum(s["target_km"] for s in c.sessions)
        verdict, patched_km, patched_sessions = check_with_patch(plan_km, c.sessions, guard_ctx)
        audit.append({
            "strategy": c.strategy, "hit_rule": verdict.hit_rule or None,
            "action": verdict.action, "reason_user": verdict.reason_user,
        })
        if not verdict.ok:
            continue                                   # block：直接出局
        c.sessions = patched_sessions
        c.guard_action = verdict.action
        c.guard_note = verdict.reason_user
        candidates.append(c)
    noop = gen_noop(ctx)
    plan_km = sum(s["target_km"] for s in noop.sessions)
    v, _, _ = check_with_patch(plan_km, noop.sessions, guard_ctx)
    audit.append({"strategy": "noop", "hit_rule": v.hit_rule or None, "action": v.action,
                  "reason_user": v.reason_user})
    noop.guard_action = v.action
    noop.guard_note = v.reason_user
    if v.ok:
        candidates.append(noop)                    # 兜底只在通过护栏时参与排序
    elif not candidates:
        return noop, None, audit                   # 全部候选被 block：强制返回原样兜底（护栏已留痕）

    scored = sorted(candidates, key=lambda c: score(ctx, c.sessions), reverse=True)
    main = scored[0] if scored else None
    alt = scored[1] if len(scored) > 1 else None
    return main, alt, audit


def _dow(iso: str) -> str:
    return "周" + "一二三四五六日"[date.fromisoformat(iso).weekday()]


def _zone_cn(zone: str) -> str:
    return {"easy": "轻松跑", "marathon": "马拉松配速", "threshold": "阈值跑",
            "interval": "间歇跑", "repetition": "重复跑", "cross_train": "交叉训练"}.get(zone, zone)
