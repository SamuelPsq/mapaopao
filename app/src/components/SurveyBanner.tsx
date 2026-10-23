import { useEffect, useState } from "react"
import { ClipboardList, X } from "lucide-react"
import { api, loadUser } from "@/lib/api"

/** 问卷题目定义（与后端 Survey.schema_json 对齐） */
interface Question {
  id: string
  text: string
  type: "likert7" | "bool" | "text"
  required?: boolean
}

interface PendingSurvey {
  survey_id: string
  kind: "pre" | "weekly" | "post"
  title: string
  schema: Question[]
  week_ref: string
}

const KIND_CN: Record<string, string> = { pre: "前测", weekly: "每周问卷", post: "后测" }

function usePending(uid: string | null) {
  const [pending, setPending] = useState<PendingSurvey | null>(null)
  const refresh = () => {
    if (!uid) return
    api.get<{ pending: PendingSurvey | null }>(`/v1/survey/pending?user_id=${uid}`)
      .then((r) => setPending(r.pending))
      .catch(() => setPending(null))
  }
  useEffect(refresh, [uid])
  return { pending, refresh, clear: () => setPending(null) }
}

/** 问卷弹窗：likert7 用 1-7 滑选，bool 用是/否，text 用输入框。≤5 题 30 秒内能填完。 */
function SurveyModal({ survey, uid, onDone, onClose }: {
  survey: PendingSurvey
  uid: string
  onDone: () => void
  onClose: () => void
}) {
  const [answers, setAnswers] = useState<Record<string, number | string | boolean>>({})
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [err, setErr] = useState("")

  async function submit() {
    setErr("")
    const missing = survey.schema.some((q) => q.required && !(q.id in answers))
    if (missing) { setErr("还有题目没回答"); return }
    setBusy(true)
    try {
      await api.post("/v1/survey/respond", { user_id: uid, survey_id: survey.survey_id, answers })
      setDone(true)
      setTimeout(onDone, 1200)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/45 backdrop-blur-sm sm:items-center"
      onClick={onClose}>
      <div className="w-full max-w-md rounded-t-3xl bg-card p-5 shadow-2xl sm:rounded-3xl"
        onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <div>
            <div className="text-base font-bold">{survey.title}</div>
            <div className="text-xs text-muted-foreground">{KIND_CN[survey.kind]} · 约 30 秒</div>
          </div>
          <button onClick={onClose} className="p-2 text-muted-foreground active:scale-95">
            <X className="h-5 w-5" />
          </button>
        </div>

        {done ? (
          <p className="py-8 text-center text-sm text-muted-foreground">已记录，感谢填写</p>
        ) : (
          <>
            <div className="max-h-[55vh] space-y-4 overflow-y-auto pr-1">
              {survey.schema.map((q, i) => (
                <div key={q.id}>
                  <p className="text-sm font-medium">{i + 1}. {q.text}</p>
                  {q.type === "likert7" ? (
                    <div className="mt-2 grid grid-cols-7 gap-1">
                      {[1, 2, 3, 4, 5, 6, 7].map((n) => (
                        <button key={n} type="button"
                          onClick={() => setAnswers({ ...answers, [q.id]: n })}
                          className={`rounded-lg py-2 text-sm font-medium transition-all active:scale-95
                            ${answers[q.id] === n ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}>
                          {n}
                        </button>
                      ))}
                    </div>
                  ) : q.type === "bool" ? (
                    <div className="mt-2 grid grid-cols-2 gap-2">
                      {[[true, "是"], [false, "否"]].map(([v, label]) => (
                        <button key={String(v)} type="button"
                          onClick={() => setAnswers({ ...answers, [q.id]: v })}
                          className={`rounded-xl py-2.5 text-sm font-medium transition-all active:scale-95
                            ${answers[q.id] === v ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}>
                          {label}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <textarea value={String(answers[q.id] ?? "")} rows={2}
                      onChange={(e) => setAnswers({ ...answers, [q.id]: e.target.value })}
                      className="mt-2 w-full rounded-xl border bg-background/60 p-2.5 text-sm outline-none focus:ring-2 focus:ring-primary/30" />
                  )}
                </div>
              ))}
            </div>
            {err ? <p className="mt-2 text-xs text-destructive">{err}</p> : null}
            <button onClick={submit} disabled={busy}
              className="mt-4 w-full rounded-full bg-gradient-to-r from-amber-500 to-orange-700 py-3 text-sm font-semibold text-white shadow-lg transition-all hover:brightness-105 active:scale-[0.98] disabled:opacity-60">
              {busy ? "提交中…" : "提交"}
            </button>
          </>
        )}
      </div>
    </div>
  )
}

/** 今日卡入口条：有待填问卷时出现（周问卷由周日定时任务生成实例，天然只在周日之后出现） */
export function SurveyBanner() {
  const uid = loadUser()
  const { pending, refresh, clear } = usePending(uid)
  const [open, setOpen] = useState(false)
  if (!pending) return null
  return (
    <>
      <button onClick={() => setOpen(true)}
        className="mb-5 flex w-full items-center gap-2 rounded-2xl border border-primary/25 bg-primary/5 px-4 py-3 text-left shadow-sm transition-all active:scale-[0.99]">
        <ClipboardList className="h-4 w-4 shrink-0 text-primary" />
        <span className="text-sm font-medium text-primary">{pending.title}</span>
        <span className="ml-auto text-xs text-muted-foreground">{KIND_CN[pending.kind]} · 点击填写</span>
      </button>
      {open ? (
        <SurveyModal survey={pending} uid={uid!} onClose={() => setOpen(false)}
          onDone={() => { setOpen(false); refresh() }} />
      ) : null}
    </>
  )
}

/** 周审视温和提示：不弹窗、不重复打扰，只放一条安静的入口 */
export function SurveyHint() {
  const uid = loadUser()
  const { pending, refresh } = usePending(uid)
  const [open, setOpen] = useState(false)
  if (!pending) return null
  return (
    <>
      <button onClick={() => setOpen(true)}
        className="mb-5 flex w-full items-center gap-2 rounded-2xl border border-dashed px-4 py-2.5 text-left text-xs text-muted-foreground transition-colors hover:text-foreground active:scale-[0.99]">
        <ClipboardList className="h-3.5 w-3.5 shrink-0" />
        {pending.title}（{KIND_CN[pending.kind]}）还未填写，点这里补交
      </button>
      {open ? (
        <SurveyModal survey={pending} uid={uid!} onClose={() => setOpen(false)}
          onDone={() => { setOpen(false); refresh() }} />
      ) : null}
    </>
  )
}
