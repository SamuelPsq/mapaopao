import { useEffect, useRef, useState } from "react"
import { useNavigate, useSearchParams } from "react-router"
import { api, loadUser, type PreviewResp, type PlanOption, STRATEGY_CN } from "@/lib/api"
import { fmtKm } from "@/lib/format"
import { Button } from "@/components/ui/button"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Shell } from "./Today"

export default function Rearrange() {
  const nav = useNavigate()
  const [sp] = useSearchParams()
  const uid = loadUser()
  const [data, setData] = useState<PreviewResp | null>(null)
  const [err, setErr] = useState("")
  const [busy, setBusy] = useState(false)
  // 防误触：接受方案前先确认（单向：替换本周剩余课表）
  const [accepting, setAccepting] = useState<null | "main" | "alt">(null)
  const t0 = useRef(Date.now())

  useEffect(() => {
    if (!uid) { nav("/login", { replace: true }); return }
    (async () => {
      try {
        const today = await api.get<{ week_no: number }>(`/v1/today?user_id=${uid}`)
        const trigger = sp.get("trigger") || "manual"
        setData(await api.post<PreviewResp>("/v1/rearrange/preview", {
          user_id: uid, week_no: today.week_no, trigger_src: trigger,
        }))
      } catch (e) { setErr((e as Error).message) }
    })()
  }, [uid, nav, sp])

  if (err) return <Shell><p className="text-muted-foreground">{err}</p></Shell>
  if (!data) return <Shell><p className="text-muted-foreground">正在计算方案（规则引擎，纯 CPU）…</p></Shell>

  async function decide(result: "accept" | "reject" | "manual", useAlt = false) {
    if (!data?.event_id) return
    setBusy(true)
    try {
      await api.post(`/v1/rearrange/${data.event_id}/decision`, {
        result, use_alt: useAlt, decision_ms: Date.now() - t0.current,
      })
      nav(result === "manual" ? "/" : "/?done=1")
    } finally { setBusy(false) }
  }

  return (
    <Shell>
      <div className="mb-5">
        <h2 className="text-xl font-bold">重排方案</h2>
        <p className="text-xs text-muted-foreground mt-1">
          候选生成 → 全量过护栏 → 打分排序 · 缺练不跨周补账
        </p>
      </div>

      {!data.main && <p className="text-muted-foreground">{data.message || "暂无可调整方案"}</p>}

      {data.main && (
        <>
          <OptionCard opt={data.main} rank="主方案" highlight
            ai={data.ai_recommendation?.choice === "main" ? data.ai_recommendation : null} />
          {data.alt && (
            <div className="mt-4">
              <OptionCard opt={data.alt} rank="备选"
                ai={data.ai_recommendation?.choice === "alt" ? data.ai_recommendation : null} />
            </div>
          )}
          <div className="grid grid-cols-3 gap-2 mt-6">
            <Button disabled={busy} onClick={() => setAccepting("main")}>接受主方案</Button>
            <Button variant="secondary" disabled={busy || !data.alt} onClick={() => setAccepting("alt")}>用备选</Button>
            <Button variant="outline" disabled={busy} onClick={() => decide("manual")}>我自己改</Button>
          </div>
          <Button variant="ghost" className="w-full mt-2" disabled={busy} onClick={() => decide("reject")}>保持原计划</Button>

          {/* 防误触：接受方案会替换本周剩余课表，先确认 */}
          <AlertDialog open={!!accepting} onOpenChange={(o) => { if (!o) setAccepting(null) }}>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认采用{accepting === "alt" ? "备选" : "主"}方案？</AlertDialogTitle>
                <AlertDialogDescription>
                  本周剩余课表将被替换，已完成的打卡记录保留；之后想再调整，可以重新触发重排。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>点错了</AlertDialogCancel>
                <AlertDialogAction onClick={() => { const a = accepting; setAccepting(null); if (a) decide("accept", a === "alt") }}>
                  确认采用
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </>
      )}

      {/* 护栏留痕 */}
      <div className="mt-8">
        <div className="text-xs font-medium text-muted-foreground mb-2">护栏校验留痕（每个候选都过，含 pass）</div>
        <div className="space-y-1.5">
          {data.guard_notes.map((g, i) => (
            <div key={i} className="text-xs flex items-center gap-2 bg-muted/50 rounded px-2.5 py-1.5">
              <Badge variant={g.action === "block" ? "destructive" : g.action === "patch" ? "default" : "secondary"} className="text-[10px]">
                {g.action}
              </Badge>
              <span className="text-muted-foreground">{STRATEGY_CN[g.strategy] || g.strategy}</span>
              {g.hit_rule && <span className="text-amber-700">{g.hit_rule}</span>}
              {g.reason_user && <span className="truncate text-muted-foreground/80">{g.reason_user}</span>}
            </div>
          ))}
        </div>
      </div>
    </Shell>
  )
}

function OptionCard({ opt, rank, highlight, ai }: { opt: PlanOption; rank: string; highlight?: boolean; ai?: PreviewResp["ai_recommendation"] }) {
  const changed = opt.sessions.filter((s) => s.note || s.status === "cancelled_by_rearrange")
  return (
    <Card className={highlight ? "border-2 border-primary" : ""}>
      <CardContent className="pt-5">
        <div className="flex items-center justify-between mb-3">
          <Badge variant={highlight ? "default" : "secondary"}>{rank}</Badge>
          <Badge variant="outline">{STRATEGY_CN[opt.strategy]}</Badge>
        </div>
        {ai ? (
          <div className="mb-3 text-xs bg-orange-500/10 text-orange-700 rounded px-2.5 py-2 leading-relaxed">
            🤖 AI 推荐这个方案（{ai.source === "llm" ? "LLM + 知识库" : "规则"}）：{ai.reason}
          </div>
        ) : null}
        <div className="space-y-2 text-sm">
          <Line k="为什么" v={opt.explain.reason} />
          <Line k="怎么改" v={opt.explain.what_changed} strong />
          <Line k="代价" v={opt.explain.cost} />
          <Line k="后续" v={opt.explain.reassurance} />
        </div>
        {changed.length > 0 && (
          <div className="mt-3 border-t pt-3 space-y-1">
            {changed.map((s) => (
              <div key={s.session_id} className="text-xs flex items-center gap-2">
                {s.status === "cancelled_by_rearrange"
                  ? <span className="text-red-600">✕ {s.dow} {s.type_cn} {fmtKm(s.target_km)}km（取消）</span>
                  : <span className="text-muted-foreground">→ {s.dow} {s.type_cn} {fmtKm(s.target_km)}km {s.note && `· ${s.note}`}</span>}
              </div>
            ))}
          </div>
        )}
        {opt.guard_action === "patch" && opt.guard_note && (
          <div className="mt-3 text-xs bg-amber-500/10 text-amber-700 rounded px-2.5 py-2">
            🛡️ 护栏自动修正：{opt.guard_note}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function Line({ k, v, strong }: { k: string; v: string; strong?: boolean }) {
  return (
    <div className="flex gap-2">
      <span className="text-muted-foreground shrink-0 w-10">{k}</span>
      <span className={strong ? "font-medium" : ""}>{v}</span>
    </div>
  )
}
