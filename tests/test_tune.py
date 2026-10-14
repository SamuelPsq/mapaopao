# -*- coding: utf-8 -*-
"""AI 微调层回归：硬校验边界 + 幂等 + 端到端接入（LLM 用桩替代）。"""
import os
import tempfile
from datetime import date, timedelta

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_tune_test.db")

from fastapi.testclient import TestClient

from server import db
from server import main as _main
from server.main import app
from server import tune

_main.AUTH_ENABLED = False
db.init_db()
c = TestClient(app)


class _S:  # 最小 Session 替身
    def __init__(self, sid, km, is_key=False, status="planned", day=0):
        self.session_id = sid
        self.target_km = km
        self.is_key = is_key
        self.status = status
        self.date = date(2026, 10, 12) + timedelta(days=day)   # 周一起


W0 = date(2026, 10, 12)


def _fresh_db():
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)


# ---------------------------------------------------------------- 纯校验
def test_validate_move_ok():
    ss = [_S("a", 5, day=1), _S("k", 10, is_key=True, day=3)]
    ops = [{"op": "move", "session_id": "a", "to_date": "2026-10-16"}]
    assert tune.validate_ops(ops, ss, W0) == [{"op": "move", "session_id": "a",
                                               "to_date": date(2026, 10, 16)}]


def test_validate_key_session_untouchable():
    ss = [_S("k", 10, is_key=True)]
    assert tune.validate_ops([{"op": "adjust", "session_id": "k", "km": 9.5}], ss, W0) is None
    assert tune.validate_ops([{"op": "move", "session_id": "k", "to_date": "2026-10-15"}], ss, W0) is None


def test_validate_km_bounds():
    ss = [_S("a", 10), _S("b", 100)]     # 大总量避免周 3% 容差干扰单课 ±10% 的测试
    assert tune.validate_ops([{"op": "adjust", "session_id": "a", "km": 9.0}], ss, W0)      # -10% 边界 ok
    assert tune.validate_ops([{"op": "adjust", "session_id": "a", "km": 10.5}], ss, W0)     # +5% ok
    assert tune.validate_ops([{"op": "adjust", "session_id": "a", "km": 11.5}], ss, W0) is None  # +15% 越界
    assert tune.validate_ops([{"op": "adjust", "session_id": "a", "km": 10.3}], ss, W0) is None  # 非 0.5 倍数
    assert tune.validate_ops([{"op": "adjust", "session_id": "a", "km": 2.0}], ss, W0) is None   # 低于下限


def test_validate_week_total_tolerance():
    ss = [_S("a", 10), _S("b", 20)]
    assert tune.validate_ops([{"op": "adjust", "session_id": "a", "km": 9.0}], ss, W0) is None  # 30/总量=3.3% 超 3%
    assert tune.validate_ops([{"op": "adjust", "session_id": "a", "km": 9.5}], ss, W0)          # 1.7% ok


def test_validate_move_outside_week_and_clash():
    ss = [_S("a", 5, day=1), _S("b", 5, day=3)]
    assert tune.validate_ops([{"op": "move", "session_id": "a", "to_date": "2026-10-20"}], ss, W0) is None
    assert tune.validate_ops([{"op": "move", "session_id": "a", "to_date": "2026-10-15"}], ss, W0) is None  # 撞 b
    # 移回自己原来的日子 = 无冲突，合法
    assert tune.validate_ops([{"op": "move", "session_id": "a", "to_date": "2026-10-13"}], ss, W0)


def test_validate_misc():
    ss = [_S("a", 5), _S("b", 5), _S("d", 5)]
    # 同节课两个操作 → 拒
    ops = [{"op": "move", "session_id": "a", "to_date": "2026-10-14"},
           {"op": "adjust", "session_id": "a", "km": 4.5}]
    assert tune.validate_ops(ops, ss, W0) is None
    # 未知 op / 不存在的课 / 超过 3 个 → 拒
    assert tune.validate_ops([{"op": "delete", "session_id": "a"}], ss, W0) is None
    assert tune.validate_ops([{"op": "adjust", "session_id": "zzz", "km": 4.5}], ss, W0) is None
    ops4 = [{"op": "adjust", "session_id": x, "km": 4.5} for x in ("a", "b", "d")] + \
           [{"op": "adjust", "session_id": "a", "km": 4.0}]
    assert tune.validate_ops(ops4, ss, W0) is None
    # 空 ops / 非列表 → 合法（无需调整）
    assert tune.validate_ops([], ss, W0) == []
    assert tune.validate_ops("junk", ss, W0) is None


# ---------------------------------------------------------------- 端到端（LLM 打桩）
def test_tune_applies_and_is_idempotent(monkeypatch):
    _fresh_db()
    c.post("/v1/profile", json={"user_id": "u1", "recent_km": 10, "recent_sec": 3600,
                                "avail_days": 4, "goal_race": "half", "ability": "B_base",
                                "injury_flag": "N", "city": "上海", "lat": 31.23,
                                "lon": 121.47, "max_duration": 90})
    plan_id = c.post("/v1/plan/generate?user_id=u1").json()["plan_id"]

    with db.SessionLocal() as s:
        before = {x.session_id: (x.target_km, x.date, x.is_key) for x in
                  s.query(db.Session).filter_by(plan_id=plan_id, week_no=1).all()}
    easy = next(sid for sid, (km, d, k) in before.items() if not k)
    orig_km = before[easy][0]
    # 按 ±10% 边界构造一个必然合法的 -5% 建议
    new_km = max(tune.KM_MIN, round(orig_km * 0.95 / tune.KM_STEP) * tune.KM_STEP)
    assert new_km != orig_km and abs(new_km - orig_km) <= orig_km * tune.KM_TOL + 1e-9

    monkeypatch.setattr(tune, "propose_ops",
                        lambda facts: [{"op": "adjust", "session_id": easy, "km": new_km}])
    r = c.get("/v1/today?user_id=u1")
    assert r.status_code == 200

    with db.SessionLocal() as s:
        rows = {x.session_id: x for x in
                s.query(db.Session).filter_by(plan_id=plan_id, week_no=1).all()}
        assert rows[easy].target_km == new_km and rows[easy].source == "ai_tuned"
        ev = s.query(db.Event).filter_by(name="ai_tune", user_id="u1").all()
        assert len(ev) == 1 and ev[0].payload["applied"] is True

    # 幂等：第二次查看不再调 LLM（桩改成抛异常都能过）
    def _boom(facts):
        raise AssertionError("should not call LLM again")
    monkeypatch.setattr(tune, "propose_ops", _boom)
    c.get("/v1/today?user_id=u1")
    with db.SessionLocal() as s:
        assert s.query(db.Event).filter_by(name="ai_tune", user_id="u1").count() == 1


def test_tune_rejects_bad_ops(monkeypatch):
    _fresh_db()
    c.post("/v1/profile", json={"user_id": "u2", "recent_km": 10, "recent_sec": 3600,
                                "avail_days": 4, "goal_race": "half", "ability": "B_base",
                                "injury_flag": "N", "city": "上海", "lat": 31.23,
                                "lon": 121.47, "max_duration": 90})
    plan_id = c.post("/v1/plan/generate?user_id=u2").json()["plan_id"]
    with db.SessionLocal() as s:
        before = {x.session_id: (x.target_km, x.date, x.source) for x in
                  s.query(db.Session).filter_by(plan_id=plan_id, week_no=1).all()}
    # LLM 想把课加量 50% → 必须被硬校验拒收，课表保持原样
    sid = next(iter(before))
    monkeypatch.setattr(tune, "propose_ops",
                        lambda facts: [{"op": "adjust", "session_id": sid, "km": 99.0}])
    assert c.get("/v1/today?user_id=u2").status_code == 200
    with db.SessionLocal() as s:
        after = {x.session_id: (x.target_km, x.date, x.source) for x in
                 s.query(db.Session).filter_by(plan_id=plan_id, week_no=1).all()}
        assert after == before
        ev = s.query(db.Event).filter_by(name="ai_tune", user_id="u2").first()
        assert ev and ev.payload["applied"] is False


def test_tune_without_llm_config(monkeypatch):
    _fresh_db()
    c.post("/v1/profile", json={"user_id": "u3", "recent_km": 10, "recent_sec": 3600,
                                "avail_days": 4, "goal_race": "half", "ability": "B_base",
                                "injury_flag": "N", "city": "上海", "lat": 31.23,
                                "lon": 121.47, "max_duration": 90})
    c.post("/v1/plan/generate?user_id=u3")
    for var in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(var, raising=False)
    r = c.get("/v1/today?user_id=u3")
    assert r.status_code == 200          # 无密钥也能正常出卡（体验不断档）
    with db.SessionLocal() as s:
        ev = s.query(db.Event).filter_by(name="ai_tune", user_id="u3").first()
        assert ev and ev.payload["applied"] is False
