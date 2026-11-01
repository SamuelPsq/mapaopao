"""接口级回归：AI 引擎设置（v3.3）——用户自配模型/API 的存取、脱敏、解析优先级与试连。"""
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


def test_view_defaults_and_masked_save():
    _fresh_db()
    uid = _register("userone")
    v = c.get(f"/v1/settings/llm?user_id={uid}").json()
    assert v["text"]["api_key"] == "" and v["fallback"]["text"] in ("env", "none")
    assert "deepseek" in v["providers"] and "custom" in v["providers"]

    # 保存文本通道 → 回显脱敏（保留后 4 位），库里是明文（仅本人查询接口可见）
    r = c.put(f"/v1/settings/llm?user_id={uid}",
              json={"text": {"provider": "deepseek", "base_url": "https://api.deepseek.com",
                             "api_key": "sk-test-1234567890abcd", "model": "deepseek-chat"}})
    assert r.status_code == 200 and r.json()["text"] == "user"
    v = c.get(f"/v1/settings/llm?user_id={uid}").json()
    assert v["text"]["api_key"].endswith("abcd") and "sk-test" not in v["text"]["api_key"]
    assert llmconf.resolve_text(uid) == ("https://api.deepseek.com", "sk-test-1234567890abcd",
                                         "deepseek-chat")


def test_key_update_semantics_mask_unchanged_clear():
    _fresh_db()
    uid = _register("usertwo")
    c.put(f"/v1/settings/llm?user_id={uid}",
          json={"text": {"base_url": "https://api.deepseek.com",
                        "api_key": "sk-aaaa-1111", "model": "deepseek-chat"}})
    # 传回显的脱敏值 → 视为「不变」
    masked = llmconf.get_row(uid).t_api_key and c.get(f"/v1/settings/llm?user_id={uid}").json()["text"]["api_key"]
    c.put(f"/v1/settings/llm?user_id={uid}", json={"text": {"api_key": masked}})
    assert llmconf.get_row(uid).t_api_key == "sk-aaaa-1111"
    # 空串 → 清除 key，通道回退 env
    c.put(f"/v1/settings/llm?user_id={uid}", json={"text": {"api_key": ""}})
    assert llmconf.get_row(uid).t_api_key == ""


def test_vision_channel_independent():
    _fresh_db()
    uid = _register("userthr")
    c.put(f"/v1/settings/llm?user_id={uid}",
          json={"vision": {"provider": "kimi", "base_url": "",
                          "api_key": "vk-vision-0000", "model": ""}})
    base, key, model = llmconf.resolve_vision(uid)
    assert key == "vk-vision-0000" and model == llmconf.DEFAULT_VISION_MODEL
    # 文本通道不受影响
    env_text = llmconf.resolve_text(uid)
    assert env_text is None or env_text != (base, key, model)


def test_resolve_priority_user_over_env(monkeypatch):
    _fresh_db()
    uid = _register("userfour")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example.com")
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    assert llmconf.resolve_text(uid) == ("https://env.example.com", "env-key", "env-model")
    c.put(f"/v1/settings/llm?user_id={uid}",
          json={"text": {"base_url": "https://user.example.com",
                        "api_key": "user-key", "model": "user-model"}})
    assert llmconf.resolve_text(uid) == ("https://user.example.com", "user-key", "user-model")
    # 用户配置不完整（清了 model）→ 回退 env
    c.put(f"/v1/settings/llm?user_id={uid}", json={"text": {"model": ""}})
    assert llmconf.resolve_text(uid) == ("https://env.example.com", "env-key", "env-model")


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
    ok = llmconf.test_connection("text", {"base_url": "https://x", "api_key": "good", "model": "m"})
    assert ok["ok"] and "成功" in ok["message"]
    bad = llmconf.test_connection("text", {"base_url": "https://x", "api_key": "bad", "model": "m"})
    assert not bad["ok"] and "Key" in bad["message"]
    empty = llmconf.test_connection("text", {"base_url": "", "api_key": "", "model": ""})
    assert not empty["ok"]


def test_endpoint_test_and_validation():
    _fresh_db()
    uid = _register("userfive")
    r = c.post(f"/v1/settings/llm/test?user_id={uid}",
               json={"channel": "bogus", "base_url": "x", "api_key": "k", "model": "m"})
    assert r.status_code == 400
    r = c.post(f"/v1/settings/llm/test?user_id={uid}",
               json={"channel": "text", "base_url": "", "api_key": "", "model": ""})
    assert r.status_code == 200 and r.json()["ok"] is False
