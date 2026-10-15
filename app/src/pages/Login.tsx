import { useState } from "react"
import { useNavigate } from "react-router"
import { api, saveUser, saveUsername, saveToken } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import "@fontsource/zcool-qingke-huangyou/chinese-simplified-400.css"
import "@fontsource/zcool-qingke-huangyou/latin-400.css"
import logo from "../assets/logo.png"

const TITLE_FONT = { fontFamily: "'ZCOOL QingKe HuangYou', 'PingFang SC', sans-serif" }

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
      const err = e as Error & { status?: number }
      if (mode === "login" && err.status === 401) {
        // 用户名或密码错误：只提示，不跳转
        alert("用户名或密码输入错误，请重试")
        setErr("")
      } else {
        setErr(err.message)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="app-bg min-h-screen flex justify-center">
      <div className="w-full max-w-md px-6 pt-20 pb-10 flex flex-col">
        {/* 品牌区：Logo + 黄油体标题 + 定位语 */}
        <div className="flex flex-col items-center text-center">
          <img src={logo} alt="马跑跑"
            className="login-rise login-rise-1 h-20 w-20 rounded-[1.35rem] shadow-xl shadow-primary/30" />
          <h1 style={TITLE_FONT}
            className="login-rise login-rise-2 mt-5 text-[2.9rem] leading-none tracking-wide text-foreground">
            马跑跑
          </h1>
          <div className="login-rise login-rise-2 mt-3 flex items-center gap-2">
            <span className="h-px w-6 bg-primary/40" />
            <p className="text-xs tracking-[0.35em] text-muted-foreground">AI 马拉松训练规划助手</p>
            <span className="h-px w-6 bg-primary/40" />
          </div>
        </div>

        {/* 表单卡 */}
        <div className="login-rise login-rise-3 mt-11 rounded-3xl border bg-card/85 p-6 shadow-xl shadow-black/5 backdrop-blur">
          {/* 登录/注册切换 */}
          <div className="grid grid-cols-2 gap-1 rounded-2xl bg-muted p-1">
            {(["login", "register"] as const).map((m) => (
              <button key={m}
                className={`rounded-xl py-2.5 text-sm font-medium transition-all
                  ${mode === m ? "bg-card shadow-sm text-foreground" : "text-muted-foreground hover:text-foreground"}`}
                onClick={() => { setMode(m); setErr("") }}>
                {m === "login" ? "登录" : "注册"}
              </button>
            ))}
          </div>

          <div className="mt-6 space-y-4">
            <div>
              <Label>账号</Label>
              <Input value={username} placeholder="3-20 个字符"
                onChange={(e) => setUsername(e.target.value)}
                className="mt-1.5 h-12 rounded-xl bg-background/60" />
            </div>
            <div>
              <Label>密码</Label>
              <Input type="password" value={password} placeholder={mode === "register" ? "至少 6 位" : ""}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1.5 h-12 rounded-xl bg-background/60"
                onKeyDown={(e) => { if (e.key === "Enter") submit() }} />
            </div>
            {err ? <p className="text-sm text-destructive">{err}</p> : null}
            <Button size="lg" disabled={busy} onClick={submit}
              className="h-12 w-full rounded-full bg-gradient-to-r from-amber-500 to-orange-700 text-base font-semibold tracking-wide text-white shadow-lg shadow-orange-700/25 transition-all hover:brightness-105 active:scale-[0.98]">
              {busy ? "请稍候…" : mode === "login" ? "登录" : "注册并创建账号"}
            </Button>
            <p className="text-xs leading-relaxed text-muted-foreground text-center">
              {mode === "login"
                ? "登录后回到你的训练计划，数据都存在你的账号下"
                : "注册后先填 6 步画像问卷，再生成 16 周计划"}
            </p>
          </div>
        </div>

        {/* 底部信任状 */}
        <p className="login-rise login-rise-3 mt-auto pt-8 text-center text-[11px] text-muted-foreground/70">
          基于 VDOT 训练体系 · 校体育学院运动与健康实验中心合作
        </p>
      </div>
    </div>
  )
}
