# -*- coding: utf-8 -*-
"""真实 LLM 端到端：注册→建档→生成计划→今日卡（触发 AI 微调）→验证幂等。"""
import json, time, urllib.request

BASE = "http://127.0.0.1:8000"
suffix = str(int(time.time()))[-6:]
user = f"tunee2e{suffix}"


def call(path, body=None, token=None):
    req = urllib.request.Request(BASE + path)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    d = json.dumps(body).encode("utf-8") if body is not None else None
    with urllib.request.urlopen(req, data=d, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


r = call("/v1/account/register", {"username": user, "password": "test1234", "nickname": "AI微调冒烟"})
tok, uid = r["token"], r["user_id"]
print("register ok, uid =", uid)
call("/v1/profile", {"user_id": uid, "recent_km": 10, "recent_sec": 3600, "avail_days": 4,
                     "goal_race": "half", "ability": "B_base", "injury_flag": "N",
                     "city": "上海", "lat": 31.23, "lon": 121.47, "max_duration": 90}, tok)
plan = call(f"/v1/plan/generate?user_id={uid}", {}, tok)
print("plan ok, plan_id =", plan["plan_id"])

# 第一次今日卡：触发真实 LLM 微调
t0 = time.time()
card = call(f"/v1/today?user_id={uid}", None, tok)
print(f"today #1 ok ({time.time()-t0:.1f}s), week =", card["week_no"],
      "| main:", card["main_session"]["type_cn"], card["main_session"]["target_km"], "km")

# 读库验证：幂等事件、课表一致性
import os, sys
sys.path.insert(0, r"E:\KimiData\kimi\Workspaces\马跑跑")
os.chdir(r"E:\KimiData\kimi\Workspaces\马跑跑")
from server import db
with db.SessionLocal() as s:
    evs = [e for e in s.query(db.Event).filter_by(name="ai_tune", user_id=uid).all()]
    print("ai_tune events:", [(e.payload["applied"], e.payload["ops_cnt"]) for e in evs])
    assert len(evs) == 1, "幂等失效：调了不止一次"
    rows = s.query(db.Session).filter_by(plan_id=card["plan_id"], week_no=card["week_no"]).all()
    for x in rows:
        assert x.source in ("template", "ai_tuned", "rearranged")
        if x.source == "ai_tuned":   # 只有 AI 微调过的跑量才要求 0.5 取整，规则引擎原值不做限制
            assert abs(x.target_km / 0.5 - round(x.target_km / 0.5)) < 1e-6, f"km 不是 0.5 倍数: {x.target_km}"
        print(f"  {x.date} {x.type:10s} {x.target_km:4.1f}km key={x.is_key} src={x.source}")
    keys = [x for x in rows if x.is_key]
    assert all(x.source != "ai_tuned" for x in keys), "关键课被 AI 动了！"

# 第二次今日卡：不应再产生事件
call(f"/v1/today?user_id={uid}", None, tok)
with db.SessionLocal() as s:
    n = s.query(db.Event).filter_by(name="ai_tune", user_id=uid).count()
assert n == 1, "第二次查看又调了 LLM"
print("E2E PASS: AI 微调真实生效/安全拒收、关键课未动、每周幂等")
