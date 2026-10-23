import { useCallback, useEffect, useState } from "react"
import { useNavigate } from "react-router"
import { Activity, ClipboardList, Download, FlaskConical, Users, AlertTriangle, ArrowLeft } from "lucide-react"
import { api, loadRole } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

/** 实验室工作台（v3.2 B 端）：概览 / 数据导出 / 问卷管理。仅 teacher 角色可达。 */

interface Overview {
  students: number
  with_active_plan: number
  status_rate_this_week: number
  survey: { instances: number; done: number; rate: number | null }
  injury_reports_14d: number
  pending_suggestions: number
}

interface Suggestion {
  suggestion_id: string
  target_type: string
  target_id: string
  metric: string
  metric_value: number
  suggested: Record<string, number>
  reason_user: string
  created_at: string
}

interface TablePreview {
  name: string
  rows_est: number | null
  fields: string[]
}

interface SurveyRow {
  survey_id: string
  kind: string
  title: string
  schema: { id: string; text: string; type: string; required?: boolean }[]
  active: boolean
  week_no: number | null
}

const KIND_CN: Record<string, string> = { pre: "前测", weekly: "每周", post: "后测" }
const KIND_BADGE: Record<string, string> = {
  pre: "bg-blue-100 text-blue-700",
  weekly: "bg-emerald-100 text-emerald-700",
  post: "bg-purple-100 text-purple-700",
}

export default function Lab() {
  const nav = useNavigate()
  const role = loadRole()
  const [tab, setTab] = useState("overview")
  const [ov, setOv] = useState<Overview | null>(null)
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [msg, setMsg] = useState("")

  const refresh = useCallback(() => {
    api.get<Overview>("/v1/lab/overview").then(setOv).catch((e) => setMsg((e as Error).message))
    api.get<{ items: Suggestion[] }>("/v1/lab/suggestions")
      .then((r) => setSuggestions(r.items)).catch(() => setSuggestions([]))
  }, [])
  useEffect(() => { if (role === "teacher") refresh() }, [role, refresh])

  if (role !== "teacher") {
    return (
      <div className="app-bg min-h-screen flex items-center justify-center">
        <div className="text-center">
          <p className="text-sm text-muted-foreground">这个功能只对实验室账号开放</p>
          <Button variant="outline" className="mt-4" onClick={() => nav("/", { replace: true })}>回到今日卡</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="app-bg min-h-screen flex justify-center">
      <div className="w-full max-w-3xl px-5 pt-8 pb-20">
        <div className="mb-6 flex items-center gap-3">
          <button onClick={() => nav("/")} className="p-2 text-muted-foreground active:scale-95">
            <ArrowLeft className="h-5 w-5" />
          </button>
          <FlaskConical className="h-6 w-6 text-primary" />
          <div>
            <h1 className="text-lg font-bold">实验室工作台</h1>
            <p className="text-xs text-muted-foreground">纵向数据交付 · 匿名化导出 · 问卷回收</p>
          </div>
        </div>

        {msg ? <p className="mb-3 text-sm text-destructive">{msg}</p> : null}

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="overview">概览</TabsTrigger>
            <TabsTrigger value="export">数据导出</TabsTrigger>
            <TabsTrigger value="surveys">问卷管理</TabsTrigger>
          </TabsList>

          {/* ================= 概览 ================= */}
          <TabsContent value="overview" className="mt-5">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatCard icon={<Users className="h-4 w-4" />} label="学生总数" value={ov?.students} />
              <StatCard icon={<Activity className="h-4 w-4" />} label="本周上报率"
                value={ov ? `${Math.round(ov.status_rate_this_week * 100)}%` : undefined} />
              <StatCard icon={<ClipboardList className="h-4 w-4" />} label="本周问卷回收"
                value={ov?.survey.rate != null ? `${Math.round(ov.survey.rate * 100)}%` : undefined}
                sub={ov ? `${ov.survey.done}/${ov.survey.instances}` : ""} />
              <StatCard icon={<AlertTriangle className="h-4 w-4 text-amber-600" />} label="14 天伤痛上报"
                value={ov?.injury_reports_14d} warn={!!ov && ov.injury_reports_14d > 0} />
            </div>

            {/* M3：调参建议待办（生成器 v3.3 上线前为空态） */}
            <div className="mt-6 rounded-2xl border bg-card p-4 shadow-sm">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold">调参建议</h2>
                <Badge variant="secondary" className="text-[10px]">每周一自动生成</Badge>
              </div>
              {suggestions.length === 0 ? (
                <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
                  暂无待处理建议。系统每周一会扫描连续 3 周关键课完成率，
                  不足 50% 时自动生成降量建议，由你审批后生效（只影响今天之后的课，不清进度）。
                </p>
              ) : (
                <ul className="mt-3 space-y-2">
                  {suggestions.map((s) => (
                    <li key={s.suggestion_id} className="rounded-xl bg-muted/50 p-3">
                      <p className="text-xs text-muted-foreground">{s.reason_user}</p>
                      <div className="mt-2 flex gap-2">
                        <Button size="sm" className="h-8"
                          onClick={() => api.post<{ message?: string }>(`/v1/lab/suggestions/${s.suggestion_id}/adopt`)
                            .then((r) => { setMsg(r.message || "已采纳"); refresh() })
                            .catch((e) => setMsg((e as Error).message))}>
                          采纳并调参</Button>
                        <Button size="sm" variant="outline" className="h-8"
                          onClick={() => api.post(`/v1/lab/suggestions/${s.suggestion_id}/dismiss`)
                            .then(refresh).catch((e) => setMsg((e as Error).message))}>
                          忽略</Button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </TabsContent>

          {/* ================= 数据导出 ================= */}
          <TabsContent value="export" className="mt-5"><ExportPanel /></TabsContent>

          {/* ================= 问卷管理 ================= */}
          <TabsContent value="surveys" className="mt-5"><SurveysPanel /></TabsContent>
        </Tabs>
      </div>
    </div>
  )
}

function StatCard({ icon, label, value, sub, warn }: {
  icon: React.ReactNode; label: string; value?: number | string; sub?: string; warn?: boolean
}) {
  return (
    <div className={`rounded-2xl border bg-card p-3 shadow-sm ${warn ? "border-amber-300 bg-amber-50" : ""}`}>
      <div className="flex items-center gap-1.5 text-muted-foreground">{icon}
        <span className="text-[11px]">{label}</span></div>
      <div className="mt-1.5 text-2xl font-black tracking-tight">{value ?? "-"}</div>
      {sub ? <div className="text-[10px] text-muted-foreground">{sub}</div> : null}
    </div>
  )
}

/* ---------------------------------------------------------------- 导出面板 */
function ExportPanel() {
  const [cohort, setCohort] = useState("")
  const [dateFrom, setDateFrom] = useState("2026-01-01")
  const [dateTo, setDateTo] = useState(new Date().toISOString().slice(0, 10))
  const [preview, setPreview] = useState<{ students: number; tables: TablePreview[] } | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState("")

  const doPreview = () => {
    setMsg("")
    api.get<{ students: number; tables: TablePreview[] }>(
      `/v1/lab/export/preview?cohort=${encodeURIComponent(cohort)}&date_from=${dateFrom}&date_to=${dateTo}`)
      .then(setPreview).catch((e) => setMsg((e as Error).message))
  }

  const doExport = async () => {
    setBusy(true); setMsg("")
    try {
      const token = localStorage.getItem("mpaopao_token")
      const r = await fetch("/v1/lab/export", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ cohort, date_from: dateFrom, date_to: dateTo }),
      })
      if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.error || "导出失败，请稍后再试")
      const blob = await r.blob()
      const a = document.createElement("a")
      a.href = URL.createObjectURL(blob)
      a.download = `mapaopao_export_${dateTo}.zip`
      a.click()
      URL.revokeObjectURL(a.href)
      setMsg("已生成并下载导出包（含变量字典与脱敏说明）")
    } catch (e) {
      setMsg((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="rounded-2xl border bg-card p-4 shadow-sm">
      <h2 className="text-sm font-semibold">匿名化数据导出</h2>
      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
        导出包含三张 CSV（周粒度训练执行 / 日粒度状态 / 事件流）、每周问卷回收表、
        变量字典与脱敏说明。用户名与学号不进入导出，学生以匿名编号呈现。
      </p>
      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <label className="text-xs text-muted-foreground">学期批次（留空 = 全部）</label>
          <input value={cohort} onChange={(e) => setCohort(e.target.value)}
            placeholder="如 2026-autumn"
            className="mt-1 h-10 w-full rounded-xl border bg-background/60 px-3 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">开始日期</label>
          <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)}
            className="mt-1 h-10 w-full rounded-xl border bg-background/60 px-3 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">结束日期</label>
          <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)}
            className="mt-1 h-10 w-full rounded-xl border bg-background/60 px-3 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
        </div>
      </div>
      <div className="mt-4 flex gap-2">
        <Button variant="outline" onClick={doPreview}>预览字段与行数</Button>
        <Button disabled={busy} onClick={doExport}>
          <Download className="mr-1.5 h-4 w-4" />{busy ? "生成中…" : "导出 ZIP"}
        </Button>
      </div>
      {msg ? <p className="mt-3 text-xs text-muted-foreground">{msg}</p> : null}
      {preview ? (
        <div className="mt-4 space-y-2">
          <p className="text-xs text-muted-foreground">覆盖学生 {preview.students} 人，各表字段与预估行数：</p>
          {preview.tables.map((t) => (
            <div key={t.name} className="rounded-xl bg-muted/50 p-3">
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs font-semibold">{t.name}</span>
                <Badge variant="secondary" className="text-[10px]">
                  {t.rows_est == null ? "按事件计" : `约 ${t.rows_est} 行`}</Badge>
              </div>
              <p className="mt-1 font-mono text-[11px] leading-relaxed text-muted-foreground">
                {t.fields.join(", ")}</p>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

/* ---------------------------------------------------------------- 问卷面板（含 M4 前测/后测） */
function SurveysPanel() {
  const [rows, setRows] = useState<SurveyRow[]>([])
  const [kind, setKind] = useState("weekly")
  const [title, setTitle] = useState("")
  const [questionsText, setQuestionsText] = useState("")
  const [msg, setMsg] = useState("")

  const refresh = useCallback(() => {
    api.get<{ surveys: SurveyRow[] }>("/v1/lab/surveys").then((r) => setRows(r.surveys))
      .catch((e) => setMsg((e as Error).message))
  }, [])
  useEffect(refresh, [refresh])

  /** 题目用简单文本格式录入：每行一题，`题干 | likert7|bool|text`（| 后可省，默认 likert7） */
  function create() {
    setMsg("")
    if (!title.trim()) { setMsg("请填写问卷标题"); return }
    const questions = questionsText.split("\n").map((l) => l.trim()).filter(Boolean).map((l, i) => {
      const [text, type] = l.split("|").map((x) => x.trim())
      return { id: `q${i + 1}`, text: text || `第 ${i + 1} 题`, type: ["likert7", "bool", "text"].includes(type) ? type : "likert7", required: true }
    })
    if (!questions.length) { setMsg("请至少录入一道题"); return }
    api.post("/v1/lab/surveys", { kind, title, questions })
      .then(() => { setTitle(""); setQuestionsText(""); refresh() })
      .catch((e) => setMsg((e as Error).message))
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border bg-card p-4 shadow-sm">
        <h2 className="text-sm font-semibold">新建问卷</h2>
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label className="text-xs text-muted-foreground">类型</label>
            <div className="mt-1 flex gap-1 rounded-xl bg-muted p-1">
              {["pre", "weekly", "post"].map((k) => (
                <button key={k} onClick={() => setKind(k)}
                  className={`flex-1 rounded-lg py-1.5 text-xs font-medium transition-all
                    ${kind === k ? "bg-card shadow-sm" : "text-muted-foreground"}`}>
                  {KIND_CN[k]}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">标题</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)}
              placeholder={kind === "weekly" ? "每周状态追踪" : "结课评估问卷"}
              className="mt-1 h-10 w-full rounded-xl border bg-background/60 px-3 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
          </div>
        </div>
        <div className="mt-3">
          <label className="text-xs text-muted-foreground">
            题目（每行一题：<code>题干 | likert7/bool/text</code>，默认 likert7 七分制）</label>
          <textarea value={questionsText} onChange={(e) => setQuestionsText(e.target.value)} rows={5}
            placeholder={"本周睡眠质量如何 | likert7\n本周是否有伤痛影响训练 | bool\n还有什么想补充的 | text"}
            className="mt-1 w-full rounded-xl border bg-background/60 p-3 font-mono text-xs outline-none focus:ring-2 focus:ring-primary/30" />
        </div>
        <Button className="mt-3" onClick={create}>创建</Button>
        {msg ? <p className="mt-2 text-xs text-destructive">{msg}</p> : null}
      </div>

      <div className="space-y-2">
        {rows.map((s) => <SurveyCard key={s.survey_id} survey={s} onChanged={refresh} />)}
      </div>
    </div>
  )
}

function SurveyCard({ survey, onChanged }: { survey: SurveyRow; onChanged: () => void }) {
  const [stats, setStats] = useState<{ rate?: number | null; weeks?: { week_ref: string; total: number; done: number }[] } | null>(null)
  const loadStats = () => api.get(`/v1/lab/surveys/${survey.survey_id}/stats`)
    .then(setStats).catch(() => setStats(null))
  return (
    <div className="rounded-2xl border bg-card p-4 shadow-sm">
      <div className="flex items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${KIND_BADGE[survey.kind]}`}>
          {KIND_CN[survey.kind]}</span>
        <span className="text-sm font-medium">{survey.title}</span>
        <span className="ml-auto text-[11px] text-muted-foreground">{survey.schema.length} 题</span>
        <Badge variant={survey.active ? "default" : "secondary"} className="text-[10px]">
          {survey.active ? "已发布" : "已下架"}</Badge>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" className="h-8" onClick={loadStats}>回收情况</Button>
        <Button size="sm" variant="outline" className="h-8"
          onClick={() => api.post(`/v1/lab/surveys/${survey.survey_id}/toggle`)
            .then(onChanged).catch(() => undefined)}>
          {survey.active ? "下架" : "发布"}</Button>
      </div>
      {stats ? (
        stats.weeks ? (
          <div className="mt-3 space-y-1">
            {stats.weeks.map((w) => (
              <div key={w.week_ref} className="flex items-center justify-between rounded-lg bg-muted/50 px-3 py-1.5 text-xs">
                <span className="font-mono">{w.week_ref}</span>
                <span className="text-muted-foreground">{w.done}/{w.total}
                  {w.total ? `（${Math.round((w.done / w.total) * 100)}%）` : ""}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-3 text-xs text-muted-foreground">
            已回收 {stats.rate != null ? `${Math.round(stats.rate * 100)}%` : "-"}</p>
        )
      ) : null}
    </div>
  )
}
