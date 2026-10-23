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
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": "overu", "password": "run12345"}).json()["user_id"]
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
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": "partu", "password": "run12345"}).json()["user_id"]
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
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": "partok", "password": "run12345"}).json()["user_id"]
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
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": "exactu", "password": "run12345"}).json()["user_id"]
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
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": "overbtn", "password": "run12345"}).json()["user_id"]
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
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": "overcnt", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    sess = _first_session(plan_id)
    sid, target = sess["session_id"], sess["target_km"]
    c.post(f"/v1/session/{sid}/log", json={"status": "over", "actual_km": round(target * 1.2, 1),
                                          "actual_pace": "", "duration_sec": 1800, "rpe": None, "skip_kind": ""})
    w1 = c.get(f"/v1/plan/{plan_id}/week/1").json()
    done = [x for x in w1["sessions"] if x["session_id"] == sid][0]
    assert done["status"] == "done", "over 打卡后课次状态应为 done"


def test_dev_reason_impulse_keeps_plan_unchanged():
    """偏差 >±30% 且用户选择「临时起意」：只记录，VDOT/计划不动（即使数据暗示能力大变）。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": "impulseu", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    with db.SessionLocal() as s:
        base_vdot = s.get(db.User, uid).vdot
    sess = _first_session(plan_id)
    sid = sess["session_id"]
    # 5km @5:00/km 相对新手基线是巨大突破（隐含 VDOT 约 56，偏移 >>1.5），且不超 3km 进阶线
    r = c.post(f"/v1/session/{sid}/log", json={"status": "full", "actual_km": 5.0,
                                              "actual_pace": "", "duration_sec": 1500,
                                              "rpe": None, "skip_kind": "", "dev_reason": "impulse"})
    assert r.status_code == 200
    assert "vdot_update" not in r.json(), "临时起意不应触发能力重估"
    with db.SessionLocal() as s:
        log = s.query(db.SessionLog).filter_by(session_id=sid).first()
        assert abs(log.actual_km - 5.0) < 0.01                    # 数据照常落库
        assert s.get(db.User, uid).vdot == base_vdot              # 计划保持不变


def test_dev_reason_mismatch_adjusts_plan():
    """用户选择「强度不匹配」：按真实跑量动态调整计划，重估门槛从 1.5 降到 0.5。"""
    payload = {"status": "full", "actual_km": 3.0, "actual_pace": "",
               "duration_sec": 990, "rpe": None, "skip_kind": ""}
    # 对照组：同样数据（隐含 VDOT 47，偏移仅 1）不带原因标记 → 低于 1.5 门槛，不重估
    _fresh_db()
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": "mismatchctl", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    with db.SessionLocal() as s:
        base_vdot = s.get(db.User, uid).vdot
    r1 = c.post(f"/v1/session/{_first_session(plan_id)['session_id']}/log", json={**payload, "dev_reason": ""})
    assert "vdot_update" not in r1.json(), "无原因标记时小偏移不应重估（对照组）"
    # 实验组：选「强度不匹配」→ 放宽到 0.5 门槛，偏移 1 也要重估
    _fresh_db()
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": "mismatches", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    r2 = c.post(f"/v1/session/{_first_session(plan_id)['session_id']}/log", json={**payload, "dev_reason": "mismatch"})
    assert r2.status_code == 200
    assert "vdot_update" in r2.json(), "强度不匹配应按数据调整计划"
    with db.SessionLocal() as s:
        assert s.get(db.User, uid).vdot == base_vdot + 1
