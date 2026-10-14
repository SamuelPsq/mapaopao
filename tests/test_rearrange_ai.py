"""重排 AI 推荐人：explain.render_recommend 的校验与降级行为。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_rec_test.db")

from server import rag
from server import explain


def test_kb_loaded_from_markdown():
    """知识库从 docs/training-principles.md 加载，且包含重排专章。"""
    ids = {c["id"] for c in rag.KB}
    assert len(rag.KB) >= 14, f"知识块数量异常：{len(rag.KB)}"
    for need in ("kb_compress", "kb_stimulus", "kb_cross", "kb_move", "kb_deload_rule"):
        assert need in ids, f"重排专章 {need} 缺失"
    # 重排查询能命中相关原则
    hits = rag.retrieve("时间不够 压缩周 删掉哪些课 刺激保留")
    assert any(h["id"] in ("kb_compress", "kb_stimulus") for h in hits)


def test_render_recommend_accepts_valid(monkeypatch=None):
    facts = {"stimulus_pct": 85, "trigger": "恶劣天气",
             "main": {"strategy": "compress"}, "alt": {"strategy": "postpone"}}
    orig = explain._llm_call
    explain._llm_call = lambda scene, f: {"choice": "alt",
                                          "reason": "备选对关键刺激保留 85%，符合刺激保留原则"}
    try:
        rec, src = explain.render_recommend(facts)
    finally:
        explain._llm_call = orig
    assert src == "llm" and rec["choice"] == "alt"


def test_render_recommend_rejects_bad_choice():
    facts = {"stimulus_pct": 85}
    orig = explain._llm_call
    explain._llm_call = lambda scene, f: {"choice": "C", "reason": "随便选一个"}
    try:
        rec, src = explain.render_recommend(facts)
    finally:
        explain._llm_call = orig
    assert rec is None and src == "template", "非法 choice 必须降级"


def test_render_recommend_rejects_hallucinated_number():
    """理由里出现 facts 中没有的数字 → 数值断言拦下，降级。"""
    facts = {"stimulus_pct": 85, "delta_km": 3.5}
    orig = explain._llm_call
    explain._llm_call = lambda scene, f: {"choice": "main",
                                          "reason": "这个方案能保留 999% 的刺激"}
    try:
        rec, src = explain.render_recommend(facts)
    finally:
        explain._llm_call = orig
    assert rec is None and src == "template"


def test_render_recommend_no_llm():
    """未配置密钥 → _llm_call 返回 None → 无推荐（不报错）。"""
    orig = explain._llm_call
    explain._llm_call = lambda scene, f: None
    try:
        rec, src = explain.render_recommend({"stimulus_pct": 70})
    finally:
        explain._llm_call = orig
    assert rec is None and src == "template"
