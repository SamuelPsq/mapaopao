import { useCallback, useEffect, useState } from "react"
import { useNavigate, useParams } from "react-router"
import { api, loadUser, loadPlan, savePlan, type WeekReview } from "@/lib/api"
import { fmtKm } from "@/lib/format"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Shell, NavBar } from "./Today"
import { ChevronLeft, ChevronRight, CloudSun, Flame, Lightbulb } from "lucide-react"

const PHASE_CN: Record<string, string> = { load: "加载周", cut: "减量周", taper: "赛前减量" }
const STATUS_CN: Record<string, string> = {
  planned: "待完成", done: "已完成", partial: "部分完成", skipped: "未跑",
  cancelled_by_rearrange: "已重排取消", cancelled_before_week: "周前取消",
}

export default function Week() {
  const nav = useNavigate()
  const { no } = useParams()
  const uid = loadUser()
  const [w, setW] = useState<WeekReview | null>(null)
  const [err, setErr] = useState("")
  const [tip, setTip] = useState<{ id: string; name: string; text: string } | null>(null)

  // 跑步知识：每次进入/刷新页面随机抽一条新手向知识（禁忌 + 训练前中后）
  useEffect(() => {
    api.get<{ id: string; name: string; text: string }>("/v1/kb/tip")
      .then(setTip)
      .catch(() => setTip(null))
  }, [no])

  const refresh = useCallback(async () => {
    if (!uid) { nav("/login", { replace: true }); return }
    try {
      // 有缓存的 plan_id 且 URL 带周号时只需一次请求，不再 today→week 串行瀑布
      const planId = loadPlan()
      const fromUrl = Math.min(16, Math.max(1, Number(no) || 0))
      if (planId && fromUrl) {
        try {
          setW(await api.get<WeekReview>(`/v1/plan/${planId}/week/${fromUrl}`))
          return
        } catch {
          // 缓存的 plan 已作废（如修改训练计划/切换城市后本地缓存没跟上）→ 回退到 active 计划
        }
      }
      const today = await api.get<{ plan_id: string; week_no: number }>(`/v1/today?user_id=${uid}`)
      savePlan(today.plan_id)             // 同步缓存，下次直接命中新计划
      const weekNo = fromUrl || Math.min(16, Math.max(1, today.week_no))
      setW(await api.get<WeekReview>(`/v1/plan/${today.plan_id}/week/${weekNo}`))
    } catch (e) { setErr((e as Error).message) }
  }, [uid, nav, no])

  useEffect(() => { refresh() }, [refresh])

  // 回到本页时自动重拉：降载/重排在别处触发后，不需要手动刷新才能看到新课表
  useEffect(() => {
    const onFocus = () => refresh()
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [refresh])

  if (err) return <Shell><p className="text-muted-foreground">{err}</p></Shell>
  if (!w) return <Shell><p className="text-muted-foreground">加载中…</p></Shell>

  const rate = Math.round(w.adherence.rate * 100)
  const R = 34
  const CIRC = 2 * Math.PI * R

  return (
    <Shell>
      <div className="flex items-baseline justify-between mb-1">
        <h2 className="text-xl font-bold">第 {w.week_no} 周 · {PHASE_CN[w.phase] || w.phase}</h2>
        <div className="flex gap-1">
          <Button variant="outline" size="icon" className="h-8 w-8 rounded-full" disabled={w.week_no <= 1}
            onClick={() => nav(`/week/${w.week_no - 1}`)}><ChevronLeft className="h-4 w-4" /></Button>
          <Button variant="outline" size="icon" className="h-8 w-8 rounded-full" disabled={w.week_no >= 16}
            onClick={() => nav(`/week/${w.week_no + 1}`)}><ChevronRight className="h-4 w-4" /></Button>
        </div>
      </div>
      <div className="flex gap-2 mb-5">
        <Badge variant="outline" className="gap-1"><CloudSun className="h-3 w-3" />{w.is_current ? "计划" : "预计"} {fmtKm(w.planned_km)} km</Badge>
        {w.is_dropped ? (
          <Badge className="bg-amber-500 hover:bg-amber-500 gap-1"><Flame className="h-3 w-3" />本周降载</Badge>
        ) : null}
      </div>
      {!w.is_current ? (
        <p className="text-xs text-muted-foreground/80 -mt-3 mb-5">
          非本周课表为预计量，会根据城市天气（高温、降雨硬约束）实时调整
        </p>
      ) : null}

      {/* 执行率：分母取周初快照 */}
      <div className="bg-card border rounded-2xl p-4 mb-5 shadow-sm">
        <div className="flex items-center gap-4">
          <div className="relative h-20 w-20 shrink-0">
            <svg viewBox="0 0 80 80" className="h-full w-full -rotate-90">
              <circle cx="40" cy="40" r={R} fill="none" className="stroke-muted" strokeWidth="8" />
              <circle cx="40" cy="40" r={R} fill="none" className="stroke-primary" strokeWidth="8"
                strokeLinecap="round" strokeDasharray={CIRC}
                strokeDashoffset={CIRC * (1 - Math.min(1, rate / 100))}
                style={{ transition: "stroke-dashoffset .6s ease" }} />
            </svg>
            <div className="absolute inset-0 flex items-center justify-center">
              <span className="text-xl font-black">{rate}%</span>
            </div>
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium">周计划执行率（北极星）</div>
            <div className="text-xs text-muted-foreground mt-1">
              完成 {w.adherence.done} / 周初快照 {w.adherence.planned_snapshot} 次
            </div>
            <div className="text-xs text-muted-foreground/70 mt-0.5">分母冻结于周一 00:00，重排不会美化它</div>
          </div>
        </div>
      </div>

      {/* AI 执行点评：LLM 生成，规则模板兜底 */}
      {w.comment?.text ? (
        <div className="bg-primary/5 border border-primary/20 rounded-2xl px-4 py-3 mb-5 shadow-sm">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-primary">本周点评</span>
            <Badge variant="secondary" className="text-[10px] px-1.5">
              {w.comment.source === "llm" ? "AI 生成" : "规则生成"}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-1 leading-relaxed">{w.comment.text}</p>
          {w.comment.note ? (
            <p className="text-xs bg-muted/60 rounded px-2.5 py-2 mt-2 leading-relaxed">💡 {w.comment.note}</p>
          ) : null}
        </div>
      ) : null}

      {/* 跑步知识：知识库随机抽取，每次刷新换一条 */}
      {tip?.text ? (
        <div className="bg-card border rounded-2xl px-4 py-3 mb-5 shadow-sm">
          <div className="flex items-center gap-2">
            <Lightbulb className="h-4 w-4 text-orange-500" />
            <span className="text-sm font-medium">跑步知识{tip.name ? ` · ${tip.name}` : ""}</span>
          </div>
          <p className="text-sm text-muted-foreground mt-1 leading-relaxed">{tip.text}</p>
        </div>
      ) : null}

      {/* 课表 */}
      <div className="space-y-2 mb-6">
        {w.sessions.map((s) => {
          const dayNum = Number(s.date.slice(8, 10))
          const isDone = s.status === "done" || s.status === "partial"
          const isCancelled = s.status.startsWith("cancelled")
          return (
            <div key={s.session_id}
              className={`flex items-center gap-3 rounded-2xl border px-3 py-3 bg-card shadow-sm transition-colors
                ${isCancelled ? "opacity-50" : ""}`}>
              <div className={`flex h-11 w-11 shrink-0 flex-col items-center justify-center rounded-xl
                ${isDone ? "bg-primary text-primary-foreground"
                  : isCancelled ? "bg-muted text-muted-foreground"
                  : "bg-muted/70 text-foreground"}`}>
                <span className="text-sm font-bold leading-none">{dayNum}</span>
                <span className="text-[10px] leading-none mt-0.5">{s.dow.slice(1)}</span>
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium truncate">
                  {s.type === "long_run" ? "长距离" : s.type_cn} {fmtKm(s.target_km)}km
                  {s.is_key ? <Badge className="ml-2 text-[10px] px-1">关键</Badge> : null}
                  {s.source === "rearranged" ? <Badge variant="secondary" className="ml-1.5 text-[10px] px-1">重排</Badge> : null}
                  {s.source === "ai_tuned" ? <Badge className="ml-1.5 text-[10px] px-1 bg-orange-100 text-orange-600 hover:bg-orange-100 border-0">AI 微调</Badge> : null}
                </div>
                <div className="text-xs text-muted-foreground">配速 {s.target_pace}</div>
              </div>
              <StatusBadge status={s.status} />
            </div>
          )
        })}
      </div>

      {/* 下周预告 */}
      {w.next_week_preview.length > 0 ? (
        <div className="mb-2">
          <div className="text-sm font-medium mb-2">下周预告</div>
          <div className="rounded-2xl border bg-card/60 px-3.5 py-1 shadow-sm">
            {w.next_week_preview.map((s) => (
              <div key={s.session_id} className="text-xs text-muted-foreground flex justify-between border-b last:border-0 py-2">
                <span>{s.dow} · {s.type === "long_run" ? "长距离" : s.type_cn} {fmtKm(s.target_km)}km</span>
                <span>{s.target_pace}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <NavBar weekNo={w.week_no} />
    </Shell>
  )
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    planned: "secondary", done: "default", partial: "default",
    skipped: "destructive", cancelled_by_rearrange: "outline",
  }
  return <Badge variant={(map[status] || "secondary") as never}>{STATUS_CN[status] || status}</Badge>
}
