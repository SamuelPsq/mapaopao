import { useState } from "react"
import { useNavigate } from "react-router"
import { api, saveUser, saveUsername, saveToken } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Flame } from "lucide-react"

/** 登录 / 注册：账号体系的入口。老用户登录回原账号，新用户注册后进画像问卷。 */
export default function Login() {
  const nav = useNavigate()
  const [mode, setMode] = useState<"login" | "register">("login")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState("")

  async function submit() {
    setErr("")
    if (!username.trim() || !password) { setErr("请输入账号和密码"); return }
    setBusy(true)
    try {
      const path = mode === "login" ? "/v1/account/login" : "/v1/account/register"
      const r = await api.post<{ user_id: string; has_plan?: boolean; token: string }>(path, {
        username: username.trim(), password,
      })
      saveUser(r.user_id)
      saveUsername(username.trim())
      saveToken(r.token)
      nav(r.has_plan ? "/" : "/onboarding", { replace: true })
    } catch (e) {
      const msg = (e as Error).message
      if (mode === "login" && msg.startsWith("401:")) {
        // 用户名或密码错误：只提示，不跳转
        alert("用户名或密码输入错误，请重试")
        setErr("")
      } else {
        setErr(msg.replace(/^\d+:\s*/, ""))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-background flex justify-center">
      <div className="w-full max-w-md px-6 pt-16 pb-8 flex flex-col">
        {/* 品牌 */}
        <div className="mb-10">
          <div className="flex items-center gap-2">
            <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/25">
              <Flame className="h-5 w-5" />
            </div>
            <div className="text-3xl font-black tracking-tight">马跑跑</div>
          </div>
          <div className="text-sm text-muted-foreground mt-2">马拉松训练规划助手 · 16 周</div>
        </div>

        {/* 登录/注册切换 */}
        <div className="grid grid-cols-2 gap-1 rounded-2xl bg-muted p-1 mb-6">
          {(["login", "register"] as const).map((m) => (
            <button key={m}
              className={`rounded-xl py-2 text-sm font-medium transition-colors
                ${mode === m ? "bg-card shadow-sm" : "text-muted-foreground"}`}
              onClick={() => { setMode(m); setErr("") }}>
              {m === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>

        <div className="space-y-4">
          <div>
            <Label>账号</Label>
            <Input value={username} placeholder="3-20 个字符"
              onChange={(e) => setUsername(e.target.value)} className="mt-1.5 bg-card" />
          </div>
          <div>
            <Label>密码</Label>
            <Input type="password" value={password} placeholder={mode === "register" ? "至少 6 位" : ""}
              onChange={(e) => setPassword(e.target.value)} className="mt-1.5 bg-card"
              onKeyDown={(e) => { if (e.key === "Enter") submit() }} />
          </div>
          {err ? <p className="text-sm text-destructive">{err}</p> : null}
          <Button className="w-full" size="lg" disabled={busy} onClick={submit}>
            {busy ? "请稍候…" : mode === "login" ? "登录" : "注册并创建账号"}
          </Button>
          <p className="text-xs text-muted-foreground text-center">
            {mode === "login"
              ? "登录后回到你的训练计划，数据都存在你的账号下"
              : "注册后先填 6 步画像问卷，再生成 16 周计划"}
          </p>
        </div>
      </div>
    </div>
  )
}
