import { useEffect, useState } from "react"
import { useNavigate } from "react-router"
import { api, loadUser, clearUser, loadUsername } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { LogOut, UserRound, UserRoundX } from "lucide-react"
import { Shell, NavBar } from "./Today"

interface Metrics {
  weeks: { plan_id: string; week_no: number; planned: number; done: number; rate: number | null; key_planned: number; key_done: number }[]
  guard: { total: number; blocked: number; effectiveness: number }
  llm_usage: { llm: number; template: number; rate: number }
}

export default function MetricsPage() {
  const nav = useNavigate()
  const uid = loadUser()
  const [m, setM] = useState<Metrics | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [password, setPassword] = useState("")
  const [deleteErr, setDeleteErr] = useState("")
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (!uid) { nav("/login", { replace: true }); return }
    api.get<Metrics>(`/v1/metrics?user_id=${uid}`).then(setM).catch(console.error)
  }, [uid, nav])

  async function deleteAccount() {
    const username = loadUsername()
    if (!username) { setDeleteErr("本地缺少账号信息，请重新登录后再注销"); return }
    if (!password) { setDeleteErr("请输入密码确认身份"); return }
    setDeleting(true)
    setDeleteErr("")
    try {
      await api.post("/v1/account/delete", { username, password })
      clearUser()
      nav("/login", { replace: true })
    } catch (e) {
      setDeleteErr((e as Error).message.replace(/^\d+:\s*/, ""))
    } finally {
      setDeleting(false)
    }
  }

  if (!m) return <Shell><p className="text-muted-foreground">加载中…</p></Shell>

  const done = m.weeks.reduce((a, w) => a + w.done, 0)
  const planned = m.weeks.reduce((a, w) => a + w.planned, 0)
  const overall = planned ? Math.round((done / planned) * 100) : 0
  const R = 44
  const CIRC = 2 * Math.PI * R

  return (
    <Shell>
      <h2 className="text-xl font-bold mb-5">指标看板</h2>

      <div className="rounded-3xl border-2 bg-card shadow-sm mb-4">
        <div className="pt-6 pb-5 text-center">
          <div className="relative mx-auto h-32 w-32">
            <svg viewBox="0 0 100 100" className="h-full w-full -rotate-90">
              <circle cx="50" cy="50" r={R} fill="none" className="stroke-muted" strokeWidth="9" />
              <circle cx="50" cy="50" r={R} fill="none" className="stroke-primary" strokeWidth="9"
                strokeLinecap="round" strokeDasharray={CIRC}
                strokeDashoffset={CIRC * (1 - Math.min(1, overall / 100))}
                style={{ transition: "stroke-dashoffset .6s ease" }} />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-4xl font-black">{overall}%</span>
            </div>
          </div>
          <div className="text-sm text-muted-foreground mt-2">累计周计划执行率（北极星）</div>
          <div className="text-xs text-muted-foreground/70 mt-1">完成 {done} / 快照 {planned} 次 · 快照口径，重排不美化</div>
        </div>
      </div>

      <div className="rounded-2xl border bg-card p-4 mb-6 shadow-sm">
        <div className="text-sm font-medium mb-1">护栏有效性</div>
        <div className="text-3xl font-black">{Math.round(m.guard.effectiveness * 100)}%</div>
        <div className="text-xs text-muted-foreground mt-1">
          拦截 {m.guard.blocked} / 校验 {m.guard.total} 次 · 没有留痕，就永远证明不了护栏有用
        </div>
      </div>

      <div className="rounded-2xl border bg-card p-4 mb-6 shadow-sm">
        <div className="text-sm font-medium mb-1">AI 文案使用率</div>
        <div className="text-3xl font-black">{Math.round(m.llm_usage.rate * 100)}%</div>
        <div className="text-xs text-muted-foreground mt-1">
          LLM 生成 {m.llm_usage.llm} 次 · 规则模板兜底 {m.llm_usage.template} 次 · 未配置密钥时自动降级，体验不断档
        </div>
      </div>

      <div className="space-y-2">
        {m.weeks.map((w) => (
          <div key={`${w.plan_id}-${w.week_no}`} className="flex items-center gap-3 text-sm rounded-xl border bg-card px-3 py-2 shadow-sm">
            <span className="w-14 text-muted-foreground shrink-0">第 {w.week_no} 周</span>
            <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
              <div className="h-full bg-primary rounded-full" style={{ width: `${(w.rate || 0) * 100}%` }} />
            </div>
            <span className="w-20 text-right text-xs text-muted-foreground shrink-0">
              关键 {w.key_done}/{w.key_planned}
            </span>
          </div>
        ))}
      </div>

      {/* 账号：退出登录 / 切换账号（数据保留在服务端，重新登录可继续） */}
      <div className="mt-6 grid grid-cols-2 gap-2">
        <Button variant="outline" className="bg-card"
          onClick={() => { api.post("/v1/account/logout").catch(() => {}); clearUser(); nav("/login") }}>
          <LogOut className="h-4 w-4 mr-1.5" />退出登录
        </Button>
        <Button variant="outline" className="bg-card"
          onClick={() => { api.post("/v1/account/logout").catch(() => {}); clearUser(); nav("/login") }}>
          <UserRound className="h-4 w-4 mr-1.5" />切换账号
        </Button>
      </div>
      <Button className="w-full mt-2 bg-orange-500 hover:bg-orange-600 text-white"
        onClick={() => { setPassword(""); setDeleteErr(""); setDeleteOpen(true) }}>
        <UserRoundX className="h-4 w-4 mr-1.5" />注销账号
      </Button>
      <p className="text-xs text-muted-foreground/70 text-center mt-2">
        训练数据保留在账号下，重新登录后进度不丢
      </p>

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确定要注销账号吗？</AlertDialogTitle>
            <AlertDialogDescription>
              你的画像、训练计划、打卡记录等全部数据将被永久删除，无法恢复。请输入密码确认身份。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="py-2">
            <Label>密码</Label>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder="输入当前账号密码" />
            {deleteErr && <p className="text-xs text-destructive mt-2">{deleteErr}</p>}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>再想想</AlertDialogCancel>
            <AlertDialogAction disabled={deleting}
              className="bg-orange-500 hover:bg-orange-600"
              onClick={(e) => { e.preventDefault(); deleteAccount() }}>
              {deleting ? "正在注销…" : "确认注销"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <NavBar />
    </Shell>
  )
}
