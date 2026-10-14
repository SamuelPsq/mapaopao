import httpx, json

BASE = "http://127.0.0.1:8000"

# ① 画像问卷
r = httpx.post(BASE + "/v1/profile", json={
    "recent_km": 5, "recent_sec": 1560, "avail_days": 4, "goal_race": "full",
    "ability": "B_base", "injury_flag": "N", "city": "上海", "lat": 31.23, "lon": 121.47})
print("profile:", r.status_code, r.json())
uid = r.json()["user_id"]

# ② 劝退用例
r2 = httpx.post(BASE + "/v1/profile", json={
    "recent_km": 3, "recent_sec": 1200, "avail_days": 1, "goal_race": "full",
    "ability": "A_zero_base", "injury_flag": "N"})
print("reject:", r2.json())

# ③ 生成计划
r3 = httpx.post(BASE + "/v1/plan/generate", params={"user_id": uid})
print("generate:", r3.status_code, json.dumps(r3.json(), ensure_ascii=False)[:300])

# ④ 今日卡
r4 = httpx.get(BASE + "/v1/today", params={"user_id": uid})
j4 = r4.json()
print("today:", json.dumps({k: j4[k] for k in ("week_no", "weekday", "days_to_race", "reason_line", "weather_line")}, ensure_ascii=False))
print("main:", json.dumps(j4["main_session"], ensure_ascii=False))

# ⑤ 身体上报 → 重排预览
httpx.post(BASE + "/v1/status/report", json={"user_id": uid, "soreness_part": "calf", "sleep_score": 6, "fatigue_score": 6, "entry": "checkin"})
r5 = httpx.post(BASE + "/v1/rearrange/preview", json={"user_id": uid, "week_no": j4["week_no"], "trigger_src": "body"})
j5 = r5.json()
print("preview main strategy:", j5["main"]["strategy"] if j5["main"] else None)
print("explain:", json.dumps(j5["main"]["explain"], ensure_ascii=False))
ev = j5["event_id"]

# ⑥ 接受主方案
r6 = httpx.post(BASE + f"/v1/rearrange/{ev}/decision", json={"result": "accept", "decision_ms": 3200})
print("decision:", r6.json())

# ⑦ 周审视
plan_id = j4["plan_id"]
r7 = httpx.get(BASE + f"/v1/plan/{plan_id}/week/{j4['week_no']}")
j7 = r7.json()
print("week:", json.dumps({"phase": j7["phase"], "adherence": j7["adherence"], "sessions": len(j7["sessions"])}, ensure_ascii=False))

# ⑧ 打卡 + 指标
sid = j7["sessions"][0]["session_id"]
r8 = httpx.post(BASE + f"/v1/session/{sid}/log", json={"status": "full", "actual_km": 8.0, "actual_pace": "6:10", "rpe": 6})
print("log:", r8.json())

# ⑨ 幂等：重复 decision
r9 = httpx.post(BASE + f"/v1/rearrange/{ev}/decision", json={"result": "accept"})
print("dedupe:", r9.json())

# ⑩ 指标看板
r10 = httpx.get(BASE + "/v1/metrics", params={"user_id": uid})
print("metrics:", json.dumps(r10.json(), ensure_ascii=False)[:400])
