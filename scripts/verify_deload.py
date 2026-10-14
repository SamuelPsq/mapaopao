"""验证：多次上报酸痛 → 本周自动强制降载，周审视可见。"""
import httpx, json

BASE = "http://127.0.0.1:7100"

# 新用户，避免被之前的降载状态污染
r = httpx.post(BASE + "/v1/profile", json={
    "recent_km": 5, "recent_sec": 1560, "avail_days": 4, "goal_race": "full",
    "ability": "B_base", "injury_flag": "N", "city": "上海", "lat": 31.23, "lon": 121.47})
uid = r.json()["user_id"]
httpx.post(BASE + "/v1/plan/generate", params={"user_id": uid})
today = httpx.get(BASE + "/v1/today", params={"user_id": uid}).json()
plan_id, week_no = today["plan_id"], today["week_no"]

before = httpx.get(BASE + f"/v1/plan/{plan_id}/week/{week_no}").json()
print("降载前课表:")
for s in before["sessions"]:
    print(" ", s["dow"], s["type"], s["pace_zone"], s["target_km"], s["status"])

# 第一次上报（threshold 未达）
r1 = httpx.post(BASE + "/v1/status/report", json={
    "user_id": uid, "soreness_part": "knee", "sleep_score": 6, "fatigue_score": 6, "entry": "checkin"})
print("\n第 1 次上报:", r1.json())

# 第二次上报 → 触发强制降载
r2 = httpx.post(BASE + "/v1/status/report", json={
    "user_id": uid, "soreness_part": "knee", "sleep_score": 5, "fatigue_score": 7, "entry": "checkin"})
print("第 2 次上报:", r2.json())

after = httpx.get(BASE + f"/v1/plan/{plan_id}/week/{week_no}").json()
print("\n降载后课表:")
for s in after["sessions"]:
    print(" ", s["dow"], s["type"], s["pace_zone"], s["target_km"], s["status"], s["source"])
print("is_dropped:", after["is_dropped"])

# 降载后再看重排预览：应不再生成 deload（已是降载形态），且 noop 通过护栏
r3 = httpx.post(BASE + "/v1/rearrange/preview", json={
    "user_id": uid, "week_no": week_no, "trigger_src": "body"})
j3 = r3.json()
print("\n降载后 preview main:", j3["main"]["strategy"] if j3["main"] else None)

# 指标：护栏应留下 patch 记录
m = httpx.get(BASE + "/v1/metrics", params={"user_id": uid}).json()
print("guard:", m["guard"])
