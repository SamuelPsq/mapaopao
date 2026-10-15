"""马跑跑 · 马拉松训练规划助手 — FastAPI 应用层。

9 个对外接口 + 2 个内部任务。刻意没有 /plan/generate-oneline —— 计划生成必须经过
画像问卷（含劝退判定）：API 的形态本身就是产品态度的表达。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
import uuid
from datetime import date, datetime, timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from server import db
from server.adapt import Candidate, RearrangeContext, rearrange
from server.explain import (degrade_advice_template, reason_line_template, render_explain,
                            render_scene, week_comment_template)
from server.infra.weather import WeatherDay, get_week_weather
from server.knowledge.rules import (implied_vdot, pace_zones_display, sec_to_pace)
from server.plan import Profile, Reject, generate_plan, should_suggest_degrade, validate_intake
from server.tune import maybe_tune_week


def _load_dotenv() -> None:
    """读取项目根目录 .env：放 LLM 密钥等本地配置（不进代码库）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # server/main.py → 项目根
    path = os.path.join(root, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

app = FastAPI(title="马跑跑", version="v3.1")

# CORS 收紧：默认只放行本地开发源，生产通过反代同源访问；多源用逗号分隔的 env 配置
CORS_ORIGINS = [o.strip() for o in os.getenv(
    "MAPAOPAO_CORS_ORIGINS", "http://localhost:7100,http://127.0.0.1:7100").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

# ---------------------------------------------------------------- 安全开关
AUTH_ENABLED = True   # token 鉴权 + 登录注册限速的总开关，测试置 False 即整体旁路

# 登录/注册限速：内存滑动窗口（IP+用户名），单进程部署够用
RATE_MAX = 10          # 每窗口最多尝试 10 次
RATE_WINDOW = 300      # 窗口 5 分钟
_attempts: dict[str, list[float]] = {}


def _hit_rate_limit(key: str) -> bool:
    if not AUTH_ENABLED:
        return False
    now = time.time()
    window = [t for t in _attempts.get(key, ()) if now - t < RATE_WINDOW]
    if len(window) >= RATE_MAX:
        _attempts[key] = window
        return True
    window.append(now)
    _attempts[key] = window
    return False


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _token_user_id(token: str) -> str | None:
    if not token:
        return None
    with db.SessionLocal() as s:
        acc = s.query(db.Account).filter_by(token=token).first()
        return acc.user_id if acc else None


_PUBLIC_PATHS = {"/v1/account/login", "/v1/account/register", "/v1/account/logout"}


@app.middleware("http")
async def token_auth(request: Request, call_next):
    """全站 token 鉴权：除登录/注册/退出外，/v1/* 必须带有效 Bearer token。

    附带一致性校验：请求里出现的 user_id（query 或 JSON body）必须与 token 归属
    一致，杜绝「知道别人 user_id 就能读写别人数据」的横向越权。
    """
    if AUTH_ENABLED and request.url.path.startswith("/v1/") \
            and request.url.path not in _PUBLIC_PATHS:
        user_id = _token_user_id(_bearer_token(request))
        if not user_id:
            return JSONResponse({"detail": "未登录或登录已失效，请重新登录"}, status_code=401)
        claimed = request.query_params.get("user_id")
        if not claimed and request.method == "POST":
            try:
                body = await request.json()
                claimed = body.get("user_id") if isinstance(body, dict) else None
            except Exception:
                claimed = None
        if claimed and claimed != user_id:
            return JSONResponse({"detail": "无权操作其他账号的数据"}, status_code=403)
    return await call_next(request)

DOW_CN = "一二三四五六日"


def _dow(d: date) -> str:
    return "周" + DOW_CN[d.weekday()]


def _zone_cn(zone: str) -> str:
    return {"easy": "轻松跑", "marathon": "马拉松配速", "threshold": "阈值跑",
            "interval": "间歇跑", "repetition": "重复跑", "cross_train": "交叉训练"}.get(zone, zone)


# ================================================================ 请求模型
class ProfileIn(BaseModel):
    user_id: str | None = None
    recent_km: float
    recent_sec: float                      # 秒
    avail_days: int
    goal_race: str = "full"                # full / half
    ability: str = "B_base"                # A_zero_base / B_base / C_advanced
    injury_flag: str = "N"                 # N / M
    city: str = "上海"
    lat: float = 31.23
    lon: float = 121.47
    max_duration: int = 90
    height_cm: float | None = None         # BMI 规划修正（可选）
    weight_kg: float | None = None


class LogIn(BaseModel):
    status: str                            # full / partial / skipped
    actual_km: float = 0.0
    actual_pace: str = ""                  # "5:30"，为空时可用 duration_sec 反算
    duration_sec: int = 0                  # 总用时（秒）
    rpe: int | None = None
    skip_kind: str = ""                    # skipped 时记录：rest 停练 / strength 室内力量


class WeightIn(BaseModel):
    user_id: str
    weight_kg: float


class AdvanceIn(BaseModel):
    user_id: str
    actual_km: float                       # 进阶判定那次跑步的里程
    duration_sec: int                      # 对应总用时（秒）


class StatusReportIn(BaseModel):
    user_id: str
    soreness_part: str = "none"            # knee / calf / ankle / none
    muscle_sore: bool = False              # 训练后肌肉酸痛（DOMS）
    breath_state: str = ""                 # gasp 喘粗气 / talk 能边跑边说话 / easy 强度很低
    sleep_score: int = 5
    fatigue_score: int = 5
    entry: str = "checkin"


class StretchIn(BaseModel):
    user_id: str
    status: str                            # full / partial / skipped
    type_cn: str = ""                      # 轻松跑 / 长距离 …
    target_km: float = 0.0


class AssistantIn(BaseModel):
    user_id: str
    message: str = ""                      # 自由提问，≤300 字


class PreviewIn(BaseModel):
    user_id: str
    week_no: int
    trigger_src: str = "manual"            # weather / body / schedule / manual


class DecisionIn(BaseModel):
    result: str                            # accept / reject / manual
    decision_ms: int = 0
    use_alt: bool = False                  # 用备选方案


class DegradeIn(BaseModel):
    to: str                                # half / defer / insist


class AccountIn(BaseModel):
    username: str
    password: str


class CityIn(BaseModel):
    user_id: str
    city: str
    lat: float
    lon: float


# ================================================================ 工具
def _week_range(start: date, week_no: int) -> tuple[date, date]:
    ws = start + timedelta(days=7 * (week_no - 1))
    return ws, ws + timedelta(days=6)


def _current_week_no(plan: db.Plan, today: date) -> int:
    return max(1, ((today - plan.start_date).days // 7) + 1)


def _sessions_of_week(db_sess, plan_id: str, week_no: int) -> list[db.Session]:
    return db_sess.query(db.Session).filter_by(plan_id=plan_id, week_no=week_no) \
        .order_by(db.Session.date, db.Session.seq).all()


def _session_dict(s: db.Session, log: db.SessionLog | None = None) -> dict:
    d = {"session_id": s.session_id, "date": s.date.isoformat(), "dow": _dow(s.date),
         "type": s.type, "type_cn": _zone_cn(s.pace_zone) if s.type != "long_run" else "长距离",
         "is_key": s.is_key, "pace_zone": s.pace_zone, "target_km": round(s.target_km, 1),
         "target_pace": s.target_pace, "source": s.source, "status": s.status}
    if log:
        d["log"] = {"status": log.status, "actual_km": log.actual_km,
                    "actual_pace": log.actual_pace, "rpe": log.rpe}
    return d


def _active_plan(db_sess, user_id: str) -> db.Plan:
    plan = db_sess.query(db.Plan).filter_by(user_id=user_id, status="active").first()
    if not plan:
        raise HTTPException(404, "没有进行中的计划，请先生成")
    return plan


def _build_ctx(db_sess, user: db.User, plan: db.Plan, week_no: int,
               trigger_src: str) -> RearrangeContext:
    today = date.today()
    week_start, week_end = _week_range(plan.start_date, week_no)
    sessions = _sessions_of_week(db_sess, plan.plan_id, week_no)
    remaining = []
    for s in sessions:
        if s.status == "planned" and s.date >= today:
            remaining.append({"session_id": s.session_id, "date": s.date.isoformat(),
                              "type": s.type, "is_key": s.is_key, "pace_zone": s.pace_zone,
                              "target_km": s.target_km, "status": s.status})
    days_used = len({s.date for s in sessions
                     if db_sess.query(db.SessionLog).filter_by(session_id=s.session_id).first()})
    avail_days_left = max(1, user.avail_days - days_used)
    # 本周剩余日期的天气（整周一次拉取并缓存）
    weather = {}
    d0 = max(week_start, today)
    for i in range((min(week_end, d0 + timedelta(days=6)) - d0).days + 1):
        dd = d0 + timedelta(days=i)
        weather[dd.isoformat()] = get_week_weather(user.lat, user.lon, dd).get(dd.isoformat(),
                                                                                WeatherDay(date=dd.isoformat()))
    soreness_today = db_sess.query(db.StatusReport).filter_by(
        user_id=user.user_id, date=today).first() is not None and \
        db_sess.query(db.StatusReport).filter_by(user_id=user.user_id, date=today).first().soreness_part != "none"
    injury_14d = db_sess.query(db.StatusReport).filter(
        db.StatusReport.user_id == user.user_id,
        db.StatusReport.date >= today - timedelta(days=14),
        db.StatusReport.soreness_part != "none").count()
    prev_week_no = week_no - 1
    prev_km = 0.0
    if prev_week_no >= 1:
        pw = db_sess.query(db.PlanWeek).filter_by(plan_id=plan.plan_id, week_no=prev_week_no).first()
        if pw:
            prev_km = pw.planned_km
    planned_km = sum(s["target_km"] for s in remaining)
    return RearrangeContext(week_no=week_no, remaining=remaining,
                            avail_days_left=avail_days_left, weather=weather,
                            soreness_today=soreness_today, prev_week_km=prev_km,
                            planned_week_km=planned_km, injury_reports_14d=injury_14d,
                            trigger_src=trigger_src)


def _facts_for(candidate: Candidate, ctx: RearrangeContext) -> dict:
    kept_key = [s for s in candidate.sessions if s.get("is_key") and s["status"] == "planned"]
    orig_key = [s for s in ctx.remaining if s.get("is_key")]
    src = sum(s["target_km"] for s in orig_key)
    kept = sum(s["target_km"] for s in kept_key)
    drop = [{"date": _dow(date.fromisoformat(s["date"])), "km": s["target_km"],
             "zone_cn": _zone_cn(s["pace_zone"])}
            for s in candidate.sessions if s["status"] == "cancelled_by_rearrange"]
    triggers = []
    if ctx.soreness_today:
        triggers.append("身体酸痛")
    if any(w.hard_constraint for w in ctx.weather.values()):
        triggers.append("恶劣天气")
    if any(w.hot_soft for w in ctx.weather.values()):
        triggers.append("高温")
    trigger_group = {"weather": "weather", "body": "body", "schedule": "schedule"}.get(
        ctx.trigger_src, "manual")
    return {"strategy": candidate.strategy, "trigger_group": trigger_group,
            "week_no": ctx.week_no, "drop": drop, "stimulus_pct": round(kept / src * 100) if src else 100,
            "trigger": triggers, "delta_km": candidate.delta_km,
            "remain_days": ctx.avail_days_left}


# ================================================================ 接口
@app.post("/v1/profile")
def create_profile(p: ProfileIn):
    """链路 A 第①步：画像问卷（含劝退判定前置）。命中劝退返回 200 + Reject（产品结果）。"""
    profile = Profile(recent_km=p.recent_km, recent_sec=p.recent_sec, avail_days=p.avail_days,
                      goal_race=p.goal_race, ability=p.ability,
                      injury_flag=p.injury_flag, major_injury_in_3m=(p.injury_flag == "M"),
                      city=p.city, lat=p.lat, lon=p.lon, max_duration=p.max_duration,
                      height_cm=p.height_cm, weight_kg=p.weight_kg)
    user_id = p.user_id or uuid.uuid4().hex[:12]
    rej = validate_intake(profile)
    with db.SessionLocal.begin() as s:
        db.track  # noqa —— 占位防止误删
        if rej:
            db_sess_user = None
        else:
            from server.knowledge.rules import vdot_from_recent_race
            vdot = vdot_from_recent_race(p.recent_km, p.recent_sec, p.ability)
            old = s.get(db.User, user_id)
            if old:
                old.vdot, old.avail_days, old.goal_race = vdot, p.avail_days, p.goal_race
                old.ability_tier, old.city, old.lat, old.lon = p.ability, p.city, p.lat, p.lon
                old.injury_flag, old.max_duration = p.injury_flag, p.max_duration
                old.height_cm, old.weight_kg = p.height_cm, p.weight_kg
            else:
                s.add(db.User(user_id=user_id, vdot=vdot, ability_tier=p.ability,
                              goal_race=p.goal_race, avail_days=p.avail_days,
                              max_duration=p.max_duration, injury_flag=p.injury_flag,
                              city=p.city, lat=p.lat, lon=p.lon,
                              height_cm=p.height_cm, weight_kg=p.weight_kg))
        s.add(db.Event(name="plan_generated", user_id=user_id,
                       payload={"rejected": bool(rej), "reason": rej.reason if rej else None,
                                "avail_days": p.avail_days, "goal_race": p.goal_race}))
    if rej:
        return {"rejected": True, "reason": rej.reason, "suggest": rej.suggest}
    return {"rejected": False, "user_id": user_id}


# ================================================================ 账号体系
def _hash_password(password: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 100_000)
    return dk.hex()


@app.post("/v1/account/register")
def register(body: AccountIn, request: Request):
    """注册：用户名 + 密码，建账号并给一个占位用户，问卷阶段回写画像。"""
    username = body.username.strip()
    ip = request.client.host if request.client else "?"
    if _hit_rate_limit(f"register|{ip}|{username}"):
        raise HTTPException(429, "尝试过于频繁，请 5 分钟后再试")
    if not (3 <= len(username) <= 20):
        raise HTTPException(400, "用户名长度需为 3-20 个字符")
    if len(body.password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    with db.SessionLocal.begin() as s:
        if s.get(db.Account, username):
            raise HTTPException(409, "用户名已被注册")
        user_id = uuid.uuid4().hex[:12]
        salt = os.urandom(16).hex()
        token = secrets.token_urlsafe(32)
        s.add(db.User(user_id=user_id, vdot=30, ability_tier="B_base", goal_race="full",
                      avail_days=3, city="上海", lat=31.23, lon=121.47))
        s.add(db.Account(username=username, user_id=user_id, salt=salt,
                         password_hash=_hash_password(body.password, salt), token=token))
        s.add(db.Event(name="account_registered", user_id=user_id,
                       payload={"username": username}))
    return {"ok": True, "user_id": user_id, "token": token}


@app.post("/v1/account/login")
def login(body: AccountIn, request: Request):
    """登录：统一返回 401，不区分「账号不存在」和「密码错误」（防账号遍历）。"""
    username = body.username.strip()
    ip = request.client.host if request.client else "?"
    if _hit_rate_limit(f"login|{ip}|{username}"):
        raise HTTPException(429, "尝试过于频繁，请 5 分钟后再试")
    with db.SessionLocal.begin() as s:
        acc = s.get(db.Account, username)
        if not acc or not hmac.compare_digest(
                acc.password_hash, _hash_password(body.password, acc.salt)):
            raise HTTPException(401, "用户名或密码输入错误，请重试")
        token = secrets.token_urlsafe(32)
        acc.token = token          # 重新登录即轮换凭证，旧 token 同时作废
        has_plan = s.query(db.Plan).filter_by(user_id=acc.user_id,
                                              status="active").first() is not None
        user_id = acc.user_id
    return {"ok": True, "user_id": user_id, "has_plan": has_plan, "token": token}


@app.post("/v1/account/logout")
def logout(request: Request):
    """退出登录：凭 token 找到账号并作废凭证；token 已失效也幂等返回 ok。"""
    token = _bearer_token(request)
    if token:
        with db.SessionLocal.begin() as s:
            acc = s.query(db.Account).filter_by(token=token).first()
            if acc:
                acc.token = None
    return {"ok": True}


@app.post("/v1/account/delete")
def delete_account(body: AccountIn):
    """注销账号：先验证用户名密码，再级联删除该用户全部数据（不可恢复）。"""
    username = body.username.strip()
    with db.SessionLocal.begin() as s:
        acc = s.get(db.Account, username)
        if not acc or not hmac.compare_digest(
                acc.password_hash, _hash_password(body.password, acc.salt)):
            raise HTTPException(401, "用户名或密码不正确")
        user_id = acc.user_id

        plan_ids = [p.plan_id for p in s.query(db.Plan).filter_by(user_id=user_id)]
        week_ids = [w.plan_week_id for w in s.query(db.PlanWeek)
                    .filter(db.PlanWeek.plan_id.in_(plan_ids))] if plan_ids else []
        session_ids = [x.session_id for x in s.query(db.Session)
                       .filter(db.Session.plan_week_id.in_(week_ids))] if week_ids else []

        if session_ids:
            s.query(db.SessionLog).filter(db.SessionLog.session_id.in_(session_ids)).delete(synchronize_session=False)
        if week_ids:
            s.query(db.Session).filter(db.Session.plan_week_id.in_(week_ids)).delete(synchronize_session=False)
            s.query(db.PlanWeekSnapshot).filter(
                db.PlanWeekSnapshot.plan_week_id.in_(week_ids)).delete(synchronize_session=False)
        if plan_ids:
            s.query(db.PlanWeek).filter(db.PlanWeek.plan_id.in_(plan_ids)).delete(synchronize_session=False)
            s.query(db.GuardAudit).filter(db.GuardAudit.plan_id.in_(plan_ids)).delete(synchronize_session=False)
            s.query(db.Plan).filter(db.Plan.user_id == user_id).delete(synchronize_session=False)
        s.query(db.RearrangeEvent).filter_by(user_id=user_id).delete(synchronize_session=False)
        s.query(db.StatusReport).filter_by(user_id=user_id).delete(synchronize_session=False)
        s.query(db.Event).filter_by(user_id=user_id).delete(synchronize_session=False)
        s.query(db.Account).filter_by(username=username).delete(synchronize_session=False)
        s.query(db.User).filter_by(user_id=user_id).delete(synchronize_session=False)
    return {"ok": True}


@app.post("/v1/user/city")
def change_city(body: CityIn):
    """切换城市：更新用户坐标，并按新城市重新生成配套训练计划（天气联动随之切换）。"""
    with db.SessionLocal.begin() as s:
        user = s.get(db.User, body.user_id)
        if not user:
            raise HTTPException(404, "用户不存在")
        user.city = body.city.strip() or user.city
        user.lat, user.lon = body.lat, body.lon
        s.add(db.Event(name="city_changed", user_id=body.user_id,
                       payload={"city": user.city, "lat": body.lat, "lon": body.lon}))
        city = user.city
    # profile_hash 含城市坐标 → 必然生成新计划，旧计划自动作废；天气建议按新坐标生效
    r = gen_plan(body.user_id)
    if r.get("rejected"):
        return {"ok": False, "message": r.get("reason", "计划生成失败")}
    return {"ok": True, "plan_id": r["plan_id"], "city": city,
            "message": f"已切换到{city}，训练计划已按新城市重新生成"}


@app.post("/v1/user/weight")
def update_weight(body: WeightIn):
    """实时修改体重：回写 user 行并返回最新 BMI；BMI 进规划修正（生成时生效）。
    BMI≥28 时建议重新生成计划（保守模式：周增幅减半），由用户主动触发。"""
    if not (30 <= body.weight_kg <= 300):
        raise HTTPException(400, "体重需在 30–300 kg 之间")
    with db.SessionLocal.begin() as s:
        user = s.get(db.User, body.user_id)
        if not user:
            raise HTTPException(404, "用户不存在")
        old_bmi = (user.weight_kg / ((user.height_cm / 100) ** 2)
                   if user.height_cm and user.weight_kg else None)
        user.weight_kg = body.weight_kg
        bmi = (user.weight_kg / ((user.height_cm / 100) ** 2)
               if user.height_cm else None)
        bmi = round(bmi, 1) if bmi else None
        crossed = (old_bmi is None or old_bmi < 28) and bmi is not None and bmi >= 28
        s.add(db.Event(name="weight_updated", user_id=body.user_id,
                       payload={"weight_kg": body.weight_kg, "bmi": bmi}))
    out: dict = {"ok": True, "weight_kg": body.weight_kg, "bmi": bmi}
    if crossed:
        out["advice"] = "BMI 已达到 28，系统将为你的关节负荷启用保守规划（周增幅减半），点「修改训练计划」即可生效"
    return out


@app.post("/v1/user/advance")
def advance_level(body: AdvanceIn):
    """进阶确认：按用户实际里程+总用时算配速 → 折算 VDOT → 刷新未开始的周。
    只动未来，已执行的周永远不改。"""
    if body.actual_km <= 0 or body.duration_sec <= 0:
        raise HTTPException(400, "里程和总用时都必须大于 0")
    from server.knowledge.rules import sec_to_pace
    pace_sec = round(body.duration_sec / body.actual_km)
    new_v = implied_vdot(body.actual_km, body.duration_sec)
    with db.SessionLocal.begin() as s:
        user = s.get(db.User, body.user_id)
        if not user:
            raise HTTPException(404, "用户不存在")
        plan = s.query(db.Plan).filter_by(user_id=body.user_id, status="active").first()
        if not plan:
            raise HTTPException(404, "没有进行中的计划")
        if abs(new_v - user.vdot) < 1.5:
            note = (f"这次表现（配速 {sec_to_pace(pace_sec)}）与当前计划匹配，"
                    "无需重排，按计划继续即可")
        else:
            old = user.vdot
            user.vdot = new_v
            from datetime import date as _date
            today = _date.today()
            cur_week = max(1, ((today - plan.start_date).days // 7) + 1)
            _refresh_future_weeks(s, plan, cur_week, new_v)
            note = f"已按照您的跑力重新安排：能力值 VDOT {old} → {new_v}，未开始的周计划已全部刷新"
        s.add(db.Event(name="level_advanced", user_id=body.user_id,
                       payload={"vdot": new_v, "pace": sec_to_pace(pace_sec),
                                "actual_km": body.actual_km,
                                "duration_sec": body.duration_sec}))
    return {"ok": True, "vdot": new_v, "pace": sec_to_pace(pace_sec), "note": note}


class PlanAdjustIn(BaseModel):
    user_id: str
    avail_days: int | None = None          # 每周训练天数（2–7）
    intensity_shift: int = 0               # 训练强度 -1 减一级 / +1 加一级；一级 = VDOT ±2


@app.post("/v1/plan/adjust")
def adjust_plan(body: PlanAdjustIn):
    """修改训练计划：调整每周训练天数 / 增减一级训练强度（一级 = VDOT ±2，只影响目标配速）。

    与切换城市同一语义：旧计划作废，按新参数重新生成 16 周计划，进度清零。
    """
    from server.knowledge.rules import VDOT_MIN, VDOT_MAX, session_cap
    if body.intensity_shift not in (-1, 0, 1):
        raise HTTPException(400, "intensity_shift 只能是 -1/0/1")
    with db.SessionLocal.begin() as s:
        user = s.get(db.User, body.user_id)
        if not user:
            raise HTTPException(404, "用户不存在")
        changes = []
        new_days = user.avail_days
        days_note = ""
        if body.avail_days is not None:
            if body.avail_days == user.avail_days:
                pass
            elif not (2 <= body.avail_days <= 7):
                raise HTTPException(400, "每周训练天数需在 2–7 天之间")
            else:
                new_days = body.avail_days
                if session_cap(new_days) == session_cap(user.avail_days):
                    # 课次上限相同 → 新课表与现有课表逐节相同，重建只会清空进度
                    old_days = user.avail_days
                    user.avail_days = new_days
                    s.add(db.Event(name="plan_adjusted", user_id=body.user_id,
                                   payload={"avail_days": new_days, "vdot": user.vdot,
                                            "changes": [], "cap_unchanged": True}))
                    return {"ok": True, "changed": False,
                            "message": f"已记录每周 {new_days} 天的偏好。课表每周最多 "
                                       f"{session_cap(new_days)} 节课，{old_days} 天和 {new_days} 天排出的课表"
                                       "完全相同，当前计划与进度已保留"}
                changes.append(f"训练天数 {user.avail_days} → {new_days} 天")
        new_vdot = user.vdot
        if body.intensity_shift:
            v = max(VDOT_MIN, min(VDOT_MAX, user.vdot + 2 * body.intensity_shift))
            if v == user.vdot:
                word = "最高" if body.intensity_shift > 0 else "最低"
                raise HTTPException(400, f"已经处于{word}训练强度（VDOT {user.vdot}），无法再{'增加' if body.intensity_shift > 0 else '减少'}")
            new_vdot = v
            changes.append(f"训练强度 VDOT {user.vdot} → {new_vdot}"
                           f"（{'增加' if body.intensity_shift > 0 else '减少'}一级，目标配速随之{'加快' if body.intensity_shift > 0 else '放慢'}）")
        if not changes:
            return {"ok": True, "changed": False, "message": "没有需要修改的内容"}
        user.avail_days, user.vdot = new_days, new_vdot
        # 强度不在 profile_hash 里 → 先把旧计划作废，gen_plan 才不会幂等 dedup 掉
        s.query(db.Plan).filter(db.Plan.user_id == body.user_id,
                                db.Plan.status == "active").update({"status": "superseded"})
        s.add(db.Event(name="plan_adjusted", user_id=body.user_id,
                       payload={"avail_days": new_days, "vdot": new_vdot,
                                "changes": changes}))
    r = gen_plan(body.user_id)
    if r.get("rejected"):
        return {"ok": False, "message": r.get("reason", "计划生成失败")}
    # 目标配速按真实 VDOT 刷新（generate_plan 内部用的是占位配速，与切换城市后同一处理）
    with db.SessionLocal.begin() as s:
        plan = s.get(db.Plan, r["plan_id"])
        _refresh_future_weeks(s, plan, 0, new_vdot)
    return {"ok": True, "changed": True, "plan_id": r["plan_id"],
            "vdot": new_vdot, "avail_days": new_days,
            "message": "已修改：" + "；".join(changes) + "。训练计划已重新生成，进度从头开始。"}


@app.post("/v1/plan/generate")
def gen_plan(user_id: str):
    """16 周计划生成 + 落 week 1 快照。幂等：同 profile 哈希生成同一计划。"""
    with db.SessionLocal() as s:
        user = s.get(db.User, user_id)
        if not user:
            raise HTTPException(404, "用户不存在，请先提交画像")
        profile = Profile(recent_km=5, recent_sec=1800, avail_days=user.avail_days,
                          goal_race=user.goal_race, ability=user.ability_tier,
                          injury_flag=user.injury_flag, city=user.city, lat=user.lat,
                          lon=user.lon, max_duration=user.max_duration,
                          height_cm=user.height_cm, weight_kg=user.weight_kg)
        # 用画像时存下的真实 vdot 生成（override），课表跑量/配速都按真实能力；
        # 不用占位成绩——否则整个计划的跑量基线约等于 VDOT 30 新手水平
        result = generate_plan(profile, vdot_override=user.vdot)
        if isinstance(result, Reject):
            return {"rejected": True, "reason": result.reason, "suggest": result.suggest}
        # 用已保存的 vdot 覆盖（画像阶段已算好，含 A 类保守系数）
        result["vdot"] = user.vdot
        # 已存在同 hash 计划 → 直接返回（幂等）
        existing = s.query(db.Plan).filter_by(user_id=user_id, profile_hash=result["profile_hash"],
                                              status="active").first()
        if existing:
            return {"plan_id": existing.plan_id, "deduped": True}
        plan_id = uuid.uuid4().hex[:12]
        start = date.today() - timedelta(days=date.today().weekday())   # 本周一
        plan = db.Plan(plan_id=plan_id, user_id=user_id, version="v3.1",
                       target_distance="marathon" if user.goal_race == "full" else "half_marathon",
                       start_date=start, profile_hash=result["profile_hash"],
                       vdot=user.vdot, goal_race=user.goal_race,
                       bmi_conservative=bool(result.get("conservative")))
        s.add(plan)
        for w in result["weeks"]:
            pw_id = f"{plan_id}-W{w['week_no']}"
            s.add(db.PlanWeek(plan_week_id=pw_id, plan_id=plan_id, week_no=w["week_no"],
                              phase=w["phase"], planned_km=w["planned_km"],
                              key_goal=f"关键课 {TEMPLATE_KEY} 次".replace(TEMPLATE_KEY, "2"),
                              is_dropped=False))
            for sess in w["sessions"]:
                s.add(db.Session(
                    session_id=uuid.uuid4().hex[:16], plan_week_id=pw_id, plan_id=plan_id,
                    week_no=w["week_no"], seq=sess["seq"],
                    date=start + timedelta(days=7 * (w["week_no"] - 1) + sess["offset"] - 1),
                    type=sess["type"], is_key=sess["is_key"], pace_zone=sess["pace_zone"],
                    target_km=sess["target_km"], target_pace=sess["target_pace"],
                    source="template", status="planned"))
        # 幂等：同 profile 哈希的旧计划作废
        s.query(db.Plan).filter(db.Plan.user_id == user_id,
                                db.Plan.profile_hash != result["profile_hash"]).update(
            {"status": "superseded"})
        s.commit()
        vdot, cap, goal_race = user.vdot, result["cap"], user.goal_race
    _take_snapshot(plan_id, 1)
    with db.SessionLocal.begin() as s:
        s.add(db.Event(name="plan_generated", user_id=user_id,
                       payload={"plan_id": plan_id, "vdot": vdot,
                                "session_cap": cap, "rejected": False}))
    return {"plan_id": plan_id, "vdot": vdot, "cap": cap,
            "weeks": len(result["weeks"]), "zones": pace_zones_display(vdot)}


TEMPLATE_KEY = "2"


@app.get("/v1/today")
def today_card(user_id: str):
    """今日卡聚合：主课 + 原因行 + 天气行 + 三按钮状态 + 重排建议/劝降提示。"""
    today = date.today()
    with db.SessionLocal() as s:
        user = s.get(db.User, user_id)
        if not user:
            raise HTTPException(404, "用户不存在")
        plan = _active_plan(s, user_id)
        week_no = min(_current_week_no(plan, today), 16)
        # AI 微调层：本周首次查看时，让 LLM 在硬边界内微调课表（幂等，每周最多一次）
        week_end = plan.start_date + timedelta(days=7 * week_no - 1)
        bad_days = []
        d = today
        while d <= week_end:
            wd = get_week_weather(user.lat, user.lon, d).get(d.isoformat())
            if wd and wd.hard_constraint:
                bad_days.append(d)
            d += timedelta(days=1)
        rep = s.query(db.StatusReport).filter_by(user_id=user_id) \
            .order_by(db.StatusReport.date.desc()).first()
        maybe_tune_week(user, plan, week_no, bad_days,
                        rep.soreness_part if rep else "none")
        s.expire_all()      # 微调在独立事务里提交，让后续查询读到调完的最新课表
        pw = s.query(db.PlanWeek).filter_by(plan_id=plan.plan_id, week_no=week_no).first()
        sessions = _sessions_of_week(s, plan.plan_id, week_no)
        logs = {l.session_id: l for l in s.query(db.SessionLog).all()
                if l.session_id in {x.session_id for x in sessions}}
        # 主课：今天 planned 的课，取最关键的；否则取本周最近一节未上的课
        todays = [x for x in sessions if x.date == today and x.status == "planned"]
        main = None
        if todays:
            main = sorted(todays, key=lambda x: not x.is_key)[0]
        else:
            future = [x for x in sessions if x.date > today and x.status == "planned"]
            main = future[0] if future else None
        done_key = sum(1 for x in sessions if x.is_key and
                       (logs.get(x.session_id) and logs[x.session_id].status in ("full", "partial")))
        key_total = sum(1 for x in sessions if x.is_key)
        # 跑量：累计（全部 full/partial）+ 本周（当前周 full/partial），一次 join 算完
        log_rows = s.query(db.SessionLog, db.Session).join(
            db.Session, db.Session.session_id == db.SessionLog.session_id).filter(
            db.Session.plan_id == plan.plan_id,
            db.SessionLog.status.in_(("full", "partial"))).all()
        total_km = round(sum(l.actual_km or 0.0 for l, _ in log_rows), 1)
        week_km = round(sum((l.actual_km or 0.0) for l, se in log_rows
                            if se.week_no == week_no), 1)
        # 天气行
        wx_line = ""
        hard_days = []
        if main:
            wk = get_week_weather(user.lat, user.lon, main.date).get(main.date.isoformat())
            if wk:
                if wk.hard_constraint:
                    wx_line = f"今天{('有雷电/大雨' if wk.is_thunder or wk.precip >= 10 else '空气质量不佳')}，建议改室内或调整"
                elif wk.hot_soft:
                    wx_line = f"体感 {wk.apparent_max:.0f}℃，强度自动降一档、时长削 20%"
                else:
                    wx_line = f"天气适宜训练（体感 {wk.apparent_max:.0f}℃）"
            for i in range(1, 4):
                wd = get_week_weather(user.lat, user.lon, today + timedelta(days=i)).get(
                    (today + timedelta(days=i)).isoformat())
                if wd and wd.hard_constraint:
                    hard_days.append(_dow(today + timedelta(days=i)))
        # 原因行：LLM 个性化解释，规则模板兜底（数字全部来自上面的计算）
        reason = ""
        reason_src = None
        if main:
            rfacts = {"is_key": bool(main.is_key), "key_done": done_key, "key_total": key_total}
            r_out, reason_src = render_scene("reason_line", rfacts, reason_line_template(rfacts))
            reason = r_out["reason"] if isinstance(r_out, dict) else r_out
        # 重排建议触发：今天天气硬约束 或 已上报酸痛
        suggestion = None
        report = s.query(db.StatusReport).filter_by(user_id=user_id, date=today).first()
        sore = report and report.soreness_part != "none"
        if main:
            wk = get_week_weather(user.lat, user.lon, main.date).get(main.date.isoformat())
            if (wk and wk.hard_constraint) or sore:
                suggestion = {"trigger_src": "body" if sore else "weather",
                              "message": ("你上报了身体酸痛" if sore else "今天的天气不适合户外") +
                                         "，点此查看调整方案"}
        # 劝降提示
        degrade = _degrade_check(s, plan, week_no)
        # 恢复护栏：连续两天睡眠 <5 或疲劳 >7 → 提示（只提示，不自动改课）
        recovery_alert = _recovery_alert(s, user_id, main)
        days_to_race = (plan.start_date + timedelta(days=16 * 7 - 1) - today).days
        if reason_src:
            db.track("llm_explain", user_id, scene="reason_line", source=reason_src)
        return {
            "user_id": user_id, "plan_id": plan.plan_id, "week_no": week_no,
            "week_phase": pw.phase if pw else "load", "is_dropped": pw.is_dropped if pw else False,
            "weekday": _dow(today), "days_to_race": max(0, days_to_race),
            "vdot": user.vdot, "city": user.city, "total_km": total_km, "week_km": week_km,
            "today_has_run": bool(todays),
            "main_session": _session_dict(main, logs.get(main.session_id)) if main else None,
            "reason_line": reason, "weather_line": wx_line, "hard_days": hard_days,
            "suggestion": suggestion, "degrade": degrade, "recovery_alert": recovery_alert,
            "week_progress": {"key_done": done_key, "key_total": key_total,
                              "done": sum(1 for x in sessions if logs.get(x.session_id)
                                          and logs[x.session_id].status in ("full", "partial")),
                              "total": sum(1 for x in sessions if x.status != "cancelled_by_rearrange"
                                           and x.status != "cancelled_before_week")},
        }


def _degrade_check(s, plan: db.Plan, week_no: int) -> dict | None:
    """连续 3 周关键课完成率 <50% → 主动劝降（只提示，决定权在用户）。"""
    if week_no < 4:
        return None
    weeks = []
    for wn in range(week_no - 2, week_no + 1):
        sessions = _sessions_of_week(s, plan.plan_id, wn)
        if not sessions:
            return None
        logs = {l.session_id: l.status for l in s.query(db.SessionLog).all()
                if l.session_id in {x.session_id for x in sessions}}
        key_plan = sum(1 for x in sessions if x.is_key)
        key_done = sum(1 for x in sessions if x.is_key and logs.get(x.session_id) in ("full", "partial"))
        weeks.append({"key_plan": key_plan, "key_done": key_done, "week_no": wn})
    if should_suggest_degrade(weeks):
        total_plan = sum(w["key_plan"] for w in weeks)
        total_done = sum(w["key_done"] for w in weeks)
        facts = {"total_done": total_done, "total_plan": total_plan,
                 "race": "全马" if plan.goal_race == "full" else "半马"}
        advice, src = render_scene("degrade_advice", facts, degrade_advice_template(facts))
        db.track("llm_explain", plan.user_id, scene="degrade_advice", source=src)
        msg = advice["advice"] if isinstance(advice, dict) else advice
        return {"triggered": True, "message": msg}
    return None


def _recovery_alert(s, user_id: str, main) -> dict | None:
    """恢复护栏：连续两个上报日睡眠 <5 或疲劳 >7 → 今日卡提示（只提示，不自动改课）。"""
    cutoff = date.today() - timedelta(days=13)
    rows = s.query(db.StatusReport).filter(
        db.StatusReport.user_id == user_id,
        db.StatusReport.date >= cutoff).all()
    by_day: dict = {}
    for r in rows:
        if r.date not in by_day or r.id > by_day[r.date].id:
            by_day[r.date] = r
    days = sorted(by_day.values(), key=lambda r: r.date)[-2:]
    if len(days) < 2 or (days[1].date - days[0].date) != timedelta(days=1):
        return None   # 必须是日历上连续两天
    if all(r.sleep_score < 5 for r in days):
        kind = "sleep"
    elif all(r.fatigue_score > 7 for r in days):
        kind = "fatigue"
    else:
        return None
    # 今天的课是强度课（T/I）时给「降一档」话术，轻松跑日给温和版
    hard = bool(main) and main.pace_zone in ("threshold", "interval")
    if kind == "sleep":
        msg = ("连续两天睡眠严重不足，今天强度课建议降一档，或改轻松跑/休息"
               if hard else "连续两天睡眠严重不足，今天以恢复为主，别硬撑")
    else:
        msg = ("连续两天自评疲劳偏高，今天强度课建议降一档，优先恢复"
               if hard else "连续两天自评疲劳偏高，今天降低训练量，优先恢复")
    return {"type": kind, "message": msg}


SORENESS_CN = {"knee": "膝盖", "ankle": "脚踝", "calf": "小腿", "hip": "髋部", "none": ""}


@app.get("/v1/calendar")
def calendar_view(user_id: str, month: str):
    """月历视图：每天标注跑量/配速、休息日，以及停练原因（伤病 / 天气 / 力量训练）。"""
    try:
        month_start = date.fromisoformat(f"{month}-01")
    except ValueError:
        raise HTTPException(400, "month 格式应为 YYYY-MM")
    if month_start.month == 12:
        month_end = date(month_start.year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(month_start.year, month_start.month + 1, 1) - timedelta(days=1)
    today = date.today()
    with db.SessionLocal() as s:
        user = s.get(db.User, user_id)
        if not user:
            raise HTTPException(404, "用户不存在")
        plan = _active_plan(s, user_id)
        sess_rows: list = []
        logs: dict = {}
        if plan:
            sess_rows = s.query(db.Session).filter(
                db.Session.plan_id == plan.plan_id,
                db.Session.date >= month_start, db.Session.date <= month_end).all()
            ids = {x.session_id for x in sess_rows}
            logs = {l.session_id: l for l in s.query(db.SessionLog).all()
                    if l.session_id in ids}
        # 当月身体上报（伤病停练的判断依据）
        reps = {r.date: r for r in s.query(db.StatusReport).filter(
            db.StatusReport.user_id == user_id,
            db.StatusReport.date >= month_start, db.StatusReport.date <= month_end)}
        # 天气硬约束日（get_week_weather 带周缓存，逐日取不会放大请求）
        wx_bad: dict = {}
        d = month_start
        while d <= month_end:
            wd = get_week_weather(user.lat, user.lon, d).get(d.isoformat())
            if wd and wd.hard_constraint:
                wx_bad[d] = "雷电/大雨" if (wd.is_thunder or wd.precip >= 10) else "空气质量不佳"
            d += timedelta(days=1)
        by_day: dict = {}
        for x in sess_rows:
            by_day.setdefault(x.date, []).append(x)
        from server.tune import TYPE_CN
        days = []
        d = month_start
        while d <= month_end:
            entry = {"date": d.isoformat(), "kind": "none", "km": None,
                     "pace": "", "reason": "", "type_cn": ""}
            items = by_day.get(d, [])
            logged = [(x, logs[x.session_id]) for x in items if x.session_id in logs]
            if logged:
                x, lg = sorted(logged, key=lambda p: p[1].logged_at)[-1]
                if lg.status in ("full", "partial"):
                    entry.update(kind="run", km=round(lg.actual_km, 1),
                                 pace=lg.actual_pace, type_cn=TYPE_CN.get(x.type, x.type))
                elif lg.skip_kind == "strength":
                    entry.update(kind="strength", type_cn=TYPE_CN.get(x.type, x.type))
                else:
                    rep = reps.get(d)
                    part = SORENESS_CN.get(rep.soreness_part, "") if rep else ""
                    if part:
                        entry.update(kind="injury", reason=f"伤病停练·{part}",
                                     type_cn=TYPE_CN.get(x.type, x.type))
                    elif d in wx_bad:
                        entry.update(kind="weather", reason=wx_bad[d],
                                     type_cn=TYPE_CN.get(x.type, x.type))
                    else:
                        entry.update(kind="skip", type_cn=TYPE_CN.get(x.type, x.type))
            elif items:
                x = sorted(items, key=lambda v: not v.is_key)[0]
                if x.type == "rest" or x.target_km == 0:
                    entry["kind"] = "rest"
                elif d > today:
                    entry.update(kind="future", type_cn=TYPE_CN.get(x.type, x.type))
                else:
                    entry.update(kind="missed", type_cn=TYPE_CN.get(x.type, x.type))
            days.append(entry)
            d += timedelta(days=1)
        return {"month": month, "today": today.isoformat(), "days": days}


@app.post("/v1/session/{session_id}/log")
def log_session(session_id: str, body: LogIn):
    """写 session_log；skipped / 超额完成 / 明显不足时自动触发候选预生成（不展示，先算好）。"""
    if body.status not in ("full", "partial", "skipped"):
        raise HTTPException(400, "status 必须是 full/partial/skipped")
    with db.SessionLocal.begin() as s:
        sess = s.get(db.Session, session_id)
        if not sess:
            raise HTTPException(404, "课次不存在")
        # 总用时反算配速：打卡弹窗填了 时分秒 但没填配速时，用 时长/里程 换算
        actual_pace = body.actual_pace
        if not actual_pace and body.duration_sec > 0 and body.actual_km > 0:
            from server.knowledge.rules import sec_to_pace
            actual_pace = sec_to_pace(round(body.duration_sec / body.actual_km))
        s.add(db.SessionLog(session_id=session_id, status=body.status,
                            actual_km=body.actual_km, actual_pace=actual_pace,
                            duration_sec=body.duration_sec, rpe=body.rpe,
                            skip_kind=body.skip_kind if body.status == "skipped" else ""))
        sess.status = {"full": "done", "partial": "partial", "skipped": "skipped"}[body.status]
        s.add(db.Event(name="session_done", user_id=sess.plan_id and _uid_of(s, sess.plan_id),
                       payload={"session_id": session_id, "degree": body.status,
                                "actual_km": body.actual_km, "week_no": sess.week_no,
                                "is_key": sess.is_key}))
        plan = s.get(db.Plan, sess.plan_id)
        user = s.get(db.User, plan.user_id) if plan else None
        # 超额完成：实际里程明显超过目标（>110%），本周刺激已够，引导压缩剩余课表
        over_done = (body.status == "full" and body.actual_km > sess.target_km * 1.1)
        # 大幅超额（>3km）：能力可能已超出当前计划，进阶询问交给前端二次确认
        advance_prompt = (body.status == "full"
                          and body.actual_km - sess.target_km > 3.0)
        # 明显不足：部分完成但低于目标 50%，缺口需要本周内重排补上
        under_done = (body.status == "partial" and sess.target_km > 0
                      and body.actual_km < sess.target_km * 0.5)
        # 能力重估：每 4 周，用实际配速回写 VDOT（只刷新未开始的周）。
        # 大幅超额时先不自动改——进阶与否交给用户二次确认（/v1/user/advance）
        vdot_note = None
        if not advance_prompt and body.status in ("full", "partial") \
                and body.actual_km > 0 and actual_pace and user:
            try:
                m, sec = actual_pace.split(":")
                pace_sec = int(m) * 60 + int(sec)
                new_v = implied_vdot(body.actual_km, pace_sec * body.actual_km)
                if abs(new_v - user.vdot) >= 1.5:
                    old = user.vdot
                    user.vdot = new_v
                    _refresh_future_weeks(s, plan, sess.week_no, new_v)
                    direction = "上调至" if new_v > old else "调整为"
                    vdot_note = f"根据你的实际表现，能力值已从 VDOT {old} {direction} {new_v}，未来周计划已刷新"
            except Exception:
                pass
        # 事务块外预生成重排要用到，先取值（离开 session 后访问会 DetachedInstanceError）
        plan_id, week_no = sess.plan_id, sess.week_no
    out = {"ok": True}
    if vdot_note:
        out["vdot_update"] = vdot_note
    if advance_prompt:
        out["advance_prompt"] = True
        out["advance_pace"] = actual_pace
    if over_done:
        out["over_done"] = True
        out["over_note"] = f"超额完成 {body.actual_km:g} km，状态很好。本周刺激已经够了，看看要不要压缩剩余课表。"
    if under_done:
        out["under_note"] = "完成量不足目标的一半，缺口已记入，可在重排里补齐。"
    if body.status == "skipped" or over_done or under_done:
        # 自动触发候选预生成（不展示）
        try:
            with db.SessionLocal() as s2:
                plan = s2.get(db.Plan, plan_id)
                user = s2.get(db.User, plan.user_id)
                ctx = _build_ctx(s2, user, plan, week_no, "schedule")
                _main, _alt, _audit = rearrange(ctx)
                out["rearrange_ready"] = True
        except Exception:
            out["rearrange_ready"] = False
    if over_done or under_done:
        # AI 响应式微调：RAG 检索 + 案例召回 → LLM 提建议 → 硬校验后落地
        # 设计边界：AI 只动非关键课 ±10%（validate_ops 把关），减量周 AI 不干预
        try:
            with db.SessionLocal() as s3:
                plan = s3.get(db.Plan, plan_id)
                user = s3.get(db.User, plan.user_id)
                week_no3 = week_no
                sess_row = s3.get(db.Session, session_id)
                target_km = sess_row.target_km if sess_row else 0
                pw = s3.query(db.PlanWeek).filter_by(plan_id=plan_id, week_no=week_no3).first()
                if pw and pw.phase != "taper" and target_km > 0:
                    rows = (s3.query(db.Session, db.SessionLog)
                            .join(db.SessionLog, db.SessionLog.session_id == db.Session.session_id)
                            .filter(db.Session.plan_id == plan_id,
                                    db.SessionLog.logged_at >= datetime.utcnow() - timedelta(days=14))
                            .order_by(db.SessionLog.logged_at).all())
                    recent = [{"date": sl.logged_at.date().isoformat(), "type": se.type,
                               "target": se.target_km, "actual": sl.actual_km,
                               "status": sl.status} for se, sl in rows]
                    trigger = "over_done" if over_done else "under_done"
                    dev_ratio = body.actual_km / target_km
                    from server.tune import tune_after_deviation
                    tune_result = tune_after_deviation(user, plan, week_no3, trigger, dev_ratio, recent)
                    if tune_result.get("note"):
                        out["ai_tune_note"] = tune_result["note"]
        except Exception:
            pass
    return out


def _uid_of(s, plan_id: str) -> str:
    plan = s.get(db.Plan, plan_id)
    return plan.user_id if plan else "unknown"


def _refresh_future_weeks(s, plan: db.Plan, from_week_no: int, new_vdot: int) -> None:
    """只刷新尚未开始的周，已执行的周永远不动。"""
    from server.knowledge.rules import pace_zones, sec_to_pace
    zones = pace_zones(new_vdot)
    today = date.today()
    rows = s.query(db.Session).filter(db.Session.plan_id == plan.plan_id,
                                      db.Session.week_no > from_week_no,
                                      db.Session.date >= today,
                                      db.Session.status == "planned").all()
    for r in rows:
        lo, hi = zones.get(r.pace_zone, zones["easy"])
        r.target_pace = f"{sec_to_pace(lo)}–{sec_to_pace(hi)}" if lo != hi else sec_to_pace(lo)
    plan.vdot = new_vdot


@app.post("/v1/status/report")
def status_report(body: StatusReportIn):
    """身体状态上报（打卡即上报，一次点击）。14 天内 ≥2 次伤痛 → 本周自动强制降载并留痕。"""
    forced_deload = False
    with db.SessionLocal.begin() as s:
        s.add(db.StatusReport(user_id=body.user_id, date=date.today(),
                              soreness_part=body.soreness_part, muscle_sore=body.muscle_sore,
                              breath_state=body.breath_state,
                              sleep_score=body.sleep_score,
                              fatigue_score=body.fatigue_score, entry=body.entry))
        s.add(db.Event(name="status_reported", user_id=body.user_id,
                       payload=body.model_dump()))
        s.flush()                                    # autoflush=False：先落盘再统计，否则本次上报漏计
        injury_14d = s.query(db.StatusReport).filter(
            db.StatusReport.user_id == body.user_id,
            db.StatusReport.date >= date.today() - timedelta(days=14),
            db.StatusReport.soreness_part != "none").count()
        if body.soreness_part != "none" and injury_14d >= 2:
            forced_deload = _apply_deload_week(s, body.user_id)
    suggest = None
    if body.soreness_part != "none":
        if injury_14d >= 2:
            suggest = ("你近 14 天伤痛上报较集中，本周已自动进入降载模式（强度课已取消、只保留轻松跑）。"
                       "若持续不适，请就医" + ("" if forced_deload else "（本周已是降载形态）"))
        else:
            suggest = "已收到你的状态上报，今天的强度课建议降级或调整，点此查看方案"
    return {"ok": True, "injury_reports_14d": injury_14d, "suggestion": suggest,
            "forced_deload": forced_deload}


@app.post("/v1/stretch")
def stretch_advice(body: StretchIn):
    """跑后拉伸建议：LLM 生成，规则模板兜底（数字全部来自 facts，过数值断言）。"""
    from server.explain import render_scene, stretch_advice_template
    facts = {"status": body.status, "type_cn": body.type_cn, "target_km": body.target_km}
    out, src = render_scene("stretch_advice", facts, stretch_advice_template(facts))
    text = out["stretch"] if isinstance(out, dict) else out
    db.track("llm_explain", body.user_id, scene="stretch_advice", source=src)
    return {"text": text, "source": src}


@app.post("/v1/assistant/chat")
def assistant_chat(body: AssistantIn, request: Request):
    """AI 跑步助手「跑小助」：RAG 召回知识条目 → LLM 生成带出处回答 → 知识拼接兜底。

    只解释不建议改课（排课主权在规则引擎）；每用户限速 20 次/5 分钟。
    """
    if not body.message.strip():
        raise HTTPException(400, "说点什么再问～")
    if _hit_rate_limit(f"assistant|{body.user_id}"):
        raise HTTPException(429, "问得太快啦，喝口水 5 分钟后再来")
    from server import assistant
    out = assistant.answer(body.user_id, body.message)
    db.track("llm_explain", body.user_id, scene="assistant_chat", source=out["from"])
    return out


def _apply_deload_week(s, user_id: str) -> bool:
    """红线三触发：本周剩余课全部改为轻松跑（×0.6 量），写护栏留痕。返回是否真的改了。"""
    plan = s.query(db.Plan).filter_by(user_id=user_id, status="active").first()
    if not plan:
        return False
    today = date.today()
    week_no = min(_current_week_no(plan, today), 16)
    rows = s.query(db.Session).filter(
        db.Session.plan_id == plan.plan_id, db.Session.week_no == week_no,
        db.Session.status == "planned", db.Session.date >= today).all()
    changed = [r for r in rows if r.pace_zone != "easy" or r.is_key]
    if not changed:
        pw = s.query(db.PlanWeek).filter_by(plan_id=plan.plan_id, week_no=week_no).first()
        return bool(pw and pw.is_dropped)
    for r in rows:
        before = {"type": r.type, "pace_zone": r.pace_zone, "target_km": r.target_km,
                  "is_key": r.is_key}
        r.type = "easy_run"
        r.pace_zone = "easy"
        r.target_km = round(r.target_km * 0.6, 1)
        r.is_key = False                # 降载后不再有强度刺激，关键课标记一并清掉
        r.source = "rearranged"
        from server.knowledge.rules import pace_zones, sec_to_pace
        lo, hi = pace_zones(plan.vdot)["easy"]
        r.target_pace = f"{sec_to_pace(lo)}–{sec_to_pace(hi)}"
        s.add(db.GuardAudit(plan_id=plan.plan_id, week_no=week_no, strategy="status_report",
                            hit_rule="INJURY_WINDOW",
                            before_json=before,
                            after_json={"type": r.type, "pace_zone": r.pace_zone,
                                        "target_km": r.target_km},
                            action="patch",
                            reason_user="近 14 天伤痛上报 ≥2 次，本周强制降载（只保留轻松跑）"))
    pw = s.query(db.PlanWeek).filter_by(plan_id=plan.plan_id, week_no=week_no).first()
    if pw:
        pw.is_dropped = True                       # 标记降载，不删课次，保留原计划对照
        # 周里程跟课表一起变：页头「计划 N km」必须反映降载后的真实剩余量
        week_rows = s.query(db.Session).filter(
            db.Session.plan_id == plan.plan_id, db.Session.week_no == week_no,
            db.Session.status.notin_(("cancelled_by_rearrange", "cancelled_before_week"))).all()
        pw.planned_km = round(sum(x.target_km for x in week_rows), 1)
    s.add(db.Event(name="guard_blocked", user_id=user_id,
                   payload={"hit_rule": "INJURY_WINDOW", "action": "patch",
                            "week_no": week_no, "auto": True}))
    return True


@app.post("/v1/rearrange/preview")
def rearrange_preview(body: PreviewIn):
    """重排预览：只读，不落成正式方案。返回主方案 + 备选 + 代价 + 护栏留痕 + 解释文案。"""
    with db.SessionLocal() as s:
        user = s.get(db.User, body.user_id)
        if not user:
            raise HTTPException(404, "用户不存在")
        plan = _active_plan(s, body.user_id)
        ctx = _build_ctx(s, user, plan, body.week_no, body.trigger_src)
        main, alt, audit = rearrange(ctx)
        # 护栏审计全量落库（pass 也记，方便算「差点被拦」）
        for a in audit:
            s.add(db.GuardAudit(plan_id=plan.plan_id, week_no=body.week_no,
                                strategy=a["strategy"], hit_rule=a["hit_rule"],
                                action=a["action"], reason_user=a["reason_user"]))
        s.add(db.Event(name="rearrange_triggered", user_id=body.user_id,
                       payload={"trigger_src": body.trigger_src, "week_no": body.week_no,
                                "done_cnt": len(ctx.remaining) - ctx.avail_days_left,
                                "avail_days_left": ctx.avail_days_left}))
        if main is None:
            s.commit()
            return {"main": None, "alt": None, "guard_notes": audit,
                    "message": "本周没有可调整的空间，保持原计划"}
        explain_main, src = render_explain(_facts_for(main, ctx))
        event_id = uuid.uuid4().hex[:16]
        candidates = [_cand_json(main, ctx)]
        if alt:
            candidates.append(_cand_json(alt, ctx))
        s.add(db.RearrangeEvent(event_id=event_id, user_id=body.user_id, plan_id=plan.plan_id,
                                week_no=body.week_no, trigger_src=body.trigger_src,
                                context_hash=ctx.context_hash(), candidates_json=candidates,
                                chosen=main.strategy, result="pending",
                                delta_km=main.delta_km,
                                explain_json=explain_main | {"source": src}))
        s.add(db.Event(name="rearrange_shown", user_id=body.user_id,
                       payload={"strategy": main.strategy, "delta_km": main.delta_km,
                                "has_explain": True, "candidate_cnt": len(candidates)}))
        s.commit()
        alt_payload = None
        if alt:
            alt_explain, alt_src = render_explain(_facts_for(alt, ctx))
            alt_payload = {"strategy": alt.strategy, "description": alt.description,
                           "delta_km": alt.delta_km, "sessions": _cand_sessions(alt),
                           "explain": alt_explain | {"source": alt_src}}
        # AI 推荐人：只在有备选时启用。只影响「推荐展示」——候选生成与落地仍是规则引擎。
        ai_rec = None
        if alt_payload:
            try:
                from server.explain import render_recommend
                from server import rag as _rag
                trigger_cn = {"weather": "恶劣天气", "body": "身体状态",
                              "schedule": "时间不够"}.get(ctx.trigger_src, ctx.trigger_src)
                rec_facts = {
                    "trigger": trigger_cn, "week_no": ctx.week_no,
                    "avail_days_left": ctx.avail_days_left,
                    "soreness_today": ctx.soreness_today,
                    "prev_week_km": round(ctx.prev_week_km, 1),
                    "planned_week_km": round(ctx.planned_week_km, 1),
                    "main": {"strategy": main.strategy, "description": main.description,
                             "delta_km": main.delta_km,
                             "stimulus_pct": _facts_for(main, ctx)["stimulus_pct"],
                             "guard_action": main.guard_action},
                    "alt": {"strategy": alt.strategy, "description": alt.description,
                            "delta_km": alt.delta_km,
                            "stimulus_pct": _facts_for(alt, ctx)["stimulus_pct"],
                            "guard_action": alt.guard_action},
                }
                kb_hits = _rag.retrieve(
                    f"重排 {trigger_cn} {main.strategy} {alt.strategy} 训练计划调整原则")
                rec_facts["principles"] = [h["text"] for h in kb_hits]
                rec, rsrc = render_recommend(rec_facts)
                if rec:
                    ai_rec = rec | {"source": rsrc}
                db.track("rearrange_ai_recommend", body.user_id,
                         choice=rec["choice"] if rec else None, source=rsrc,
                         kb_ids=[h["id"] for h in kb_hits],
                         strategies=[main.strategy, alt.strategy])
            except Exception:
                ai_rec = None
        return {"event_id": event_id, "main": {
            "strategy": main.strategy, "description": main.description,
            "delta_km": main.delta_km, "sessions": _cand_sessions(main),
            "guard_action": main.guard_action, "guard_note": main.guard_note,
            "explain": explain_main | {"source": src},
        }, "alt": alt_payload, "guard_notes": audit,
            "ai_recommendation": ai_rec,
            "context_hash": ctx.context_hash()}


def _cand_sessions(c: Candidate) -> list[dict]:
    out = []
    for s2 in sorted(c.sessions, key=lambda x: (x["date"], x.get("seq", 0))):
        out.append({"session_id": s2["session_id"], "date": s2["date"],
                    "dow": _dow(date.fromisoformat(s2["date"])),
                    "type": s2["type"], "type_cn": _zone_cn(s2["pace_zone"]),
                    "is_key": s2.get("is_key", False), "pace_zone": s2["pace_zone"],
                    "target_km": round(s2["target_km"], 1), "status": s2["status"],
                    "note": s2.get("note", "")})
    return out


def _cand_json(c: Candidate, ctx: RearrangeContext) -> dict:
    return {"strategy": c.strategy, "description": c.description, "delta_km": c.delta_km,
            "sessions": c.sessions, "score": round(
                0.55 * __import__("server.adapt", fromlist=["p_complete"]).p_complete(ctx, c.sessions)
                + 0.30 * __import__("server.adapt", fromlist=["key_stimulus_kept"]).key_stimulus_kept(ctx, c.sessions)
                + 0.15 * __import__("server.adapt", fromlist=["safety_margin"]).safety_margin(ctx, c.sessions), 3)}


@app.post("/v1/rearrange/{event_id}/decision")
def rearrange_decision(event_id: str, body: DecisionIn):
    """接受 → 把方案落到 plan_week（快照不动，误接纳可回滚）；拒绝/自己改只记录。"""
    t0 = datetime.utcnow()
    with db.SessionLocal.begin() as s:
        ev = s.get(db.RearrangeEvent, event_id)
        if not ev:
            raise HTTPException(404, "重排事件不存在")
        if ev.result != "pending":
            return {"ok": True, "deduped": True}      # context_hash 幂等：重复请求不产生第二条
        ev.result = body.result
        ev.decision_ms = body.decision_ms or int((datetime.utcnow() - t0).total_seconds() * 1000)
        s.add(db.Event(name="rearrange_result", user_id=ev.user_id,
                       payload={"result": body.result, "decision_ms": ev.decision_ms,
                                "strategy": ev.chosen}))
        if body.result == "accept":
            chosen = ev.candidates_json[1] if (body.use_alt and len(ev.candidates_json) > 1) \
                else ev.candidates_json[0]
            # 只改尚未开始的 session；原始版本留在 snapshot，回滚 = 从快照重建
            for cs in chosen["sessions"]:
                row = s.get(db.Session, cs["session_id"])
                if not row or row.status != "planned":
                    continue
                row.date = date.fromisoformat(cs["date"])
                row.type = cs["type"]
                row.pace_zone = cs["pace_zone"]
                row.target_km = cs["target_km"]
                row.status = cs["status"]
                row.source = "rearranged"
            # noop = 「保持原计划不变」，接受它不需要也不应该动周标记
        return {"ok": True, "applied": body.result == "accept"}


@app.get("/v1/plan/{plan_id}/week/{week_no}")
def week_review(plan_id: str, week_no: int):
    """周审视页：本周课表 + 完成情况 + 下周预告 + 执行率（分母取周初快照）。"""
    with db.SessionLocal() as s:
        plan = s.get(db.Plan, plan_id)
        if not plan:
            raise HTTPException(404, "计划不存在")
        pw = s.query(db.PlanWeek).filter_by(plan_id=plan_id, week_no=week_no).first()
        if not pw:
            raise HTTPException(404, "周不存在")
        sessions = _sessions_of_week(s, plan_id, week_no)
        logs = {l.session_id: l for l in s.query(db.SessionLog).all()
                if l.session_id in {x.session_id for x in sessions}}
        snap = s.query(db.PlanWeekSnapshot).filter_by(plan_week_id=pw.plan_week_id).first()
        planned_denom = len([x for x in (snap.snapshot_json.get("sessions", []) if snap else [])
                             if x.get("status") != "cancelled_before_week"])
        done = sum(1 for x in sessions if logs.get(x.session_id)
                   and logs[x.session_id].status in ("full", "partial"))
        current_total = sum(1 for x in sessions
                            if x.status not in ("cancelled_by_rearrange", "cancelled_before_week"))
        # 下周预告
        nxt = []
        if week_no < 16:
            nxt = [_session_dict(x) for x in _sessions_of_week(s, plan_id, week_no + 1)][:4]
        # AI 执行点评：LLM 个性化，规则模板兜底（数字全部来自上面的统计）
        denom = planned_denom or current_total
        cfacts = {"week_no": week_no, "phase": pw.phase,
                  "rate_pct": round(done / (denom or 1) * 100), "done": done, "planned": denom}
        comment, csrc = render_scene("week_comment", cfacts, week_comment_template(cfacts))
        db.track("llm_explain", plan.user_id, scene="week_comment", source=csrc)
        # 训练天数 > 课次上限（6/7 天上限 5 节）→ 确定性的补充提醒，不依赖 LLM
        extra_note = None
        user = s.get(db.User, plan.user_id)
        if user:
            from server.knowledge.rules import session_cap
            cap = session_cap(user.avail_days)
            if user.avail_days > cap:
                extra_note = (f"你设置了每周 {user.avail_days} 天训练，课表最多排 {cap} 节课"
                              f"（每周超过 5 节课对新手受伤风险大于收益）；多出来的 "
                              f"{user.avail_days - cap} 天适合安排力量训练或完全休息。")
        # 回填 AI 案例效果：周结束后按执行率给 pending 案例打 good/bad，供相似情境召回
        week_end = plan.start_date + timedelta(days=7 * week_no - 1)
        if date.today() > week_end:
            try:
                from server import rag as _rag
                _rag.backfill_outcome(plan.user_id, plan_id, week_no,
                                      round(done / (planned_denom or current_total or 1), 3))
            except Exception:
                pass
        return {"week_no": week_no, "phase": pw.phase, "planned_km": round(pw.planned_km, 1),
                "is_dropped": pw.is_dropped,
                "bmi_conservative": bool(getattr(plan, "bmi_conservative", False)),
                "is_current": week_no == _current_week_no(plan, date.today()),
                "comment": {"text": comment["comment"] if isinstance(comment, dict) else comment,
                            "source": csrc, "note": extra_note},
                "adherence": {"done": done,
                              "planned_snapshot": planned_denom or current_total,
                              "rate": round(done / (planned_denom or current_total or 1), 3)},
                "sessions": [_session_dict(x, logs.get(x.session_id)) for x in sessions],
                "next_week_preview": nxt}


@app.post("/v1/plan/{plan_id}/degrade-goal")
def degrade_goal(plan_id: str, body: DegradeIn):
    """劝降三选项：half 改半马重排未来周 / defer 延期一期 / insist 保持但本周降载。"""
    if body.to not in ("half", "defer", "insist"):
        raise HTTPException(400, "to 必须是 half/defer/insist")
    with db.SessionLocal.begin() as s:
        plan = s.get(db.Plan, plan_id)
        if not plan:
            raise HTTPException(404, "计划不存在")
        user = s.get(db.User, plan.user_id)
        plan.degrade_choice = body.to
        today = date.today()
        week_no = _current_week_no(plan, today)
        s.add(db.Event(name="goal_degrade_shown", user_id=plan.user_id,
                       payload={"choice": body.to, "week_no": week_no}))
        if body.to == "insist":
            # 不能只打标签：课表要真的切成轻松跑，不然「本周降载」成了空话
            _apply_deload_week(s, plan.user_id)
            return {"ok": True, "message": "已确认。本周自动进入降载模式，下周按原计划继续"}
        if body.to == "defer":
            plan.start_date = today - timedelta(days=today.weekday())
            return {"ok": True, "message": "赛事已顺延到下一期，计划从本周重新开始"}
        if body.to == "half":
            plan.goal_race = "half"
            user.goal_race = "half"
            plan.target_distance = "half_marathon"
            # 半马：剩余周减量（简易处理：未来周 planned_km × 0.6，长距离上限 16km）
            rows = s.query(db.Session).filter(
                db.Session.plan_id == plan_id, db.Session.week_no >= week_no,
                db.Session.status == "planned").all()
            for r in rows:
                if r.type == "long_run":
                    r.target_km = min(r.target_km, 16.0)
                else:
                    r.target_km = round(r.target_km * 0.7, 1)
            pws = s.query(db.PlanWeek).filter(db.PlanWeek.plan_id == plan_id,
                                              db.PlanWeek.week_no >= week_no).all()
            for pw in pws:
                pw.planned_km = round(pw.planned_km * 0.7, 1)
            return {"ok": True, "message": "目标已调整为半马（21.1km），剩余计划已重新生成"}


# ================================================================ 内部任务
def _take_snapshot(plan_id: str, week_no: int) -> None:
    """每周一 00:00 冻结本周课表。执行率分母的唯一来源，此后永不修改。"""
    with db.SessionLocal.begin() as s:
        pw = s.query(db.PlanWeek).filter_by(plan_id=plan_id, week_no=week_no).first()
        if not pw:
            return
        exists = s.query(db.PlanWeekSnapshot).filter_by(plan_week_id=pw.plan_week_id,
                                                        week_no=week_no).first()
        if exists:
            return                                     # 幂等：不重复冻结
        sessions = _sessions_of_week(s, plan_id, week_no)
        snap = {"week_no": week_no, "phase": pw.phase,
                "sessions": [{"session_id": x.session_id, "date": x.date.isoformat(),
                              "type": x.type, "is_key": x.is_key, "pace_zone": x.pace_zone,
                              "target_km": x.target_km, "status": x.status}
                             for x in sessions]}
        s.add(db.PlanWeekSnapshot(plan_week_id=pw.plan_week_id, plan_id=plan_id,
                                  week_no=week_no, snapshot_json=snap))
        s.add(db.Event(name="session_planned", user_id=_uid_of(s, plan_id),
                       payload={"week_no": week_no, "snapshot": True,
                                "session_cnt": len(sessions)}))


@app.post("/jobs/weekly_snapshot")
def weekly_snapshot_job():
    """内部任务：为所有 active 计划的当前周补快照（幂等）。"""
    today = date.today()
    done = 0
    with db.SessionLocal() as s:
        plans = s.query(db.Plan).filter_by(status="active").all()
        for plan in plans:
            week_no = min(_current_week_no(plan, today), 16)
            existing = s.query(db.PlanWeekSnapshot).filter_by(
                plan_id=plan.plan_id, week_no=week_no).first()
            if not existing:
                _take_snapshot(plan.plan_id, week_no)
                done += 1
    return {"ok": True, "snapshotted": done}


@app.post("/jobs/weather_refresh")
def weather_refresh_job():
    """内部任务：拉未来 7 天天气入库（apparent_max 入库即算好，复盘同口径）。"""
    with db.SessionLocal() as s:
        users = s.query(db.User).all()
        n = 0
        for u in users:
            for i in range(7):
                d = date.today() + timedelta(days=i)
                wd = get_week_weather(u.lat, u.lon, d).get(d.isoformat())
                if not wd:
                    continue
                row = s.query(db.WeatherDaily).filter_by(city=u.city, date=d).first()
                if row:
                    row.t_max, row.rh_mean, row.precip = wd.t_max, wd.rh_mean, wd.precip
                    row.weather_code, row.apparent_max = wd.weather_code, wd.apparent_max
                    row.aqi, row.source = wd.aqi, wd.source
                else:
                    s.add(db.WeatherDaily(city=u.city, date=d, t_max=wd.t_max,
                                          rh_mean=wd.rh_mean, precip=wd.precip,
                                          weather_code=wd.weather_code, aqi=wd.aqi,
                                          apparent_max=wd.apparent_max, source=wd.source))
                n += 1
            s.commit()
    return {"ok": True, "rows": n}


@app.get("/v1/kb/tip")
def kb_tip(user_id: str | None = None):
    """「跑步知识」板块：随机抽一条新手向知识；连续不足打卡 ≥2 次时
    优先推恢复/补跑主题（user_id 由中间件校验与 token 一致）。"""
    from server import rag
    return rag.pick_tip(user_id)


@app.get("/v1/metrics")
def metrics(user_id: str | None = None):
    """北极星看板：周计划执行率等（分母取周初快照，不被重排美化）。"""
    with db.SessionLocal() as s:
        q = s.query(db.PlanWeekSnapshot)
        snaps = q.all()
        if user_id:
            # 只统计 active 计划：被作废的旧计划残留的快照不算数
            active_ids = {p.plan_id for p in s.query(db.Plan).filter_by(user_id=user_id,
                                                                        status="active")}
            snaps = [x for x in snaps if x.plan_id in active_ids]
        rows = []
        for snap in snaps:
            planned = [x for x in snap.snapshot_json.get("sessions", [])
                       if x.get("status") != "cancelled_before_week"]
            done = 0
            key_planned = key_done = 0
            for x in planned:
                log = s.query(db.SessionLog).filter_by(session_id=x["session_id"]).first()
                ok = log and log.status in ("full", "partial")
                if x.get("is_key"):
                    key_planned += 1
                    if ok:
                        key_done += 1
                if ok:
                    done += 1
            rows.append({"plan_id": snap.plan_id, "week_no": snap.week_no,
                         "planned": len(planned), "done": done,
                         "rate": round(done / len(planned), 3) if planned else None,
                         "key_planned": key_planned, "key_done": key_done})
        guards = s.query(db.GuardAudit).all()
        blocked = sum(1 for g in guards if g.action == "block")
        llm_rows = s.query(db.Event).filter_by(name="llm_explain").all()
        llm_n = sum(1 for e in llm_rows if e.payload.get("source") == "llm")
        # AI 决策统计：微调落地、重排推荐、知识库注入与案例召回（全部来自埋点事件）
        ai_evs = s.query(db.Event).filter(db.Event.name.in_(
            ("ai_tune", "ai_tune_deviation", "rearrange_ai_recommend"))).all()
        tune_evs = [e for e in ai_evs if e.name in ("ai_tune", "ai_tune_deviation")]
        tune_applied = sum(1 for e in tune_evs if e.payload.get("applied"))
        rec_evs = [e for e in ai_evs if e.name == "rearrange_ai_recommend"]
        kb_inject = 0
        kb_ids: set[str] = set()
        for e in ai_evs:
            ids = e.payload.get("kb_ids") or []
            if ids:
                kb_inject += 1
                kb_ids.update(ids)
        cases_recalled = sum(len(e.payload.get("case_ids") or []) for e in ai_evs)
        ai_decision = {"tune_total": len(tune_evs), "tune_applied": tune_applied,
                       "tune_rate": round(tune_applied / len(tune_evs), 3) if tune_evs else 0,
                       "recommend_total": len(rec_evs),
                       "recommend_llm": sum(1 for e in rec_evs
                                            if e.payload.get("source") == "llm"),
                       "kb_injections": kb_inject, "kb_blocks_used": len(kb_ids),
                       "cases_recalled": cases_recalled}
        user = s.get(db.User, user_id) if user_id else None
        bmi = (round(user.weight_kg / ((user.height_cm / 100) ** 2), 1)
               if user and user.height_cm and user.weight_kg else None)
        return {"weeks": rows, "user": ({"avail_days": user.avail_days, "vdot": user.vdot,
                                         "goal_race": user.goal_race,
                                         "height_cm": user.height_cm, "weight_kg": user.weight_kg,
                                         "bmi": bmi} if user else None),
                "guard": {"total": len(guards), "blocked": blocked,
                          "effectiveness": round(blocked / len(guards), 3) if guards else 0},
                "ai_decision": ai_decision,
                "llm_usage": {"llm": llm_n, "template": len(llm_rows) - llm_n,
                              "rate": round(llm_n / len(llm_rows), 3) if llm_rows else 0}}


@app.get("/v1/plan/{plan_id}")
def plan_overview(plan_id: str):
    with db.SessionLocal() as s:
        plan = s.get(db.Plan, plan_id)
        if not plan:
            raise HTTPException(404, "计划不存在")
        weeks = s.query(db.PlanWeek).filter_by(plan_id=plan_id).order_by(db.PlanWeek.week_no).all()
        return {"plan_id": plan_id, "vdot": plan.vdot, "goal_race": plan.goal_race,
                "start_date": plan.start_date.isoformat(), "status": plan.status,
                "weeks": [{"week_no": w.week_no, "phase": w.phase,
                           "planned_km": round(w.planned_km, 1),
                           "is_dropped": w.is_dropped} for w in weeks]}


@app.on_event("startup")
def _startup():
    db.init_db()
    # APScheduler：每周一 00:00 周快照；每天 05:00 天气刷新。启动即补一次本周快照。
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        sched = BackgroundScheduler(timezone="Asia/Shanghai")
        sched.add_job(weekly_snapshot_job, "cron", day_of_week="mon", hour=0, minute=0)
        sched.add_job(weather_refresh_job, "cron", hour=5, minute=0)
        sched.start()
        app.state.scheduler = sched
    except Exception:
        app.state.scheduler = None
    weekly_snapshot_job()
