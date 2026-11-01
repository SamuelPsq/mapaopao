# -*- coding: utf-8 -*-
"""AI 引擎配置（v3.3 → v3.3.1 单通道化）：用户在设置页自配一份模型与 API，全站 AI 通用。

- 一个 Key 同时驱动：跑小助问答 / 周审视点评 / 课表微调 / 拉伸建议 / 智能识别成绩。
- 模型必须是多模态的（能读图）——预设列表只保留各家的视觉模型，自定义模型由
  前端提示用户确认支持图片；文本场景用多模态模型完全没问题（读文是基本功）。
- 解析顺序：用户配置（完整才算）→ .env → 规则降级（体验不断档）。
  视觉兜底仍先看 VISION_* env（服务器默认可保持文本/识图分家），用户配置优先。
- key 只服务本人：GET 接口脱敏返回（保留后 4 位），B 端导出不含本表。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime

from . import db

# ---------------------------------------------------------------- 供应商预设
# 只列「确认支持图片输入」的模型：智能识别成绩（读成绩单/手表截图）依赖多模态能力。
# DeepSeek 全系不支持图片，不在预设里（服务器 .env 仍可用它做纯文本兜底）。
PROVIDERS: dict[str, dict] = {
    "kimi": {"label": "Kimi（Moonshot）", "base_url": "https://api.moonshot.cn/v1",
             "models": ["moonshot-v1-8k-vision-preview", "moonshot-v1-32k-vision-preview",
                        "moonshot-v1-128k-vision-preview"]},
    "openai": {"label": "OpenAI / GPT", "base_url": "https://api.openai.com/v1",
               "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]},
    "qwen": {"label": "Qwen（通义）", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "models": ["qwen-vl-max", "qwen-vl-plus", "qwen-omni-turbo"]},
    "glm": {"label": "GLM（智谱）", "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "models": ["glm-4v-flash", "glm-4v", "glm-4.5v"]},
    "custom": {"label": "自定义 / OpenAI 兼容", "base_url": "", "models": []},
}

DEFAULT_VISION_BASE = "https://api.moonshot.cn"
DEFAULT_VISION_MODEL = "moonshot-v1-8k-vision-preview"


def mask(key: str) -> str:
    """脱敏：保留后 4 位，其余打点。空串原样返回。"""
    if not key:
        return ""
    return "•" * 12 + key[-4:] if len(key) > 4 else "•" * 8


def _complete(base: str, key: str, model: str) -> bool:
    return bool(base and key and model)


def get_row(user_id: str) -> db.LLMConfig | None:
    with db.SessionLocal() as s:
        return s.query(db.LLMConfig).filter_by(user_id=user_id).first()


def resolve(user_id: str | None) -> tuple[str, str, str] | None:
    """统一解析：(base, key, model)。用户配置完整优先，否则文本 env，都没有返回 None。"""
    if user_id:
        row = get_row(user_id)
        if row and _complete(row.t_base_url, row.t_api_key, row.t_model):
            return row.t_base_url, row.t_api_key, row.t_model
    base, key, model = (os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY"),
                        os.getenv("LLM_MODEL"))
    return (base, key, model) if _complete(base, key, model) else None


def resolve_vision(user_id: str | None) -> tuple[str, str, str] | None:
    """视觉通道与用户共用一份配置：用户配置完整 → 直接用它（选模型时已保证多模态）；
    否则回退 VISION_* env；都没有返回 None（调用侧抛 VisionNotConfigured）。"""
    if user_id:
        row = get_row(user_id)
        if row and _complete(row.t_base_url, row.t_api_key, row.t_model):
            return row.t_base_url, row.t_api_key, row.t_model
    key = os.getenv("VISION_API_KEY")
    if not key:
        return None
    return (os.getenv("VISION_BASE_URL", DEFAULT_VISION_BASE), key,
            os.getenv("VISION_MODEL", DEFAULT_VISION_MODEL))


def view(user_id: str) -> dict:
    """设置页回显：脱敏 key + 兜底来源（user / env / none）+ 供应商预设。"""
    row = get_row(user_id)
    env_ok = _complete(os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY"),
                       os.getenv("LLM_MODEL"))
    return {
        "providers": PROVIDERS,
        "config": {"provider": row.t_provider if row else "",
                   "base_url": row.t_base_url if row else "",
                   "api_key": mask(row.t_api_key) if row else "",
                   "model": row.t_model if row else ""},
        "fallback": "user" if (row and _complete(row.t_base_url, row.t_api_key, row.t_model))
                    else ("env" if env_ok else "none"),
    }


def save(user_id: str, cfg: dict) -> None:
    """保存用户配置。api_key 字段：缺省/None = 不变；空串 = 清除。
    单通道化后同步清空遗留的 v_* 字段，避免旧视觉配置幽灵复活。"""
    with db.SessionLocal.begin() as s:
        row = s.query(db.LLMConfig).filter_by(user_id=user_id).first()
        if not row:
            row = db.LLMConfig(config_id=uuid.uuid4().hex[:16], user_id=user_id)
            s.add(row)
        if cfg.get("provider") is not None:
            row.t_provider = str(cfg.get("provider") or "")[:20]
        if cfg.get("base_url") is not None:
            row.t_base_url = str(cfg.get("base_url") or "").strip()[:120]
        if cfg.get("model") is not None:
            row.t_model = str(cfg.get("model") or "").strip()[:60]
        if "api_key" in cfg and cfg.get("api_key") is not None:
            k = str(cfg["api_key"]).strip()
            if not k:
                row.t_api_key = ""                                # 空串 = 清除
            elif k != mask(k):                                    # 脱敏占位 = 不变，勿覆盖
                row.t_api_key = k[:120]
        row.v_provider = row.v_base_url = row.v_api_key = row.v_model = ""
        row.updated_at = datetime.utcnow()


def test_connection(cfg: dict) -> dict:
    """用（未保存的）配置试连：先试 GET /models，不通再试最小 chat 补全。
    返回 {ok, message}，message 说人话。"""
    base = str(cfg.get("base_url") or "").strip().rstrip("/")
    key = str(cfg.get("api_key") or "").strip()
    model = str(cfg.get("model") or "").strip()
    if not (base and key and model):
        return {"ok": False, "message": "请先填完整 接口地址 / API Key / 模型 再测试"}
    try:
        import httpx
        with httpx.Client(timeout=6.0) as client:
            try:
                r = client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
                if r.status_code == 200:
                    return {"ok": True, "message": "连接成功：已拿到模型列表，可以保存"}
                if r.status_code in (401, 403):
                    return {"ok": False, "message": "Key 校验失败：请检查 API Key 是否正确"}
            except Exception:
                pass
            payload = {"model": model, "temperature": 0, "max_tokens": 1,
                       "messages": [{"role": "user", "content": "ping"}]}
            r = client.post(f"{base}/chat/completions", json=payload,
                            headers={"Authorization": f"Bearer {key}"})
            if r.status_code == 200:
                return {"ok": True, "message": "连接成功：模型应答正常，可以保存"}
            if r.status_code in (401, 403):
                return {"ok": False, "message": "Key 校验失败：请检查 API Key 是否正确"}
            if r.status_code == 404:
                return {"ok": False, "message": "接口地址不通：请检查地址是否为 OpenAI 兼容端点"}
            return {"ok": False, "message": f"服务返回异常（HTTP {r.status_code}），请检查配置"}
    except Exception:
        return {"ok": False, "message": "连不上服务器：请检查网络或接口地址"}
