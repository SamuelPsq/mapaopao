"""护栏层评测：三条红线 + patch 行为 + 独立性约束。"""
from datetime import date, timedelta

import pytest

from server.guard import check, check_with_patch


def _s(offset, zone, is_key=False, km=6.0):
    return {"date": (date(2026, 9, 7) + timedelta(days=offset)).isoformat(),
            "pace_zone": zone, "target_km": km, "is_key": is_key, "status": "planned"}


class TestGuard:
    def test_weekly_gain_patch(self):
        sessions = [_s(0, "easy", km=10), _s(2, "easy", km=10)]
        v, patched_km, patched = check_with_patch(20.0, sessions, {"prev_week_km": 10.0})
        assert v.action == "patch" and v.hit_rule == "WEEKLY_GAIN"
        assert patched_km <= 11.0

    def test_consecutive_hard(self):
        sessions = [_s(0, "interval", True), _s(1, "threshold", True), _s(2, "interval", True)]
        v = check(18.0, sessions, {"prev_week_km": 20.0})
        assert not v.ok and v.hit_rule == "CONSECUTIVE_HARD"

    def test_consecutive_hard_patch_drops_non_key(self):
        sessions = [_s(0, "interval", True), _s(1, "threshold", True), _s(2, "threshold", False)]
        v, _, patched = check_with_patch(18.0, sessions, {"prev_week_km": 20.0})
        assert v.action == "patch"
        assert patched[2]["pace_zone"] == "easy"

    def test_injury_window_block(self):
        sessions = [_s(0, "easy"), _s(2, "threshold", True)]
        v = check(12.0, sessions, {"prev_week_km": 12.0, "injury_reports_14d": 2})
        assert not v.ok and v.action == "block" and v.hit_rule == "INJURY_WINDOW"
        assert v.reason_user                       # 必须有一句人话

    def test_injury_window_pass_when_deloaded(self):
        """红线三放行降载形态：全部轻松跑 + 量不超上周 10%。"""
        sessions = [_s(0, "easy", km=3.6), _s(2, "easy", km=4.8), _s(4, "easy", km=3.0)]
        v = check(11.4, sessions, {"prev_week_km": 12.0, "injury_reports_14d": 3})
        assert v.ok

    def test_all_pass(self):
        sessions = [_s(0, "easy"), _s(2, "threshold", True), _s(4, "easy"), _s(5, "marathon", True)]
        v = check(24.0, sessions, {"prev_week_km": 22.0})
        assert v.ok and v.action == "pass"

    def test_guard_does_not_import_adapt(self):
        """CI 静态检查：护栏不允许反向依赖适应层。"""
        import ast
        from pathlib import Path
        src = Path(__import__("server.guard").guard.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for n in names:
                    assert "adapt" not in n and "rearrange" not in n, f"护栏依赖了优化层: {n}"
