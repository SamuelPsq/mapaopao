# -*- coding: utf-8 -*-
"""AI 跑步助手「跑小助」：RAG + LLM 的自由问答层。

与规则引擎/微调层的关系：助手只解释、不建议改课——所有排课逻辑仍由 VDOT 规则引擎与
tune 微调层负责。知识只来自 rag.retrieve() 召回的条目，回答必须标注出处；
未配置 LLM 时降级为直接拼接知识条目（出处 + 原文），体验不断档。
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Optional

from server import db, rag

SYSTEM_PROMPT = (
    "你是「跑小助」，马跑跑（基于 VDOT 的马拉松训练助手）内置的 AI 跑步助手，"
    "服务对象是马拉松新手。回答要求：\n"
    "1. 只能依据「知识条目」回答，涉及经验/原理的结论用 [编号] 标注出处；"
    "知识条目没覆盖的问题，明确说「知识库里暂时没有这条经验」，不要编造。\n"
    "2. 「用户情况」里的数字（VDOT、周数、配速、睡眠/疲劳分）是唯一事实来源，"
    "禁止引入任何新数字、新配速建议；配速类具体数字让用户去今日卡/周审视页看。\n"
    "3. 不做医疗诊断；关节锐痛、肿胀、步态改变等红灯信号，一律建议停跑并就医。\n"
    "4. 引用了「伤病康复」出处条目时，回答结尾必须加上："
    "「以上为科普参考，不构成医疗建议；症状持续或加重请及时就医。」\n"
    "5. 语气像耐心的老跑友，口语化，先给结论再解释，150 字以内。\n"
    "6. 「近期对话」是多轮上下文：用户用「那/还是/这样/它」等指代时，先结合上文"
    "弄清指代对象再回答；上文提到过的部位（如膝盖）和问题（如能否训练）要衔接得上。"
)

DISCLAIMER = "以上为科普参考，不构成医疗建议；症状持续或加重请及时就医。"

HISTORY_TURNS = 6        # 注入 LLM 的最近对话轮数（一轮 = 用户+助手各一条）
HISTORY_KEEP = 50        # 每用户最多保留的消息条数


def load_history(user_id: str, limit: int = HISTORY_KEEP) -> list[dict]:
    """读取最近对话（旧→新），供前端恢复面板和多轮注入。"""
    with db.SessionLocal() as s:
        rows = (s.query(db.AssistantMsg).filter_by(user_id=user_id)
                .order_by(db.AssistantMsg.id.desc()).limit(limit).all())
    return [{"role": r.role, "content": r.content} for r in reversed(rows)]


def save_turn(user_id: str, question: str, answer_text: str) -> None:
    """落一条问答（用户+助手各一条），并裁剪到保留上限。"""
    with db.SessionLocal.begin() as s:
        s.add(db.AssistantMsg(user_id=user_id, role="user", content=question))
        s.add(db.AssistantMsg(user_id=user_id, role="assistant", content=answer_text))
        ids = [r.id for r in s.query(db.AssistantMsg).filter_by(user_id=user_id)
               .order_by(db.AssistantMsg.id.desc()).all()]
        if len(ids) > HISTORY_KEEP:
            s.query(db.AssistantMsg).filter(db.AssistantMsg.id.in_(ids[HISTORY_KEEP:])) \
                .delete(synchronize_session=False)


def _user_context(user_id: str) -> dict:
    """组装用户现状摘要：只读，供 LLM 对齐「你是谁、你在第几周、最近身体如何」。"""
    ctx: dict = {}
    try:
        with db.SessionLocal() as s:
            user = s.get(db.User, user_id)
            if not user:
                return ctx
            ctx["vdot"] = user.vdot
            ctx["city"] = user.city
            plan = s.query(db.Plan).filter_by(user_id=user_id, status="active").first()
            if plan:
                week_no = min((date.today() - plan.start_date).days // 7 + 1, 16)
                ctx["week_no"] = week_no
                ctx["days_to_race"] = max(
                    0, (plan.start_date + timedelta(days=16 * 7 - 1) - date.today()).days)
            rep = s.query(db.StatusReport).filter_by(user_id=user_id) \
                .order_by(db.StatusReport.date.desc()).first()
            if rep:
                ctx["latest_report"] = {
                    "date": rep.date.isoformat(), "sleep_score": rep.sleep_score,
                    "fatigue_score": rep.fatigue_score, "soreness_part": rep.soreness_part}
            ctx["consecutive_under"] = rag.consecutive_under_streak(user_id)
    except Exception:
        pass
    return ctx


def _raw_call(payload: dict, base: str, key: str) -> str:
    """真实网关请求（独立成函数，测试可注入桩）。"""
    import httpx
    with httpx.Client(timeout=9.0) as client:
        r = client.post(f"{base.rstrip('/')}/chat/completions", json=payload,
                        headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


def assistant_llm(question: str, context: dict, kb: list[dict],
                  history: Optional[list[dict]] = None) -> Optional[str]:
    """调用兼容 OpenAI 协议的网关；未配置或失败返回 None（走知识拼接降级）。

    history 为「近期对话」（旧→新，每条 {role, content}），注入 system 与当前问题之间，
    让 LLM 能衔接指代词（「那/还是/这样」）和上文提到的部位/问题。
    """
    base, key, model = os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY"), os.getenv("LLM_MODEL")
    if not (base and key and model):
        return None
    try:
        kb_in = [{"no": i + 1, "出处": f"{c['source']}·{c['name']}", "内容": c["text"]}
                 for i, c in enumerate(kb)]
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        # 多轮上下文：最近 HISTORY_TURNS 轮原样注入，LLM 可见上文指代
        for h in (history or [])[-HISTORY_TURNS * 2:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": json.dumps(
            {"问题": question, "用户情况": context, "知识条目": kb_in},
            ensure_ascii=False)})
        payload = {
            "model": model, "temperature": 0.4, "max_tokens": 400, "timeout": 8.0,
            "messages": messages,
        }
        text = (_raw_call(payload, base, key) or "").strip()
        # 防幻觉底线：LLM 引用了不存在的出处编号 → 整条作废，走降级
        cited = {int(n) for n in __import__("re").findall(r"\[(\d{1,2})\]", text)}
        if cited and max(cited) > len(kb_in):
            return None
        return text or None
    except Exception:
        return None


def fallback_answer(question: str, kb: list[dict], medical: bool = False) -> str:
    """无 LLM 时直接拼接召回条目：出处 + 原文，保证信息可溯源。"""
    if not kb:
        return ("这个问题超出了我现在的知识库。换个问法试试——比如「睡眠不足还能训练吗」"
                "「跑的时候怎么判断强度」「膝盖疼要不要停跑」。")
    parts = [f"[{c['source']}·{c['name']}] {c['text']}" for c in kb[:3]]
    text = "\n\n".join(parts)
    if medical:                        # 仅当当前问题本身是伤病话题时才挂就医提示
        text += f"\n\n⚠️ {DISCLAIMER}"
    return text


def answer(user_id: str, question: str) -> dict:
    """问答主入口：历史上下文 + 召回 → LLM 生成（带出处校验）→ 兜底拼接，最后落库一轮。"""
    question = (question or "").strip()[:300]
    history = load_history(user_id)                 # 旧→新，最多 HISTORY_KEEP 条
    # 召回 query 拼接最近几条用户消息：「那今天还能跑吗」这类指代问法也能召回到上文主题
    recent_user = " ".join(c["content"] for c in history if c["role"] == "user")[-200:]
    kb = rag.retrieve(f"{recent_user} {question}" if recent_user else question, k=4)
    # medical 只看当前问题本身的话题：上文伤病语境可以帮助召回，但不能给普通追问误挂医疗标志
    medical = any(c["source"] == "伤病康复" for c in rag.retrieve(question, k=4))
    context = _user_context(user_id)
    text = assistant_llm(question, context, kb, history=history)
    from_llm = bool(text)
    if text and medical and "就医" not in text:
        text += f"\n\n{DISCLAIMER}"      # LLM 漏带就医提示时兜底补上
    if not text:
        text = fallback_answer(question, kb, medical)
    try:
        save_turn(user_id, question, text)          # 多轮记忆：本轮问答入库，下轮可见
    except Exception:
        pass                                        # 落库失败不阻断回答
    return {
        "answer": text,
        "from": "llm" if from_llm else "kb",
        "medical": medical,               # 伤病类回答：前端据此展示「仅供参考」警示条
        "sources": [{"source": c["source"], "name": c["name"], "score": c["score"]}
                    for c in kb],
    }
