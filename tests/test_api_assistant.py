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
    uid = c.post("/v1/account/register", json={"username": username, "password": "run12345"}).json()["user_id"]
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
