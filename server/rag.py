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
# 单一来源：docs/training-principles.md（蒸馏版训练原则，含重排专章）。
# 文件缺失或解析失败时回退到内联 KB，保证服务不挂。
_KB_FALLBACK: list[dict] = [
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

_KB_EXTRA_FALLBACK = [
    {"id": "kb_compress", "tags": ["压缩", "时间不够", "辅助课", "重排"],
     "text": "压缩周原则：可用时间装不下全部课次时，删除顺序是辅助轻松跑优先、质量课次之、"
             "长距离最后。压缩后的周应保证至少保留 1 次关键课和 1 次长距离，"
             "训练刺激保留率目标不低于 70%。"},
    {"id": "kb_stimulus", "tags": ["刺激", "保留率", "评分", "重排"],
     "text": "刺激保留率：重排方案的优劣首要看关键训练刺激保留了多少，而不是周跑量数字好不好看。"
             "刺激保留 85% 以上为优，70~85% 为可接受，低于 70% 等于本周白练，应优先换别的策略。"},
    {"id": "kb_cross", "tags": ["交叉训练", "室内", "替代", "天气"],
     "text": "交叉训练替代：恶劣天气无法户外时，用室内单车、椭圆机或游泳按 1:1 时长替代轻松跑，"
             "可以保留训练日和有氧刺激，但质量课（间歇、节奏）的配速刺激无法被交叉训练完全替代，"
             "天气转好后优先补回质量课。"},
    {"id": "kb_move", "tags": ["移动", "换天", "硬约束", "重排"],
     "text": "课次移动原则：把课从硬约束日子（雷雨、重度污染）移到本周内条件更好的日子，"
             "课次、跑量、刺激全部不变，是代价最小的重排策略。移动要遵守至少间隔 1 个恢复日的原则，"
             "连续两天强度课必须避免。"},
    {"id": "kb_deload_rule", "tags": ["降载", "护栏", "伤痛", "红线"],
     "text": "强制降载红线：同一部位伤痛 14 天内上报 2 次以上，或任何关节痛连续出现，"
             "本周强度课全部取消、只保留低强度有氧，周跑量下调 30~40%。"
             "这条红线优先于一切其他原则，属于安全护栏而非可协商的建议。"},
    {"id": "kb_auxcap", "tags": ["辅助课", "跑量上限", "长距离", "结构"],
     "text": "辅助课跑量上限：轻松跑等辅助课的距离不得超过本周长距离的 70%。"
             "长距离承载本周最大的有氧刺激，辅助课的作用是打底和恢复性有氧，"
             "一旦辅助课比长距离还长，本周的训练结构就失衡了——微调课表时这条是硬边界，不是参考建议。"},
    {"id": "kb_winter", "tags": ["冬季", "冬天", "天冷", "寒冷", "室内", "热身", "气候带"],
     "text": "北方冬季训练：气温低于 -5℃ 或路面积雪结冰时改室内跑步机或交叉训练，按 1:1 时长折算；"
             "户外跑把热身延长到 15 分钟、配速放慢 10~15 秒/公里，结束立刻换干衣防失温。"
             "冬季周跑量允许比标准模板低 10%，开春后再补。"},
    {"id": "kb_rainy", "tags": ["梅雨", "下雨", "雨天", "雨季", "降雨", "湿滑", "交叉训练", "气候带"],
     "text": "连续降雨周：周内有 3 天以上中到大雨时，本周改为「2 次户外关键课 + 其余交叉训练」的结构，"
             "关键课挑雨停的窗口日；湿滑路面配速放慢 15~20 秒/公里。连续两周以上梅雨则整体降一个跑量档位。"},
    {"id": "kb_aqi", "tags": ["雾霾", "AQI", "室内", "呼吸", "气候带"],
     "text": "空气污染应对：AQI 超过 150 当天改室内交叉训练，AQI 100~150 强度课降为低强度并缩短 30%。"
             "污染日跑步对呼吸道的伤害大于训练收益，这是硬约束不是软建议。"},
    {"id": "kb_rehab_early", "tags": ["伤痛", "急性期", "休息", "冰敷", "康复"],
     "text": "伤痛急性期（48~72 小时）：出现尖锐痛、肿胀或步态改变时立即停止跑步，"
             "遵循休息、冰敷、加压、抬高；这期间任何「慢跑恢复」都可能把小伤拖成慢性伤。"
             "72 小时后无痛且日常行走正常，才进入回归流程。"},
    {"id": "kb_rehab_return", "tags": ["回归", "复跑", "10%规则", "康复"],
     "text": "伤后回归原则：停训一周以内从原周跑量的 80% 回归，停训两周以上从 50% 起步，"
             "之后每周增幅不超过 10%。回归第一周全部跑走结合，旧伤部位有任何不适立刻退回上一档。"},
    {"id": "kb_rehab_maintain", "tags": ["康复", "交叉训练", "体能维持", "泳池"],
     "text": "康复期有氧维持：不能跑的日子里用游泳、水中跑步、单车按 1:1 时长维持有氧能力，每周 2~3 次。"
             "停训两周有氧能力约下降 5~8%，有交叉训练维持可压到 2~3%，回归几乎不需要重建期。"},
    {"id": "kb_fuel", "tags": ["补给", "吃胶", "比赛中", "碳水", "长距离", "能量胶"],
     "text": "长距离补给：训练超过 75 分钟需补碳水，每小时 30~60 克，从第 40 分钟开始每 20~25 分钟一小口。"
             "比赛用补给必须在长距离训练里试吃至少 3 次，比赛日不允许用没试过的东西。"},
    {"id": "kb_hydration", "tags": ["补水", "电解质", "低钠", "天热"],
     "text": "补水原则：常温每小时 400~500ml，高温不超过 800ml，单次小口多次。"
             "只喝白水超过 2 小时要补电解质；看尿液颜色判断，淡黄色为宜。"},
    {"id": "kb_hr_easy", "tags": ["心率", "谈话测试", "E区", "体感"],
     "text": "强度自感判定：没有心率设备时用谈话测试——E/M 区能完整说句子，T 区只能说短句，I/R 区说不出话。"
             "体感 RPE 轻松跑保持 3~4，关键课 6~8，任何时候不碰 9~10。"},
    {"id": "kb_race72", "tags": ["赛前", "碳水", "装备", "taper"],
     "text": "赛前 72 小时：不再做训练刺激，只做 15~20 分钟放松 jog；碳水提高到总热量 60~70%；"
             "比赛装备提前一天全部试一遍，比赛日早晨不做任何新尝试。"},
    {"id": "kb_race_pacing", "tags": ["比赛", "配速", "负分段", "起跑"],
     "text": "比赛配速策略：前程比目标配速慢 5~10 秒/公里起跑，中段稳定，后程有余力再加速（负分段）。"
             "前程每快 1 秒，后程大约还 2~3 秒；前 5 公里觉得「太慢」才是正确的。"},
    {"id": "kb_race_warmup", "tags": ["热身", "比赛日", "加速跑"],
     "text": "比赛日热身：开跑前 20~25 分钟开始，10~15 分钟慢跑 + 4~6 组 60~80 米加速跑 + 少量动态拉伸。"
             "全马热身不宜过量，气温低时延长 5 分钟。"},
]


def _load_kb() -> list[dict]:
    """从 docs/training-principles.md 解析知识块；失败回退内联 KB。"""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "docs", "training-principles.md")
    try:
        chunks, cur = [], None
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("### "):
                    head = line[4:].split(" · ")
                    cur = {"id": head[0].strip(), "name": head[1].strip() if len(head) > 1 else "",
                           "tags": [], "text": ""}
                    chunks.append(cur)
                elif cur is not None and line.startswith("- tags:"):
                    cur["tags"] = [t.strip() for t in line[7:].split(",") if t.strip()]
                elif cur is not None and line.startswith("- text:"):
                    cur["text"] = line[7:].strip()
        chunks = [c for c in chunks if c["id"] and c["text"]]
        if chunks:
            return chunks
    except Exception:
        pass
    return _KB_FALLBACK + _KB_EXTRA_FALLBACK


KB: list[dict] = _load_kb()

def _bigrams(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _score(query: str, chunk: dict) -> float:
    q = _bigrams(query)
    if not q:
        return 0.0
    text_bg = _bigrams(chunk["text"])
    tag_str = "".join(chunk["tags"])
    # 标签是人工标注的同义词入口，命中权重高于正文 bigram
    hit = sum((2 if g in tag_str else 1) if (g in tag_str or g in text_bg) else 0
              for g in q)
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
