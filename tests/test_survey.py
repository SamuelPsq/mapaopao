"""v3.2 知情同意层 + 问卷系统（M1/M2）测试。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_survey_test.db")

from fastapi.testclient import TestClient

from server import db
from server.main import app
from server import main as _main
from server.lab import CONSENT_VERSION

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
    return uid


WEEKLY_Q = [{"id": "q1", "text": "本周睡眠质量", "type": "likert7", "required": True},
            {"id": "q2", "text": "本周是否有伤痛影响训练", "type": "bool", "required": True}]


def test_register_requires_consent():
    """伦理层：不勾选《数据使用协议》不能注册，报错是人话。"""
    _fresh_db()
    r = c.post("/v1/account/register", json={"username": "noconsent", "password": "run12345"})
    assert r.status_code == 400
    assert "数据使用协议" in r.json()["error"]


def test_register_records_consent():
    """同意后注册成功，同意记录单独成表并带协议版本号。"""
    _fresh_db()
    uid = _register("withconsent").json()["user_id"]
    with db.SessionLocal() as s:
        rec = s.query(db.ConsentRecord).filter_by(user_id=uid).first()
        assert rec and rec.version == CONSENT_VERSION
        assert s.get(db.User, uid).consent_version == CONSENT_VERSION


def test_consent_doc():
    _fresh_db()
    doc = c.get("/v1/consent/doc").json()
    assert doc["version"] == CONSENT_VERSION and doc["items"]


def test_weekly_survey_full_flow():
    """周问卷闭环：教师创建 → 定时任务生成实例 → 学生待填 → 提交 → 幂等重复提交。"""
    _fresh_db()
    _teacher()
    r = c.post("/v1/lab/surveys", json={"kind": "weekly", "title": "每周状态追踪",
                                        "questions": WEEKLY_Q})
    assert r.status_code == 200
    uid = _user_with_plan("stu1")

    # 实例生成前：无待填
    assert c.get(f"/v1/survey/pending?user_id={uid}").json()["pending"] is None
    n = c.post("/jobs/weekly_survey").json()["created"]
    assert n == 1
    # 幂等：再跑不重复
    assert c.post("/jobs/weekly_survey").json()["created"] == 0

    p = c.get(f"/v1/survey/pending?user_id={uid}").json()["pending"]
    assert p and p["kind"] == "weekly" and len(p["schema"]) == 2

    # 缺必填 → 400 人话
    bad = c.post("/v1/survey/respond", json={"user_id": uid, "survey_id": p["survey_id"],
                                             "answers": {"q1": 5}})
    assert bad.status_code == 400 and "还有题目" in bad.json()["error"]

    ok = c.post("/v1/survey/respond", json={"user_id": uid, "survey_id": p["survey_id"],
                                            "answers": {"q1": 6, "q2": False}})
    assert ok.json()["ok"] and not ok.json()["duplicate"]
    # 重复提交幂等
    dup = c.post("/v1/survey/respond", json={"user_id": uid, "survey_id": p["survey_id"],
                                             "answers": {"q1": 6, "q2": False}})
    assert dup.json()["duplicate"]
    # 填完后无待填 + 实例置为 done
    assert c.get(f"/v1/survey/pending?user_id={uid}").json()["pending"] is None
    with db.SessionLocal() as s:
        assert s.query(db.SurveyInstance).filter_by(user_id=uid).first().status == "done"


def test_pre_survey_pending_when_no_weekly():
    """无激活周问卷时，前测优先出现在待填里。"""
    _fresh_db()
    _teacher()
    c.post("/v1/lab/surveys", json={"kind": "pre", "title": "开课前基线",
                                    "questions": WEEKLY_Q[:1]})
    uid = _user_with_plan("stu2")
    p = c.get(f"/v1/survey/pending?user_id={uid}").json()["pending"]
    assert p and p["kind"] == "pre"
    r = c.post("/v1/survey/respond", json={"user_id": uid, "survey_id": p["survey_id"],
                                           "answers": {"q1": 4}})
    assert r.json()["ok"]
    assert c.get(f"/v1/survey/pending?user_id={uid}").json()["pending"] is None
