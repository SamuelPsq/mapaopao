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
    assert card["week_km"] == round(sessions[0]["target_km"], 1)


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


def _seed_user_with_plan(username):
    uid = c.post("/v1/account/register", json={"username": username, "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "height_cm": 170, "weight_kg": 60, "city": "上海", "lat": 31.23, "lon": 121.47})
    c.post(f"/v1/plan/generate?user_id={uid}")
    return uid


def _insert_reports(uid, rows):
    """直接落库不同日期的身体上报（接口只能写今天）。"""
    from datetime import date as _date, timedelta as _td
    with db.SessionLocal.begin() as s:
        for days_ago, sleep, fatigue in rows:
            s.add(db.StatusReport(
                user_id=uid, date=_date.today() - _td(days=days_ago),
                soreness_part="none", sleep_score=sleep, fatigue_score=fatigue,
                entry="popup"))


def test_recovery_alert_sleep_two_days():
    """连续两天睡眠 <5：今日卡返回 recovery_alert(type=sleep)；只提示不自动改课。"""
    _fresh_db()
    uid = _seed_user_with_plan("bodysleep")
    _insert_reports(uid, [(1, 3, 5), (0, 4, 5)])
    card = c.get("/v1/today", params={"user_id": uid}).json()
    assert card["recovery_alert"] and card["recovery_alert"]["type"] == "sleep"
    assert "睡眠" in card["recovery_alert"]["message"]
    # 主课（强度课）应出现「降一档」话术
    if card["main_session"] and card["main_session"]["pace_zone"] in ("threshold", "interval"):
        assert "降一档" in card["recovery_alert"]["message"]
    # 护栏不自动改课：主课仍在
    assert card["main_session"] is not None


def test_recovery_alert_fatigue_and_normal_case():
    """连续两天疲劳 >7 → fatigue 告警；分数正常 → recovery_alert 为 None。"""
    _fresh_db()
    uid = _seed_user_with_plan("bodyfatigue")
    _insert_reports(uid, [(1, 7, 9), (0, 7, 8)])
    card = c.get("/v1/today", params={"user_id": uid}).json()
    assert card["recovery_alert"] and card["recovery_alert"]["type"] == "fatigue"
    assert "疲劳" in card["recovery_alert"]["message"]

    # 只异常一天 → 不触发；分数正常 → 不触发
    normal_uid = _seed_user_with_plan("bodynormal")
    _insert_reports(normal_uid, [(1, 7, 4), (0, 6, 5)])
    card = c.get("/v1/today", params={"user_id": normal_uid}).json()
    assert card["recovery_alert"] is None

    one_uid = _seed_user_with_plan("bodyoneday")
    _insert_reports(one_uid, [(3, 2, 5), (0, 3, 5)])   # 间隔两天，不算「连续两天」
    card = c.get("/v1/today", params={"user_id": one_uid}).json()
    assert card["recovery_alert"] is None


def test_week_review_exposes_bmi_conservative_flag():
    """BMI≥28 生成的计划：周审视接口应带 bmi_conservative=true，标准计划为 false。"""
    _fresh_db()
    heavy_uid = c.post("/v1/account/register", json={"username": "bodye", "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={
        "user_id": heavy_uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "height_cm": 165, "weight_kg": 80, "city": "上海", "lat": 31.23, "lon": 121.47})
    heavy_plan = c.post(f"/v1/plan/generate?user_id={heavy_uid}").json()["plan_id"]
    w = c.get(f"/v1/plan/{heavy_plan}/week/1").json()
    assert w["bmi_conservative"] is True

    normal_uid = c.post("/v1/account/register", json={"username": "bodyf", "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={
        "user_id": normal_uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "height_cm": 170, "weight_kg": 60, "city": "上海", "lat": 31.23, "lon": 121.47})
    normal_plan = c.post(f"/v1/plan/generate?user_id={normal_uid}").json()["plan_id"]
    w = c.get(f"/v1/plan/{normal_plan}/week/1").json()
    assert w["bmi_conservative"] is False


def test_skip_kind_and_calendar_view():
    """跳过方式（停练/力量）落库；/v1/calendar 正确分类 run/rest/strength/skip。"""
    from datetime import date as _date
    _fresh_db()
    uid = _seed_user_with_plan("bodycal")
    plan_id = c.get(f"/v1/today?user_id={uid}").json()["plan_id"]
    sessions = c.get(f"/v1/plan/{plan_id}/week/1").json()["sessions"]

    today_sess = next((s for s in sessions if s["date"] == _date.today().isoformat()), None)
    rest_days = [s for s in sessions if s["type"] == "rest"]
    # 找 3 节课：今天打卡、一节报力量、一节报停练
    targets = [s for s in sessions if s["type"] != "rest" and
               (today_sess is None or s["session_id"] != today_sess["session_id"])][:2]
    if today_sess is not None and len(targets) >= 1:
        c.post(f"/v1/session/{today_sess['session_id']}/log",
               json={"status": "full", "actual_km": today_sess["target_km"], "duration_sec": 3000})
        c.post(f"/v1/session/{targets[0]['session_id']}/log",
               json={"status": "skipped", "skip_kind": "strength"})
    if len(targets) >= 2:
        c.post(f"/v1/session/{targets[1]['session_id']}/log",
               json={"status": "skipped", "skip_kind": "rest"})
        # 给停练那天补一条酸痛上报 → 月历应归类为伤病停练
        with db.SessionLocal.begin() as s:
            s.add(db.StatusReport(user_id=uid, date=_date.fromisoformat(targets[1]["date"]),
                                  soreness_part="knee", sleep_score=6, fatigue_score=4))

    month = _date.today().strftime("%Y-%m")
    cal = c.get("/v1/calendar", params={"user_id": uid, "month": month}).json()
    assert cal["month"] == month and len(cal["days"]) >= 28
    by_date = {d["date"]: d for d in cal["days"]}
    if today_sess is not None:
        d = by_date[today_sess["date"]]
        assert d["kind"] == "run" and d["km"] == round(today_sess["target_km"], 1) and d["pace"]
    if len(targets) >= 2:
        d_str = by_date[targets[0]["date"]]
        assert d_str["kind"] == "strength", "标记力量的跳过应显示为室内力量"
        d_rest = by_date[targets[1]["date"]]
        assert d_rest["kind"] == "injury" and "膝盖" in d_rest["reason"], \
            "有酸痛上报的停练应红标注伤病原因"
    # 休息日
    if rest_days:
        assert by_date[rest_days[0]["date"]]["kind"] == "rest"
    # skip_kind 落库校验
    with db.SessionLocal() as s:
        row = s.query(db.SessionLog).filter_by(skip_kind="strength").first()
        assert row is not None


def test_today_weather_block_structured(monkeypatch):
    """课表卡片的结构化天气：mock 7 天预报，断言气温/状况/周序列格式。"""
    from datetime import date as _date, timedelta as _td
    from server.infra import weather as _wx
    _fresh_db()
    uid = _seed_user_with_plan("bodywx")
    today = _date.today()
    fake = {}
    for i in range(7):
        d = today + _td(days=i)
        fake[d.isoformat()] = _wx.WeatherDay(
            date=d.isoformat(), t_max=20 + i, rh_mean=50, precip=0,
            weather_code=0, aqi=42, apparent_max=22 + i,
            source="live", confidence="high")
    monkeypatch.setattr(_main, "get_week_weather", lambda *a, **k: fake)
    card = c.get("/v1/today", params={"user_id": uid}).json()
    wx = card["weather"]
    assert wx["t_max"] == 20.0 and wx["condition"] == "晴" and wx["aqi"] == 42
    assert len(wx["week"]) == 7 and wx["week"][3]["t_max"] == 23.0
    # 未 mock 时（离线降级）字段为 None，不报错
    monkeypatch.undo()
    card2 = c.get("/v1/today", params={"user_id": uid}).json()
    assert card2["weather"] is None or "t_max" in (card2["weather"] or {})


def test_over_status_counts_in_total_and_week_km():
    """回归：超额完成(over)的里程必须计入今日卡 total_km / week_km，口径与月历一致。

    bug：/v1/today 聚合跑量时漏了 "over"，导致超额打卡后今日卡与月历对不上。
    """
    from datetime import date as _date
    _fresh_db()
    uid = _seed_user_with_plan("bodyover")
    plan_id = c.get(f"/v1/today?user_id={uid}").json()["plan_id"]
    sessions = c.get(f"/v1/plan/{plan_id}/week/1").json()["sessions"]
    target = max(sessions, key=lambda s: s["target_km"])
    over_km = round(target["target_km"] * 1.3, 1)   # 明显超额，保证 status=over 生效

    r = c.post(f"/v1/session/{target['session_id']}/log",
               json={"status": "over", "actual_km": over_km, "duration_sec": 5400})
    assert r.status_code == 200
    with db.SessionLocal() as s:
        log = s.query(db.SessionLog).filter_by(session_id=target["session_id"]).first()
        assert log.status == "over", "log 接口应原样落 over 状态"

    card = c.get("/v1/today", params={"user_id": uid}).json()
    assert card["total_km"] == over_km, "累计总跑量必须包含超额完成里程"
    # 若打卡的课在当前计划周，本周跑量也必须包含它
    with db.SessionLocal() as s:
        sess_row = s.get(db.Session, target["session_id"])
        if sess_row.week_no == card["week_no"]:
            assert card["week_km"] == over_km, "本周跑量必须包含超额完成里程"

    month = _date.fromisoformat(target["date"]).strftime("%Y-%m")
    cal = c.get("/v1/calendar", params={"user_id": uid, "month": month}).json()
    day = next(d for d in cal["days"] if d["date"] == target["date"])
    assert day["kind"] == "run" and day["km"] == over_km, "月历口径应与今日卡一致"
