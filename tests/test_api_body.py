"""身高体重 BMI 规划修正 / 修改体重 / 打卡总用时反算配速 / 大幅超额进阶 的回归测试。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_api_body_test.db")

from fastapi.testclient import TestClient

from server import db
from server.main import app
from server import main as _main
from server.plan import Profile, generate_plan

_main.AUTH_ENABLED = False

db.init_db()
c = TestClient(app)


def _fresh_db():
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)


def _profile(**kw):
    base = dict(recent_km=10, recent_sec=3600, avail_days=4, goal_race="half",
                ability="B_base", injury_flag="N")
    base.update(kw)
    return Profile(**base)


def test_bmi_conservative_plan_tightens_gain():
    """BMI≥28：跑量基线打 9 折、周增幅上限减半（保守规划）。"""
    normal = generate_plan(_profile(height_cm=170, weight_kg=60), vdot_override=40)
    heavy = generate_plan(_profile(height_cm=165, weight_kg=80), vdot_override=40)  # BMI 29.4
    assert normal["bmi"] is not None and normal["conservative"] is False
    assert heavy["conservative"] is True and heavy["bmi"] >= 28
    # 同 VDOT 同参数：保守计划每周跑量都不高于标准计划
    for wn, wh in zip(normal["weeks"], heavy["weeks"]):
        assert wh["planned_km"] <= wn["planned_km"] + 1e-6
    # 标准计划第 2 周按 ramp 1.10 展开；保守计划被 5% 增幅上限压住
    assert normal["weeks"][1]["planned_km"] > heavy["weeks"][1]["planned_km"]


def test_weight_endpoint_updates_bmi_and_advice():
    """修改体重：实时回写，跨 BMI 28 阈值时给出保守规划建议。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "bodyw", "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "height_cm": 170, "weight_kg": 60, "city": "上海", "lat": 31.23, "lon": 121.47})
    r = c.post("/v1/user/weight", json={"user_id": uid, "weight_kg": 85})   # BMI 29.4
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and abs(body["bmi"] - 29.4) < 0.1
    assert "advice" in body, "跨阈值时应提示保守规划"
    # 指标页能读到最新体重与 BMI
    m = c.get("/v1/metrics", params={"user_id": uid}).json()
    assert m["user"]["weight_kg"] == 85 and m["user"]["bmi"] >= 28


def test_log_duration_computes_pace_and_prompts_advance():
    """打卡填总用时：自动反算配速并落库；超额 >3km 时返回进阶询问。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "bodya", "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "height_cm": 170, "weight_kg": 60, "city": "上海", "lat": 31.23, "lon": 121.47})
    plan_id = c.post(f"/v1/plan/generate?user_id={uid}").json()["plan_id"]
    sessions = c.get(f"/v1/plan/{plan_id}/week/1").json()["sessions"]
    long_run = max(sessions, key=lambda s: s["target_km"])

    # 长距离目标 12km 上下，实跑 16.5km / 90 分钟（配速 5:27）→ 超额 >3km
    r = c.post(f"/v1/session/{long_run['session_id']}/log",
               json={"status": "full", "actual_km": 16.5, "duration_sec": 5400})
    assert r.status_code == 200
    body = r.json()
    assert body.get("advance_prompt") is True
    assert body.get("advance_pace") == "5:27"
    with db.SessionLocal() as s:
        log = s.query(db.SessionLog).filter_by(session_id=long_run["session_id"]).first()
        assert log.duration_sec == 5400 and log.actual_pace == "5:27"


def test_advance_endpoint_refreshes_future_weeks():
    """进阶确认：按真实里程+用时折算 VDOT 并刷新未开始的周。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "bodyb", "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "height_cm": 170, "weight_kg": 60, "city": "上海", "lat": 31.23, "lon": 121.47})
    c.post(f"/v1/plan/generate?user_id={uid}").json()["plan_id"]
    before = c.get(f"/v1/today", params={"user_id": uid}).json()
    old_vdot = before["vdot"]
    old_pace = before["main_session"]["target_pace"] if before["main_session"] else ""

    r = c.post("/v1/user/advance", json={"user_id": uid, "actual_km": 16.5, "duration_sec": 5400})
    assert r.status_code == 200
    body = r.json()
    assert body["pace"] == "5:27"
    if abs(body["vdot"] - old_vdot) >= 1.5:
        assert "已按照您的跑力重新安排" in body["note"]
        after = c.get("/v1/today", params={"user_id": uid}).json()
        assert after["vdot"] == body["vdot"]
    else:
        assert "无需重排" in body["note"]


def test_today_card_has_total_km():
    """今日卡返回累计总跑量（full/partial 的实际里程之和）。"""
    _fresh_db()
    uid = c.post("/v1/account/register", json={"username": "bodyc", "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "city": "上海", "lat": 31.23, "lon": 121.47})
    plan_id = c.post(f"/v1/plan/generate?user_id={uid}").json()["plan_id"]
    sessions = c.get(f"/v1/plan/{plan_id}/week/1").json()["sessions"]
    c.post(f"/v1/session/{sessions[0]['session_id']}/log",
           json={"status": "full", "actual_km": sessions[0]["target_km"], "duration_sec": 3000})
    card = c.get("/v1/today", params={"user_id": uid}).json()
    assert card["total_km"] == round(sessions[0]["target_km"], 1)


def test_status_report_new_fields_and_stretch_advice(monkeypatch):
    """打卡问卷：训练中状态/肌肉酸痛新字段落库；拉伸建议接口（模板路径语义确定性）。"""
    _fresh_db()
    # 关掉 LLM 走模板路径，保证文案语义可断言（LLM 路径由 explain 专项测试覆盖）
    from server import explain
    monkeypatch.setattr(explain, "_llm_call", lambda *a, **k: None)
    uid = c.post("/v1/account/register", json={"username": "bodyd", "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "city": "上海", "lat": 31.23, "lon": 121.47})

    r = c.post("/v1/status/report", json={
        "user_id": uid, "soreness_part": "none", "muscle_sore": True,
        "breath_state": "gasp", "sleep_score": 6, "fatigue_score": 5, "entry": "popup"})
    assert r.status_code == 200 and r.json()["ok"]
    with db.SessionLocal() as s:
        rep = s.query(db.StatusReport).filter_by(user_id=uid).first()
        assert rep.muscle_sore is True and rep.breath_state == "gasp"

    # 长距离 → 模板含泡沫轴放松
    r = c.post("/v1/stretch", json={"user_id": uid, "status": "full",
                                    "type_cn": "长距离", "target_km": 16.0})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] and body["source"] in ("llm", "template")
    if body["source"] == "template":
        assert "泡沫轴" in body["text"]

    # 今天跑不了 → 恢复建议而非拉伸动作（LLM 措辞不固定，只校验语义）
    r = c.post("/v1/stretch", json={"user_id": uid, "status": "skipped",
                                    "type_cn": "轻松跑", "target_km": 6.0})
    skipped_text = r.json()["text"]
    assert any(k in skipped_text for k in ("省", "恢复", "休息", "睡眠")), \
        "skipped 场景应给恢复建议而不是拉伸动作清单"
