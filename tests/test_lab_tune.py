"""M3 调参建议生成器 + 采纳应用（只影响未来课、不清进度）测试。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_lab_tune_test.db")

from datetime import date

from fastapi.testclient import TestClient

from server import db
from server.main import app
from server import main as _main

_main.AUTH_ENABLED = False

db.init_db()
c = TestClient(app)


def _fresh_db():
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)


def _register(u):
    return c.post("/v1/account/register",
                  json={"agree_data_use": True, "username": u, "password": "run12345"})


def _teacher():
    _register("teacher1")
    with db.SessionLocal.begin() as s:
        s.get(db.Account, "teacher1").role = "teacher"


def _user_with_plan(u):
    uid = _register(u).json()["user_id"]
    c.post("/v1/profile", json={
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "city": "上海", "lat": 31.23, "lon": 121.47})
    c.post(f"/v1/plan/generate?user_id={uid}")
    with db.SessionLocal() as s:
        plan = s.query(db.Plan).filter_by(user_id=uid, status="active").first()
        return uid, plan.plan_id


def _log_all_key_sessions(plan_id, skip_upto_week=3):
    """第 1~3 周关键课全部记 skipped（完成率 0）；已有记录的课跳过。"""
    with db.SessionLocal.begin() as s:
        for x in s.query(db.Session).filter_by(plan_id=plan_id).all():
            if x.is_key and x.week_no <= skip_upto_week:
                if not s.query(db.SessionLog).filter_by(session_id=x.session_id).first():
                    s.add(db.SessionLog(session_id=x.session_id, status="skipped",
                                        actual_km=0.0))


def test_suggestion_generated_for_low_completion():
    """连续 3 周关键课完成率 <50% → 生成建议；重复运行去重。"""
    _fresh_db()
    _teacher()
    _user_with_plan("stu1")
    _log_all_key_sessions(_plan_id_of("stu1"))
    r = c.post("/jobs/tune_suggest").json()
    assert r["created"] == 1
    items = c.get("/v1/lab/suggestions").json()["items"]
    assert len(items) == 1
    assert items[0]["suggested"]["volume_multiplier"] == 0.9
    assert "连续 3 周" in items[0]["reason_user"]
    # 去重：同计划不重复生成
    assert c.post("/jobs/tune_suggest").json()["created"] == 0


def _plan_id_of(u):
    with db.SessionLocal() as s:
        acc = s.get(db.Account, u)
        return s.query(db.Plan).filter_by(user_id=acc.user_id, status="active").first().plan_id


def test_adopt_applies_to_future_only():
    """采纳：未来课程按系数缩放，过去/已完成课不动，PlanConfig 留痕。"""
    _fresh_db()
    _teacher()
    uid, plan_id = _user_with_plan("stu1")
    # 本周最早的一节课标记完成（代表"过去"），其余关键课全 skipped
    with db.SessionLocal.begin() as s:
        first = s.query(db.Session).filter_by(plan_id=plan_id, week_no=1) \
            .order_by(db.Session.date).first()
        s.add(db.SessionLog(session_id=first.session_id, status="full",
                            actual_km=first.target_km))
        done_id, done_km = first.session_id, first.target_km
    _log_all_key_sessions(plan_id)
    c.post("/jobs/tune_suggest")
    sid = c.get("/v1/lab/suggestions").json()["items"][0]["suggestion_id"]

    before = _planned_kms(plan_id)
    with db.SessionLocal() as s:
        sess_before = {x.session_id: x.target_km
                       for x in s.query(db.Session).filter_by(plan_id=plan_id).all()
                       if x.date >= date.today() and x.status == "planned"}
    r = c.post(f"/v1/lab/suggestions/{sid}/adopt").json()
    assert r["ok"] and r["applied"]["updated_sessions"] > 0
    assert "重新安排" in r["message"]
    after = _planned_kms(plan_id)

    with db.SessionLocal() as s:
        # 已完成的课不动
        assert s.get(db.Session, done_id).target_km == done_km
        # 未来某节未执行的课被 ×0.9
        scaled = [x for x in s.query(db.Session).filter_by(plan_id=plan_id).all()
                  if x.session_id in sess_before]
        assert scaled and any(abs(x.target_km - round(sess_before[x.session_id] * 0.9, 1)) < 0.15
                              for x in scaled)
        # 周跑量整体下降
        assert sum(after.values()) < sum(before.values())
        # 配置与采纳留痕
        cfg = s.query(db.PlanConfig).filter_by(plan_id=plan_id).first()
        assert cfg and cfg.status == "approved"
        assert s.get(db.TuneSuggestion, sid).status == "adopted"


def _planned_kms(plan_id):
    with db.SessionLocal() as s:
        return {w.week_no: w.planned_km
                for w in s.query(db.PlanWeek).filter_by(plan_id=plan_id).all()}


def test_no_suggestion_when_injury_window():
    """14 天内伤痛 ≥2 次：护栏已强制降载，不再叠加调参建议。"""
    _fresh_db()
    _teacher()
    uid, plan_id = _user_with_plan("stu1")
    _log_all_key_sessions(plan_id)
    for _ in range(2):
        c.post("/v1/status/report", json={"user_id": uid, "soreness_part": "knee"})
    assert c.post("/jobs/tune_suggest").json()["created"] == 0
