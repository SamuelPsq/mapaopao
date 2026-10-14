"""LLM 解释层：把规则引擎算出的结构化方案，翻译成用户能接受的建议。

约束（PRD 13 章）：
- 唯一允许调用 LLM 的模块；模型绝不参与任何数值计算（数字一律由 rules 产出后传入）。
- 输出侧数值断言：文案中出现的每个数字必须存在于 facts 中，否则判失败走模板降级。
- 降级链路不是占位符：每个场景都有按规则预写的模板文案，LLM 不可用时体验不塌。

场景（scene）：
- explain_rearrange：重排解释（四槽位：原因/改了什么/代价/安抚）
- reason_line：今日卡原因行（1 句）
- week_comment：周审视执行点评（1–2 句）
- degrade_advice：劝降建议话术（1 句）
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

# ---------------------------------------------------------------- 数值断言
def flatten_numbers(obj, out: Optional[set] = None) -> set[str]:
    """把 facts 里所有数字（含列表/嵌套）摊平成字符串集合。"""
    if out is None:
        out = set()
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(_fmt(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            flatten_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            flatten_numbers(v, out)
    return out


def _fmt(n) -> str:
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    return str(n)


def assert_no_hallucination(text: str, facts: dict) -> bool:
    """文案中出现的每个数字都必须来自 facts（1–3 的纯量词豁免）。
    这条断言比任何 prompt 都管用。"""
    allowed = flatten_numbers(facts)
    found = {_fmt(float(x)) for x in re.findall(r"\d+(?:\.\d+)?", text)}
    suspect = {x for x in found if x not in allowed and x not in {"1", "2", "3"}}
    return not suspect


# ---------------------------------------------------------------- 模板降级（8 条）
_TEMPLATES = {
    ("compress", "weather"): {
        "reason": "未来几天天气不配合，本周时间也紧",
        "what_changed": "已删掉辅助轻松跑，保住两次关键课",
        "cost": "本周训练刺激略有下降，但关键刺激全部保留",
        "reassurance": "下周按原计划继续，落下的课不用补",
    },
    ("compress", "body"): {
        "reason": "你上报了身体状态不佳",
        "what_changed": "已删掉辅助课，保留关键课，本周总量降下来",
        "cost": "本周跑量减少，但训练结构保住了",
        "reassurance": "恢复优先，下周按原计划继续，不需要补",
    },
    ("compress", "schedule"): {
        "reason": "本周可用时间不够装下全部课次",
        "what_changed": "已删掉辅助轻松跑，把关键课固定在能跑的日子",
        "cost": "辅助课可以丢，关键刺激全保留",
        "reassurance": "下周重新按原计划开始，缺口不用补",
    },
    ("downgrade", "weather"): {
        "reason": "高温高湿，直接跑强度课风险大",
        "what_changed": "质量课已降一档强度、时长削减",
        "cost": "本周强度刺激打折，但训练没有中断",
        "reassurance": "天气转好后按计划恢复，不需要补",
    },
    ("downgrade", "body"): {
        "reason": "你上报了酸痛/疲劳",
        "what_changed": "今天的强度课已降为更轻松的档位",
        "cost": "强度刺激让位给恢复",
        "reassurance": "养好了下周照常，落下的不用补",
    },
    ("deload", "body"): {
        "reason": "你最近上报的伤痛有点集中",
        "what_changed": "本周强度课全部取消，只保留轻松跑，跑量同步下调",
        "cost": "本周训练刺激明显下降，这是护栏强制要求的",
        "reassurance": "养好了下周按原计划继续，落下的课不用补",
    },
    ("replace", "weather"): {
        "reason": "今天的天气不适合户外跑步",
        "what_changed": "已替换为室内交叉训练，保留训练日",
        "cost": "跑步刺激暂由交叉训练替代",
        "reassurance": "天气恢复后按计划继续，无需补跑",
    },
    ("postpone", "weather"): {
        "reason": "原定日子天气不好",
        "what_changed": "已把课顺到本周内更好的日子",
        "cost": "课次不变，只是换了时间",
        "reassurance": "仍在本周内消化，不影响下周",
    },
    ("noop", "manual"): {
        "reason": "没有必须调整的信号",
        "what_changed": "本周计划保持不变",
        "cost": "无",
        "reassurance": "按计划跑就行",
    },
}


def _template(strategy: str, trigger: str) -> dict:
    return _TEMPLATES.get((strategy, trigger)) or _TEMPLATES.get((strategy, "body")) \
           or _TEMPLATES.get((strategy, "manual")) or _TEMPLATES[("noop", "manual")]


def _fill(tpl: dict, facts: dict) -> dict:
    """把 facts 里的关键数字嵌进模板（数字来自规则引擎，模型不参与计算）。"""
    drop = facts.get("drop", [])
    pct = facts.get("stimulus_pct")
    out = dict(tpl)
    if drop and "what_changed" in out:
        names = "、".join(f"{d.get('date', '')}{d.get('zone_cn', '')} {d.get('km', '')}km" for d in drop)
        out["what_changed"] = f"已删除 {names}，保住关键课"
    if pct is not None and "cost" in out:
        out["cost"] = f"本周训练刺激维持在 {pct}%，仍在安全区间"
    return out


# ---------------------------------------------------------------- 场景 schema 注册表
def _obj(**fields: dict) -> dict:
    return {"type": "object", "properties": fields, "required": list(fields)}


SCENE_SCHEMAS: dict[str, dict] = {
    "explain_rearrange": _obj(
        reason={"type": "string"}, what_changed={"type": "string"},
        cost={"type": "string"}, reassurance={"type": "string"}),
    "reason_line": _obj(reason={"type": "string"}),
    "week_comment": _obj(comment={"type": "string"}),
    "degrade_advice": _obj(advice={"type": "string"}),
    "rearrange_recommend": _obj(choice={"type": "string"}, reason={"type": "string"}),
}


def render_recommend(facts: dict) -> tuple[Optional[dict], str]:
    """重排候选推荐：模型在 main / alt 中选一个（或 either），附一句理由。

    只影响「推荐展示」，不影响候选生成与落地——决策主权在规则引擎和用户。
    校验：choice 必须是 main/alt/either；理由过数值断言；不过即降级（None, template）。
    """
    llm = _llm_call("rearrange_recommend", facts)
    if llm is not None and llm.get("choice") in ("main", "alt", "either"):
        reason = str(llm.get("reason", "")).strip()
        if reason and assert_no_hallucination(reason, facts):
            return {"choice": llm["choice"], "reason": reason}, "llm"
    return None, "template"


# ---------------------------------------------------------------- 各场景模板兜底
def reason_line_template(f: dict) -> str:
    if f.get("is_key"):
        t = f"本周关键课 {f['key_done']}/{f['key_total']}，这是本周第 {f['key_done'] + 1} 次关键课"
        if f["key_done"] + 1 >= f["key_total"]:
            t += "，完成后本周训练刺激达标"
        return t
    return "辅助课，为关键课打底，能跑就跑，跑不了可以放弃"


def week_comment_template(f: dict) -> str:
    rate = f["rate_pct"]
    if rate >= 80:
        return f"本周执行率 {rate}%，完成质量很高，按当前节奏继续即可"
    if rate >= 50:
        return f"本周执行率 {rate}%，整体稳住了，下周优先保住两次关键课"
    return f"本周执行率 {rate}%，落下的不用补，下周从关键课开始恢复"


def degrade_advice_template(f: dict) -> str:
    return (f"过去 3 周你完成了 {f['total_plan']} 次关键课里的 {f['total_done']} 次，"
            f"按当前进度直接跑{f['race']}会有风险")


# ---------------------------------------------------------------- LLM 调用
def _llm_call(scene: str, facts: dict) -> Optional[dict]:
    """调用兼容 OpenAI 协议的网关（DeepSeek / Kimi 等）。未配置或失败返回 None（走模板降级）。"""
    base = os.getenv("LLM_BASE_URL")
    key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    if not (base and key and model):
        return None
    try:
        import httpx
        payload = {
            "model": model, "temperature": 0.3, "max_tokens": 300, "timeout": 3.0,
            "messages": [
                {"role": "system", "content":
                 "你是马拉松训练助手的文案层。所有数字已给定，禁止引入任何新数字。"
                 "用 calm_short 语气，每个字段最多 2 句。"},
                {"role": "user", "content": json.dumps(
                    {"task": scene, "facts": facts,
                     "tone": "calm_short", "max_sentences": 2}, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_schema", "json_schema": {"name": scene, "schema": SCENE_SCHEMAS[scene]}},
        }
        with httpx.Client(timeout=3.5) as client:
            r = client.post(f"{base.rstrip('/')}/chat/completions", json=payload,
                            headers={"Authorization": f"Bearer {key}"})
            if r.status_code == 400:
                # 部分网关（如 DeepSeek）不接受 json_schema，降级为 json_object + prompt 约束
                payload.pop("response_format", None)
                example = {k: f"<{v['type']}>" for k, v in SCENE_SCHEMAS[scene]["properties"].items()}
                payload["messages"][1]["content"] += (
                    "。只输出一个 JSON 对象，形如 "
                    + json.dumps(example, ensure_ascii=False)
                    + "，键名完全一致，值替换成你的文案，不要输出任何其他内容")
                # deepseek-flash 默认开思考模式，reasoning 会吃掉 max_tokens 导致正文为空；
                # 关掉思考并放大 token 预算。仅重试分支注入，兼容走 json_schema 的网关不受影响。
                payload["thinking"] = {"type": "disabled"}
                payload["max_tokens"] = 1500
                r = client.post(f"{base.rstrip('/')}/chat/completions", json=payload,
                                headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        data = json.loads(_extract_json(text))
        required = SCENE_SCHEMAS[scene]["required"]
        if any(k not in data for k in required) and isinstance(data.get(scene), dict):
            data = data[scene]      # 兼容模型用场景名做外层键的包裹输出
        if not all(k in data for k in required):
            return None
        return data
    except Exception:
        return None


def _extract_json(text: str) -> str:
    """容错解析：剥掉 ```json 围栏，截取第一个 { 到最后一个 }。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else text


def render_scene(scene: str, facts: dict, fallback):
    """通用解释入口：返回 (文案, 来源 llm/template)。

    LLM 输出必须过数值断言，不过即降级到 fallback——这条断言比任何 prompt 都管用。
    fallback 为 str（单字段场景直接返回）或 dict（多字段场景）。
    """
    llm = _llm_call(scene, facts)
    if llm is not None:
        joined = " ".join(str(v) for v in llm.values())
        if assert_no_hallucination(joined, facts):
            return llm, "llm"
    return fallback, "template"


def render_explain(facts: dict) -> tuple[dict, str]:
    """重排解释（兼容旧签名）：返回 (四槽位文案, 来源 llm/template)。"""
    tpl = _fill(_template(facts.get("strategy", "noop"), facts.get("trigger_group", "manual")), facts)
    return render_scene("explain_rearrange", facts, tpl)
