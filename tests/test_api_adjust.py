"""接口级回归：修改训练计划（训练天数 / 训练强度档位）。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_api_adjust_test.db")

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


def _onboard(uid):
    c.post("/v1/profile", json={**{
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "city": "上海", "lat": 31.23, "lon": 121.47, "max_duration": 90}})
    return c.post(f"/v1/plan/generate?user_id={uid}").json()["plan_id"]


def _week_sessions(plan_id, week=1):
    return c.get(f"/v1/plan/{plan_id}/week/{week}").json()["sessions"]


def test_adjust_avail_days_regenerates_plan():
    """改训练天数：新计划生成、旧计划作废、课次数随天数变化。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "adjd", "password": "run12345"}).json()["user_id"]
    old_plan = _onboard(uid)
    old_n = len(_week_sessions(old_plan))
    r = c.post("/v1/plan/adjust", json={"user_id": uid, "avail_days": 6})
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["changed"] is True
    new_plan = r.json()["plan_id"]
    assert new_plan != old_plan
    assert r.json()["avail_days"] == 6
    with db.SessionLocal() as s:
        assert s.get(db.Plan, old_plan).status == "superseded"
        assert s.get(db.User, uid).avail_days == 6
    assert len(_week_sessions(new_plan)) > old_n, "天数增加课次数应增加"


def test_adjust_intensity_changes_vdot_and_pace():
    """增加一级强度：VDOT +2，目标配速变化，计划重建。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "adjv", "password": "run12345"}).json()["user_id"]
    old_plan = _onboard(uid)
    old_vdot = c.get(f"/v1/metrics?user_id={uid}").json()["user"]["vdot"]
    old_pace = _week_sessions(old_plan)[-1]["target_pace"]   # 最后一节课在本周末尾，必然 >= 今天
    r = c.post("/v1/plan/adjust", json={"user_id": uid, "intensity_shift": 1})
    assert r.status_code == 200 and r.json()["vdot"] == old_vdot + 2
    new_pace = _week_sessions(r.json()["plan_id"])[-1]["target_pace"]
    assert new_pace != old_pace, "强度变化必须体现在目标配速上"
    m = c.get(f"/v1/metrics?user_id={uid}").json()
    assert m["user"]["vdot"] == old_vdot + 2


def test_adjust_intensity_boundary():
    """最低 VDOT 30 再减一级 → 400，不产生新计划。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "adjb", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    with db.SessionLocal.begin() as s:
        s.get(db.User, uid).vdot = 30
    r = c.post("/v1/plan/adjust", json={"user_id": uid, "intensity_shift": -1})
    assert r.status_code == 400
    with db.SessionLocal() as s:
        assert s.query(db.Plan).filter_by(user_id=uid, status="active").count() == 1


def test_adjust_noop_returns_unchanged():
    """没有实际改动 → changed=False，不重建计划。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "adjn", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    r = c.post("/v1/plan/adjust", json={"user_id": uid, "avail_days": 4, "intensity_shift": 0})
    assert r.json() == {"ok": True, "changed": False, "message": "没有需要修改的内容"}
    with db.SessionLocal() as s:
        assert s.get(db.Plan, plan_id).status == "active"


def test_adjust_days_validation():
    """天数越界 → 400。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "adjx", "password": "run12345"}).json()["user_id"]
    _onboard(uid)
    assert c.post("/v1/plan/adjust", json={"user_id": uid, "avail_days": 1}).status_code == 400
    assert c.post("/v1/plan/adjust", json={"user_id": uid, "avail_days": 8}).status_code == 400
