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


def test_plan_uses_real_vdot_not_placeholder():
    """回归：课表必须按用户真实 VDOT 生成（配速来自 pace_zones(user.vdot)）。

    曾有的 bug：gen_plan 用占位成绩 5km/1800s 内部折算 VDOT≈30 生成全部配速和跑量，
    之后只是覆盖结果里的 vdot 数字——导致调整训练强度后课表内容不变。
    """
    from server.knowledge.rules import pace_zones, sec_to_pace
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "realv", "password": "run12345"}).json()["user_id"]
    _onboard(uid)   # 10km/3600s → VDOT 明显高于占位折算的 30
    with db.SessionLocal() as s:
        vdot = s.get(db.User, uid).vdot
    assert vdot > 32, "建档成绩 10km/60min 的 VDOT 必须显著高于占位折算值"
    plan_id = c.post(f"/v1/plan/generate?user_id={uid}").json()["plan_id"]
    sess = _week_sessions(plan_id)
    easy = next(x for x in sess if x["pace_zone"] == "easy" and x["status"] == "planned")
    lo, hi = pace_zones(vdot)["easy"]
    expect = f"{sec_to_pace(lo)}–{sec_to_pace(hi)}" if lo != hi else sec_to_pace(lo)
    assert easy["target_pace"] == expect, \
        f"轻松跑配速必须等于 VDOT {vdot} 的 E 区 {expect}，实际 {easy['target_pace']}"
    # 跑量基线也应随真实能力上浮（占位 VDOT30 基线约 16.5km，46 应明显更高）
    total = sum(x["target_km"] for x in sess if not x["status"].startswith("cancelled"))
    assert total > 20, f"周跑量 {total} 仍像占位能力生成的，应显著更高"


def test_adjust_days_same_cap_keeps_plan():
    """天数变化但课次上限相同（5→6→7 都是 5 节）→ 不重建计划，保留进度。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "adjc", "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={**{
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 5,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "city": "上海", "lat": 31.23, "lon": 121.47, "max_duration": 90}})
    plan_id = c.post(f"/v1/plan/generate?user_id={uid}").json()["plan_id"]
    r = c.post("/v1/plan/adjust", json={"user_id": uid, "avail_days": 6})
    assert r.json()["changed"] is False and "完全相同" in r.json()["message"]
    with db.SessionLocal() as s:
        assert s.get(db.Plan, plan_id).status == "active", "课表没变就不该作废旧计划"
        assert s.get(db.User, uid).avail_days == 6, "天数偏好要更新"


def test_week_review_notes_extra_days():
    """训练天数 6/7（课次上限 5）→ 周审视本周点评带补充提醒；4 天不带。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "note6", "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={**{
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 6,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "city": "上海", "lat": 31.23, "lon": 121.47, "max_duration": 90}})
    plan_id = c.post(f"/v1/plan/generate?user_id={uid}").json()["plan_id"]
    r = c.get(f"/v1/plan/{plan_id}/week/1").json()
    note = r["comment"]["note"]
    assert note and "每周 6 天" in note and "力量训练" in note and "最多排 5 节课" in note
    # 4 天 → 上限 4，无提醒
    uid2 = c.post("/v1/account/register", json={"username": "note4", "password": "run12345"}).json()["user_id"]
    plan2 = _onboard(uid2)
    r2 = c.get(f"/v1/plan/{plan2}/week/1").json()
    assert r2["comment"]["note"] is None


def test_adjust_days_validation():
    """天数越界 → 400。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "adjx", "password": "run12345"}).json()["user_id"]
    _onboard(uid)
    assert c.post("/v1/plan/adjust", json={"user_id": uid, "avail_days": 1}).status_code == 400
    assert c.post("/v1/plan/adjust", json={"user_id": uid, "avail_days": 8}).status_code == 400
