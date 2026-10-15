import { useEffect, useState } from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { api } from "../lib/api"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"

interface DayEntry {
  date: string            // YYYY-MM-DD
  kind: string            // run / rest / strength / injury / weather / skip / missed / future / none
  km: number | null
  pace: string
  reason: string
  type_cn: string
}

interface CalendarResp {
  month: string
  today: string
  days: DayEntry[]
}

/** 月历：每天标注跑量/配速、休息日、停练原因（伤病醒目红、天气琥珀、力量蓝色） */
export default function MonthCalendar({ open, onOpenChange, uid }: {
  open: boolean
  onOpenChange: (o: boolean) => void
  uid: string
}) {
  const [ym, setYm] = useState(() => {
    const n = new Date()
    return `${n.getFullYear()}-${String(n.getMonth() + 1).padStart(2, "0")}`
  })
  const [data, setData] = useState<CalendarResp | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    api.get<CalendarResp>(`/v1/calendar?user_id=${uid}&month=${ym}`)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [open, ym, uid])

  function shift(delta: number) {
    const [y, m] = ym.split("-").map(Number)
    const nd = new Date(y, m - 1 + delta, 1)
    setYm(`${nd.getFullYear()}-${String(nd.getMonth() + 1).padStart(2, "0")}`)
  }

  // 月首星期（周一=0）对齐网格
  const [y, m] = ym.split("-").map(Number)
  const firstDow = (new Date(y, m - 1, 1).getDay() + 6) % 7
  const title = `${y} 年 ${m} 月`

  const cell = (d: DayEntry | undefined) => {
    if (!d) return <div key="pad" />
    const isToday = d.date === data?.today
    let body: React.ReactNode = null
    let cls = "text-muted-foreground"
    switch (d.kind) {
      case "run":
        body = <><b>{d.km}</b> km{d.pace ? <span className="block">{d.pace}</span> : null}</>
        cls = "bg-primary/10 text-foreground"
        break
      case "rest":
        body = "休息日"
        break
      case "strength":
        body = "室内力量"
        cls = "bg-blue-500/10 text-blue-700"
        break
      case "injury":
        body = <>🔴 {d.reason}</>
        cls = "bg-red-500/15 text-red-700 border border-red-500/30 font-medium"
        break
      case "weather":
        body = <>🌧 {d.reason}</>
        cls = "bg-amber-500/15 text-amber-700"
        break
      case "skip":
        body = "停练"
        cls = "bg-muted text-muted-foreground"
        break
      case "missed":
        body = `未打卡·${d.type_cn || "计划课"}`
        cls = "border border-dashed border-orange-400/50 text-orange-700"
        break
      case "future":
        body = d.type_cn
        cls = "text-muted-foreground/70"
        break
    }
    return (
      <div key={d.date} className={`min-h-[52px] rounded-lg p-1 text-[10px] leading-tight ${cls} ${isToday ? "ring-2 ring-primary" : ""}`}>
        <div className="text-[10px] font-semibold opacity-80">{Number(d.date.slice(8))}</div>
        <div className="mt-0.5">{body}</div>
      </div>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center justify-between pr-6">
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => shift(-1)} aria-label="上个月">
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span>{title}</span>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => shift(1)} aria-label="下个月">
              <ChevronRight className="h-4 w-4" />
            </Button>
          </DialogTitle>
        </DialogHeader>
        {loading ? (
          <p className="text-sm text-muted-foreground text-center py-12">正在加载训练记录…</p>
        ) : (
          <>
            <div className="grid grid-cols-7 gap-1 text-center text-[10px] text-muted-foreground mb-1">
              {["一", "二", "三", "四", "五", "六", "日"].map(d => <div key={d}>{d}</div>)}
            </div>
            <div className="grid grid-cols-7 gap-1">
              {Array.from({ length: firstDow }).map((_, i) => <div key={`pad${i}`} />)}
              {(data?.days ?? []).map(d => cell(d))}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1 mt-3 text-[10px] text-muted-foreground">
              <span><i className="inline-block h-2 w-2 rounded-sm bg-primary/40 mr-1" />跑步（km/配速）</span>
              <span>休息日</span>
              <span><i className="inline-block h-2 w-2 rounded-sm bg-blue-500/40 mr-1" />室内力量</span>
              <span><i className="inline-block h-2 w-2 rounded-sm bg-red-500/40 mr-1" />伤病停练</span>
              <span><i className="inline-block h-2 w-2 rounded-sm bg-amber-500/40 mr-1" />天气停跑</span>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
