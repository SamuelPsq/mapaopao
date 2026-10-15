import { useCallback, useEffect, useRef, useState } from "react"
import { useLocation, useNavigate } from "react-router"
import { api, loadUser, clearUser, savePlan, type TodayCard } from "@/lib/api"
import { fmtKm } from "@/lib/format"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { AlertDialog, AlertDialogAction, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Slider } from "@/components/ui/slider"
import { Label } from "@/components/ui/label"
import { CityPicker } from "@/components/CityPicker"
import type { CityInfo } from "@/lib/cities"
import { CalendarDays, ChartNoAxesColumnIncreasing, CloudSun, Dumbbell, Flame, House, MapPin, MoonStar, Target, Timer, Wrench } from "lucide-react"

export default function Today() {
  const nav = useNavigate()
  const uid = loadUser()
  const [card, setCard] = useState<TodayCard | null>(null)
  const [err, setErr] = useState("")
  const [toast, setToast] = useState("")
  const [notTimeOpen, setNotTimeOpen] = useState(false)
  // 打卡弹窗：完成了 / 只跑了一部分 / 超额完成 三个按钮统一走「里程 + 时分秒」弹窗
  const [logDialog, setLogDialog] = useState<null | { mode: "full" | "partial" | "over" }>(null)
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

  async function log(status: "full" | "partial" | "skipped", actualKm?: number, durationSec?: number) {
    if (!isToday) { setNotTimeOpen(true); return }          // 时间对不上：不是不练，时候未到
    const actual_km = actualKm ?? (status === "full" ? m!.target_km : 0)
    const r = await api.post<{ vdot_update?: string; over_note?: string; under_note?: string; ai_tune_note?: string; rearrange_ready?: boolean; advance_prompt?: boolean; advance_pace?: string }>(
      `/v1/session/${m!.session_id}/log`,
      { status, actual_km, actual_pace: "", duration_sec: durationSec ?? 0, rpe: null })
    // 大幅超额（>3km）：先问要不要按真实跑力进阶重排（确认完再进问卷）
    if (r.advance_prompt) {
      pendingSurvey.current = { status, typeCn: m!.type_cn, km: actual_km }
      setAdvance({ km: actual_km, sec: durationSec ?? 0, pace: r.advance_pace || "" })
      refresh()
      return
    }
    setToast(status === "full" ? "已记录完成，继续保持 💪" : status === "partial" ? "已记录，部分完成也算数" : "已记录，别担心，不用补")
    if (r.vdot_update) setToast(r.vdot_update)
    if (r.over_note) setToast(r.over_note)
    if (r.under_note) setToast(r.under_note)
    if (r.ai_tune_note) setToast(r.ai_tune_note)
    // 统一进入打卡后问卷（含「今天跑不了」）
    setSurvey({ status, typeCn: m!.type_cn, km: actual_km })
    refresh()
  }

  // 打开打卡弹窗：完成了预填目标里程和预计用时
  function openLogDialog(mode: "full" | "partial" | "over") {
    if (!isToday) { setNotTimeOpen(true); return }
    setKmInput(mode === "full" && m ? String(m.target_km) : "")
    if (m) {
      const est = Math.round(m.target_km * paceSec(m.target_pace))
      setDur({ h: Math.floor(est / 3600), m: Math.floor((est % 3600) / 60), s: est % 60 })
    }
    setLogDialog({ mode })
  }

  // 填完里程 + 时分秒后的提交
  function submitLog() {
    if (!logDialog || !m) return
    const km = Number(kmInput)
    const target = m.target_km
    const durationSec = dur.h * 3600 + dur.m * 60 + dur.s
    if (!Number.isFinite(km) || km <= 0) { setToast("请输入大于 0 的公里数"); return }
    if (durationSec <= 0) { setToast("请填一下总用时（时分秒）"); return }
    if (logDialog.mode === "over" && km <= target) { setToast(`超额完成要大于目标 ${fmtKm(target)} km 哦`); return }
    if (logDialog.mode === "partial" && km >= target) { setToast(`只跑了一部分应该小于目标 ${fmtKm(target)} km，超过了就选「超额完成」`); return }
    setLogDialog(null)
    setKmInput("")
    log(logDialog.mode, Math.round(km * 10) / 10, durationSec)
  }

  // 进阶确认：是 → 按真实跑力重排剩余周；否 → 保持原计划，提醒循序渐进。
  // 两个分支结束后都进入打卡后问卷。
  async function confirmAdvance(yes: boolean) {
    if (!advance) return
    const a = advance
    setAdvance(null)
    if (!yes) {
      setToast("好——那就循序渐进，超额的部分当作状态好的奖励，后面的课按原计划来")
      setSurvey(pendingSurvey.current)
      return
    }
    try {
      const r = await api.post<{ note: string }>("/v1/user/advance",
        { user_id: uid, actual_km: a.km, duration_sec: a.sec })
      setToast(r.note || "已按照您的跑力重新安排")
    } catch (e) {
      setToast("重排失败：" + (e as Error).message)
    }
    setSurvey(pendingSurvey.current)
    refresh()
  }

  // 问卷提交：状态上报（含训练状态/酸痛/睡眠/疲劳）→ 拉伸建议弹窗
  async function submitSurvey() {
    if (!survey) return
    const sv = survey
    setSurvey(null)
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
        <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-primary to-[hsl(28,95%,45%)] text-primary-foreground shadow-md shadow-primary/20 mb-4">
          <div className="absolute -right-6 -top-8 h-24 w-24 rounded-full bg-white/10" />
          <div className="relative px-4 py-3.5">
            <div className="flex items-center justify-between mb-1.5">
              <div className="text-[11px] font-medium text-white/85">
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
            {card.reason_line ? <div className="text-[11px] text-white/80 mt-2 border-t border-white/15 pt-2">{card.reason_line}</div> : null}
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
        <div className="text-sm bg-amber-500/10 text-amber-700 rounded-2xl px-3.5 py-2.5 mb-4">
          ⚠️ {card.hard_days.join("、")}天气硬约束，系统已准备调整方案
        </div>
      ) : null}

      {/* 重排建议 */}
      {card.suggestion ? (
        <button className="w-full text-left text-sm bg-primary/10 text-primary rounded-2xl px-3.5 py-3 mb-4 font-medium flex items-center gap-2"
          onClick={() => nav(`/rearrange?trigger=${card.suggestion!.trigger_src}`)}>
          <Wrench className="h-4 w-4 shrink-0" />{card.suggestion.message}
        </button>
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

      {/* 打卡按钮：三个都会弹窗填里程 + 总用时（时分秒） */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <Button onClick={() => openLogDialog("full")}>完成了</Button>
        <Button variant="secondary" onClick={() => openLogDialog("partial")}>只跑了一部分</Button>
        <Button variant="outline" className="bg-card" onClick={() => log("skipped")}>今天跑不了</Button>
        <Button className="bg-orange-500 hover:bg-orange-600 text-white border-0"
          onClick={() => openLogDialog("over")}>超额完成</Button>
      </div>

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

      {/* 打卡弹窗：里程 + 总用时（时分秒滚轮） */}
      <Dialog open={!!logDialog} onOpenChange={(o) => { if (!o) setLogDialog(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{{ full: "完成了 · 记录今天的训练", partial: "只跑了一部分 · 实际跑了多少？", over: "超额完成 · 实际跑了多少？" }[logDialog?.mode || "full"]}</DialogTitle>
            <DialogDescription>
              {m ? <>这节课目标是 <b>{fmtKm(m.target_km)} km</b>。填实际里程和总用时，系统会自动换算配速，并影响接下来的排期。</> : null}
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
            <Button variant="outline" onClick={() => setLogDialog(null)}>取消</Button>
            <Button className={logDialog?.mode === "over" ? "bg-orange-500 hover:bg-orange-600 text-white border-0" : ""}
              onClick={submitLog}>确认记录</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
            <Button variant="outline" onClick={() => confirmAdvance(false)}>不用，循序渐进</Button>
            <AlertDialogAction onClick={() => confirmAdvance(true)}>是，按我的跑力重排</AlertDialogAction>
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
            <Button variant="ghost" onClick={() => setSurvey(null)}>跳过</Button>
            <Button onClick={submitSurvey}>提交并看拉伸建议</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 跑后拉伸提醒：LLM 生成，规则模板兜底 */}
      <Dialog open={!!stretch} onOpenChange={(o) => { if (!o) setStretch(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              🧘 别忘了拉伸
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
                <p className="text-xs bg-muted/60 rounded px-2.5 py-2 leading-relaxed">💡 {stretch.suggestion}</p>
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
    </Shell>
  )
}

function CitySheet({ current, onDone }: { current: string; onDone: (msg: string) => void }) {
  const uid = loadUser()!
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)

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
            ? <p className="text-sm text-muted-foreground text-center py-12">正在按新城市天气重新生成计划…</p>
            : <CityPicker current={current} onConfirm={submit} />}
          {!busy && (
            <p className="text-xs text-muted-foreground mt-4">
              切换后将按新城市的天气（体感温度、降雨硬约束）重新生成配套训练计划，当前计划进度会清零。
            </p>
          )}
        </div>
      </SheetContent>
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
  return (
    <div className="flex h-[180px] rounded-xl border overflow-hidden mt-1.5">
      {cols.map((c) => (
        <div key={c.key} className="flex-1 overflow-y-auto py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          <div className="text-center text-[10px] text-muted-foreground/70 sticky top-0 bg-background/90 py-0.5">{c.unit}</div>
          {Array.from({ length: c.max + 1 }, (_, i) => (
            <button key={i} type="button"
              onClick={() => onChange({ ...value, [c.key]: i })}
              className={`w-full py-2 text-center text-sm transition-colors
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
    <div className="min-h-screen bg-background flex justify-center">
      <div className="w-full max-w-md px-5 pt-8 pb-28">{children}</div>
    </div>
  )
}
