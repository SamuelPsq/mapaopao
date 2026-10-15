import { useEffect, useRef, useState } from "react"
import { FlaskConical, WifiOff, PowerOff, RotateCcw, RefreshCw } from "lucide-react"
import { api } from "../lib/api"

/** 断网/挂起/重启后端演示工具：验证「连不上服务器，请检查网络后重试」降级文案与 GET 自动重试。
 *  仅 vite 开发模式可见（import.meta.env.DEV），build 产物里整个组件不渲染。
 *  /dev/shutdown 由 vite 代理到后端（需 MAPAOPAO_DEV=1 启动才有此接口）；
 *  /dev/restart 由 vite 中间件直接处理——后端全挂时它也已死，重启开关必须由 dev server 代劳。 */
export default function DevTools() {
  const [offline, setOffline] = useState(false)
  const [dead, setDead] = useState(false)
  const [busy, setBusy] = useState(false)
  // 重启的二次确认态 + 重启后探活轮询
  const [confirmRestart, setConfirmRestart] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const pollRef = useRef<number | null>(null)

  useEffect(() => () => {
    if (pollRef.current !== null) window.clearInterval(pollRef.current)
  }, [])

  if (!import.meta.env.DEV) return null

  // 模拟断网：让全局 fetch 以网络层 TypeError 失败——与真实断网/后端关闭的表现完全一致，
  // api.ts 的 GET 自动重试（600ms/1.2s 退避）也会真实触发
  function toggleOffline() {
    const next = !offline
    setOffline(next)
    const w = window as unknown as { __origFetch?: typeof fetch }
    if (next) {
      w.__origFetch = window.fetch
      window.fetch = () => Promise.reject(new TypeError("Failed to fetch"))
    } else if (w.__origFetch) {
      window.fetch = w.__origFetch
      delete w.__origFetch
    }
  }

  // 真挂起后端：调 /dev/shutdown（后端需 MAPAOPAO_DEV=1 启动才有此接口）
  async function killBackend() {
    setBusy(true)
    try {
      await api.post("/dev/shutdown")
    } catch {
      /* 进程已退出、请求必然失败，属预期 */
    }
    setBusy(false)
    setDead(true)
  }

  // 探活：/v1/kb/tip 由 vite 代理到后端——任何 HTTP 响应（含 401）都说明后端活了，
  // 只有网络层错误说明还没起来。轮询到恢复即停。
  function startPolling() {
    if (pollRef.current !== null) window.clearInterval(pollRef.current)
    pollRef.current = window.setInterval(async () => {
      try {
        await fetch("/v1/kb/tip", { cache: "no-store" })
        setDead(false)
        setRestarting(false)
        if (pollRef.current !== null) window.clearInterval(pollRef.current)
        pollRef.current = null
      } catch {
        /* 还没起来，继续等下一轮 */
      }
    }, 1000)
  }

  // 一键重启：防误触——第一次点击变「确认重启？」，3 秒内再点才真执行
  async function restartBackend() {
    if (!confirmRestart) {
      setConfirmRestart(true)
      window.setTimeout(() => setConfirmRestart(false), 3000)
      return
    }
    setConfirmRestart(false)
    setBusy(true)
    try {
      await api.post("/dev/restart")
    } catch {
      /* 进程即将退出，响应失败也属预期 */
    }
    setBusy(false)
    setDead(true)
    setRestarting(true)
    startPolling()
  }

  return (
    <div className="fixed z-50 left-3 bottom-24 flex flex-col gap-1.5 items-start">
      <span className="text-[10px] text-muted-foreground/70 bg-background/80 rounded px-1.5 py-0.5 flex items-center gap-1">
        <FlaskConical className="h-3 w-3" />开发演示
      </span>
      <button onClick={toggleOffline}
        className={`h-9 px-3 rounded-full shadow-md flex items-center gap-1.5 text-xs font-medium active:scale-95 transition-all
          ${offline ? "bg-amber-500 text-white" : "bg-card border text-muted-foreground"}`}>
        <WifiOff className="h-3.5 w-3.5" />
        {offline ? <><RotateCcw className="h-3 w-3" />恢复网络</> : "模拟断网"}
      </button>
      <button onClick={killBackend} disabled={busy || dead}
        className="h-9 px-3 rounded-full shadow-md bg-card border text-muted-foreground flex items-center gap-1.5 text-xs font-medium active:scale-95 transition-all disabled:opacity-40">
        <PowerOff className="h-3.5 w-3.5" />
        {dead ? "后端已挂起" : busy ? "挂起中…" : "挂起后端"}
      </button>
      <button onClick={restartBackend} disabled={busy || restarting}
        className={`h-9 px-3 rounded-full shadow-md flex items-center gap-1.5 text-xs font-medium active:scale-95 transition-all disabled:opacity-40
          ${confirmRestart ? "bg-amber-500 text-white" : "bg-card border text-muted-foreground"}`}>
        <RefreshCw className={`h-3.5 w-3.5 ${restarting ? "animate-spin" : ""}`} />
        {restarting ? "重启中…" : confirmRestart ? "确认重启？" : "重启后端"}
      </button>
      {dead && (
        <span className="text-[10px] leading-snug text-muted-foreground/80 bg-background/80 rounded px-1.5 py-1 max-w-[180px]">
          {restarting
            ? "后端正在重启，约 5 秒后自动恢复，请稍候…"
            : "点「重启后端」一键恢复——重启开关由前端 dev server 托管，后端全挂也能拉起"}
        </span>
      )}
    </div>
  )
}
