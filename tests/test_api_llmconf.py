"""接口级回归：AI 引擎设置（v3.3.1 单通道化）——一份 Key 驱动全站 AI，模型预设均为多模态。"""
import os
import tempfile

os.environ["MAPAOPAO_DB_URL"] = "sqlite:///" + os.path.join(
    tempfile.gettempdir(), "mapaopao_llmconf_test.db")

from fastapi.testclient import TestClient

from server import db, llmconf
from server.main import app
from server import main as _main

_main.AUTH_ENABLED = False   # 本文件回归业务逻辑，鉴权由 test_api_auth.py 覆盖

db.init_db()
c = TestClient(app)


def _fresh_db():
    db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)


def _register(username):
    r = c.post("/v1/account/register",
               json={"agree_data_use": True, "username": username, "password": "run12345"})
    return r.json()["user_id"]


def _save(uid, **cfg):
    return c.put(f"/v1/settings/llm?user_id={uid}", json=cfg)


def test_view_defaults_and_masked_save():
    _fresh_db()
    uid = _register("userone")
    v = c.get(f"/v1/settings/llm?user_id={uid}").json()
    assert v["config"]["api_key"] == "" and v["fallback"] in ("env", "none")
    # 预设全部为多模态模型（智能识别成绩依赖读图能力）
    assert "kimi" in v["providers"] and "custom" in v["providers"]
    assert "deepseek" not in v["providers"]
    for p in v["providers"].values():
        if not p["models"]:
            continue  # custom 预设无固定模型列表
        assert "vl" in ",".join(p["models"]) or "vision" in ",".join(p["models"]) \
            or "gpt-4o" in ",".join(p["models"]) or "4v" in ",".join(p["models"])

    # 保存 → 回显脱敏（保留后 4 位），库里是明文（仅本人查询接口可见）
    r = _save(uid, provider="kimi", base_url="https://api.moonshot.cn/v1",
              api_key="sk-test-1234567890abcd", model="moonshot-v1-8k-vision-preview")
    assert r.status_code == 200 and r.json()["fallback"] == "user"
    v = c.get(f"/v1/settings/llm?user_id={uid}").json()
    assert v["config"]["api_key"].endswith("abcd") and "sk-test" not in v["config"]["api_key"]
    assert llmconf.resolve(uid) == ("https://api.moonshot.cn/v1", "sk-test-1234567890abcd",
                                    "moonshot-v1-8k-vision-preview")


def test_single_key_powers_vision_too():
    _fresh_db()
    uid = _register("usertwo")
    _save(uid, provider="kimi", base_url="https://api.moonshot.cn/v1",
          api_key="sk-vision-shared", model="moonshot-v1-8k-vision-preview")
    # 视觉通道与用户共用同一份配置（不再需要单独的 VISION Key）
    assert llmconf.resolve_vision(uid) == ("https://api.moonshot.cn/v1", "sk-vision-shared",
                                           "moonshot-v1-8k-vision-preview")


def test_key_update_semantics_mask_unchanged_clear():
    _fresh_db()
    uid = _register("userthr")
    _save(uid, base_url="https://api.moonshot.cn/v1",
          api_key="sk-aaaa-1111", model="moonshot-v1-8k-vision-preview")
    # 传回显的脱敏值 → 视为「不变」
    masked = c.get(f"/v1/settings/llm?user_id={uid}").json()["config"]["api_key"]
    _save(uid, api_key=masked)
    assert llmconf.get_row(uid).t_api_key == "sk-aaaa-1111"
    # 空串 → 清除 key，回退 env
    _save(uid, api_key="")
    assert llmconf.get_row(uid).t_api_key == ""


def test_resolve_priority_user_over_env(monkeypatch):
    _fresh_db()
    uid = _register("userfour")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example.com")
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    assert llmconf.resolve(uid) == ("https://env.example.com", "env-key", "env-model")
    _save(uid, base_url="https://user.example.com", api_key="user-key", model="user-model")
    assert llmconf.resolve(uid) == ("https://user.example.com", "user-key", "user-model")
    # 用户配置不完整（清了 model）→ 回退 env
    _save(uid, model="")
    assert llmconf.resolve(uid) == ("https://env.example.com", "env-key", "env-model")


def test_test_connection_human_messages(monkeypatch):
    class _Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._payload = payload or {}
        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, headers=None):
            key = (headers or {}).get("Authorization", "")
            if "bad" in key:
                return _Resp(401)
            return _Resp(200, {"data": []})
        def post(self, url, json=None, headers=None):
            return _Resp(200, {"choices": [{"message": {"content": "pong"}}]})

    monkeypatch.setattr("httpx.Client", _Client)
    ok = llmconf.test_connection({"base_url": "https://x", "api_key": "good", "model": "m"})
    assert ok["ok"] and "成功" in ok["message"]
    bad = llmconf.test_connection({"base_url": "https://x", "api_key": "bad", "model": "m"})
    assert not bad["ok"] and "Key" in bad["message"]
    empty = llmconf.test_connection({"base_url": "", "api_key": "", "model": ""})
    assert not empty["ok"]


def test_endpoint_test_and_validation():
    _fresh_db()
    uid = _register("userfive")
    r = c.post(f"/v1/settings/llm/test?user_id={uid}",
               json={"base_url": "", "api_key": "", "model": ""})
    assert r.status_code == 200 and r.json()["ok"] is False
