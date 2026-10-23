"""v3.2 实验室 B 端（M1 导出 / M3 建议桩）测试。"""
import io
import os
import tempfile
import zipfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_lab_test.db")

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
    c.post("/v1/status/report", json={"user_id": uid, "soreness_part": "knee",
                                      "muscle_sore": True, "breath_state": "talk",
                                      "sleep_score": 4, "fatigue_score": 6,
                                      "entry": "checkin"})
    return uid


def test_user_role_denied():
    """普通学生访问 B 端：403 人话，不泄露数据。"""
    _fresh_db()
    _register("stu0")
    r = c.get("/v1/lab/overview", headers={"X-Test-Role": "user"})
    assert r.status_code == 403 and "实验室" in r.json()["error"]


def test_overview():
    _fresh_db()
    _teacher()
    uid = _user_with_plan("stu1")
    ov = c.get("/v1/lab/overview").json()
    assert ov["students"] == 2            # 学生 + 教师占位用户
    assert ov["with_active_plan"] == 1
    assert ov["injury_reports_14d"] == 1
    assert ov["status_rate_this_week"] == 1.0


def test_export_preview_and_zip():
    """导出闭环：预览字段 → ZIP 含字典 → CSV 匿名化（无 user_id）→ 审计留痕。"""
    _fresh_db()
    _teacher()
    uid = _user_with_plan("stu1")
    pv = c.get("/v1/lab/export/preview").json()
    names = [t["name"] for t in pv["tables"]]
    assert "weekly_training.csv" in names and "daily_state.csv" in names
    assert pv["tables"][0]["fields"][0] == "anon_id"

    r = c.post("/v1/lab/export", json={})
    assert r.status_code == 200
    assert "zip" in r.headers["content-type"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert {"weekly_training.csv", "daily_state.csv", "events.csv",
            "dictionary.md", "readme.md"} <= set(zf.namelist())
    wt = zf.read("weekly_training.csv").decode("utf-8")
    ds = zf.read("daily_state.csv").decode("utf-8")
    assert "anon_id" in wt and uid not in wt
    assert uid not in ds
    assert "knee" in ds                       # 分组/内容字段保留
    assert "injury_report" in zf.read("events.csv").decode("utf-8")
    # 导出审计留痕
    with db.SessionLocal() as s:
        assert s.query(db.ExportLog).count() == 1


def test_export_cohort_filter():
    _fresh_db()
    _teacher()
    _user_with_plan("stuA")
    uid_b = _user_with_plan("stuB")
    with db.SessionLocal.begin() as s:
        s.get(db.User, uid_b).cohort = "2026-autumn"
    r = c.post("/v1/lab/export", json={"cohort": "2026-autumn"})
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    ds = zf.read("daily_state.csv").decode("utf-8")
    wt = zf.read("weekly_training.csv").decode("utf-8")
    assert uid_b not in ds
    assert "2026-autumn" in wt                # 分组变量保留在周粒度表里


def test_suggestions_stub():
    """M3 桩：生成器上线前返回空表，接口可用。"""
    _fresh_db()
    _teacher()
    r = c.get("/v1/lab/suggestions").json()
    assert r["items"] == [] and r["engine"] == "v3.3"


def test_survey_management():
    """问卷管理：创建（周问卷唯一激活）→ 下架/发布 → 回收统计。"""
    _fresh_db()
    _teacher()
    uid = _user_with_plan("stu1")
    r1 = c.post("/v1/lab/surveys", json={"kind": "weekly", "title": "每周追踪",
                                         "questions": [{"id": "q1", "text": "睡眠", "type": "likert7"}]})
    r2 = c.post("/v1/lab/surveys", json={"kind": "weekly", "title": "每周追踪 v2",
                                         "questions": [{"id": "q1", "text": "睡眠", "type": "likert7"}]})
    with db.SessionLocal() as s:
        rows = s.query(db.Survey).filter_by(kind="weekly").all()
        assert sum(1 for x in rows if x.active) == 1    # 新周问卷激活时自动下架旧版
        assert s.get(db.Survey, r1.json()["survey_id"]).active is False
    c.post("/jobs/weekly_survey")
    p = c.get(f"/v1/survey/pending?user_id={uid}").json()["pending"]
    assert p["survey_id"] == r2.json()["survey_id"]
    c.post("/v1/survey/respond", json={"user_id": uid, "survey_id": p["survey_id"],
                                       "answers": {"q1": 5}})
    stats = c.get(f"/v1/lab/surveys/{r2.json()['survey_id']}/stats").json()
    assert stats["kind"] == "weekly" and stats["weeks"][0]["done"] == 1
