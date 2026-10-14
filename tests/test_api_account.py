"""接口级回归：账号体系 + 切换城市重生成计划。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_api_account_test.db")

from fastapi.testclient import TestClient

from server import db
from server.main import app
from server import main as _main

_main.AUTH_ENABLED = False   # 本文件回归业务逻辑，鉴权与限速由 test_api_auth.py 专门覆盖

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


def test_register_login_flow():
    _fresh_db()
    r = c.post("/v1/account/register", json={"username": "paopao", "password": "run12345"})
    assert r.status_code == 200 and r.json()["user_id"]
    uid = r.json()["user_id"]
    # 重复注册 → 409
    assert c.post("/v1/account/register", json={"username": "paopao", "password": "x12345"}).status_code == 409
    # 错误密码 → 401
    assert c.post("/v1/account/login", json={"username": "paopao", "password": "wrong66"}).status_code == 401
    # 正确登录，无计划
    r = c.post("/v1/account/login", json={"username": "paopao", "password": "run12345"})
    assert r.json()["user_id"] == uid and r.json()["has_plan"] is False
    # 有计划后 has_plan=True
    _onboard(uid)
    r = c.post("/v1/account/login", json={"username": "paopao", "password": "run12345"})
    assert r.json()["has_plan"] is True
    # 密码以哈希存储，库里没有明文
    with db.SessionLocal() as s:
        acc = s.get(db.Account, "paopao")
        assert acc.password_hash != "run12345" and len(acc.salt) == 32


def test_change_city_regenerates_plan():
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "cityuser", "password": "run12345"}).json()["user_id"]
    old_plan = _onboard(uid)
    r = c.post("/v1/user/city", json={"user_id": uid, "city": "成都", "lat": 30.57, "lon": 104.07})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["plan_id"] != old_plan, "换城市必须生成新计划"
    # 旧计划作废、用户坐标更新
    with db.SessionLocal() as s:
        old = s.get(db.Plan, old_plan)
        user = s.get(db.User, uid)
        assert old.status == "superseded"
        assert user.city == "成都" and abs(user.lat - 30.57) < 0.01
    # 新计划可用
    w = c.get(f"/v1/plan/{r.json()['plan_id']}/week/1")
    assert w.status_code == 200


def test_delete_account_cascades():
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "byeuser", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    # 制造打卡与上报数据，验证级联删除
    w1 = c.get(f"/v1/plan/{plan_id}/week/1").json()
    sid = w1["sessions"][0]["session_id"]
    c.post(f"/v1/session/{sid}/log", json={"status": "full",
                                          "actual_km": 8, "actual_pace": "6:15", "rpe": 5})
    c.post("/v1/status/report", json={"user_id": uid, "soreness_part": "none",
                                      "sleep_score": 7, "fatigue_score": 6})
    # 错误密码 → 401，数据还在
    r = c.post("/v1/account/delete", json={"username": "byeuser", "password": "wrong66"})
    assert r.status_code == 401
    with db.SessionLocal() as s:
        assert s.get(db.User, uid) is not None
    # 正确密码 → 级联删干净
    r = c.post("/v1/account/delete", json={"username": "byeuser", "password": "run12345"})
    assert r.status_code == 200 and r.json()["ok"] is True
    with db.SessionLocal() as s:
        assert s.get(db.User, uid) is None
        assert s.get(db.Account, "byeuser") is None
        assert s.query(db.Plan).filter_by(user_id=uid).count() == 0
        assert s.query(db.PlanWeek).filter_by(plan_id=plan_id).count() == 0
        assert s.query(db.PlanWeekSnapshot).filter_by(plan_id=plan_id).count() == 0
        assert s.query(db.Session).filter_by(plan_id=plan_id).count() == 0
        assert s.query(db.SessionLog).count() == 0
        assert s.query(db.RearrangeEvent).filter_by(user_id=uid).count() == 0
        assert s.query(db.StatusReport).filter_by(user_id=uid).count() == 0
        assert s.query(db.GuardAudit).filter_by(plan_id=plan_id).count() == 0
        assert s.query(db.Event).filter_by(user_id=uid).count() == 0
    # 删掉的账号可以重新注册（用户名被释放）
    r = c.post("/v1/account/register", json={"username": "byeuser", "password": "newpass123"})
    assert r.status_code == 200 and r.json()["user_id"] != uid
