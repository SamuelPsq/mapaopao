"""实验室 B 端服务层：数据导出（脱敏）、变量字典、问卷、知情同意。

设计原则与 guard 层同纪律：本包只做「数据怎么交付」，不参与任何训练决策；
脱敏规则改动走 PR review，与 template_16w.yaml 同纪律。
"""
from __future__ import annotations

import csv
import hashlib
import io
import secrets
import zipfile
from datetime import date, datetime

from server import db

CONSENT_VERSION = "1.0"

CONSENT_DOC = {
    "version": CONSENT_VERSION,
    "title": "数据使用协议",
    "items": [
        "你的训练数据（打卡里程、用时、配速）与状态上报（睡眠、疲劳、酸痛）用于生成个人训练计划，",
        "经匿名化处理（去除用户名/学号等可识别信息，替换为随机哈希编号）后，",
        "可提供给合作实验室（校体育学院运动与健康实验中心）用于运动健康研究，",
        "研究数据以聚合统计形式呈现，不指向任何个人。",
        "你可以随时注销账号，注销后你的全部数据将被删除。",
        "继续使用即表示同意本协议（v%s）。" % CONSENT_VERSION,
    ],
}

# ---------------------------------------------------------------- 匿名化
def anon_map(user_ids: list[str], salt: str) -> dict[str, str]:
    """user_id → 匿名编号。盐每次导出随机生成、不随导出包交付。"""
    return {u: hashlib.sha1(f"{u}|{salt}".encode()).hexdigest()[:12] for u in user_ids}


# ---------------------------------------------------------------- 变量字典（改口径走 PR review）
DICTIONARY_MD = """# 马跑跑数据集 · 变量字典 v1

## weekly_training.csv（周粒度训练执行，一行 = 一名学生 × 一周）
| 字段 | 类型 | 口径 |
| --- | --- | --- |
| anon_id | str | 匿名学生编号（本次导出随机盐哈希，不可反推） |
| cohort | str | 学期批次（分组变量） |
| week_no | int | 计划周次 1-16 |
| phase | str | base/build/peak/cut/taper |
| vdot_at_week | int | 该周生效的跑力值（能力变化轨迹） |
| planned_km | float | 本周计划跑量（kg） |
| actual_km | float | 本周实际完成跑量（打卡回执之和） |
| key_plan | int | 本周关键课节数（长距离+质量课） |
| key_done | int | 完成的关键课节数（full/over/partial 计完成） |
| completion_rate | float | 全部课程完成率 |
| rearrange_count | int | 本周重排触发次数（天气/身体/手动） |
| guard_hit_count | int | 本周护栏命中次数（周增幅/连高强度/伤痛窗口） |

## daily_state.csv（日粒度状态上报，一行 = 一名学生 × 一天）
| 字段 | 类型 | 口径 |
| --- | --- | --- |
| anon_id | str | 同上 |
| date | date | 上报日期 |
| source | str | checkin=日常打卡流程内上报 / popup=打卡弹窗 |
| sleep_score | int | 自评睡眠 1-10 |
| fatigue_score | int | 自评疲劳 1-10 |
| muscle_sore | bool | 训练后肌肉酸痛（DOMS） |
| soreness_part | str | knee/calf/ankle/none |
| breath_state | str | 训练中体感：gasp 喘粗气 / talk 能说话 / easy 强度低 |

## survey_weekly.csv（每周问卷，一行 = 一名学生 × 一周）
| 字段 | 类型 | 口径 |
| --- | --- | --- |
| anon_id / week_ref | | 同上 |
| submitted | bool | 该周是否完成问卷（未填也保留一行，回收率分母） |
| q_* | | 题目按 id 展开为列（likert7=1-7 分，bool=0/1） |

## events.csv（事件流）
| event_type | 口径 |
| --- | --- |
| injury_report | 当日上报酸痛部位 != none 或 DOMS（源：状态上报） |
| guard_hit | 护栏命中（含 action：pass/patch/block 与规则名） |
| rearrange | 重排事件（trigger_src / chosen 策略 / 用户决定 accept/reject） |
| goal_degrade / vdot_advance | 目标降级 / 能力进阶 |

## 脱敏说明
- 用户名/学号/手机号不进入导出；城市经纬度仅保留布尔位（未实施，默认不含）。
- 保留分组变量：年龄段、BMI 分档（<24 / 24-28 / ≥28）、基础档位（A/B/C）、cohort。
- 匿名盐仅保存在导出方服务器的审计日志中，不随导出包交付。
"""

README_MD = """# 马跑跑匿名数据集

- 采集周期见文件名；consent 版本：{consent}；脱敏规则见 dictionary.md。
- 执行率分母为周一 00:00 冻结的周初快照，重排不会美化它。
- 本数据仅用于运动健康研究，请勿尝试反推个人身份。
"""


# ---------------------------------------------------------------- 导出
def _csv_str(headers: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    return buf.getvalue()


def build_export(cohort: str | None, date_from: date, date_to: date) -> tuple[bytes, dict]:
    """聚合三张 CSV + 字典 + readme 为 ZIP。返回 (zip_bytes, 统计信息)。"""
    with db.SessionLocal() as s:
        users = s.query(db.User).all()
        if cohort:
            users = [u for u in users if u.cohort == cohort]
        uids = [u.user_id for u in users]
        amap = anon_map(uids, salt := secrets.token_hex(8))
        plans = s.query(db.Plan).all()
        plan_by_id = {p.plan_id: p for p in plans}

        # ---- weekly_training.csv
        wt_headers = ["anon_id", "cohort", "week_no", "phase", "vdot_at_week",
                      "planned_km", "actual_km", "key_plan", "key_done",
                      "completion_rate", "rearrange_count", "guard_hit_count"]
        wt_rows: list[list] = []
        for pw in s.query(db.PlanWeek).all():
            plan = plan_by_id.get(pw.plan_id)
            if not plan or plan.user_id not in amap:
                continue
            if not (date_from <= plan.start_date <= date_to):
                continue
            sessions = s.query(db.Session).filter_by(plan_week_id=pw.plan_week_id).all()
            actual = key_plan = key_done = done_n = planned_n = 0
            for x in sessions:
                log = s.query(db.SessionLog).filter_by(session_id=x.session_id).first()
                ok = log and log.status in ("full", "over", "partial")
                if x.status != "cancelled_before_week":
                    planned_n += 1
                    if ok:
                        done_n += 1
                if x.is_key:
                    key_plan += 1
                    if ok:
                        key_done += 1
                if log:
                    actual += log.actual_km
            rc = s.query(db.RearrangeEvent).filter_by(plan_id=pw.plan_id, week_no=pw.week_no).count()
            gc = s.query(db.GuardAudit).filter_by(plan_id=pw.plan_id, week_no=pw.week_no).count()
            u = next(x for x in users if x.user_id == plan.user_id)
            wt_rows.append([amap[plan.user_id], u.cohort or "", pw.week_no, pw.phase,
                            plan.vdot, round(pw.planned_km, 1), round(actual, 1),
                            key_plan, key_done,
                            round(done_n / planned_n, 3) if planned_n else "",
                            rc, gc])

        # ---- daily_state.csv
        ds_headers = ["anon_id", "date", "source", "sleep_score", "fatigue_score",
                      "muscle_sore", "soreness_part", "breath_state"]
        ds_rows = [[amap[r.user_id], r.date.isoformat(), r.entry, r.sleep_score,
                    r.fatigue_score, int(r.muscle_sore), r.soreness_part or "none",
                    r.breath_state or ""]
                   for r in s.query(db.StatusReport).all() if r.user_id in amap
                   and date_from <= r.date <= date_to]

        # ---- survey_weekly.csv（未填也保留一行 = 回收率分母）
        weekly = s.query(db.Survey).filter_by(kind="weekly", active=True).first()
        sv_headers = ["anon_id", "week_ref", "submitted"]
        sv_rows: list[list] = []
        answered: dict[tuple[str, str], dict] = {}
        if weekly:
            q_ids = [q["id"] for q in weekly.schema_json]
            sv_headers += [f"q_{q}" for q in q_ids]
            for r in s.query(db.SurveyResponse).filter_by(survey_id=weekly.survey_id).all():
                if r.user_id in amap:
                    answered[(r.user_id, r.week_ref)] = r.answers_json
            for inst in s.query(db.SurveyInstance).all():
                if inst.user_id not in amap:
                    continue
                a = answered.get((inst.user_id, inst.week_ref), {})
                sv_rows.append([amap[inst.user_id], inst.week_ref, int(inst.status == "done")]
                               + [a.get(q, "") for q in q_ids])

        # ---- events.csv
        ev_headers = ["anon_id", "date", "event_type", "detail"]
        ev_rows: list[list] = []
        for r in s.query(db.StatusReport).all():
            if r.user_id in amap and date_from <= r.date <= date_to and \
                    (r.soreness_part not in ("", "none") or r.muscle_sore):
                ev_rows.append([amap[r.user_id], r.date.isoformat(), "injury_report",
                                f"part={r.soreness_part or 'none'};doms={int(r.muscle_sore)}"])
        for g in s.query(db.GuardAudit).all():
            plan = plan_by_id.get(g.plan_id)
            if plan and plan.user_id in amap:
                ev_rows.append([amap[plan.user_id], g.created_at.date().isoformat(),
                                "guard_hit", f"{g.hit_rule or 'pass'};action={g.action}"])
        for e in s.query(db.RearrangeEvent).all():
            if e.user_id in amap:
                ev_rows.append([amap[e.user_id], e.created_at.date().isoformat(),
                                "rearrange",
                                f"trigger={e.trigger_src};chosen={e.chosen or '-'};result={e.result}"])
        for e in s.query(db.Event).filter(db.Event.name.in_(("goal_degrade", "vdot_advance"))).all():
            if e.user_id in amap:
                ev_rows.append([amap[e.user_id], e.created_at.date().isoformat(),
                                e.name, str(e.payload)[:120]])

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("weekly_training.csv", _csv_str(wt_headers, wt_rows))
        z.writestr("daily_state.csv", _csv_str(ds_headers, ds_rows))
        z.writestr("survey_weekly.csv", _csv_str(sv_headers, sv_rows))
        z.writestr("events.csv", _csv_str(ev_headers, ev_rows))
        z.writestr("dictionary.md", DICTIONARY_MD)
        z.writestr("readme.md", README_MD.format(consent=CONSENT_VERSION))
    return mem.getvalue(), {"students": len(uids), "weekly_rows": len(wt_rows),
                            "state_rows": len(ds_rows), "salt_sha": hashlib.sha1(salt.encode()).hexdigest()[:12]}


# ---------------------------------------------------------------- 问卷：学生侧待填
def pending_survey(user_id: str) -> dict | None:
    """本周待填问卷：周问卷（实例驱动，周日生成）优先，其次前测，最后后测。"""
    with db.SessionLocal() as s:
        weekly = s.query(db.Survey).filter_by(kind="weekly", active=True).first()
        if weekly:
            inst = s.query(db.SurveyInstance).filter_by(
                user_id=user_id, survey_id=weekly.survey_id, status="pending").first()
            if inst:
                return {"survey_id": weekly.survey_id, "kind": "weekly",
                        "title": weekly.title, "schema": weekly.schema_json,
                        "week_ref": inst.week_ref}
        for kind in ("pre", "post"):
            sv = s.query(db.Survey).filter_by(kind=kind, active=True).first()
            if sv and not s.query(db.SurveyResponse).filter_by(
                    user_id=user_id, survey_id=sv.survey_id).first():
                return {"survey_id": sv.survey_id, "kind": kind,
                        "title": sv.title, "schema": sv.schema_json, "week_ref": ""}
    return None


def respond_survey(user_id: str, survey_id: str, answers: dict) -> dict:
    """提交问卷：必填校验 + 幂等（同人同卷同周重复提交不重复入库）。"""
    with db.SessionLocal() as s:
        sv = s.get(db.Survey, survey_id)
        if not sv or not sv.active:
            raise ValueError("问卷不存在或未发布")
        for q in sv.schema_json:
            if q.get("required") and q["id"] not in answers:
                raise ValueError(f"还有题目没回答：{q.get('text', q['id'])}")
        week_ref = ""
        if sv.kind == "weekly":
            today = date.today()
            week_ref = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
            dup = s.query(db.SurveyResponse).filter_by(
                user_id=user_id, survey_id=survey_id, week_ref=week_ref).first()
            if dup:
                return {"ok": True, "duplicate": True}
        dup = s.query(db.SurveyResponse).filter_by(
            user_id=user_id, survey_id=survey_id, week_ref=week_ref).first() if sv.kind != "weekly" else None
        if dup:
            return {"ok": True, "duplicate": True}
        s.add(db.SurveyResponse(user_id=user_id, survey_id=survey_id, week_ref=week_ref,
                                answers_json=answers, source=sv.kind))
        if sv.kind == "weekly":
            inst = s.query(db.SurveyInstance).filter_by(
                user_id=user_id, survey_id=survey_id, week_ref=week_ref).first()
            if inst:
                inst.status = "done"
        s.commit()
        return {"ok": True, "duplicate": False}


def create_weekly_instances() -> int:
    """周日定时任务：为所有 active 计划用户生成本周问卷实例（幂等）。"""
    today = date.today()
    week_ref = f"{today.isocalendar().year}-W{today.isocalendar().week:02d}"
    with db.SessionLocal() as s:
        weekly = s.query(db.Survey).filter_by(kind="weekly", active=True).first()
        if not weekly:
            return 0
        plan_uids = {p.user_id for p in s.query(db.Plan).filter_by(status="active").all()}
        n = 0
        for uid in plan_uids:
            exists = s.query(db.SurveyInstance).filter_by(
                user_id=uid, survey_id=weekly.survey_id, week_ref=week_ref).first()
            if not exists:
                s.add(db.SurveyInstance(user_id=uid, survey_id=weekly.survey_id,
                                        week_ref=week_ref))
                n += 1
        s.commit()
        return n
