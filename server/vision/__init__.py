"""拍照/上传识别运动成绩 → 结构化数据（距离 + 总用时）。

防幻觉设计延续项目红线：AI 只负责「从图里读出数字」，一切数值合法性由代码校验
（量纲、区间、配速合理性），校验不过直接拒用——绝不让模型幻觉出的成绩进计划。
"""
from __future__ import annotations

import base64
import json
import os

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 10 * 1024 * 1024        # 手表/成绩单的截图或照片，10MB 上限足够

# 校验区间（数值断言，与 prompts 无关）：超出即视为识别错误而非用户成绩
KM_MIN, KM_MAX = 0.4, 200.0         # 400m 冲刺 ~ 超马
SEC_MIN, SEC_MAX = 60, 6 * 3600     # 1 分钟 ~ 6 小时
PACE_MIN, PACE_MAX = 90, 720        # 90s/km(1'30") ~ 720s/km(12'00")，更慢/更快都不可信

_PROMPT = """你在帮一个跑步训练 App 读取用户上传的运动成绩图片，可能是：
- 马拉松/路跑比赛成绩单（号码布、完赛证书、短信截图）
- 运动手表的跑步记录界面（Garmin/高驰/Apple Watch 等）
- 手机跑步 App 的记录页（Keep/悦跑圈/NRC 等）

请只输出一个 JSON 对象（不要输出任何其他文字）：
{"type": "race_result|watch|app|other", "distance_km": 数字, "duration_sec": 整数,
 "note": "一句话说明你从图中读到了什么（如「 Garmin 界面，10.02 公里，用时 58:24」）"}

规则：
1. distance_km 是公里数（如 10、21.0975、42.195、5.02、9.01）；duration_sec 是总用时换算成的秒
   （如 59:07 → 3547，1:23:45 → 5025）。
2. 注意国产跑步 App（悦跑圈/Keep/咕咚等）的常见布局：**数字在上、中文标签在下**——
   例如大字「9.01 公里」是距离，「6'33"」上方对应标签「平均配速」，「00:59:07」上方对应
   标签「用时」。一定要按中文标签取数，不要把配速当用时。
3. 如果图上同时有多个记录，取**最近一次完整跑步**的那个。
4. 如果只有配速和距离没有总用时，用 距离×配速 换算总秒数（配速 6'33" = 393 秒/公里）。
5. 如果图片里根本没有跑步成绩（比如是自拍、风景、无关截图），把 distance_km 填 0。
6. 看不清的数字宁可填 0，也不要猜。"""


class VisionNotConfigured(Exception):
    """未配置视觉识别密钥——与「识别失败」区分，应用侧应给出不同的用户提示。"""


def extract_result(image_bytes: bytes, mime: str) -> dict | None:
    """调视觉模型提取成绩；任何失败（未配置/超时/校验不过）都返回 None。

    独立环境变量 VISION_BASE_URL / VISION_API_KEY / VISION_MODEL：
    视觉能力绑定具体多模态模型（DeepSeek 全系不支持图片），不能与文本 LLM 混用。
    """
    base = os.getenv("VISION_BASE_URL", "https://api.moonshot.cn")
    key = os.getenv("VISION_API_KEY")
    model = os.getenv("VISION_MODEL", "moonshot-v1-8k-vision-preview")
    if not key:
        raise VisionNotConfigured("缺少 VISION_API_KEY")
    try:
        import httpx
        b64 = base64.b64encode(image_bytes).decode()
        payload = {"model": model, "temperature": 0.0, "max_tokens": 400,
                   "messages": [{"role": "user", "content": [
                       {"type": "text", "text": _PROMPT},
                       {"type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"}}]}]}
        with httpx.Client(timeout=30.0) as client:
            r = client.post(f"{base.rstrip('/')}/v1/chat/completions", json=payload,
                            headers={"Authorization": f"Bearer {key}"})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        return _validate(_parse_json(text))
    except Exception:
        return None


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _validate(data: dict) -> dict | None:
    """数值断言：类型、区间、配速合理性全过才算识别成功。宁缺毋滥。"""
    try:
        km = float(data["distance_km"])
        sec = float(data["duration_sec"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (KM_MIN <= km <= KM_MAX and SEC_MIN <= sec <= SEC_MAX):
        return None                      # 含 AI 主动填 0 的「识别不出来」
    pace = sec / km
    if not (PACE_MIN <= pace <= PACE_MAX):
        return None                      # 例：把 5'30" 读成 330 公里/秒这类量纲错误
    return {"distance_km": round(km, 2), "duration_sec": int(round(sec)),
            "type": str(data.get("type", "other"))[:20],
            "note": str(data.get("note", ""))[:200]}
