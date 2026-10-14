# -*- coding: utf-8 -*-
"""Prompt 调优离线评测：回放历史事实，对比 tune_v1 / tune_v2 的硬校验通过率。

用法：
    .venv/Scripts/python.exe scripts/eval_tune.py            # 只测校验器 + prompt 构建（离线）
    .venv/Scripts/python.exe scripts/eval_tune.py --live   # 有 LLM_API_KEY 时真调模型

口径：通过率 = validate_ops 接受的 ops 数 / 模型返回的非空 ops 数。
v2 因为注入了 RAG 知识块与召回案例，预期一次通过率高于 v1；
不过的输出会被硬校验拒收（保持规则课表），所以通过率只影响体验不影响安全。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.tune import validate_ops, propose_ops, build_facts, MAX_OPS   # noqa: E402
from server.prompts import build_tune_prompt                               # noqa: E402
from server import rag                                                     # noqa: E402

# 黄金集：用与线上一致的 facts 结构造三组典型情境（校验器离线可验）。
GOLDEN = [
    {"name": "超额完成_加载周", "trigger": "over_done", "dev": 1.5,
     "week_no": 3, "phase": "load",
     "sessions": [
         ("s1", "2026-10-19", "easy_run", 6.0, False, "planned"),
         ("s2", "2026-10-21", "tempo", 8.0, True, "planned"),
         ("s3", "2026-10-23", "long_run", 14.0, True, "planned")]},
    {"name": "完成不足_强化期", "trigger": "under_done", "dev": 0.3,
     "week_no": 8, "phase": "build",
     "sessions": [
         ("s1", "2026-10-19", "interval", 6.0, True, "planned"),
         ("s2", "2026-10-21", "easy_run", 5.0, False, "planned"),
         ("s3", "2026-10-23", "long_run", 15.0, True, "planned")]},
    {"name": "缺课_基础期", "trigger": "skipped", "dev": 0.0,
     "week_no": 2, "phase": "base",
     "sessions": [
         ("s1", "2026-10-19", "easy_run", 5.0, False, "planned"),
         ("s2", "2026-10-21", "tempo", 6.0, False, "planned"),
         ("s3", "2026-10-23", "long_run", 10.0, True, "planned")]},
]


class _S:  # 与 tune.validate_ops 对齐的最小 session 存根
    def __init__(self, sid, d, t, km, key, status):
        import datetime as _dt
        self.session_id, self.date, self.type = sid, _dt.date.fromisoformat(d), t
        self.target_km, self.is_key, self.status = km, key, status
        self.pace_zone = "E"


def _facts(g):
    sessions = [_S(*x) for x in g["sessions"]]
    f = build_facts(g["week_no"], g["phase"], sessions, [], "")
    f["deviation"] = {"trigger": g["trigger"], "ratio": g["dev"], "cn": g["trigger"],
                      "recent_actual_vs_target": []}
    return f, sessions


def _rag_ctx(g):
    return rag.build_rag_context("eval_user", f"{g['trigger']} {g['phase']} 训练计划调整",
                                 g["phase"], g["trigger"], g["dev"] or 1.0)


def main() -> None:
    live = "--live" in sys.argv
    print(f"模式：{'真调 LLM' if live else '离线（只验校验器与 prompt 构建）'}\n")
    for g in GOLDEN:
        facts, sessions = _facts(g)
        ctx = _rag_ctx(g)
        p1, v1 = build_tune_prompt(facts, None)
        p2, v2 = build_tune_prompt(facts, ctx)
        print(f"■ {g['name']}  [{v1} prompt {len(p1)} 字 → {v2} prompt {len(p2)} 字，"
              f"注入知识 {len(ctx['kb'])} 块]")
        # 离线部分：模拟一组「模型可能给出」的 ops 校验行为
        tgt = next((x for x in sessions if not x.is_key), sessions[0])
        demo_ops = [{"op": "adjust", "session_id": tgt.session_id,
                     "km": round(tgt.target_km * 1.1 * 2) / 2}]   # 取 0.5 的倍数
        ok = validate_ops(demo_ops, sessions, sessions[0].date)
        print(f"  校验器自检（+10% 边界内 adjust）：{'通过' if ok else '拒绝'}")
        bad = validate_ops([{"op": "adjust", "session_id": "s2", "km": 99.0}], sessions, sessions[0].date)
        print(f"  校验器自检（动关键课必须拒收）：{'正确拒收' if bad is None else '漏洞！'}")
        if live:
            for ver, c in (("v1", None), ("v2", ctx)):
                ops = propose_ops(facts, c)
                accepted = validate_ops(ops, sessions, sessions[0].date) if ops else None
                print(f"  {ver}: 模型给出 {len(ops) if ops else 0} 个 ops → "
                      f"{'接受' if accepted else ('拒收/无效' if ops else '调用失败')}")
        print()


if __name__ == "__main__":
    main()
