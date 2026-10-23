"""LLM 解释层多场景回归：未配置密钥时全部降级为规则模板，埋点与指标正确。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_explain_scenes_test.db")

from fastapi.testclient import TestClient

from server import db
from server.main import app
from server import main as _main

_main.AUTH_ENABLED = False   # 本文件回归业务逻辑，鉴权由 test_api_auth.py 专门覆盖

db.init_db()
c = TestClient(app)


def _fresh():
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)


def _onboard(uid):
    c.post("/v1/profile", json={**{
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "city": "上海", "lat": 31.23, "lon": 121.47, "max_duration": 90}})
    return c.post(f"/v1/plan/generate?user_id={uid}").json()["plan_id"]


def test_all_scenes_fallback_without_llm():
    """未配置 LLM 环境变量时，每个场景都返回规则模板，来源标记 template。"""
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(var, None)
    from server.explain import (degrade_advice_template, reason_line_template,
                                render_scene, week_comment_template)
    r, src = render_scene("reason_line", {"is_key": True, "key_done": 0, "key_total": 2},
                          reason_line_template({"is_key": True, "key_done": 0, "key_total": 2}))
    assert src == "template" and "关键课 0/2" in r
    r, src = render_scene("week_comment", {"week_no": 1, "phase": "load", "rate_pct": 60,
                                           "done": 3, "planned": 5},
                          week_comment_template({"rate_pct": 60}))
    assert src == "template" and "60%" in r
    r, src = render_scene("degrade_advice", {"total_done": 1, "total_plan": 6, "race": "全马"},
                          degrade_advice_template({"total_done": 1, "total_plan": 6, "race": "全马"}))
    assert src == "template" and "全马" in r


def test_week_review_comment_and_llm_usage_metric():
    _fresh()
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": "scenuser", "password": "run12345"}).json()["user_id"]
    plan_id = _onboard(uid)
    w = c.get(f"/v1/plan/{plan_id}/week/1").json()
    assert w["comment"]["text"] and w["comment"]["source"] == "template"
    assert "执行率" in w["comment"]["text"]
    # 今日卡原因行也走了 reason_line 场景（无跑步日可能不产生，这里只验证埋点累计）
    c.get(f"/v1/today?user_id={uid}")
    m = c.get(f"/v1/metrics?user_id={uid}").json()
    total = m["llm_usage"]["llm"] + m["llm_usage"]["template"]
    assert m["llm_usage"]["rate"] == 0 and total >= 2, "week_comment 与 reason_line 都应留痕"


def test_no_hallucination_still_guards_scenes():
    """数值断言对单字段场景同样生效：LLM 编造数字则整条作废。"""
    from server.explain import assert_no_hallucination
    facts = {"week_no": 1, "phase": "load", "rate_pct": 60, "done": 3, "planned": 5}
    assert assert_no_hallucination("本周执行率 60%，完成 3 次", facts)
    assert not assert_no_hallucination("本周执行率 85%，超出预期", facts)


def test_stretch_plan_moves_and_assertion_whitelist():
    """拉伸 facts 结构化后，LLM 照抄具体动作（30 秒×2 等）能通过数值断言，编造数字仍被拦。"""
    from server.explain import (assert_no_hallucination, stretch_advice_template,
                                stretch_plan)

    f = {"status": "full", "type_cn": "长距离", "target_km": 16.0}
    plan = stretch_plan(f)
    assert plan["skipped"] is False
    names = [m["name"] for m in plan["moves"]]
    assert any("泡沫轴" in n for n in names) and any("靠墙静蹲" in n for n in names)

    facts = {**f, "plan": plan}
    # 模板兜底文案含具体动作
    tpl = stretch_advice_template(facts)
    assert "泡沫轴" in tpl and "秒×" in tpl

    # LLM 照抄动作清单里的数字 → 断言放行（这是本次改造的关键）
    llm_ok = ("跑完先走 3–5 分钟让心率回落，再做：小腿后侧拉伸（扶墙弓步）30 秒×2；"
              "髂胫束放松（泡沫轴）每侧 60 秒×1；靠墙静蹲 45 秒×2。"
              "今晚睡眠尽量充足，恢复发生在休息里")
    assert assert_no_hallucination(llm_ok, facts), "照抄 facts 里的动作数字必须放行"
    # 编造动作时长（90 秒不在 facts 里）→ 拦下
    llm_bad = "小腿后侧拉伸 90 秒×2，然后收工"
    assert not assert_no_hallucination(llm_bad, facts)

    # skipped 场景：恢复建议而非动作清单，分钟数（10）来自 facts 放行
    fs = {"status": "skipped", "type_cn": "轻松跑", "target_km": 6.0}
    ps = stretch_plan(fs)
    assert ps["skipped"] is True
    facts_s = {**fs, "plan": ps}
    assert assert_no_hallucination("睡前全身静态拉伸 10 分钟×1，明天按原计划来", facts_s)
    assert "省" in stretch_advice_template(facts_s) or "恢复" in stretch_advice_template(facts_s)
