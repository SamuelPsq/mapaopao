# -*- coding: utf-8 -*-
"""AI 微调层：LLM 参与周课表安排，但只在严格边界内"提建议"，规则校验通过才落地。

设计原则（VDOT 算法主权不让渡）：
- 课表由规则引擎（VDOT 查表 + 周期模板）生成，AI 只能微调；
- 关键课一律不许动；只能：把非关键课移到本周另一天 / 非关键课跑量 ±10% 内调整；
- 周总跑量变化 ≤ 3%，每周最多 3 个操作，每节课最多 1 个操作；
- LLM 失败、输出非法或校验不过 → 保持规则课表，埋点留痕。
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Optional

from . import db

# ---------------------------------------------------------------- 边界常量
KM_STEP = 0.5          # 跑量取整步长
KM_MIN, KM_MAX = 3.0, 30.0
KM_TOL = 0.10          # 单次调整 ±10%
WEEK_TOL = 0.03        # 周总变化 ±3%
MAX_OPS = 3

TYPE_CN = {"easy_run": "轻松跑", "long_run": "长距离", "tempo": "节奏跑",
           "interval": "间歇跑", "recovery": "恢复跑", "rest": "休息"}
DOW_CN = "一二三四五六日"


def _dow(d: date) -> str:
    return "周" + DOW_CN[d.weekday()]


# ---------------------------------------------------------------- 事实组装
def build_facts(week_no: int, phase: str, sessions: list, bad_dates: list[date],
                sore_part: str) -> dict:
    """喂给 LLM 的全部事实——数字全部来自规则引擎，模型不许引入新数字。"""
    planned = [x for x in sessions if x.status == "planned"]
    return {
        "week_no": week_no, "phase": phase,
        "soreness": sore_part or "none",
        "bad_weather_days": [_dow(d) for d in bad_dates],
        "sessions": [
            {"session_id": x.session_id, "date": x.date.isoformat(), "dow": _dow(x.date),
             "type": TYPE_CN.get(x.type, x.type), "pace_zone": x.pace_zone,
             "km": x.target_km, "key": bool(x.is_key)}
            for x in planned
        ],
    }


# ---------------------------------------------------------------- 硬校验（比 prompt 管用）
def validate_ops(ops: list, sessions: list, week_start: date) -> Optional[list]:
    """校验 LLM 提议。通过返回规范化 ops，任何一条越界返回 None（整体拒收）。"""
    if not isinstance(ops, list) or len(ops) > MAX_OPS:
        return None
    by_id = {x.session_id: x for x in sessions}
    touched: set[str] = set()
    total = sum(x.target_km for x in sessions if x.status == "planned")
    # 结构红线：辅助课调整后的跑量不得超过本周长距离的 70%。
    # 没有长距离（异常周）时按关键课最大值兜底；都没有则不允许任何加量。
    long_km = max((x.target_km for x in sessions if getattr(x, "type", "") == "long_run"),
                  default=None)
    if long_km is None:
        long_km = max((x.target_km for x in sessions if x.is_key), default=None)
    aux_cap = round(long_km * 0.7, 2) if long_km else None
    delta = 0.0
    out = []
    for op in ops:
        if not isinstance(op, dict):
            return None
        sid, kind = op.get("session_id"), op.get("op")
        x = by_id.get(sid)
        # 只能动：存在、本周、未上、非关键
        if x is None or x.is_key or x.status != "planned" or sid in touched:
            return None
        if kind == "move":
            try:
                d = date.fromisoformat(str(op.get("to_date", "")))
            except ValueError:
                return None
            if not (week_start <= d <= week_start + timedelta(days=6)):
                return None
            clash = [y for y in sessions
                     if y.session_id != sid and y.date == d
                     and y.status not in ("cancelled_by_rearrange", "cancelled_before_week")]
            if clash:
                return None
            out.append({"op": "move", "session_id": sid, "to_date": d})
        elif kind == "adjust":
            km = op.get("km")
            if not isinstance(km, (int, float)) or isinstance(km, bool):
                return None
            km = round(float(km), 2)
            if not (KM_MIN <= km <= KM_MAX):
                return None
            if abs(km / KM_STEP - round(km / KM_STEP)) > 1e-6:      # 0.5 的倍数
                return None
            if abs(km - x.target_km) > x.target_km * KM_TOL + 1e-9:  # ±10%
                return None
            if km > x.target_km and aux_cap is not None and km > aux_cap:
                return None      # 结构红线：辅助课不得膨胀超过长距离的 70%
            delta += km - x.target_km
            out.append({"op": "adjust", "session_id": sid, "km": km})
        else:
            return None
        touched.add(sid)
    if total and abs(delta) > total * WEEK_TOL + 1e-9:
        return None
    return out


# ---------------------------------------------------------------- LLM 提议
_SCHEMA = {"type": "object",
           "properties": {"ops": {"type": "array", "items": {"type": "object",
                            "properties": {"op": {"type": "string"},
                                           "session_id": {"type": "string"},
                                           "to_date": {"type": "string"},
                                           "km": {"type": "number"}},
                            "required": ["op", "session_id"]}}},
           "required": ["ops"]}


def propose_ops(facts: dict, rag_ctx: Optional[dict] = None) -> Optional[list]:
    """调网关让 LLM 提微调建议；失败/非法返回 None。

    rag_ctx 非空时走 tune_v2（注入知识库 + 召回案例）；为 None 时保持 v1 行为。
    """
    base, key, model = os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY"), os.getenv("LLM_MODEL")
    if not (base and key and model):
        return None
    try:
        import httpx
        from .prompts import build_tune_prompt
        prompt, _ver = build_tune_prompt(facts, rag_ctx)
        payload = {"model": model, "temperature": 0.2, "max_tokens": 600, "timeout": 3.0,
                   "messages": [{"role": "user", "content": prompt}],
                   "response_format": {"type": "json_schema",
                                       "json_schema": {"name": "ai_tune", "schema": _SCHEMA}}}
        with httpx.Client(timeout=4.0) as client:
            r = client.post(f"{base.rstrip('/')}/chat/completions", json=payload,
                            headers={"Authorization": f"Bearer {key}"})
            if r.status_code == 400:
                # 部分网关（如 DeepSeek）不接受 json_schema，降级 json_object
                payload.pop("response_format", None)
                payload["messages"][0]["content"] += "。只输出一个 JSON 对象，形如 {\"ops\": [...]}"
                # deepseek-flash 默认开思考模式会吃掉 token 预算，显式关闭
                payload["thinking"] = {"type": "disabled"}
                payload["max_tokens"] = 1500
                r = client.post(f"{base.rstrip('/')}/chat/completions", json=payload,
                                headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        data = json.loads(_extract_json(text))
        ops = data.get("ops", []) if isinstance(data, dict) else None
        return ops if isinstance(ops, list) else None
    except Exception:
        return None


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else text


# ---------------------------------------------------------------- 编排入口
def maybe_tune_week(user: db.User, plan: db.Plan, week_no: int, bad_dates: list[date],
                    sore_part: str) -> str:
    """本周第一次被查看时，让 LLM 微调课表。幂等：每周最多调一次。

    返回 "llm"（AI 微调生效/确认无需调整）或 "template"（未配置或拒收，保持规则课表）。
    """
    with db.SessionLocal() as s:
        done = s.query(db.Event).filter_by(name="ai_tune", user_id=user.user_id).all()
        if any(e.payload.get("plan_id") == plan.plan_id and e.payload.get("week_no") == week_no
               for e in done):
            return "template"
        sessions = s.query(db.Session).filter_by(plan_id=plan.plan_id, week_no=week_no).all()
        if not sessions:
            return "template"
        week_start = plan.start_date + timedelta(days=7 * (week_no - 1))
        phase = (s.query(db.PlanWeek).filter_by(plan_id=plan.plan_id, week_no=week_no).first()
                 or None)
        facts = build_facts(week_no, phase.phase if phase else "", sessions, bad_dates, sore_part)
    ops = propose_ops(facts)
    ok = validate_ops(ops, sessions, week_start) if ops is not None else None
    src = "llm" if ok is not None else "template"
    if ok:
        with db.SessionLocal.begin() as s:
            by_id = {x.session_id: x for x in
                     s.query(db.Session).filter_by(plan_id=plan.plan_id, week_no=week_no).all()}
            for op in ok:
                row = by_id[op["session_id"]]
                if op["op"] == "move":
                    row.date = op["to_date"]
                else:
                    row.target_km = op["km"]
                row.source = "ai_tuned"
            live = [x for x in by_id.values() if x.status == "planned"]
            pw = s.query(db.PlanWeek).filter_by(plan_id=plan.plan_id, week_no=week_no).first()
            if pw:
                pw.planned_km = round(sum(x.target_km for x in live), 1)
    db.track("ai_tune", user.user_id, plan_id=plan.plan_id, week_no=week_no,
             applied=bool(ok), ops_cnt=len(ok) if ok else 0)
    db.track("llm_explain", user.user_id, scene="ai_tune", source=src)
    return src


# ---------------------------------------------------------------- 偏差触发微调（RAG + 案例召回）
_TRIGGER_CN = {"over_done": "超额完成", "under_done": "完成不足", "skipped": "缺课"}


def tune_after_deviation(user: db.User, plan: db.Plan, week_no: int, trigger: str,
                         dev_ratio: float, recent_logs: list[dict]) -> dict:
    """用户打卡偏差（超额/不足/跳过）后：RAG 检索 + 案例召回 → LLM 微调本周剩余课表。

    与 maybe_tune_week 的区别：那是周初预防性微调，这是事后响应式微调；
    但边界完全一样——非关键课、±10%、周变化 ≤3%，validate_ops 不过一律拒收。
    幂等：同一计划同一周最多微调一次——±10% 是相对当前值校验的，
    不冷却的话同一节轻松跑每次 +10% 会复利膨胀，最终超过长距离（结构失衡）。

    返回 {"applied": bool, "ops": list, "src": "llm"/"template", "case_id": str, "note": str}
    """
    from . import rag
    from .prompts import TUNE_PROMPT_VERSION

    with db.SessionLocal() as s:
        done = s.query(db.Event).filter_by(name="ai_tune_deviation", user_id=user.user_id).all()
        if any(e.payload.get("plan_id") == plan.plan_id and e.payload.get("week_no") == week_no
               for e in done):
            return {"applied": False, "ops": [], "src": "template", "case_id": "", "note": ""}

    week_start = plan.start_date + timedelta(days=7 * (week_no - 1))
    with db.SessionLocal() as s:
        sessions = s.query(db.Session).filter_by(plan_id=plan.plan_id, week_no=week_no).all()
        pw = s.query(db.PlanWeek).filter_by(plan_id=plan.plan_id, week_no=week_no).first()
        phase = pw.phase if pw else ""
        planned = [x for x in sessions if x.status == "planned"]
    if not planned:
        return {"applied": False, "ops": [], "src": "template", "case_id": "", "note": ""}
    # 事实：本周课表 + 最近实际 vs 目标（recent_logs 由调用方从 session_log 组装）
    facts = build_facts(week_no, phase, sessions, [], "")
    facts["deviation"] = {"trigger": trigger, "ratio": round(dev_ratio, 2),
                          "cn": _TRIGGER_CN.get(trigger, trigger),
                          "recent_actual_vs_target": recent_logs[-7:]}
    # RAG 检索：用触发类型 + 阶段构造查询
    query = f"{_TRIGGER_CN.get(trigger, trigger)} {phase} 训练计划调整"
    rag_ctx = rag.build_rag_context(user.user_id, query, phase, trigger, dev_ratio)
    ver = TUNE_PROMPT_VERSION

    raw = propose_ops(facts, rag_ctx)
    ok = validate_ops(raw, sessions, week_start) if raw is not None else None
    applied = bool(ok)
    if applied:
        with db.SessionLocal.begin() as s:
            by_id = {x.session_id: x for x in
                     s.query(db.Session).filter_by(plan_id=plan.plan_id, week_no=week_no).all()}
            for op in ok:
                row = by_id[op["session_id"]]
                if op["op"] == "move":
                    row.date = op["to_date"]
                else:
                    row.target_km = op["km"]
                row.source = "ai_tuned"
            live = [x for x in by_id.values() if x.status == "planned"]
            pw2 = s.query(db.PlanWeek).filter_by(plan_id=plan.plan_id, week_no=week_no).first()
            if pw2:
                pw2.planned_km = round(sum(x.target_km for x in live), 1)
    case_id = rag.record_case(user.user_id, week_no, phase, trigger, dev_ratio,
                              context={"prompt_ver": ver, **rag_ctx},
                              decision=ok)
    src = "llm" if applied else "template"
    db.track("ai_tune_deviation", user.user_id, plan_id=plan.plan_id, week_no=week_no,
             trigger=trigger, dev_ratio=round(dev_ratio, 3), applied=applied,
             prompt_ver=ver, kb_ids=rag_ctx.get("kb_ids", []),
             case_ids=rag_ctx.get("case_ids", []), case_id=case_id)
    note = ""
    if applied:
        note = (f"AI 已按「{_TRIGGER_CN.get(trigger, trigger)}」微调本周剩余课表"
                f"（{len(ok)} 项调整，关键课未动）")
    return {"applied": applied, "ops": ok or [], "src": src, "case_id": case_id, "note": note}
