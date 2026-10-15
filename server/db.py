"""数据层：SQLAlchemy（开发默认 SQLite，生产可切 PostgreSQL）。

schema 上最能体现产品判断的两处：
① plan_week_snapshot 独立成表 —— 执行率分母的唯一来源，周一 00:00 冻结后永不修改。
② guard_audit 与 rearrange_event 分开 —— 前者回答「系统拦了什么」，后者回答「用户选了什么」。
"""
from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String,
                        Text, create_engine, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

DB_URL = os.getenv("MAPAOPAO_DB_URL", "sqlite:///./mapaopao.db")
engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "user"
    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    vdot: Mapped[int] = mapped_column(Integer)
    ability_tier: Mapped[str] = mapped_column(String(16))
    goal_race: Mapped[str] = mapped_column(String(8))           # full / half
    avail_days: Mapped[int] = mapped_column(Integer)
    max_duration: Mapped[int] = mapped_column(Integer, default=90)
    injury_flag: Mapped[str] = mapped_column(String(1), default="N")
    city: Mapped[str] = mapped_column(String(32), default="上海")
    lat: Mapped[float] = mapped_column(Float, default=31.23)
    lon: Mapped[float] = mapped_column(Float, default=121.47)
    conservative_factor: Mapped[float] = mapped_column(Float, default=1.0)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)   # BMI 规划修正用
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Account(Base):
    """账号体系：用户名 + 加盐 PBKDF2 密码哈希，一对一挂在 user 上。"""
    __tablename__ = "account"
    username: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.user_id"), index=True, unique=True)
    salt: Mapped[str] = mapped_column(String(64))
    password_hash: Mapped[str] = mapped_column(String(128))
    token: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # 登录凭证，退出即作废
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Plan(Base):
    __tablename__ = "plan"
    plan_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.user_id"), index=True)
    version: Mapped[str] = mapped_column(String(8), default="v3.1")
    target_distance: Mapped[str] = mapped_column(String(16))
    start_date: Mapped[Date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="active")
    profile_hash: Mapped[str] = mapped_column(String(16), default="")
    vdot: Mapped[int] = mapped_column(Integer, default=0)
    goal_race: Mapped[str] = mapped_column(String(8), default="full")
    bmi_conservative: Mapped[bool] = mapped_column(Boolean, default=False)  # BMI≥28 生成的保守计划
    degrade_choice: Mapped[str] = mapped_column(String(16), nullable=True)  # half / defer / insist


class PlanWeek(Base):
    __tablename__ = "plan_week"
    plan_week_id: Mapped[str] = mapped_column(String(40), primary_key=True)   # {plan_id}-W{n}
    plan_id: Mapped[str] = mapped_column(ForeignKey("plan.plan_id"), index=True)
    week_no: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(12))              # base/build/peak/cut/taper
    planned_km: Mapped[float] = mapped_column(Float)
    key_goal: Mapped[str] = mapped_column(String(64), default="")
    is_dropped: Mapped[bool] = mapped_column(Boolean, default=False)


class PlanWeekSnapshot(Base):
    __tablename__ = "plan_week_snapshot"
    __table_args__ = (UniqueConstraint("plan_week_id", "week_no", name="uq_snapshot"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_week_id: Mapped[str] = mapped_column(String(40), index=True)
    plan_id: Mapped[str] = mapped_column(String(32), index=True)
    week_no: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[dict] = mapped_column(JSON)           # 冻结的课表（含 session 列表）
    taken_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Session(Base):
    __tablename__ = "session"
    session_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    plan_week_id: Mapped[str] = mapped_column(ForeignKey("plan_week.plan_week_id"), index=True)
    plan_id: Mapped[str] = mapped_column(String(32), index=True)
    week_no: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer)
    date: Mapped[Date] = mapped_column(Date, index=True)
    type: Mapped[str] = mapped_column(String(20))
    is_key: Mapped[bool] = mapped_column(Boolean, default=False)
    pace_zone: Mapped[str] = mapped_column(String(16))
    target_km: Mapped[float] = mapped_column(Float)
    target_pace: Mapped[str] = mapped_column(String(24), default="")
    source: Mapped[str] = mapped_column(String(12), default="template")   # template / rearranged
    status: Mapped[str] = mapped_column(String(24), default="planned")
    # planned / done / partial / skipped / cancelled_by_rearrange / cancelled_before_week


class SessionLog(Base):
    __tablename__ = "session_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("session.session_id"), index=True)
    status: Mapped[str] = mapped_column(String(12))             # full / partial / skipped
    actual_km: Mapped[float] = mapped_column(Float, default=0.0)
    actual_pace: Mapped[str] = mapped_column(String(16), default="")
    duration_sec: Mapped[int] = mapped_column(Integer, default=0)  # 总用时（秒），打卡弹窗填
    rpe: Mapped[int] = mapped_column(Integer, nullable=True)
    logged_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RearrangeEvent(Base):
    __tablename__ = "rearrange_event"
    event_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    plan_id: Mapped[str] = mapped_column(String(32))
    week_no: Mapped[int] = mapped_column(Integer)
    trigger_src: Mapped[str] = mapped_column(String(16))
    context_hash: Mapped[str] = mapped_column(String(16), index=True)
    candidates_json: Mapped[dict] = mapped_column(JSON)         # 被选中的与被拒绝的都存
    chosen: Mapped[str] = mapped_column(String(20), default="") # 策略名或 ""
    result: Mapped[str] = mapped_column(String(12), default="pending")  # accept / reject / manual / pending
    decision_ms: Mapped[int] = mapped_column(Integer, default=0)
    delta_km: Mapped[float] = mapped_column(Float, default=0.0)
    explain_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StatusReport(Base):
    __tablename__ = "status_report"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    date: Mapped[Date] = mapped_column(Date, index=True)
    soreness_part: Mapped[str] = mapped_column(String(16), default="")  # knee / calf / none ...
    muscle_sore: Mapped[bool] = mapped_column(Boolean, default=False)   # 训练后肌肉酸痛（DOMS）
    breath_state: Mapped[str] = mapped_column(String(16), default="")   # gasp 喘粗气 / talk 能说话 / easy 强度低
    sleep_score: Mapped[int] = mapped_column(Integer, default=5)
    fatigue_score: Mapped[int] = mapped_column(Integer, default=5)
    entry: Mapped[str] = mapped_column(String(12), default="checkin")   # checkin / popup


class GuardAudit(Base):
    __tablename__ = "guard_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(32), index=True)
    week_no: Mapped[int] = mapped_column(Integer)
    strategy: Mapped[str] = mapped_column(String(20), default="")
    hit_rule: Mapped[str] = mapped_column(String(24), nullable=True)
    before_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    after_json: Mapped[dict] = mapped_column(JSON, nullable=True)
    action: Mapped[str] = mapped_column(String(8), default="pass")      # pass / patch / block
    reason_user: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WeatherDaily(Base):
    __tablename__ = "weather_daily"
    __table_args__ = (UniqueConstraint("city", "date", name="uq_weather"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    city: Mapped[str] = mapped_column(String(32), index=True)
    date: Mapped[Date] = mapped_column(Date)
    t_max: Mapped[float] = mapped_column(Float, default=0.0)
    rh_mean: Mapped[float] = mapped_column(Float, default=50.0)
    precip: Mapped[float] = mapped_column(Float, default=0.0)
    weather_code: Mapped[int] = mapped_column(Integer, default=0)
    aqi: Mapped[int] = mapped_column(Integer, nullable=True)
    apparent_max: Mapped[float] = mapped_column(Float, default=0.0)       # 入库即算好（复盘同口径）
    source: Mapped[str] = mapped_column(String(12), default="live")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Event(Base):
    __tablename__ = "event"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(32), index=True)   # plan_generated / session_done / ...
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AICase(Base):
    """AI 调整案例库：情境 + 决策 + 后续效果，供相似情境召回（个人记忆，不做跨用户共享）。"""
    __tablename__ = "ai_case"
    case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    week_no: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(12), default="")
    trigger: Mapped[str] = mapped_column(String(16), default="")   # over_done / under_done / skipped / soreness / weather
    dev_ratio: Mapped[float] = mapped_column(Float, default=1.0)   # actual_km / target_km
    context_json: Mapped[dict] = mapped_column(JSON, default=dict) # 检索到的知识块 id + 召回的历史 case_id
    decision_json: Mapped[dict] = mapped_column(JSON, nullable=True)  # AI 落地的微调 ops；未落地为 null
    outcome: Mapped[str] = mapped_column(String(12), default="pending")  # pending / good / bad（后续执行率回填）
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def init_db() -> None:
    Base.metadata.create_all(engine)
    if DB_URL.startswith("sqlite"):
        # 老库补列：SQLite 不支持 IF NOT EXISTS，列已存在时吞掉异常即可
        from sqlalchemy import text
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE account ADD COLUMN token VARCHAR(64)"))
            except Exception:
                pass
            for ddl in ("ALTER TABLE user ADD COLUMN height_cm FLOAT",
                        "ALTER TABLE user ADD COLUMN weight_kg FLOAT",
                        "ALTER TABLE session_log ADD COLUMN duration_sec INTEGER",
                        "ALTER TABLE status_report ADD COLUMN muscle_sore INTEGER",
                        "ALTER TABLE status_report ADD COLUMN breath_state VARCHAR(16)",
                        "ALTER TABLE plan ADD COLUMN bmi_conservative INTEGER"):
                try:
                    conn.execute(text(ddl))
                except Exception:
                    pass


def track(name: str, user_id: str, **payload) -> None:
    """埋点：与业务同事务写的简化版（演示环境单写）。"""
    with SessionLocal.begin() as db:
        db.add(Event(name=name, user_id=user_id, payload=payload))
