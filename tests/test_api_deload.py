"""接口级回归：降载必须「标签 + 课表 + 周里程」三者一致。

覆盖三个曾不一致的路径：
① 酸痛上报 ≥2 次触发的强制降载（_apply_deload_week）
② 劝降里选「坚持目标」（insist）——不能只打标签不动课表
③ 周审视页读到的 planned_km 必须反映降载后的剩余量（且不能二次缩水）
"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_api_test.db")

from datetime import date

from fastapi.testclient import TestClient

from server import db
from server.main import app
from server import main as _main

_main.AUTH_ENABLED = False   # 本文件回归业务逻辑，鉴权与限速由 test_api_auth.py 专门覆盖

db.init_db()                 # 不走 lifespan，避免拉起 APScheduler 拖住测试进程
c = TestClient(app)

PROFILE = {"recent_km": 10, "recent_sec": 3600, "avail_days": 4,
           "goal_race": "half", "ability": "B_base", "injury_flag": "N",
           "city": "上海", "lat": 31.23, "lon": 121.47, "max_duration": 90}


def _fresh_db():
    # Windows 上引擎占着文件句柄删不掉库文件，改用 drop + create 清表
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)


def _make_user_plan():
    uid = c.post("/v1/profile", json=PROFILE).json()["user_id"]
    plan_id = c.post(f"/v1/plan/generate?user_id={uid}").json()["plan_id"]
    return uid, plan_id


def _future(s):
    return s["date"] >= date.today().isoformat() and s["status"] == "planned"


def test_forced_deload_updates_km_and_clears_key():
    _fresh_db()
    uid, plan_id = _make_user_plan()
    before = c.get(f"/v1/plan/{plan_id}/week/1").json()
    km_before = before["planned_km"]

    c.post("/v1/status/report", json={"user_id": uid, "soreness_part": "knee"})
    r = c.post("/v1/status/report", json={"user_id": uid, "soreness_part": "ankle"}).json()
    assert r["forced_deload"] is True

    w = c.get(f"/v1/plan/{plan_id}/week/1").json()
    assert w["is_dropped"] is True
    fut = [s for s in w["sessions"] if _future(s)]
    assert fut, "本周应有未来课次"
    assert all(s["pace_zone"] == "easy" for s in fut), "降载后未来课必须全是轻松跑"
    assert all(not s["is_key"] for s in fut), "降载后未来课不该再标关键"
    # 周里程跟课表一致：页头不能再显示旧总量
    total = round(sum(s["target_km"] for s in w["sessions"]
                      if s["status"] not in ("cancelled_by_rearrange",
                                             "cancelled_before_week")), 1)
    assert abs(w["planned_km"] - total) < 0.15, "planned_km 必须等于剩余课表之和"
    assert w["planned_km"] < km_before, "降载后周里程必须下降"


def test_insist_choice_really_deloads_week():
    _fresh_db()
    uid, plan_id = _make_user_plan()
    r = c.post(f"/v1/plan/{plan_id}/degrade-goal", json={"to": "insist"}).json()
    assert r["ok"] is True
    w = c.get(f"/v1/plan/{plan_id}/week/1").json()
    assert w["is_dropped"] is True
    fut = [s for s in w["sessions"] if _future(s)]
    assert fut and all(s["pace_zone"] == "easy" for s in fut), \
        "insist 不能只打标签：课表必须真的切成轻松跑"


def test_second_deload_is_idempotent():
    _fresh_db()
    uid, plan_id = _make_user_plan()
    c.post("/v1/status/report", json={"user_id": uid, "soreness_part": "knee"})
    c.post("/v1/status/report", json={"user_id": uid, "soreness_part": "ankle"})
    w1 = c.get(f"/v1/plan/{plan_id}/week/1").json()
    # 第三次上报不应把 ×0.6 再乘一遍
    c.post("/v1/status/report", json={"user_id": uid, "soreness_part": "calf"})
    w2 = c.get(f"/v1/plan/{plan_id}/week/1").json()
    km1 = {s["session_id"]: s["target_km"] for s in w1["sessions"]}
    km2 = {s["session_id"]: s["target_km"] for s in w2["sessions"]}
    assert km1 == km2, "重复降载不能二次缩水"
    assert w2["planned_km"] == w1["planned_km"]
