"""AI 跑步助手：知识库合并加载、RAG 召回出处、/v1/assistant/chat 双路径（LLM/兜底）。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_assistant_test.db")

from fastapi.testclient import TestClient

from server import db, rag
from server.main import app
from server import main as _main

_main.AUTH_ENABLED = False

db.init_db()
c = TestClient(app)


def _fresh_user(username):
    uid = c.post("/v1/account/register", json={"agree_data_use": True, "username": username, "password": "run12345"}).json()["user_id"]
    c.post("/v1/profile", json={
        "user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
        "goal_race": "half", "ability": "B_base", "injury_flag": "N",
        "height_cm": 170, "weight_kg": 60, "city": "上海", "lat": 31.23, "lon": 121.47})
    return uid


def test_kb_merged_from_two_docs():
    """知识库合并自训练原则 + 跑者经验 + 伤病康复三个文档，召回结果带来源标注。"""
    sources = {c.get("source") for c in rag.KB}
    assert {"训练原则", "跑者经验", "伤病康复"} <= sources
    hits = rag.retrieve("睡眠不足还能照常训练吗", k=4)
    assert hits, "经验类问题应能召回条目"
    assert all("source" in h and "name" in h for h in hits)
    assert any(h["source"] == "跑者经验" for h in hits), "应优先命中 UGC 经验条目"


def test_injury_kb_and_medical_disclaimer(monkeypatch):
    """伤病问题召回到「伤病康复」条目，且回答带就医提示（medical 标志 + 文案）。"""
    from server import assistant
    hits = rag.retrieve("跟腱疼，早上起床僵硬怎么办", k=4)
    assert any(h["source"] == "伤病康复" for h in hits), "跟腱问题应命中伤病康复库"
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    out = assistant.answer("dummy_uid", "跟腱疼，早上起床僵硬怎么办？")
    assert out["medical"] is True
    assert "就医" in out["answer"], "伤病类回答必须带就医提示"
    # 非伤病问题不挂 medical 标志
    out2 = assistant.answer("dummy_uid", "没有手表怎么判断强度？")
    assert out2["medical"] is False


def test_assistant_chat_kb_fallback(monkeypatch):
    """LLM 不可用（未配置网关）时降级为知识拼接，出处可溯源。"""
    from server import assistant
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    out = assistant.answer("dummy_uid", "膝盖有点疼要不要停跑？")
    assert out["from"] == "kb"
    assert "[跑者经验·" in out["answer"] or "[训练原则·" in out["answer"]
    assert out["sources"] and all(s["name"] for s in out["sources"])


def test_topic_switch_not_polluted_by_history(monkeypatch):
    """换话题提问不被上文主题劫持：问过厌跑后再问膝痛，召回应以伤痛条目为主。

    线上 badcase：历史消息无条件拼进检索 query，聊过「厌跑」后再问
    「膝盖有点痛要不要停跑」，top3 全是厌跑/状态差条目。修复：仅指代问法
    （命中 _ANAPHORA_RE）才拼接历史。
    """
    from server import assistant
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    uid = "topic_switch_regression"
    assistant.answer(uid, "最近很厌跑不想练了怎么办")
    out = assistant.answer(uid, "膝盖有点痛，要不要停跑？")
    names = [s["name"] for s in out["sources"]]
    assert not any("厌跑" in n or "状态差" in n for n in names), \
        f"换话题后召回仍被上文主题劫持: {names}"
    assert any("酸痛和伤痛" in n or "疼痛评分" in n for n in names), \
        f"膝痛问题应优先命中伤痛判断/疼痛评分条目: {names}"


def test_anaphora_question_still_uses_history(monkeypatch):
    """指代问法仍受益于历史：上文聊过厌跑，「那我今天还跑吗」应召回上文主题。"""
    from server import assistant
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    uid = "anaphora_regression"
    assistant.answer(uid, "最近很厌跑不想练了怎么办")
    out = assistant.answer(uid, "那我今天还跑吗？")
    names = [s["name"] for s in out["sources"]]
    assert any("厌跑" in n or "状态差" in n for n in names), \
        f"指代问法应能借历史召回到上文主题: {names}"


def test_assistant_chat_llm_path_guard(monkeypatch):
    """LLM 返回引用超范围编号（幻觉出处）时整条作废，走降级。"""
    from server import assistant
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.setenv(var, "stub")
    monkeypatch.setattr(assistant, "_raw_call", lambda payload, base, key: "答案正文 [99]")
    out = assistant.answer("dummy_uid", "为什么轻松跑那么多？")
    assert out["from"] == "kb", "引用编号超出召回条数必须作废"
    # 正常引用编号则放行
    monkeypatch.setattr(assistant, "_raw_call", lambda payload, base, key: "答案正文 [1]")
    out = assistant.answer("dummy_uid", "为什么轻松跑那么多？")
    assert out["from"] == "llm" and "[1]" in out["answer"]


def test_assistant_endpoint_and_validation():
    """接口路径：正常提问返回 answer/sources；空消息 400。"""
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)
    uid = _fresh_user("asker1")
    c.post(f"/v1/plan/generate?user_id={uid}")
    r = c.post("/v1/assistant/chat", json={"user_id": uid, "message": "没有手表怎么判断强度？"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] and body["from"] in ("llm", "kb")
    assert isinstance(body["sources"], list)
    r = c.post("/v1/assistant/chat", json={"user_id": uid, "message": "   "})
    assert r.status_code == 400


def test_assistant_history_isolated_between_users():
    """多账号隔离：A 的聊天记录绝不能被 B 看到。"""
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)
    uid_a = _fresh_user("iso_a")
    uid_b = _fresh_user("iso_b")
    c.post(f"/v1/plan/generate?user_id={uid_a}")
    c.post("/v1/assistant/chat", json={"user_id": uid_a, "message": "为什么我计划里大半是轻松跑？"})
    assert c.get(f"/v1/assistant/history?user_id={uid_a}").json()["messages"], "A 应有历史"
    assert c.get(f"/v1/assistant/history?user_id={uid_b}").json()["messages"] == [], "B 绝不能看到 A 的对话"


def test_logout_clears_assistant_history():
    """退出登录即物理清空该账号的跑小助聊天记录（用户要求：退出即清）。"""
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)
    uid = _fresh_user("chatter1")
    c.post(f"/v1/plan/generate?user_id={uid}")
    c.post("/v1/assistant/chat", json={"user_id": uid, "message": "睡眠不足还能照常训练吗？"})
    assert c.get(f"/v1/assistant/history?user_id={uid}").json()["messages"], "退出前应有历史"
    token = c.post("/v1/account/login",
                   json={"username": "chatter1", "password": "run12345"}).json()["token"]
    r = c.post("/v1/account/logout", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert c.get(f"/v1/assistant/history?user_id={uid}").json()["messages"] == [], "退出后历史必须清空"
    # 再次登录是全新会话，不带旧记录
    c.post("/v1/account/login", json={"username": "chatter1", "password": "run12345"})
    assert c.get(f"/v1/assistant/history?user_id={uid}").json()["messages"] == []


def test_delete_account_clears_assistant_msg():
    """注销账号时聊天记录一并级联删除，不留孤儿数据。"""
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)
    uid = _fresh_user("deleter1")
    c.post(f"/v1/plan/generate?user_id={uid}")
    c.post("/v1/assistant/chat", json={"user_id": uid, "message": "厌跑怎么办？"})
    r = c.post("/v1/account/delete", json={"username": "deleter1", "password": "run12345"})
    assert r.status_code == 200
    with db.SessionLocal() as s:
        assert s.query(db.AssistantMsg).filter_by(user_id=uid).count() == 0
