import { useCallback, useEffect, useRef, useState } from "react"
import { useLocation, useNavigate } from "react-router"
import { api, loadUser, clearUser, savePlan, type TodayCard } from "@/lib/api"
import { fmtKm } from "@/lib/format"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Slider } from "@/components/ui/slider"
import { Label } from "@/components/ui/label"
import { CityPicker } from "@/components/CityPicker"
import MonthCalendar from "@/components/MonthCalendar"
import type { CityInfo } from "@/lib/cities"
import { CalendarDays, ChartNoAxesColumnIncreasing, ChevronRight, CloudSun, Dumbbell, Flame, House, Lightbulb, MapPin, MoonStar, PersonStanding, Target, Timer, TriangleAlert, Wrench } from "lucide-react"

export default function Today() {
  const nav = useNavigate()
  const uid = loadUser()
  const [card, setCard] = useState<TodayCard | null>(null)
  const [err, setErr] = useState("")
  const [toast, setToast] = useState("")
  const [notTimeOpen, setNotTimeOpen] = useState(false)
  // 「今天跑不了」的二次确认：确认后再记录并进入状态记录卡
  const [skipConfirm, setSkipConfirm] = useState(false)
  // 跳过的方式：彻底休息 / 室内力量训练（记入月历）
  const [skipKindOpen, setSkipKindOpen] = useState(false)
  // 月历弹窗
  const [calendarOpen, setCalendarOpen] = useState(false)
  // 打卡弹窗：单一「今日打卡」入口，里程/总用时默认值 = 当天课程的目标值
  const [logOpen, setLogOpen] = useState(false)
  // 偏差 >±30% 后的原因询问：临时起意（不动计划）/ 强度不匹配（按数据调整计划）
  const [devAsk, setDevAsk] = useState<null | { km: number; sec: number }>(null)
  const [kmInput, setKmInput] = useState("")
  const [dur, setDur] = useState({ h: 0, m: 30, s: 0 })
  // 大幅超额（>3km）后的进阶二次确认
  const [advance, setAdvance] = useState<null | { km: number; sec: number; pace: string }>(null)
  // 进阶确认期间暂存问卷上下文，确认完再进入问卷
  const pendingSurvey = useRef<null | { status: string; typeCn: string; km: number }>(null)
  // 打卡后问卷：训练状态 / 肌肉酸痛 / 关节不适 / 睡眠 / 疲劳
  const [survey, setSurvey] = useState<null | { status: string; typeCn: string; km: number }>(null)
  const [breath, setBreath] = useState("talk")
  const [muscle, setMuscle] = useState("no")
  const [joint, setJoint] = useState("none")
  const [sleep, setSleep] = useState(7)
  const [fatigue, setFatigue] = useState(4)
  // 跑后拉伸建议（LLM 生成，规则模板兜底）
  const [stretch, setStretch] = useState<null | { text: string; source: string; suggestion: string }>(null)

  // 问卷提交中 / 进阶重排中：禁用按钮防重复提交
  const [surveyBusy, setSurveyBusy] = useState(false)
  const [advanceBusy, setAdvanceBusy] = useState(false)

  const refresh = useCallback(async () => {
    if (!uid) { nav("/login", { replace: true }); return }
    try {
      const c = await api.get<TodayCard>(`/v1/today?user_id=${uid}`)
      savePlan(c.plan_id)                    // 缓存 plan_id，周审视页就不用先等 /v1/today
      setCard(c)
    } catch (e) {
      setErr((e as Error).message)
    }
  }, [uid, nav])

  useEffect(() => { refresh() }, [refresh])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(""), 4000)
    return () => clearTimeout(t)
  }, [toast])

  if (err) {
    return (
      <Shell>
        <p className="text-muted-foreground">{err}</p>
        <Button className="mt-4" onClick={() => { clearUser(); nav("/login") }}>重新登录</Button>
      </Shell>
    )
  }
  if (!card) return <Shell><p className="text-muted-foreground">加载中…</p></Shell>

  const m = card.main_session
  const todayStr = new Date().toLocaleDateString("sv-SE")   // YYYY-MM-DD（本地时区）
  const isToday = !!m && m.date === todayStr                // 主课是不是今天的课

  async function log(status: "full" | "partial" | "over" | "skipped", actualKm?: number, durationSec?: number, skipKind?: "rest" | "strength", devReason?: "impulse" | "mismatch") {
    if (!isToday) { setNotTimeOpen(true); return }          // 时间对不上：不是不练，时候未到
    const actual_km = actualKm ?? (status === "full" ? m!.target_km : 0)
    let r: { vdot_update?: string; over_note?: string; under_note?: string; ai_tune_note?: string; rearrange_ready?: boolean; advance_prompt?: boolean; advance_pace?: string }
    try {
      r = await api.post<typeof r>(
        `/v1/session/${m!.session_id}/log`,
        { status, actual_km, actual_pace: "", duration_sec: durationSec ?? 0, rpe: null,
          skip_kind: skipKind ?? "", dev_reason: devReason ?? "" })
    } catch (e) {
      // 打卡失败必须让用户知道（早期版本静默失败，数据丢失且无感知）
      setToast("记录失败：" + (e as Error).message)
      return
    }
    // 大幅超额（>3km）：先问要不要按真实跑力进阶重排（确认完再进问卷）
    if (r.advance_prompt) {
      pendingSurvey.current = { status, typeCn: m!.type_cn, km: actual_km }
      setAdvance({ km: actual_km, sec: durationSec ?? 0, pace: r.advance_pace || "" })
      refresh()
      return
    }
    setToast(status === "full" ? "已记录完成，继续保持 💪"
      : status === "over" ? "已记录超额完成，状态很好 🔥"
      : status === "partial" ? "已记录，部分完成也算数" : "已记录，别担心，不用补")
    if (r.vdot_update) setToast(r.vdot_update)
    if (r.over_note) setToast(r.over_note)
    if (r.under_note) setToast(r.under_note)
    if (r.ai_tune_note) setToast(r.ai_tune_note)
    // 统一进入打卡后问卷（含「今天跑不了」）
    setSurvey({ status, typeCn: m!.type_cn, km: actual_km })
    refresh()
  }

  // 打开打卡弹窗：里程默认目标值、总用时默认按目标配速估算，用户可手动调整
  function openCheckin() {
    if (!isToday) { setNotTimeOpen(true); return }
    setKmInput(m ? String(m.target_km) : "")
    if (m) {
      const est = Math.round(m.target_km * paceSec(m.target_pace))
      setDur({ h: Math.floor(est / 3600), m: Math.floor((est % 3600) / 60), s: est % 60 })
    }
    setLogOpen(true)
  }

  // 填完里程 + 时分秒后的提交：偏差 >±30% 先问原因，否则直接记录
  function submitLog() {
    if (!m) return
    const km = Number(kmInput)
    const target = m.target_km
    const durationSec = dur.h * 3600 + dur.m * 60 + dur.s
    if (!Number.isFinite(km) || km <= 0) { setToast("请输入大于 0 的公里数"); return }
    if (durationSec <= 0) { setToast("请填一下总用时（时分秒）"); return }
    if (Math.abs(km - target) > target * 0.3) {
      // 偏差过大：先弄清是临时起意还是强度不匹配，再决定动不动计划
      setLogOpen(false)
      setKmInput("")
      setDevAsk({ km: Math.round(km * 10) / 10, sec: durationSec })
      return
    }
    setLogOpen(false)
    setKmInput("")
    log(km >= target ? "full" : "partial", Math.round(km * 10) / 10, durationSec)
  }

  // 偏差原因回答：临时起意 → 只记录不动计划；强度不匹配 → 按真实跑量动态调整计划
  function answerDevAsk(reason: "impulse" | "mismatch") {
    const d = devAsk
    setDevAsk(null)
    if (!d) return
    if (reason === "mismatch") setToast("会根据今日跑量调整训练计划哦")
    log(d.km >= m!.target_km ? "full" : "partial", d.km, d.sec, undefined, reason)
  }

  // 进阶确认：是 → 按真实跑力重排剩余周；否 → 保持原计划，提醒循序渐进。
  // 两个分支结束后都进入打卡后问卷。
  async function confirmAdvance(yes: boolean) {
    if (!advance || advanceBusy) return
    const a = advance
    setAdvance(null)
    if (!yes) {
      setToast("好——那就循序渐进，超额的部分当作状态好的奖励，后面的课按原计划来")
      setSurvey(pendingSurvey.current)
      return
    }
    setAdvanceBusy(true)
    try {
      const r = await api.post<{ note: string }>("/v1/user/advance",
        { user_id: uid, actual_km: a.km, duration_sec: a.sec })
      setToast(r.note || "已按照您的跑力重新安排")
    } catch (e) {
      setToast("重排失败：" + (e as Error).message)
    } finally {
      setAdvanceBusy(false)
    }
    setSurvey(pendingSurvey.current)
    refresh()
  }

  // 问卷提交：状态上报（含训练状态/酸痛/睡眠/疲劳）→ 拉伸建议弹窗
  async function submitSurvey() {
    if (!survey || surveyBusy) return
    const sv = survey
    setSurvey(null)
    setSurveyBusy(true)
    try {
      const r = await api.post<{ suggestion: string | null }>("/v1/status/report", {
        user_id: uid, soreness_part: joint, muscle_sore: muscle === "yes",
        breath_state: breath, sleep_score: sleep, fatigue_score: fatigue, entry: "checkin",
      })
      const st = await api.post<{ text: string; source: string }>("/v1/stretch", {
        user_id: uid, status: sv.status, type_cn: sv.typeCn, target_km: sv.km,
      })
      setStretch({ ...st, suggestion: r.suggestion || "" })
      if (r.suggestion) setToast(r.suggestion)
    } catch (e) {
      setToast("状态上报失败：" + (e as Error).message)
    } finally {
      setSurveyBusy(false)
    }
    refresh()
  }

  const phaseCn: Record<string, string> = { load: "加载周", cut: "减量周", taper: "赛前减量", base: "基础期", build: "强化期", peak: "峰值期" }
  const durationMin = m ? Math.round(m.target_km * paceSec(m.target_pace) / 60) : 0

  return (
    <Shell>
      {/* 顶部 */}
      <div className="flex items-baseline justify-between mb-1">
        <div className="text-lg font-bold">{card.weekday} · 第 {card.week_no} 周 / 16 周</div>
        <div className="text-xs text-muted-foreground">距赛事 {card.days_to_race} 天</div>
      </div>
      <div className="flex gap-2 mb-5">
        <Badge variant="secondary">{phaseCn[card.week_phase] || card.week_phase}</Badge>
        <Badge variant="outline">VDOT {card.vdot}</Badge>
        {card.is_dropped ? (
          <Badge className="bg-amber-500 hover:bg-amber-500 gap-1"><Flame className="h-3 w-3" />本周降载</Badge>
        ) : null}
      </div>

      {/* 查看月记录：整行右上的热区加大，避免小字难点（触控目标 ≥40px） */}
      <div className="flex justify-end -mb-2">
        <button onClick={() => setCalendarOpen(true)}
          className="text-xs text-primary flex items-center gap-1 py-2 px-2 -mx-2 active:scale-95 transition-transform">
          <CalendarDays className="h-3.5 w-3.5" />查看月记录
        </button>
      </div>

      {/* 跑量数据：页面最主要元素（纯数据，无卡片） */}
      <div className="mb-6">
        <div className="flex items-baseline justify-between">
          <span className="text-xs text-muted-foreground">累计总跑量</span>
          <span className="text-5xl font-black tracking-tight leading-none">
            {fmtKm(card.total_km || 0)}<span className="text-base font-bold text-muted-foreground ml-1">km</span>
          </span>
        </div>
        <div className="flex items-baseline justify-between mt-2.5">
          <span className="text-xs text-muted-foreground">本周跑量</span>
          <span className="text-2xl font-bold tracking-tight leading-none">
            {fmtKm(card.week_km || 0)}<span className="text-xs font-normal text-muted-foreground ml-1">km</span>
          </span>
        </div>
      </div>

      {/* 主课卡：次主要元素，窄卡片小字号 */}
      {m ? (
        <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-primary to-[hsl(20,88%,33%)] text-primary-foreground shadow-md shadow-primary/20 mb-4">
          <div className="absolute -right-6 -top-8 h-24 w-24 rounded-full bg-white/10" />
          <div className="relative px-4 py-3.5">
            <div className="flex items-center justify-between mb-1.5">
              <div className="text-[11px] font-medium text-white/90">
                {!isToday ? "下一节 · " : ""}{m.dow} · {m.is_key ? "关键课" : "辅助课"}
              </div>
              {m.is_key ? <Badge className="bg-white/20 hover:bg-white/20 text-white border-0 text-[10px] px-1.5">关键课</Badge> : null}
            </div>
            <div className="text-xl font-black tracking-tight">
              {m.type_cn} <span className="text-sm font-bold">{fmtKm(m.target_km)} km</span>
            </div>
            <div className="flex gap-1.5 mt-2">
              <span className="inline-flex items-center gap-1 rounded-full bg-white/15 px-2 py-0.5 text-[11px]">
                <Timer className="h-3 w-3" />约 {durationMin} 分钟
              </span>
              <span className="inline-flex items-center gap-1 rounded-full bg-white/15 px-2 py-0.5 text-[11px]">
                配速 {m.target_pace}
              </span>
            </div>
            {/* 当前城市气温 + 天气状况 + 未来 7 天趋势 */}
            {card.weather ? (
              <div className="mt-2 border-t border-white/15 pt-2">
                <div className="flex items-end justify-between gap-2">
                  <div className="flex items-baseline gap-1.5">
                    <span className="text-2xl font-black leading-none">
                      {Math.round(card.weather.t_max)}<span className="text-sm font-bold">°C</span>
                    </span>
                    <span className="text-[11px] text-white/85">
                      {card.weather.condition} · 体感 {Math.round(card.weather.apparent_max)}°
                    </span>
                  </div>
                  {card.weather.precip > 0 ? (
                    <span className="text-[10px] text-white/80">降雨 {fmtKm(card.weather.precip)} mm</span>
                  ) : null}
                </div>
                <WxTrend week={card.weather.week} />
              </div>
            ) : null}
            {card.reason_line ? <div className="text-[11px] text-white/90 mt-2 border-t border-white/15 pt-2">{card.reason_line}</div> : null}
          </div>
        </div>
      ) : (
        <div className="rounded-2xl border bg-card px-4 py-4 text-sm text-muted-foreground shadow-sm mb-4">今天没有排课，好好休息。</div>
      )}

      {/* 力量训练提醒：不进周计划，空闲日提示即可 */}
      {!card.today_has_run ? (
        <div className="rounded-2xl border border-primary/30 bg-primary/5 px-4 py-3.5 mb-4 shadow-sm">
          <div className="flex items-center gap-2 text-sm font-medium text-primary">
            <Dumbbell className="h-4 w-4 shrink-0" />今日无跑步课 · 适合加练力量
          </div>
          <div className="text-xs text-muted-foreground mt-1.5 leading-relaxed">
            深蹲 3×12 · 臀桥 3×15 · 平板支撑 3×45 秒 · 单腿提踵 3×15，20–30 分钟。
            核心和臀腿力量是跑步稳定性的底座，也能降低受伤风险。
          </div>
        </div>
      ) : null}

      {/* 天气行 */}
      {card.weather_line ? (
        <div className="text-sm bg-card border rounded-2xl px-3.5 py-2.5 mb-4 flex items-center gap-2 shadow-sm">
          <CloudSun className="h-4 w-4 text-primary shrink-0" />{card.weather_line}
        </div>
      ) : null}
      {card.hard_days.length > 0 ? (
        <div className="text-sm bg-amber-500/10 text-amber-700 rounded-2xl px-3.5 py-2.5 mb-4 flex items-center gap-2">
          <TriangleAlert className="h-4 w-4 shrink-0" />
          <span>{card.hard_days.join("、")}天气硬约束，系统已准备调整方案</span>
        </div>
      ) : null}

      {/* 劝降 */}
      {card.degrade?.triggered ? (
        <button className="w-full text-left text-sm bg-red-500/10 text-red-700 rounded-2xl px-3.5 py-3 mb-4 font-medium flex items-center gap-2"
          onClick={() => nav("/degrade")}>
          <Target className="h-4 w-4 shrink-0" />{card.degrade.message} —— 点此调整目标
        </button>
      ) : null}

      {/* 恢复护栏：连续两天睡眠/疲劳异常（只提示，不自动改课） */}
      {card.recovery_alert ? (
        <div className="w-full text-sm bg-amber-500/10 text-amber-700 rounded-2xl px-3.5 py-3 mb-4 font-medium flex items-center gap-2">
          <MoonStar className="h-4 w-4 shrink-0" />{card.recovery_alert.message}
        </div>
      ) : null}

      {/* 打卡按钮：今日打卡（默认值=今日课程，可手改）+ 今天跑不了 */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <Button className="border-0" onClick={openCheckin}>今日打卡</Button>
        <Button variant="outline" className="bg-card" onClick={() => {
          if (!isToday) { setNotTimeOpen(true); return }   // 时间对不上：不是不练，时候未到
          setSkipConfirm(true)
        }}>今天跑不了</Button>
      </div>

      {/* 重排建议：跟在打卡按钮下方，实心按钮样式，一眼可点 */}
      {card.suggestion ? (
        <button
          className="w-full text-sm font-semibold rounded-2xl px-3.5 py-3 mb-3 flex items-center justify-center gap-1.5
                     bg-primary text-primary-foreground shadow-md shadow-primary/25
                     active:scale-[0.98] transition-transform"
          onClick={() => nav(`/rearrange?trigger=${card.suggestion!.trigger_src}`)}>
          <Wrench className="h-4 w-4 shrink-0" />
          <span className="truncate">{card.suggestion.message}</span>
          <ChevronRight className="h-4 w-4 shrink-0" />
        </button>
      ) : null}

      {/* 本周进度 */}
      <div className="mt-6 rounded-2xl border bg-card p-4 shadow-sm">
        <div className="flex justify-between text-xs text-muted-foreground mb-2">
          <span>本周关键课 {card.week_progress.key_done}/{card.week_progress.key_total}</span>
          <span>全部课次 {card.week_progress.done}/{card.week_progress.total}</span>
        </div>
        <div className="h-2.5 bg-muted rounded-full overflow-hidden">
          <div className="h-full bg-primary rounded-full transition-all"
            style={{ width: `${(card.week_progress.done / Math.max(1, card.week_progress.total)) * 100}%` }} />
        </div>
      </div>

      {/* 切换城市：换坐标 + 按新城市天气重新生成配套计划 */}
      <CitySheet current={card.city} onDone={(msg) => { setToast(msg); refresh() }} />

      <NavBar weekNo={card.week_no} />
      {toast ? <div className="fixed bottom-20 left-1/2 -translate-x-1/2 bg-foreground text-background text-sm px-4 py-2 rounded-full shadow-lg max-w-xs text-center z-50">{toast}</div> : null}

      {/* 打卡弹窗：里程 + 总用时（时分秒滚轮），默认值即今日课程目标 */}
      <Dialog open={logOpen} onOpenChange={setLogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>今日打卡 · 实际跑了多少？</DialogTitle>
            <DialogDescription>
              {m ? <>这节课目标是 <b>{fmtKm(m.target_km)} km</b>。已按目标预填，可直接改。填实际里程和总用时，系统会自动换算配速，并影响接下来的排期。</> : null}
            </DialogDescription>
          </DialogHeader>
          <div className="py-2 space-y-4">
            <div>
              <Label className="text-xs text-muted-foreground">实际里程（km）</Label>
              <Input type="number" inputMode="decimal" min="0" step="0.5" autoFocus
                placeholder="例如 6.5"
                value={kmInput}
                onChange={(e) => setKmInput(e.target.value)} />
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">总用时（时 : 分 : 秒）</Label>
              <TimeWheel value={dur} onChange={setDur} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLogOpen(false)}>取消</Button>
            <Button className="border-0" onClick={submitLog}>确认记录</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 偏差 >±30%：问原因是临时起意还是强度不匹配，决定动不动训练计划 */}
      <AlertDialog open={!!devAsk} onOpenChange={(o) => { if (!o) setDevAsk(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>今天的训练量和计划差了不少</AlertDialogTitle>
            <AlertDialogDescription>
              {devAsk && m ? <>计划 {fmtKm(m.target_km)} km，你填了 <b>{fmtKm(devAsk.km)} km</b>，
                偏差超过 30%。是临时起意，还是这节课的强度和你不匹配？</> : null}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {/* sm 断点以上 footer 默认变横排，两个 w-full 按钮会撑破弹窗（溢出屏幕），
              这里强制纵向排列 */}
          <AlertDialogFooter className="!flex-col gap-2">
            <Button variant="outline" className="w-full"
              onClick={() => answerDevAsk("impulse")}>临时起意 —— 只记录，原计划不变</Button>
            <AlertDialogAction className="w-full"
              onClick={() => answerDevAsk("mismatch")}>强度不匹配 —— 按今日跑量调整训练计划</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 大幅超额后的进阶确认 */}
      <AlertDialog open={!!advance} onOpenChange={(o) => { if (!o) setAdvance(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>你今天比计划多跑了 {advance ? fmtKm(Math.max(0, advance.km - (m?.target_km || 0))) : ""} km</AlertDialogTitle>
            <AlertDialogDescription>
              {advance ? <>按你填的 {fmtKm(advance.km)} km、总用时折算，实际配速约 <b>{advance.pace || "—"}</b>，
                明显强于当前计划的配速要求。要不要按这个真实跑力重新安排后面的训练？</> : null}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button variant="outline" disabled={advanceBusy} onClick={() => confirmAdvance(false)}>不用，循序渐进</Button>
            <AlertDialogAction disabled={advanceBusy} onClick={() => confirmAdvance(true)}>
              {advanceBusy ? "正在按跑力重排…" : "是，按我的跑力重排"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 打卡后问卷：训练状态 / 酸痛 / 睡眠 / 疲劳（四个按钮统一入口） */}
      <Dialog open={!!survey} onOpenChange={(o) => { if (!o) setSurvey(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>今天的状态怎么样？</DialogTitle>
            <DialogDescription>
              30 秒答完，系统会结合你的回答调整后续建议（连续伤痛会自动触发降载护栏）。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-5 py-2">
            <div>
              <Label className="text-xs text-muted-foreground">训练中是什么状态？</Label>
              <RadioGroup value={breath} onValueChange={setBreath} className="mt-2">
                {[["gasp", "喘粗气，说不出整句"], ["talk", "能边跑边说话"], ["easy", "强度很低，很轻松"]].map(([v, l]) => (
                  <label key={v} className="flex items-center gap-2 py-1.5 cursor-pointer">
                    <RadioGroupItem value={v} /><span className="text-sm">{l}</span>
                  </label>
                ))}
              </RadioGroup>
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">训练结束后肌肉酸痛？</Label>
              <RadioGroup value={muscle} onValueChange={setMuscle} className="mt-2">
                <div className="flex gap-4">
                  {[["no", "没有"], ["yes", "有酸痛"]].map(([v, l]) => (
                    <label key={v} className="flex items-center gap-1.5 cursor-pointer">
                      <RadioGroupItem value={v} /><span className="text-sm">{l}</span>
                    </label>
                  ))}
                </div>
              </RadioGroup>
            </div>
            <div>
              <Label className="text-xs text-muted-foreground">关节不适？（膝盖 / 脚踝 / 小腿，连续上报会触发降载）</Label>
              <RadioGroup value={joint} onValueChange={setJoint} className="mt-2">
                <div className="flex gap-4">
                  {[["none", "没有"], ["knee", "膝盖"], ["ankle", "脚踝"], ["calf", "小腿"]].map(([v, l]) => (
                    <label key={v} className="flex items-center gap-1.5 cursor-pointer">
                      <RadioGroupItem value={v} /><span className="text-sm">{l}</span>
                    </label>
                  ))}
                </div>
              </RadioGroup>
            </div>
            <div>
              <Label>今日睡眠：{sleep}/10</Label>
              <Slider value={[sleep]} min={1} max={10} onValueChange={([v]) => setSleep(v)} />
            </div>
            <div>
              <Label>疲劳程度：{fatigue}/10</Label>
              <Slider value={[fatigue]} min={1} max={10} onValueChange={([v]) => setFatigue(v)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="ghost" disabled={surveyBusy} onClick={() => setSurvey(null)}>跳过</Button>
            <Button disabled={surveyBusy} onClick={submitSurvey}>
              {surveyBusy ? "正在生成拉伸建议…" : "提交并看拉伸建议"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 跑后拉伸提醒：LLM 生成，规则模板兜底 */}
      <Dialog open={!!stretch} onOpenChange={(o) => { if (!o) setStretch(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <PersonStanding className="h-5 w-5 text-primary" />别忘了拉伸
              {stretch ? (
                <Badge variant="secondary" className="text-[10px] px-1.5">
                  {stretch.source === "llm" ? "AI 生成" : "规则生成"}
                </Badge>
              ) : null}
            </DialogTitle>
          </DialogHeader>
          {stretch ? (
            <div className="space-y-3 py-2">
              <p className="text-sm text-muted-foreground leading-relaxed">{stretch.text}</p>
              {stretch.suggestion ? (
                <p className="text-xs bg-muted/60 rounded px-2.5 py-2 leading-relaxed flex gap-1.5">
                  <Lightbulb className="h-3.5 w-3.5 shrink-0 mt-0.5 text-primary" />{stretch.suggestion}
                </p>
              ) : null}
            </div>
          ) : null}
          <DialogFooter>
            <Button onClick={() => setStretch(null)}>收到，这就去</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 时间对不上时的弹窗：不是不练，时候未到 */}
      <AlertDialog open={notTimeOpen} onOpenChange={setNotTimeOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>不是不练，时候未到</AlertDialogTitle>
            <AlertDialogDescription>
              {m
                ? <>现在打卡的应该是「今天」的课，而当前显示的是 <b>{m.dow}</b> 的{m.type_cn}。到 {m.dow} 跑完再打卡；真跑不了，那天点「今天跑不了」也一样。</>
                : "今天课表上没有排课，好好休息。到上课那天再来打卡。"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => setNotTimeOpen(false)}>好的，到时候练</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 「今天跑不了」二次确认：确认 → 记录并进入状态记录卡；点错了 → 关闭不记录 */}
      <AlertDialog open={skipConfirm} onOpenChange={setSkipConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认无误？</AlertDialogTitle>
            <AlertDialogDescription>
              这节课将记为「今天跑不了」。跳过的课不用补，计划会按原计划继续，
              之后进入 30 秒状态记录。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button variant="outline" onClick={() => setSkipConfirm(false)}>点错了</Button>
            <AlertDialogAction onClick={() => { setSkipConfirm(false); setSkipKindOpen(true) }}>
              确认，今天跑不了
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 跳过方式记录：停练 or 室内力量（月历上会区分展示） */}
      <Dialog open={skipKindOpen} onOpenChange={(o) => { if (!o) setSkipKindOpen(false) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>今天是怎么安排的？</DialogTitle>
            <DialogDescription>
              记一笔，月历里就能看到这些天你不是「偷懒」，是在恢复或变强。
            </DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-2 py-2">
            <Button variant="outline" className="h-16 flex-col gap-1 bg-card"
              onClick={() => { setSkipKindOpen(false); log("skipped", undefined, undefined, "rest") }}>
              <span className="text-base font-bold">彻底休息</span>
              <span className="text-[11px] text-muted-foreground font-normal">恢复也是训练</span>
            </Button>
            <Button className="h-16 flex-col gap-1" variant="secondary"
              onClick={() => { setSkipKindOpen(false); log("skipped", undefined, undefined, "strength") }}>
              <span className="text-base font-bold">室内力量训练</span>
              <span className="text-[11px] text-muted-foreground font-normal">核心/下肢力量</span>
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 月历：当月每天的跑量/配速与停练原因 */}
      <MonthCalendar open={calendarOpen} onOpenChange={setCalendarOpen} uid={uid ?? ""} />
    </Shell>
  )
}

function CitySheet({ current, onDone }: { current: string; onDone: (msg: string) => void }) {
  const uid = loadUser()!
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  // 防误触：切换城市会调整后续排期，选定城市后再确认一次
  const [pending, setPending] = useState<CityInfo | null>(null)

  async function submit(sel: CityInfo) {
    setBusy(true)
    try {
      const r = await api.post<{ ok: boolean; plan_id?: string; message?: string }>("/v1/user/city", {
        user_id: uid, city: sel.name, lat: sel.lat, lon: sel.lon,
      })
      setOpen(false)
      if (r.plan_id) savePlan(r.plan_id)          // 新计划 id 写回缓存，周审视页直接可用
      onDone(r.message || `已切换到${sel.name}`)
    } catch (e) {
      onDone("切换失败：" + (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <button className="w-full text-left text-sm bg-card border rounded-2xl px-3.5 py-3 mt-3 text-muted-foreground flex items-center gap-2 shadow-sm">
          <MapPin className="h-4 w-4 text-primary shrink-0" />当前城市：<b className="text-foreground">{current}</b>
          <span className="ml-auto text-xs">切换</span>
        </button>
      </SheetTrigger>
      <SheetContent side="bottom" className="rounded-t-2xl">
        <SheetHeader><SheetTitle>切换城市</SheetTitle></SheetHeader>
        <div className="px-1 py-4">
          {busy
            ? <p className="text-sm text-muted-foreground text-center py-12">正在按新城市天气调整计划…</p>
            : <CityPicker current={current} onConfirm={(sel) => setPending(sel)} />}
          {!busy && (
            <p className="text-xs text-muted-foreground mt-4">
              切换后今天及之前的打卡记录原样保留，今天之后的训练安排按新城市的天气（体感温度、降雨硬约束）执行。
            </p>
          )}
        </div>
      </SheetContent>

      {/* 防误触：确认切换城市 */}
      <AlertDialog open={!!pending} onOpenChange={(o) => { if (!o) setPending(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认切换到{pending?.name}？</AlertDialogTitle>
            <AlertDialogDescription>
              今天之后的训练安排将按{pending?.name}的天气（体感温度、降雨硬约束）调整，今天及之前的打卡记录原样保留。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>点错了</AlertDialogCancel>
            <AlertDialogAction onClick={() => { const p = pending; setPending(null); if (p) submit(p) }}>
              确认切换
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Sheet>
  )
}

export function NavBar({ weekNo }: { weekNo?: number }) {
  const nav = useNavigate()
  const { pathname } = useLocation()
  const items = [
    { to: "/", label: "今日卡", icon: House, match: (p: string) => p === "/" || p.startsWith("/rearrange") || p.startsWith("/degrade") },
    { to: `/week/${weekNo ?? 1}`, label: "周审视", icon: CalendarDays, match: (p: string) => p.startsWith("/week") },
    { to: "/metrics", label: "指标", icon: ChartNoAxesColumnIncreasing, match: (p: string) => p.startsWith("/metrics") },
  ]
  return (
    <div className="fixed bottom-0 left-1/2 -translate-x-1/2 w-full max-w-md bg-background/85 backdrop-blur border-t z-40">
      <div className="flex">
        {items.map(({ to, label, icon: Icon, match }) => {
          const active = match(pathname)
          return (
            <button key={label} className={`flex-1 py-2.5 flex flex-col items-center gap-0.5 text-[11px] transition-colors
              ${active ? "text-primary font-semibold" : "text-muted-foreground"}`}
              onClick={() => nav(to)}>
              <Icon className="h-5 w-5" />
              {label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

/** 未来 7 天气温迷你趋势图：纯 SVG 折线（<1KB，无图表库），硬约束日用琥珀色圆点标出 */
function WxTrend({ week }: { week: { date: string; t_max: number; hard: boolean }[] }) {
  if (week.length < 2) return null
  const W = 300, H = 44, PAD = 6
  const temps = week.map((d) => d.t_max)
  const lo = Math.min(...temps), hi = Math.max(...temps)
  const span = Math.max(hi - lo, 1)
  const pts = week.map((d, i) => [
    PAD + (i * (W - 2 * PAD)) / (week.length - 1),
    H - PAD - ((d.t_max - lo) / span) * (H - 2 * PAD),
  ] as const)
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ")
  const dow = ["日", "一", "二", "三", "四", "五", "六"]
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mt-1.5 w-full" aria-hidden>
      <path d={line} fill="none" stroke="rgba(255,255,255,0.75)" strokeWidth={2}
        strokeLinecap="round" strokeLinejoin="round" />
      {pts.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={i === 0 ? 3.5 : 2.5}
          fill={week[i].hard ? "#fbbf24" : "#fff"} />
      ))}
      {pts.map(([x], i) => (
        <text key={`t${i}`} x={x} y={H - 0.5} textAnchor="middle" fontSize={7.5}
          fill="rgba(255,255,255,0.7)">
          {i === 0 ? "今" : "周" + dow[new Date(`${week[i].date}T00:00:00`).getDay()]}
        </text>
      ))}
    </svg>
  )
}

function paceSec(pace: string): number {
  const m = pace.match(/(\d+):(\d+)/)
  return m ? Number(m[1]) * 60 + Number(m[2]) : 360
}

/** 时分秒滚轮选择器：与注册页城市选择同款滑轮交互（滚动 + 点击） */
function TimeWheel({ value, onChange }: {
  value: { h: number; m: number; s: number }
  onChange: (v: { h: number; m: number; s: number }) => void
}) {
  const cols = [
    { key: "h" as const, label: "时", max: 9, unit: "时" },
    { key: "m" as const, label: "分", max: 59, unit: "分" },
    { key: "s" as const, label: "秒", max: 59, unit: "秒" },
  ]
  // 打开弹窗时把已选值（默认值=课程估算用时）滚进可视区，否则预填了也看不见
  useEffect(() => {
    document.querySelectorAll("[data-timewheel-selected]").forEach((el) =>
      el.scrollIntoView({ block: "center" }))
  }, [])
  return (
    <div className="flex h-[180px] rounded-xl border overflow-hidden mt-1.5">
      {cols.map((c) => (
        <div key={c.key} className="flex-1 overflow-y-auto py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          <div className="text-center text-[10px] text-muted-foreground sticky top-0 bg-background/90 py-0.5">{c.unit}</div>
          {Array.from({ length: c.max + 1 }, (_, i) => (
            <button key={i} type="button"
              {...(value[c.key] === i ? { "data-timewheel-selected": "1" } : {})}
              onClick={() => onChange({ ...value, [c.key]: i })}
              className={`w-full py-2.5 text-center text-base transition-colors
                ${value[c.key] === i ? "text-primary font-bold bg-primary/5" : "text-muted-foreground"}`}>
              {String(i).padStart(2, "0")}
            </button>
          ))}
        </div>
      ))}
    </div>
  )
}

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="app-bg min-h-screen flex justify-center">
      <div className="w-full max-w-md px-5 pt-8 pb-28">{children}</div>
    </div>
  )
}
