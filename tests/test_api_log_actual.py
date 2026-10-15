"""接口级回归：打卡时填写实际训练量（超额完成 / 只跑了一部分）对排期的影响。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_api_log_actual_test.db")

from fastapi.testclient import TestClient

from server import db
from server.main import app
from server import main as _main

_main.AUTH_ENABLED = False   # 本文件回归业务逻辑，鉴权由 test_api_auth.py 覆盖

db.init_db()
c = TestClient(app)


def _fresh_db():
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)


def _onboard(uid):
    c.post("/v1/profile", json={**{
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "city": "上海", "lat": 31.23, "lon": 121.47, "max_duration": 90}})
    return c.post(f"/v1/plan/generate?user_id={uid}").json()["plan_id"]


def _first_session(plan_id):
    w1 = c.get(f"/v1/plan/{plan_id}/week/1").json()
    return w1["sessions"][0]


def test_over_done_triggers_rearrange():
    """超额完成（>110% 目标）：记 full + actual_km，返回 over_done 并重排预生成。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "overu", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    sess = _first_session(plan_id)
    sid, target = sess["session_id"], sess["target_km"]
    over_km = round(target * 1.5, 1)
    r = c.post(f"/v1/session/{sid}/log", json={"status": "full",
                                              "actual_km": over_km, "actual_pace": "", "rpe": None})
    assert r.status_code == 200
    body = r.json()
    assert body["over_done"] is True and body["over_note"]
    assert body["rearrange_ready"] is True, "超额完成应预生成重排候选"
    # 实际公里数落库
    with db.SessionLocal() as s:
        log = s.query(db.SessionLog).filter_by(session_id=sid).first()
        assert abs(log.actual_km - over_km) < 0.01
        assert s.get(db.Session, sid).status == "done"
        ev = s.query(db.Event).filter_by(name="session_done").first()
        assert ev and abs(ev.payload["actual_km"] - over_km) < 0.01


def test_partial_with_km_under_half_triggers_rearrange():
    """只跑了一部分且不足目标 50%：缺口记入，预生成重排候选。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "partu", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    sess = _first_session(plan_id)
    sid, target = sess["session_id"], sess["target_km"]
    part_km = round(target * 0.3, 1)
    r = c.post(f"/v1/session/{sid}/log", json={"status": "partial",
                                              "actual_km": part_km, "actual_pace": "", "rpe": None})
    assert r.status_code == 200
    body = r.json()
    assert body["under_note"]
    assert body["rearrange_ready"] is True, "不足一半应预生成重排候选"
    assert "over_done" not in body
    with db.SessionLocal() as s:
        log = s.query(db.SessionLog).filter_by(session_id=sid).first()
        assert abs(log.actual_km - part_km) < 0.01
        assert s.get(db.Session, sid).status == "partial"


def test_partial_with_km_above_half_no_rearrange():
    """部分完成但 ≥50%：只记录，不触发重排。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "partok", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    sess = _first_session(plan_id)
    sid, target = sess["session_id"], sess["target_km"]
    part_km = round(target * 0.7, 1)
    r = c.post(f"/v1/session/{sid}/log", json={"status": "partial",
                                              "actual_km": part_km, "actual_pace": "", "rpe": None})
    assert r.status_code == 200
    body = r.json()
    assert "rearrange_ready" not in body and "under_note" not in body
    with db.SessionLocal() as s:
        log = s.query(db.SessionLog).filter_by(session_id=sid).first()
        assert abs(log.actual_km - part_km) < 0.01


def test_exact_target_is_not_over():
    """刚好完成目标（≤110%）不算超额，走普通完成路径。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "exactu", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    sess = _first_session(plan_id)
    sid, target = sess["session_id"], sess["target_km"]
    r = c.post(f"/v1/session/{sid}/log", json={"status": "full",
                                              "actual_km": target, "actual_pace": "", "rpe": None})
    body = r.json()
    assert "over_done" not in body
    assert "rearrange_ready" not in body


def test_over_status_from_frontend_button():
    """前端「超额完成」按钮发 status="over"：必须按完成受理，触发 over_done 与重排预生成。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "overbtn", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    sess = _first_session(plan_id)
    sid, target = sess["session_id"], sess["target_km"]
    over_km = round(target * 1.5, 1)
    r = c.post(f"/v1/session/{sid}/log", json={"status": "over",
                                              "actual_km": over_km, "actual_pace": "",
                                              "duration_sec": 1800, "rpe": None, "skip_kind": ""})
    assert r.status_code == 200, f"over 状态不应再被 400 拒绝: {r.text}"
    body = r.json()
    assert body["over_done"] is True and body["over_note"]
    assert body["rearrange_ready"] is True
    with db.SessionLocal() as s:
        log = s.query(db.SessionLog).filter_by(session_id=sid).first()
        assert log.status == "over"
        assert abs(log.actual_km - over_km) < 0.01
        assert s.get(db.Session, sid).status == "done"


def test_over_status_counts_as_done_in_metrics():
    """「超额完成」计入执行率/关键课完成统计（周报、降载判定都读 session_log.status）。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "overcnt", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    sess = _first_session(plan_id)
    sid, target = sess["session_id"], sess["target_km"]
    c.post(f"/v1/session/{sid}/log", json={"status": "over", "actual_km": round(target * 1.2, 1),
                                          "actual_pace": "", "duration_sec": 1800, "rpe": None, "skip_kind": ""})
    w1 = c.get(f"/v1/plan/{plan_id}/week/1").json()
    done = [x for x in w1["sessions"] if x["session_id"] == sid][0]
    assert done["status"] == "done", "over 打卡后课次状态应为 done"
