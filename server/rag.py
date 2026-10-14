# -*- coding: utf-8 -*-
"""RAG 知识检索 + 案例召回：让 AI 的调整建议有依据，而不是凭空生成。

两层记忆：
1. 知识库（RAG）：训练学原则切块带标签，中文 bigram 检索，零外部依赖。
   调 LLM 前 retrieve() 取 top-k 注入 prompt，AI 的建议必须能引用这些原则。
2. 案例库（召回）：用户每次触发调整（超额/不足/跳过/酸痛/天气）都存一条 AICase，
   记录情境、AI 当时的决策、检索上下文；后续执行率出来后回填 outcome。
   相似情境（同阶段+同触发+偏差率接近）召回，让 AI 参考「自己过去被验证有效的做法」。

主权边界：检索结果只进 prompt 做参考，不直接改课表；课表改动仍由 tune.validate_ops 硬校验。
"""
from __future__ import annotations

import math
import re
import secrets
from datetime import date, timedelta
from typing import Optional

from . import db

# ---------------------------------------------------------------- 知识库
# 原则来源：Jack Daniels 训练法、ACSM 立场声明、超量恢复理论。块保持短小，便于精确检索。
KB: list[dict] = [
    {"id": "kb_overload", "tags": ["超额", "超量恢复", "刺激", "进阶"],
     "text": "超量恢复原则：训练刺激超出日常负荷后，身体在恢复中超额补偿。"
             "偶尔小幅超额（110%~130%）且次日无异常疲劳，说明当前负荷偏保守，"
             "可以在后续非关键课小幅上调，但单次上调不超过 10%，周总量变化不超过 3%。"},
    {"id": "kb_10pct", "tags": ["跑量", "周增量", "安全", "伤病预防"],
     "text": "跑量周增量原则：周总跑量相较上周增加一般不超过 10%，"
             "新手或有过伤痛史者建议控制在 5% 以内。一次大课的超额完成"
             "不构成上调下周跑量的充分理由，需要连续 2 周以上的趋势确认。"},
    {"id": "kb_8020", "tags": ["强度分布", "轻松跑", "80/20", "恢复"],
     "text": "80/20 强度分布：约 80% 跑量放在低强度（E/M 区），20% 为高质量强度（T/I/R）。"
             "压缩周时优先删减轻松跑和辅助课，关键课（长距离、间歇、节奏）尽量保留。"
             "辅助课可以丢，关键刺激全保留，是减量的第一优先级。"},
    {"id": "kb_gap", "tags": ["不足", "缺口", "补跑", "缺课"],
     "text": "缺口处理原则：单次课完成不足一半，缺口不通过加量猛补。"
             "正确做法是在本周剩余时间把缺失的轻松跑刺激分散补齐，"
             "或在重排时压缩辅助课保住关键课；猛补会显著抬高受伤风险。"},
    {"id": "kb_skip", "tags": ["跳过", "缺课", "心理", "连续"],
     "text": "缺课原则：偶尔缺一次课不影响整体适应，训练成果按周和月看，不按单次看。"
             "连续两周以上关键课缺失才需要降级或重排。缺课后直接按原计划继续，"
             "落下的课不用补。"},
    {"id": "kb_soreness", "tags": ["酸痛", "伤痛", "恢复", "交叉训练"],
     "text": "酸痛应对：关节部位（膝、踝、髋）酸痛是警示信号，当日强度课应降级或取消；"
             "肌肉延迟性酸痛（DOMS）可在低强度轻松跑中恢复。"
             "同一部位酸痛连续出现 3 次以上必须强制降载。"},
    {"id": "kb_taper", "tags": ["减量", "赛前", "taper", "峰值"],
     "text": "赛前减量原则：减量周（taper）跑量降至峰值期的 40~60%，"
             "强度课的组数减少但保留配速刺激，让疲劳消退、竞技状态浮现。"
             "减量周任何情况下都不追加跑量，超额完成也不调整。"},
    {"id": "kb_sleep", "tags": ["睡眠", "疲劳", "恢复", "HRV"],
     "text": "恢复监控：睡眠评分持续低于 5 或疲劳自评持续高于 7 时，"
             "当日训练量建议下调一档；恢复是适应的一部分，硬撑训练得不偿失。"},
    {"id": "kb_weather", "tags": ["天气", "高温", "湿度", "配速"],
     "text": "天气调整原则：体感温度超过 28℃ 或湿度超过 85% 时，"
             "强度课配速每升高 1℃ 放慢约 5~8 秒/公里，或改为室内交叉训练；"
             "雷雨等硬约束直接把课移到本周内更好的日子。"},
    {"id": "kb_keysession", "tags": ["关键课", "优先级", "间歇", "长距离"],
     "text": "关键课保护原则：长距离、间歇、节奏跑是关键课，承载主要训练刺激。"
             "任何微调不许改动关键课的日期和跑量；能动的只有非关键课的"
             "时间和 ±10% 以内的跑量。"},
]

def _bigrams(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _score(query: str, chunk: dict) -> float:
    q = _bigrams(query)
    if not q:
        return 0.0
    hit = sum(1 for g in q if g in _bigrams(chunk["text"]) or g in "".join(chunk["tags"]))
    return hit / len(q)


def retrieve(query: str, k: int = 3) -> list[dict]:
    """按相关性取 top-k 知识块，带分数返回。"""
    scored = sorted(((_score(query, c), c) for c in KB), key=lambda x: -x[0])
    return [{"id": c["id"], "score": round(s, 3), "text": c["text"]}
            for s, c in scored[:k] if s > 0.05]


# ---------------------------------------------------------------- 案例库
def _dev_bucket(ratio: float) -> float:
    """偏差率分桶：log 刻度，1.0=刚好完成，<1 不足，>1 超额。"""
    return math.log(max(ratio, 0.05))


def record_case(user_id: str, week_no: int, phase: str, trigger: str, dev_ratio: float,
                context: dict, decision: Optional[list]) -> str:
    """存一条调整案例，返回 case_id。"""
    case_id = f"case-{secrets.token_hex(8)}"
    with db.SessionLocal.begin() as s:
        s.add(db.AICase(case_id=case_id, user_id=user_id, week_no=week_no, phase=phase,
                        trigger=trigger, dev_ratio=round(float(dev_ratio), 3),
                        context_json=context, decision_json={"ops": decision} if decision else None))
    return case_id


def recall_cases(user_id: str, phase: str, trigger: str, dev_ratio: float, k: int = 3) -> list[dict]:
    """召回相似历史案例（限本人）：阶段匹配 + 触发类型匹配 + 偏差率接近，加权打分。"""
    with db.SessionLocal() as s:
        rows = s.query(db.AICase).filter_by(user_id=user_id).all()
    target = _dev_bucket(dev_ratio)
    scored = []
    for r in rows:
        score = 0.0
        if r.phase == phase:
            score += 2.0
        if r.trigger == trigger:
            score += 2.0
        score += max(0.0, 1.0 - abs(_dev_bucket(r.dev_ratio) - target))   # 偏差率越接近越高分
        if score >= 3.0:                                                 # 至少要像一点才召回
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [{"case_id": r.case_id, "score": round(sc, 2), "trigger": r.trigger,
             "dev_ratio": r.dev_ratio, "decision": r.decision_json, "outcome": r.outcome}
            for sc, r in scored[:k]]


def backfill_outcome(user_id: str, plan_id: str, week_no: int, rate: float) -> None:
    """周末复盘时回填本周案例的执行效果：执行率 ≥70% 记 good，<40% 记 bad。"""
    outcome = "good" if rate >= 0.7 else ("bad" if rate < 0.4 else "pending")
    with db.SessionLocal.begin() as s:
        rows = s.query(db.AICase).filter_by(user_id=user_id, week_no=week_no, outcome="pending").all()
        for r in rows:
            if outcome != "pending":
                r.outcome = outcome


# ---------------------------------------------------------------- 组装：一次拿全 LLM 需要的检索上下文
def build_rag_context(user_id: str, query: str, phase: str, trigger: str,
                      dev_ratio: float, k_kb: int = 3, k_case: int = 3) -> dict:
    """检索 + 召回打包。任何一步失败都不阻塞主流程（返回空上下文）。"""
    try:
        kb = retrieve(query, k_kb)
    except Exception:
        kb = []
    try:
        cases = recall_cases(user_id, phase, trigger, dev_ratio, k_case)
    except Exception:
        cases = []
    return {"kb": kb, "cases": cases,
            "kb_ids": [x["id"] for x in kb], "case_ids": [x["case_id"] for x in cases]}
