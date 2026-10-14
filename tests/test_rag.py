"""RAG 知识检索 + 案例召回 + 偏差触发 AI 微调的回归测试。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_rag_test.db")

from datetime import date, timedelta

from server import db, rag
from server.tune import tune_after_deviation, validate_ops


def _fresh():
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)


def _user_plan(uid="raguser"):
    _fresh()
    with db.SessionLocal.begin() as s:
        s.add(db.User(user_id=uid, vdot=40, ability_tier="B_base", goal_race="half",
                      avail_days=4, max_duration=90, injury_flag="N",
                      city="上海", lat=31.23, lon=121.47))
        plan = db.Plan(plan_id=f"plan-{uid}", user_id=uid, version="v3.1",
                       target_distance="half", start_date=date.today() - timedelta(days=7))
        s.add(plan)
        s.add(db.PlanWeek(plan_week_id=f"plan-{uid}-W1", plan_id=f"plan-{uid}",
                          week_no=1, phase="load", planned_km=20.0))
        s.add(db.Session(session_id=f"plan-{uid}-W1-s1", plan_week_id=f"plan-{uid}-W1",
                         plan_id=f"plan-{uid}", week_no=1, seq=1,
                         date=date.today() + timedelta(days=1),
                         type="easy_run", is_key=False, pace_zone="E",
                         target_km=6.0, target_pace="6:30"))
        s.add(db.Session(session_id=f"plan-{uid}-W1-s2", plan_week_id=f"plan-{uid}-W1",
                         plan_id=f"plan-{uid}", week_no=1, seq=2,
                         date=date.today() + timedelta(days=3),
                         type="long_run", is_key=True, pace_zone="M",
                         target_km=12.0, target_pace="6:45"))
    with db.SessionLocal() as s:
        return s.get(db.User, uid), s.get(db.Plan, f"plan-{uid}")


def test_retrieve_relevant_kb():
    hits = rag.retrieve("超额完成 之后怎么调整训练量")
    assert hits, "必须有命中的知识块"
    ids = [h["id"] for h in hits]
    assert "kb_overload" in ids, "超额查询必须召回超量恢复原则"
    # 相关性排序：第一个分数不低于后面的
    assert hits[0]["score"] >= hits[-1]["score"]


def test_retrieve_under_done_gap():
    hits = rag.retrieve("只跑了一半 缺口要不要补跑")
    ids = [h["id"] for h in hits]
    assert "kb_gap" in ids


def test_record_and_recall_case():
    _user_plan()
    cid = rag.record_case("raguser", 1, "load", "over_done", 1.5,
                          context={"kb_ids": ["kb_overload"]},
                          decision=[{"op": "adjust", "session_id": "x", "km": 6.5}])
    # 相似情境（同阶段同触发、偏差率接近）应召回
    hits = rag.recall_cases("raguser", "load", "over_done", 1.4)
    assert hits and hits[0]["case_id"] == cid
    # 偏差率差很远的不同触发不应召回
    far = rag.recall_cases("raguser", "taper", "skipped", 0.1)
    assert far == []


def test_backfill_outcome():
    _user_plan()
    rag.record_case("raguser", 1, "load", "over_done", 1.5, context={}, decision=None)
    rag.backfill_outcome("raguser", "plan-raguser", 1, 0.8)
    with db.SessionLocal() as s:
        row = s.query(db.AICase).first()
        assert row.outcome == "good"
    # 已有结果的案例不被低执行率覆盖（good 是一种已验证的记忆）
    rag.backfill_outcome("raguser", "plan-raguser", 1, 0.2)
    with db.SessionLocal() as s:
        assert s.query(db.AICase).first().outcome == "good"
    # 另一条 pending 案例遇到低执行率 → bad
    rag.record_case("raguser", 1, "load", "under_done", 0.3, context={}, decision=None)
    rag.backfill_outcome("raguser", "plan-raguser", 1, 0.2)
    with db.SessionLocal() as s:
        bad = s.query(db.AICase).filter_by(trigger="under_done").first()
        assert bad.outcome == "bad"


def test_tune_after_deviation_records_case_without_llm():
    """未配置密钥时：微调不落地（template），但案例必须留档、埋点必须完整。"""
    _user_plan()
    user, plan = None, None
    with db.SessionLocal() as s:
        user = s.get(db.User, "raguser")
        plan = s.get(db.Plan, "plan-raguser")
    r = tune_after_deviation(user, plan, 1, "over_done", 1.5,
                             recent_logs=[{"date": "2026-10-14", "type": "easy_run",
                                           "target": 6.0, "actual": 9.0, "status": "full"}])
    assert r["src"] == "template" and r["applied"] is False
    assert r["case_id"]
    with db.SessionLocal() as s:
        case = s.get(db.AICase, r["case_id"])
        assert case.trigger == "over_done" and case.dev_ratio == 1.5
        assert case.context_json.get("kb_ids"), "案例必须记录检索到的知识块"
        ev = s.query(db.Event).filter_by(name="ai_tune_deviation").first()
        assert ev and ev.payload["prompt_ver"] == "tune_v2"
        assert ev.payload["trigger"] == "over_done"
        # 未落地 → 课表不变
        sess = s.get(db.Session, "plan-raguser-W1-s1")
        assert sess.target_km == 6.0 and sess.source == "template"


def test_tune_after_deviation_applies_valid_ops():
    """模拟 LLM 返回合法 ops：校验通过后落地、写案例、埋点 applied=true。"""
    user, plan = _user_plan()
    from server import tune
    orig = tune.propose_ops
    tune.propose_ops = lambda facts, rag_ctx=None: [
        {"op": "adjust", "session_id": "plan-raguser-W1-s1", "km": 6.5}]   # 6.0→6.5，+8.3%，合法
    try:
        r = tune_after_deviation(user, plan, 1, "over_done", 1.5, recent_logs=[])
    finally:
        tune.propose_ops = orig
    assert r["applied"] is True and r["src"] == "llm"
    with db.SessionLocal() as s:
        sess = s.get(db.Session, "plan-raguser-W1-s1")
        assert sess.target_km == 6.5 and sess.source == "ai_tuned"
        key = s.get(db.Session, "plan-raguser-W1-s2")
        assert key.target_km == 12.0, "关键课绝不允许被动"
        ev = s.query(db.Event).filter_by(name="ai_tune_deviation").first()
        assert ev.payload["applied"] is True


def test_tune_after_deviation_rejects_bad_ops():
    """LLM 越界（动关键课）→ 拒收，课表不动，案例 decision 为 null。"""
    user, plan = _user_plan()
    from server import tune
    orig = tune.propose_ops
    tune.propose_ops = lambda facts, rag_ctx=None: [
        {"op": "adjust", "session_id": "plan-raguser-W1-s2", "km": 20.0}]
    try:
        r = tune_after_deviation(user, plan, 1, "over_done", 1.5, recent_logs=[])
    finally:
        tune.propose_ops = orig
    assert r["applied"] is False
    with db.SessionLocal() as s:
        assert s.get(db.Session, "plan-raguser-W1-s2").target_km == 12.0
        case = s.get(db.AICase, r["case_id"])
        assert case.decision_json is None, "被拒收的决策不写入案例 decision"


def test_validate_ops_blocks_aux_exceeding_long_run():
    """结构红线：辅助课调整后不得超过长距离的 70%（轻松跑不许比长距离还长）。"""
    from server.tune import validate_ops
    import datetime as _dt
    base = _dt.date(2026, 10, 19)

    class S:
        def __init__(self, sid, km, key, t="easy_run"):
            self.session_id, self.target_km, self.is_key = sid, km, key
            self.status, self.type, self.date = "planned", t, base
            self.pace_zone = "E"

    sessions = [S("aux", 6.0, False), S("long", 12.0, True, "long_run")]
    # 长距离 12 → 上限 8.4；6.0→8.5 在 ±10% 外会被拒，构造 8.0→8.5（+6.25%）但超红线
    sessions[0].target_km = 8.0
    bad = validate_ops([{"op": "adjust", "session_id": "aux", "km": 8.5}], sessions, base)
    assert bad is None, "辅助课超过长距离 70% 必须拒收"
    ok = validate_ops([{"op": "adjust", "session_id": "aux", "km": 8.0 + 0.5}], sessions, base) \
        if False else validate_ops([{"op": "adjust", "session_id": "aux", "km": 8.0}], sessions, base)
    assert ok is not None


def test_metrics_ai_decision_stats():
    """指标页 AI 决策卡片：微调落地、推荐、知识库注入与案例召回的统计口径。"""
    from fastapi.testclient import TestClient
    from server.main import app
    from server import main as _main
    saved = _main.AUTH_ENABLED
    _main.AUTH_ENABLED = False
    try:
        _user_plan()
        user, plan = None, None
        with db.SessionLocal() as s:
            user = s.get(db.User, "raguser")
            plan = s.get(db.Plan, "plan-raguser")
        r = tune_after_deviation(user, plan, 1, "over_done", 1.5, recent_logs=[])
        m = TestClient(app).get("/v1/metrics?user_id=raguser").json()
        d = m["ai_decision"]
        assert d["tune_total"] == 1 and d["kb_injections"] == 1
        assert d["kb_blocks_used"] >= 1, "注入的知识块要去重计数"
        assert d["tune_applied"] in (0, 1)
        # 埋点格式带 kb_ids → cases_recalled 口径存在
        assert "cases_recalled" in d and "recommend_total" in d
    finally:
        _main.AUTH_ENABLED = saved


def test_tune_after_deviation_weekly_cooldown():
    """同一计划同一周只微调一次：第二次调用直接冷却，不再改课表。"""
    user, plan = _user_plan()
    from server import tune
    orig = tune.propose_ops
    calls = {"n": 0}

    def fake(facts, rag_ctx=None):
        calls["n"] += 1
        return [{"op": "adjust", "session_id": "plan-raguser-W1-s1", "km": 6.5}]

    tune.propose_ops = fake
    try:
        r1 = tune_after_deviation(user, plan, 1, "over_done", 1.5, recent_logs=[])
        r2 = tune_after_deviation(user, plan, 1, "under_done", 0.3, recent_logs=[])
    finally:
        tune.propose_ops = orig
    assert r1["applied"] is True
    assert r2["applied"] is False and calls["n"] == 1, "第二次必须冷却，不重复调 LLM"
    with db.SessionLocal() as s:
        assert s.get(db.Session, "plan-raguser-W1-s1").target_km == 6.5, "只生效一次"


def test_week_review_backfills_case_outcome():
    """周审视接口：周结束后访问，pending 案例按执行率回填 good/bad。"""
    from fastapi.testclient import TestClient
    from server.main import app
    from server import main as _main
    saved = _main.AUTH_ENABLED
    _main.AUTH_ENABLED = False
    try:
        _user_plan()   # start_date = 今天-7，第 1 周已于昨天结束
        rag.record_case("raguser", 1, "load", "over_done", 1.5, context={}, decision=None)
        rag.record_case("raguser", 1, "load", "under_done", 0.3, context={}, decision=None)
        # 两节课：一节 full → 执行率 1/2 = 0.5 → good/bad 都不打（中性周）
        with db.SessionLocal.begin() as s:
            s.add(db.SessionLog(session_id="plan-raguser-W1-s1", status="full",
                                actual_km=6.0, actual_pace="", rpe=None))
        c = TestClient(app)
        r = c.get("/v1/plan/plan-raguser/week/1")
        assert r.status_code == 200 and r.json()["adherence"]["rate"] == 0.5
        with db.SessionLocal() as s:
            assert s.query(db.AICase).filter_by(trigger="over_done").first().outcome == "pending"
        # 再补一节课 full → 执行率 1.0 → 两条都 good
        with db.SessionLocal.begin() as s:
            s.add(db.SessionLog(session_id="plan-raguser-W1-s2", status="full",
                                actual_km=12.0, actual_pace="", rpe=None))
        r = c.get("/v1/plan/plan-raguser/week/1")
        assert r.json()["adherence"]["rate"] == 1.0
        with db.SessionLocal() as s:
            for case in s.query(db.AICase).all():
                assert case.outcome == "good", "执行率 100% 的周末，pending 案例应回填 good"
    finally:
        _main.AUTH_ENABLED = saved


def test_beginner_tips_subset_and_endpoint():
    """跑步知识子集：只含新手/禁忌/前中后标签的块，接口每次随机返回一条。"""
    from fastapi.testclient import TestClient
    from server.main import app
    from server import main as _main
    saved = _main.AUTH_ENABLED
    _main.AUTH_ENABLED = False
    try:
        tips = rag.beginner_tips()
        assert len(tips) >= 8, "新手向知识块应覆盖禁忌+跑前+跑中+跑后"
        ids = {t["id"] for t in tips}
        assert "kb_taboo_daily_intensity" in ids
        assert "kb_before_run" in ids and "kb_during_run" in ids and "kb_after_run" in ids
        assert "kb_longrun_rest" not in ids, "非新手标签的块不应进跑步知识子集"
        assert all(t["name"] and t["text"] for t in tips)

        c = TestClient(app)
        seen = set()
        for _ in range(20):
            r = c.get("/v1/kb/tip")
            assert r.status_code == 200
            body = r.json()
            assert body["text"] and body["name"]
            seen.add(body["id"])
        assert len(seen) > 1, "多次调用应能抽到不同的知识块"
    finally:
        _main.AUTH_ENABLED = saved
