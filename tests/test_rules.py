"""规则引擎评测集：参数化用例 + 边界钳制（PRD 第 16 章）。

新增一条规则必须补用例，否则 PR 不允许合并。
"""
import pytest


class TestVDOT:
    @pytest.mark.parametrize("vdot,exp_marathon", [
        (30, "7:43"), (35, "7:06"), (40, "6:33"), (42, "6:21"), (45, "6:04"),
        (50, "5:38"), (55, "5:16"), (60, "4:57"), (70, "4:25"),
    ])
    def test_pace_table_lookup(self, vdot, exp_marathon):
        from server.knowledge.rules import pace_zones, sec_to_pace
        row_mid = sec_to_pace(sum(pace_zones(vdot)["marathon"]) / 2)
        assert row_mid == exp_marathon

    @pytest.mark.parametrize("vdot", [10, 25, 90, 200])
    def test_clamp_no_exception(self, vdot):
        from server.knowledge.rules import clamp
        assert 30 <= clamp(vdot) <= 70

    def test_threshold_zone_tolerance(self):
        from server.knowledge.rules import pace_zones, sec_to_pace
        z = pace_zones(42)["threshold"]
        assert sec_to_pace(z[0]) == "6:07" and sec_to_pace(z[1]) == "6:19"

    def test_vdot_from_race_zero_base_factor(self):
        from server.knowledge.rules import vdot_from_recent_race
        v_b = vdot_from_recent_race(5.0, 25 * 60, "B_base")
        v_a = vdot_from_recent_race(5.0, 25 * 60, "A_zero_base")
        assert v_a < v_b                       # 0.92 保守系数生效
        assert 30 <= v_a <= 60


class TestSpike:
    def test_first_week_pass(self):
        from server.knowledge.rules import check_spike
        assert check_spike(0, 30).ok

    def test_within_limit(self):
        from server.knowledge.rules import check_spike
        assert check_spike(30, 33).ok

    def test_over_limit(self):
        from server.knowledge.rules import check_spike
        v = check_spike(30, 35)
        assert not v.ok and v.rule == "WEEKLY_GAIN"

    def test_exactly_10pct_pass(self):
        from server.knowledge.rules import check_spike
        assert check_spike(30, 33.0).ok


class Test8020:
    def test_pass(self):
        from server.knowledge.rules import check_8020
        s = [{"target_km": 8, "pace_zone": "easy"}, {"target_km": 10, "pace_zone": "marathon"},
             {"target_km": 4, "pace_zone": "threshold"}]
        assert check_8020(s).ok                     # 18/22 = 81.8%

    def test_fail(self):
        from server.knowledge.rules import check_8020
        s = [{"target_km": 5, "pace_zone": "easy"}, {"target_km": 8, "pace_zone": "interval"},
             {"target_km": 6, "pace_zone": "threshold"}]
        assert not check_8020(s).ok


class TestSessionCap:
    @pytest.mark.parametrize("days,cap", [(2, 2), (3, 3), (4, 4), (5, 5), (6, 5), (7, 5)])
    def test_cap_by_avail_days(self, days, cap):
        from server.knowledge.rules import session_cap
        assert session_cap(days) == cap


class TestWeather:
    def test_apparent_temp_anchor(self):
        """单测锚点：同样 30℃，湿度 40% 可跑，湿度 85% 触发降级 —— 这就是用体感而非气温的全部理由。"""
        from server.infra.weather import apparent_max
        assert apparent_max(30, 40) < 30 <= apparent_max(30, 85)

    def test_hard_constraint_matrix(self):
        from server.infra.weather import WeatherDay
        assert WeatherDay(date="2026-01-01", precip=12).hard_constraint
        assert WeatherDay(date="2026-01-01", weather_code=95).hard_constraint
        assert WeatherDay(date="2026-01-01", aqi=180).hard_constraint
        d = WeatherDay(date="2026-01-01", apparent_max=33, confidence="high")
        assert d.hot_soft and not d.hard_constraint
        d2 = WeatherDay(date="2026-01-01", apparent_max=33, confidence="low")
        assert not d2.hot_soft                    # low 置信不触发软调节
