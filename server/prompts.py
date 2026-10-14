# -*- coding: utf-8 -*-
"""Prompt 注册表：所有进 LLM 的 prompt 集中管理、带版本号。

为什么单独一层：
- 调优要 A/B，prompt 必须可寻址、可回放；
- 每次调用埋点记录版本号 + 检索上下文，指标页/离线评测能按版本对比通过率；
- v1 保留作对照组，v2 注入 RAG 知识块与召回案例。
"""
from __future__ import annotations

import json
from typing import Optional

TUNE_PROMPT_VERSION = "tune_v2"


def build_tune_prompt(facts: dict, rag_ctx: Optional[dict] = None) -> tuple[str, str]:
    """返回 (prompt, version)。rag_ctx 为 rag.build_rag_context 的产物，可为 None（v1 行为）。

    v1（tune_v1）：纯事实指令，约束全靠 prompt 文字；
    v2（tune_v2）：在事实之上注入检索到的训练学原则 + 本人历史相似案例，
                  要求模型建议与原则一致、优先复用 outcome=good 的历史决策。
    无论哪版，落地前都必须过 tune.validate_ops 硬校验——prompt 只是提高一次通过率。
    """
    base = (
        "你是马拉松课表微调助手。课表由 VDOT 规则引擎生成，你只能做微小调整，"
        "绝不做大改。允许的操作只有两种：\n"
        '1. {"op":"move","session_id":"<id>","to_date":"<YYYY-MM-DD，必须在本周内且当天无其他课>"} 把一节【非关键课】移到本周另一天\n'
        '2. {"op":"adjust","session_id":"<id>","km":"<数字，必须在原跑量±10%内且是0.5的倍数>"} 微调一节【非关键课】的跑量\n'
        "规则：key=true 的关键课绝对不许出现在 ops 里；每周最多 3 个操作，每节课最多 1 个；"
        "天气差的日子（bad_weather_days）优先把轻松跑移走；用户酸痛时优先减量非关键课。\n"
        "如果课表已经合理，ops 可以是空数组。只输出 JSON，不要输出任何其他内容。\n"
        "本周事实：" + json.dumps(facts, ensure_ascii=False)
    )
    if not rag_ctx:
        return base, "tune_v1"
    parts = [base]
    if rag_ctx.get("kb"):
        parts.append("\n【训练学原则（检索自知识库，建议必须与之一致）】")
        for item in rag_ctx["kb"]:
            parts.append(f"- {item['text']}")
    if rag_ctx.get("cases"):
        parts.append("\n【该用户相似情境下的历史调整（优先复用效果好的）】")
        for c in rag_ctx["cases"]:
            decision = json.dumps(c.get("decision"), ensure_ascii=False) if c.get("decision") else "未做调整"
            parts.append(f"- 偏差率 {c['dev_ratio']}，当时决策：{decision}，后续效果：{c['outcome']}")
    parts.append(
        "\n要求：你的建议必须能对应到上面某条原则；如果历史案例效果为 good，"
        "在相似情境下优先给出同类调整。仍然只输出 JSON。"
    )
    return "".join(parts), TUNE_PROMPT_VERSION
