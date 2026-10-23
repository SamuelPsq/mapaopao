"""实验室 B 端与问卷路由（v3.2）。

鉴权约定：全部走全站 token 中间件；/v1/lab/* 额外要求 account.role == "teacher"。
M3 的调参建议接口先落桩（表结构已建），引擎 v3.3 上线。
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from server import db
from server.lab import (CONSENT_DOC, CONSENT_VERSION, build_export,
                        create_weekly_instances, pending_survey, respond_survey)

router = APIRouter()


def _teacher(request: Request) -> db.Account:
    """实验室账号校验：未登录 401，非教师 403（人话）。
    AUTH_ENABLED 关闭时（pytest 模式）按 X-Test-Role 头直通，与全站中间路的测试旁路一致。"""
    from server import main as _main
    if not _main.AUTH_ENABLED:
        if request.headers.get("x-test-role", "teacher") != "teacher":
            raise HTTPException(403, "这个功能只对实验室账号开放")
        return db.Account(username="tester", role="teacher")
    from server.main import _bearer_token
    with db.SessionLocal() as s:
        acc = s.query(db.Account).filter_by(token=_bearer_token(request)).first()
        if not acc:
            raise HTTPException(401, "未登录或登录已失效，请重新登录")
        if acc.role != "teacher":
            raise HTTPException(403, "这个功能只对实验室账号开放")
        return acc


# ================================================================ C 端：知情同意 + 问卷
@router.get("/v1/consent/doc")
def consent_doc():
    """数据使用协议文本与当前版本号（注册页展示用）。"""
    return CONSENT_DOC


class SurveyRespIn(BaseModel):
    user_id: str
    survey_id: str
    answers: dict = {}


@router.get("/v1/survey/pending")
def survey_pending(user_id: str):
    """本周待填问卷：周问卷实例（周日生成）优先，其次前测/后测。无则 ok=null。"""
    p = pending_survey(user_id)
    return {"ok": True, "pending": p}


@router.post("/v1/survey/respond")
def survey_respond(body: SurveyRespIn):
    """提交问卷：必填校验，重复提交幂等。"""
    try:
        r = respond_survey(body.user_id, body.survey_id, body.answers)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **r}


# ================================================================ B 端：概览 / 导出 / 问卷管理
@router.get("/v1/lab/overview")
def lab_overview(request: Request):
    _teacher(request)
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    with db.SessionLocal() as s:
        users = s.query(db.User).all()
        active_uids = {p.user_id for p in s.query(db.Plan).filter_by(status="active").all()}
        n_status_this_week = s.query(db.StatusReport).filter(db.StatusReport.date >= monday).count()
        weekly = s.query(db.Survey).filter_by(kind="weekly", active=True).first()
        week_ref = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
        inst_total = inst_done = 0
        if weekly:
            insts = s.query(db.SurveyInstance).filter_by(survey_id=weekly.survey_id,
                                                         week_ref=week_ref).all()
            inst_total, inst_done = len(insts), sum(1 for i in insts if i.status == "done")
        from datetime import timedelta as _td
        since = today - _td(days=14)
        injury_recent = s.query(db.StatusReport).filter(
            db.StatusReport.date >= since,
            (db.StatusReport.soreness_part.notin_(["", "none"]))
            | (db.StatusReport.muscle_sore.is_(True))).count()
        pending_suggestions = s.query(db.TuneSuggestion).filter_by(status="pending").count()
        return {"students": len(users), "with_active_plan": len(active_uids),
                "status_rate_this_week": round(n_status_this_week / len(active_uids), 3)
                if active_uids else 0,
                "survey": {"instances": inst_total, "done": inst_done,
                           "rate": round(inst_done / inst_total, 3) if inst_total else None},
                "injury_reports_14d": injury_recent,
                "pending_suggestions": pending_suggestions}


@router.get("/v1/lab/export/preview")
def lab_export_preview(request: Request, cohort: str = "", date_from: date | None = None,
                       date_to: date | None = None):
    """导出前预览：字段清单 + 行数预估，不返回数据本身。"""
    _teacher(request)
    date_from = date_from or date(2024, 1, 1)
    date_to = date_to or date.today()
    with db.SessionLocal() as s:
        users = s.query(db.User).all()
        if cohort:
            users = [u for u in users if u.cohort == cohort]
        n_week = s.query(db.PlanWeek).count()
        n_state = s.query(db.StatusReport).filter(
            db.StatusReport.date >= date_from, db.StatusReport.date <= date_to).count()
        n_inst = s.query(db.SurveyInstance).count()
        return {"cohort": cohort or "全部", "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(), "students": len(users),
                "tables": [
                    {"name": "weekly_training.csv", "rows_est": n_week,
                     "fields": ["anon_id", "cohort", "week_no", "phase", "vdot_at_week",
                                "planned_km", "actual_km", "key_plan", "key_done",
                                "completion_rate", "rearrange_count", "guard_hit_count"]},
                    {"name": "daily_state.csv", "rows_est": n_state,
                     "fields": ["anon_id", "date", "source", "sleep_score", "fatigue_score",
                                "muscle_sore", "soreness_part", "breath_state"]},
                    {"name": "survey_weekly.csv", "rows_est": n_inst,
                     "fields": ["anon_id", "week_ref", "submitted", "q_*（按题目展开）"]},
                    {"name": "events.csv", "rows_est": None,
                     "fields": ["anon_id", "date", "event_type", "detail"]},
                ]}


@router.post("/v1/lab/export")
def lab_export(request: Request, body: dict):
    """一键导出匿名化数据 ZIP（含变量字典与脱敏说明），写审计日志。"""
    acc = _teacher(request)
    cohort = (body.get("cohort") or "").strip() or None
    date_from = date.fromisoformat(body["date_from"]) if body.get("date_from") else date(2024, 1, 1)
    date_to = date.fromisoformat(body["date_to"]) if body.get("date_to") else date.today()
    data, stats = build_export(cohort, date_from, date_to)
    with db.SessionLocal.begin() as s:
        s.add(db.ExportLog(teacher_id=acc.username, cohort=cohort or "",
                           date_from=date_from, date_to=date_to,
                           anon_salt_sha=stats["salt_sha"]))
    fname = f"mapaopao_export_{date_to.isoformat()}.zip"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ---------------------------------------------------------------- 问卷管理
class SurveyIn(BaseModel):
    kind: str                       # pre / weekly / post
    title: str
    questions: list[dict] = []      # [{id, text, type: likert7|bool|text, required}]
    week_no: int | None = None


@router.get("/v1/lab/surveys")
def lab_surveys(request: Request):
    _teacher(request)
    with db.SessionLocal() as s:
        rows = s.query(db.Survey).order_by(db.Survey.created_at).all()
        return {"surveys": [{"survey_id": x.survey_id, "kind": x.kind, "title": x.title,
                             "schema": x.schema_json, "active": x.active,
                             "week_no": x.week_no} for x in rows]}


@router.post("/v1/lab/surveys")
def lab_survey_create(request: Request, body: SurveyIn):
    _teacher(request)
    if body.kind not in ("pre", "weekly", "post"):
        raise HTTPException(400, "问卷类型只支持 pre / weekly / post")
    if not body.title.strip():
        raise HTTPException(400, "请填写问卷标题")
    sid = uuid.uuid4().hex[:16]
    with db.SessionLocal.begin() as s:
        if body.kind == "weekly":
            old = s.query(db.Survey).filter_by(kind="weekly", active=True).first()
            if old:
                old.active = False     # 每周问卷同时只生效一份
        s.add(db.Survey(survey_id=sid, kind=body.kind, title=body.title.strip(),
                        schema_json=body.questions, active=True, week_no=body.week_no))
    return {"ok": True, "survey_id": sid}


@router.post("/v1/lab/surveys/{survey_id}/toggle")
def lab_survey_toggle(request: Request, survey_id: str):
    """发布/下架：weekly 激活时自动下架旧周问卷。"""
    _teacher(request)
    with db.SessionLocal.begin() as s:
        sv = s.get(db.Survey, survey_id)
        if not sv:
            raise HTTPException(404, "问卷不存在")
        sv.active = not sv.active
        if sv.active and sv.kind == "weekly":
            for old in s.query(db.Survey).filter_by(kind="weekly", active=True):
                if old.survey_id != survey_id:
                    old.active = False
        return {"ok": True, "active": sv.active}


@router.get("/v1/lab/surveys/{survey_id}/stats")
def lab_survey_stats(request: Request, survey_id: str):
    _teacher(request)
    with db.SessionLocal() as s:
        sv = s.get(db.Survey, survey_id)
        if not sv:
            raise HTTPException(404, "问卷不存在")
        if sv.kind == "weekly":
            insts = s.query(db.SurveyInstance).filter_by(survey_id=survey_id).all()
            by_week: dict[str, list[int]] = {}
            for i in insts:
                a, b = by_week.get(i.week_ref, [0, 0])
                by_week[i.week_ref] = [a + 1, b + (i.status == "done")]
            return {"kind": "weekly",
                    "weeks": [{"week_ref": w, "total": v[0], "done": v[1],
                               "rate": round(v[1] / v[0], 3) if v[0] else None}
                              for w, v in sorted(by_week.items())]}
        n_resp = s.query(db.SurveyResponse).filter_by(survey_id=survey_id).count()
        n_students = s.query(db.User).count()
        return {"kind": sv.kind, "responded": n_resp, "students": n_students,
                "rate": round(n_resp / n_students, 3) if n_students else None}


# ---------------------------------------------------------------- M3 桩：调参建议（引擎 v3.3）
@router.get("/v1/lab/suggestions")
def lab_suggestions(request: Request):
    """调参建议列表。生成器 v3.3 上线前返回空表（表结构已就绪）。"""
    _teacher(request)
    with db.SessionLocal() as s:
        rows = s.query(db.TuneSuggestion).filter_by(status="pending").order_by(
            db.TuneSuggestion.created_at.desc()).all()
        return {"items": [{"suggestion_id": x.suggestion_id, "target_type": x.target_type,
                           "target_id": x.target_id, "metric": x.metric,
                           "metric_value": x.metric_value, "suggested": x.suggested_json,
                           "reason_user": x.reason_user,
                           "created_at": x.created_at.isoformat()} for x in rows],
                "engine": "v3.3"}


@router.post("/v1/lab/suggestions/{suggestion_id}/dismiss")
def lab_suggestion_dismiss(request: Request, suggestion_id: str):
    _teacher(request)
    with db.SessionLocal.begin() as s:
        row = s.get(db.TuneSuggestion, suggestion_id)
        if not row:
            raise HTTPException(404, "建议不存在")
        row.status = "dismissed"
        return {"ok": True}


@router.post("/v1/lab/suggestions/{suggestion_id}/adopt")
def lab_suggestion_adopt(request: Request, suggestion_id: str):
    """采纳建议：落 PlanConfig 并把周量系数应用到今天之后的课程（不清进度）。"""
    _teacher(request)
    from server.lab.suggest import apply_config_to_future
    with db.SessionLocal.begin() as s:
        row = s.get(db.TuneSuggestion, suggestion_id)
        if not row:
            raise HTTPException(404, "建议不存在")
        row.status = "adopted"
        target_id = row.target_id
        multiplier = row.suggested_json.get("volume_multiplier", 1.0)
        s.add(db.PlanConfig(config_id=uuid.uuid4().hex[:16],
                            plan_id=target_id if row.target_type == "plan" else "",
                            scope=row.target_type,
                            volume_multiplier=multiplier,
                            gain_cap=row.suggested_json.get("gain_cap"),
                            aux_cap_ratio=row.suggested_json.get("aux_cap_ratio"),
                            proposed_by="system",
                            proposed_reason=f"采纳建议 {suggestion_id}（{row.metric}={row.metric_value}）",
                            status="approved", approved_by="system"))
    applied = apply_config_to_future(target_id, multiplier)
    return {"ok": True, "applied": applied,
            "message": f"已按您的跑力重新安排：{applied['updated_sessions']} 节课、"
                       f"{applied['updated_weeks']} 个周计划已更新（今天之后的课程，进度保留）"}


# ---------------------------------------------------------------- 定时任务
@router.post("/jobs/weekly_survey")
def weekly_survey_job():
    """内部任务：周日为所有 active 计划用户生成本周问卷实例（幂等）。"""
    return {"ok": True, "created": create_weekly_instances()}


@router.post("/jobs/tune_suggest")
def tune_suggest_job():
    """内部任务（周一）：扫描连续 3 周关键课完成率 <50% 的计划，生成调参建议。"""
    from server.lab.suggest import generate_suggestions
    return {"ok": True, "created": generate_suggestions()}
