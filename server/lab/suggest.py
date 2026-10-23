"""M3 调参建议生成器 + 配置应用（纯规则，非 LLM）。

生成器：每周一扫描 active 计划，连续 3 周关键课完成率 <50% → 建议周量系数 -10%。
应用：采纳后把系数乘到「今天之后」的未执行课程上（不清进度、不动过去），
生成的周跑量再过一遍护栏（周增幅红线）——收紧方向永远受代码约束。
gain_cap / aux_cap_ratio 字段已在 PlanConfig 落表，完整引擎 v3.3 消费。
"""
from __future__ import annotations

from datetime import date, timedelta

from server import db
from server.guard import check_with_patch


def _key_rate(s, plan_id: str, week_no: int) -> tuple[int, int] | None:
    """某周关键课 (完成数, 计划数)；无关键课返回 None。"""
    sessions = s.query(db.Session).filter_by(plan_id=plan_id, week_no=week_no).all()
    keys = [x for x in sessions if x.is_key]
    if not keys:
        return None
    done = 0
    for x in keys:
        log = s.query(db.SessionLog).filter_by(session_id=x.session_id).first()
        if log and log.status in ("full", "over", "partial"):
            done += 1
    return done, len(keys)


def generate_suggestions() -> int:
    """周一任务：为连续 3 周关键课完成率 <50% 的计划生成建议（去重：同计划不重复 pending）。"""
    today = date.today()
    n = 0
    with db.SessionLocal() as s:
        for plan in s.query(db.Plan).filter_by(status="active").all():
            cur = max((w.week_no for w in s.query(db.PlanWeek).filter_by(plan_id=plan.plan_id)), default=0)
            window = [cur - 2, cur - 1, cur]
            rates = []
            for w in window:
                if w < 1:
                    break
                kr = _key_rate(s, plan.plan_id, w)
                if not kr:
                    break
                rates.append(kr[0] / kr[1])
            if len(rates) < 3 or any(r >= 0.5 for r in rates):
                continue
            # 伤痛窗口内不提议加负荷方向的建议（这里是收紧，也跳过以免与护栏降载叠加）
            since = today - timedelta(days=14)
            injuries = s.query(db.StatusReport).filter(
                db.StatusReport.user_id == plan.user_id,
                db.StatusReport.date >= since,
                (db.StatusReport.soreness_part.notin_(["", "none"]))
                | (db.StatusReport.muscle_sore.is_(True))).count()
            if injuries >= 2:
                continue
            dup = s.query(db.TuneSuggestion).filter_by(
                target_type="plan", target_id=plan.plan_id, status="pending").first()
            if dup:
                continue
            import uuid
            s.add(db.TuneSuggestion(
                suggestion_id=uuid.uuid4().hex[:16],
                target_type="plan", target_id=plan.plan_id,
                metric="key_completion_rate_3w", metric_value=round(sum(rates) / 3, 3),
                suggested_json={"volume_multiplier": 0.9},
                reason_user="该生连续 3 周关键课完成率不足 50%（平均 "
                            f"{sum(rates) / 3:.0%}），建议把未来课程的周量系数下调 10%，"
                            "并建议与学生沟通训练时间安排。采纳后只影响今天之后的课，不清进度。"))
            n += 1
        s.commit()
    return n


def apply_config_to_future(plan_id: str, multiplier: float) -> dict:
    """把周量系数应用到今天之后的未执行课程（收紧方向，0.5~1.0）。

    返回 {updated_sessions, updated_weeks}；周跑量重新过护栏（增幅红线压回）。
    """
    multiplier = min(1.0, max(0.5, multiplier))
    today = date.today()
    updated_sessions = updated_weeks = 0
    with db.SessionLocal.begin() as s:
        plan = s.get(db.Plan, plan_id)
        if not plan:
            raise ValueError("计划不存在")
        weeks = s.query(db.PlanWeek).filter_by(plan_id=plan_id).order_by(db.PlanWeek.week_no).all()
        prev_km = None
        for w in weeks:
            sessions = s.query(db.Session).filter_by(plan_week_id=w.plan_week_id).all()
            future = [x for x in sessions if x.date >= today and x.status == "planned"]
            for x in future:
                x.target_km = round(x.target_km * multiplier, 1)
                updated_sessions += 1
            new_km = round(sum(x.target_km for x in sessions), 1)
            if future:
                if prev_km:
                    probe = [dict(session_id=x.session_id, date=x.date, type=x.type,
                                  pace_zone=x.pace_zone, target_km=x.target_km,
                                  status=x.status) for x in sessions]
                    verdict, patched_km, patched = check_with_patch(
                        new_km, probe, {"prev_week_km": prev_km, "injury_reports_14d": 0})
                    if verdict.action == "patch" and verdict.hit_rule == "WEEKLY_GAIN":
                        new_km = patched_km
                        for orig, p in zip(sessions, patched):
                            orig.target_km = p["target_km"]
                w.planned_km = new_km
                updated_weeks += 1
            prev_km = new_km
        s.add(db.Event(name="config_applied", user_id=plan.user_id,
                       payload={"plan_id": plan_id, "volume_multiplier": multiplier,
                                "sessions": updated_sessions, "weeks": updated_weeks}))
    return {"updated_sessions": updated_sessions, "updated_weeks": updated_weeks}
