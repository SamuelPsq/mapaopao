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
    uid = c.post("/v1/account/register", json={"username": "scenuser", "password": "run12345"}).json()["user_id"]
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
