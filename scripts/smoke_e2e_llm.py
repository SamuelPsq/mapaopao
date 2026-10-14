# -*- coding: utf-8 -*-
"""端到端验证：注册→登录→建档→生成计划→重排预览，断言 explain.source=llm"""
import json, time, urllib.request

BASE = "http://127.0.0.1:8000"
suffix = str(int(time.time()))[-6:]
user = f"llmtest{suffix}"
pwd = "test1234"


def call(path, body=None, token=None):
    req = urllib.request.Request(BASE + path)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# 1. 注册
r = call("/v1/account/register", {"username": user, "password": pwd, "nickname": "llm冒烟"})
token = r["token"]
uid = r["user_id"]
print("register ok, uid =", uid)

# 2. 登录
r = call("/v1/account/login", {"username": user, "password": pwd})
token = r["token"]
print("login ok")

# 3. 建档
profile = {
    "user_id": uid,
    "recent_km": 20, "recent_sec": 3600, "avail_days": 3,
    "goal_race": "full", "ability": "B_base", "injury_flag": "N",
    "city": "杭州", "lat": 30.27, "lon": 120.15, "max_duration": 90,
}
r = call("/v1/profile", profile, token)
print("profile ok")

# 4. 生成 16 周计划（POST 路由，user_id 走 query）
r = call(f"/v1/plan/generate?user_id={uid}", {}, token)
print("plan ok, plan_id =", r.get("plan_id"))

# 5. 重排预览（触发 explain_rearrange）
r = call("/v1/rearrange/preview", {"user_id": uid, "week_no": 1, "trigger_src": "manual"}, token)
src = r.get("main", {}).get("explain", {}).get("source")
print("rearrange preview explain.source =", src)
assert src == "llm", f"expected llm, got {src!r}"
print("E2E PASS: 重排解释走真实 LLM（DeepSeek）")
