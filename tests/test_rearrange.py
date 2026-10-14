"""重排引擎评测：候选→护栏→打分排序，含 PRD 10.5 的端到端 trace。"""
from datetime import date, timedelta

import pytest

from server.adapt import (RearrangeContext, rearrange, p_complete, key_stimulus_kept,
                          score, gen_compress)
from server.infra.weather import WeatherDay


def _wx(d, hard=False, hot=25.0, conf="high"):
    return WeatherDay(date=d.isoformat(), apparent_max=hot, precip=12.0 if hard else 0.0,
                      confidence=conf)


def _base_ctx(**kw):
    """第 9 周：周四阈值 8km / 周六长距离 18km / 周日轻松 6km；周三上报小腿酸痛，周四中雨。"""
    thu = date(2026, 9, 10)
    remaining = [
        {"session_id": "th", "date": thu.isoformat(), "type": "threshold",
         "is_key": True, "pace_zone": "threshold", "target_km": 8.0, "status": "planned"},
        {"session_id": "lo", "date": (thu + timedelta(days=2)).isoformat(), "type": "long_run",
         "is_key": True, "pace_zone": "marathon", "target_km": 18.0, "status": "planned"},
        {"session_id": "ez", "date": (thu + timedelta(days=3)).isoformat(), "type": "easy_run",
         "is_key": False, "pace_zone": "easy", "target_km": 6.0, "status": "planned"},
    ]
    weather = {r["date"]: _wx(date.fromisoformat(r["date"]),
                              hard=(r["session_id"] == "th"),
                              hot=28.0 if r["session_id"] == "th" else 24.0)
               for r in remaining}
    return RearrangeContext(week_no=9, remaining=remaining, avail_days_left=3,
                            weather=weather, soreness_today=True,
                            prev_week_km=32.0, planned_week_km=32.0,
                            injury_reports_14d=0, trigger_src="body")


class TestRearrange:
    def test_compress_beats_postpone(self):
        ctx = _base_ctx()
        main, alt, audit = rearrange(ctx)
        assert main is not None
        assert main.strategy == "compress"          # 砍辅助课保关键课排第一
        strategies = {c["strategy"] for c in audit}
        assert {"replace", "downgrade", "compress", "postpone", "noop"} <= strategies

    def test_key_sessions_never_dropped(self):
        ctx = _base_ctx()
        main, _, _ = rearrange(ctx)
        kept_ids = {s["session_id"] for s in main.sessions if s["status"] == "planned"}
        assert {"th", "lo"} <= kept_ids

    def test_stimulus_kept_full_for_compress(self):
        ctx = _base_ctx()
        main, _, _ = rearrange(ctx)
        assert key_stimulus_kept(ctx, main.sessions) == 1.0   # 关键刺激全保留

    def test_p_complete_monotonic_with_hard_weather(self):
        ctx = _base_ctx()
        good = [dict(s, date=(date.fromisoformat(s["date"]) + timedelta(days=4)).isoformat())
                     if s["session_id"] == "th" else s for s in ctx.remaining]
        assert p_complete(ctx, good) > p_complete(ctx, ctx.remaining)

    def test_score_range(self):
        ctx = _base_ctx()
        main, _, _ = rearrange(ctx)
        s = score(ctx, main.sessions)
        assert 0.0 <= s <= 1.0

    def test_guard_block_removes_candidate(self):
        ctx = _base_ctx()
        ctx.injury_reports_14d = 2                   # 强制降载：强度课全部出局
        main, alt, audit = rearrange(ctx)
        assert main.strategy == "deload"             # 主方案 = 强制降载（红线三放行形态）
        planned = [s for s in main.sessions if s["status"] == "planned"]
        assert all(s["pace_zone"] == "easy" for s in planned)
        blocked = [a for a in audit if a["action"] == "block"]
        assert blocked and all(a["hit_rule"] == "INJURY_WINDOW" for a in blocked)

    def test_audit_logs_pass_too(self):
        ctx = _base_ctx()
        _, _, audit = rearrange(ctx)
        assert any(a["action"] == "pass" for a in audit)

    def test_context_hash_stable(self):
        ctx = _base_ctx()
        assert ctx.context_hash() == _base_ctx().context_hash()


class TestIntake:
    def test_reject_avail_days_lt_2(self):
        from server.plan import Profile, validate_intake
        assert validate_intake(Profile(recent_km=5, recent_sec=1500, avail_days=1)) is not None

    def test_reject_full_marathon_lt_3_days(self):
        from server.plan import Profile, validate_intake
        r = validate_intake(Profile(recent_km=5, recent_sec=1500, avail_days=2, goal_race="full"))
        assert r is not None and "半马" in r.suggest

    def test_reject_major_injury(self):
        from server.plan import Profile, validate_intake
        r = validate_intake(Profile(recent_km=5, recent_sec=1500, avail_days=4,
                                    injury_flag="M", major_injury_in_3m=True))
        assert r is not None

    def test_generate_16_weeks_with_cap(self):
        from server.plan import Profile, generate_plan, Reject
        out = generate_plan(Profile(recent_km=5, recent_sec=1560, avail_days=3,
                                    ability="B_base"))
        assert not isinstance(out, Reject)
        assert len(out["weeks"]) == 16
        assert all(len([s for s in w["sessions"] if s["is_key"]]) == 2 for w in out["weeks"])
        assert all(len(w["sessions"]) <= 3 for w in out["weeks"])   # SESSION_CAP 收敛
        # 周增幅护栏：负荷周之间 ≤10%；cut/taper 是主动恢复，其后的回升以最近负荷周为基准
        kms = [w["planned_km"] for w in out["weeks"]]
        phases = [w["phase"] for w in out["weeks"]]
        prev_load = 0.0
        for i, (phase, cur) in enumerate(zip(phases, kms)):
            if phase in ("cut", "taper"):
                continue
            baseline = prev_load if (i > 0 and phases[i - 1] in ("cut", "taper")) else (kms[i - 1] if i > 0 else 0.0)
            if baseline > 0:
                assert (cur - baseline) / baseline <= 0.10 + 1e-6, f"第 {i + 1} 周增幅超 10%"
            prev_load = cur
        # 主动恢复周之后不允许永久塌缩：第 5 周应回到基线附近
        assert kms[4] > kms[3] * 1.3, "cut 周后的回升不应被增幅护栏锁死"


class TestExplain:
    def test_no_hallucination_assert(self):
        from server.explain import assert_no_hallucination
        facts = {"week_no": 9, "drop": [{"km": 6.0}]}
        assert assert_no_hallucination("本周删除 6km 轻松跑", facts)
        assert not assert_no_hallucination("本周删除 12km 轻松跑", facts)

    def test_template_fallback_always_valid(self):
        from server.explain import render_explain, assert_no_hallucination
        for strategy in ("compress", "downgrade", "replace", "postpone", "deload", "noop"):
            for trig in ("weather", "body", "schedule", "manual"):
                facts = {"strategy": strategy, "trigger_group": trig, "week_no": 9,
                         "drop": [{"date": "周日", "km": 6.0, "zone_cn": "轻松跑"}],
                         "stimulus_pct": 100, "delta_km": 6.0, "remain_days": 3}
                out, src = render_explain(facts)
                assert src == "template"            # 未配置 LLM 时稳定降级
                assert all(k in out for k in ("reason", "what_changed", "cost", "reassurance"))
                joined = " ".join(out.values())
                assert assert_no_hallucination(joined, facts)
