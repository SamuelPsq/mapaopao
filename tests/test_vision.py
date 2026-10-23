"""智能识别成绩（/v1/vision/vdot）：AI 读图 + 代码数值校验的防幻觉分工。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_vision_test.db")

from fastapi.testclient import TestClient

from server import db
from server import vision
from server.main import app
from server import main as _main

_main.AUTH_ENABLED = False

db.init_db()
c = TestClient(app)


def _fresh_db():
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)


def _register(uid_name):
    return c.post("/v1/account/register",
                  json={"agree_data_use": True, "username": uid_name, "password": "run12345"}).json()["user_id"]


def test_validate_accepts_plausible_and_rejects_garbage():
    """数值断言：量纲错误 / 超区间 / 缺字段一律拒用，宁缺毋滥。"""
    assert vision._validate({"distance_km": 10.02, "duration_sec": 3504}) == {
        "distance_km": 10.02, "duration_sec": 3504, "type": "other", "note": ""}
    # 把 5'30"/km 读成 330 km 这类量纲错误
    assert vision._validate({"distance_km": 10, "duration_sec": 330}) is None
    # 超出人类合理区间
    assert vision._validate({"distance_km": 9999, "duration_sec": 60}) is None
    assert vision._validate({"distance_km": 0, "duration_sec": 0}) is None      # AI 主动填 0 = 识别不出
    # 缺字段 / 非数字
    assert vision._validate({"distance_km": "十公里"}) is None
    assert vision._validate({}) is None


def test_endpoint_returns_computed_vdot(monkeypatch):
    """识别成功：返回距离/用时/配速/折算 VDOT 与当前 VDOT，供用户确认。"""
    _fresh_db()
    uid = _register("visok")
    monkeypatch.setattr(vision, "extract_result",
                        lambda b, m: {"distance_km": 10.02, "duration_sec": 3504,
                                      "type": "watch", "note": "Garmin 10.02km 58:24"})
    r = c.post("/v1/vision/vdot", params={"user_id": uid},
               files={"file": ("run.jpg", b"\xff\xd8fake", "image/jpeg")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["distance_km"] == 10.02 and j["duration_sec"] == 3504
    assert j["current_vdot"] == 30
    from server.knowledge.rules import implied_vdot
    assert j["vdot"] == implied_vdot(10.02, 3504)


def test_endpoint_rejects_when_recognition_fails(monkeypatch):
    """识别失败：422 + 人话文案，引导手动输入，绝不静默吞掉。"""
    _fresh_db()
    uid = _register("visbad")
    monkeypatch.setattr(vision, "extract_result", lambda b, m: None)
    r = c.post("/v1/vision/vdot", params={"user_id": uid},
               files={"file": ("blur.jpg", b"\xff\xd8fake", "image/jpeg")})
    assert r.status_code == 422
    assert "没能从图片里识别出" in r.json()["error"]


def test_endpoint_rejects_non_image():
    """非图片格式：415，挡住把文本/可执行文件当图片传上来的情况。"""
    _fresh_db()
    uid = _register("vistype")
    r = c.post("/v1/vision/vdot", params={"user_id": uid},
               files={"file": ("evil.txt", b"not an image", "text/plain")})
    assert r.status_code == 415
    assert "只支持" in r.json()["error"]


def test_vision_result_applies_through_advance(monkeypatch):
    """端到端：识别出的数字经 /v1/user/advance 应用——VDOT 刷新、未开始的周重排。"""
    _fresh_db()
    uid = _register("visend")
    c.post("/v1/profile", json={
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "city": "上海", "lat": 31.23, "lon": 121.47, "max_duration": 90})
    c.post(f"/v1/plan/generate?user_id={uid}")
    monkeypatch.setattr(vision, "extract_result",
                        lambda b, m: {"distance_km": 10.02, "duration_sec": 3504,
                                      "type": "race_result", "note": ""})
    got = c.post("/v1/vision/vdot", params={"user_id": uid},
                 files={"file": ("race.jpg", b"\xff\xd8fake", "image/jpeg")}).json()
    r = c.post("/v1/user/advance", json={
        "user_id": uid, "actual_km": got["distance_km"], "duration_sec": got["duration_sec"]})
    assert r.status_code == 200, r.text
    assert r.json()["vdot"] == got["vdot"]
    with db.SessionLocal() as s:
        assert s.get(db.User, uid).vdot == got["vdot"]
