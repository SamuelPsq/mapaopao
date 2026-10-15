import { useState } from "react"
import { FlaskConical, WifiOff, PowerOff, RotateCcw } from "lucide-react"
import { api } from "../lib/api"

/** 断网/挂起后端演示工具：验证「连不上服务器，请检查网络后重试」降级文案与 GET 自动重试。
 *  仅 vite 开发模式可见（import.meta.env.DEV），build 产物里整个组件不渲染。 */
export default function DevTools() {
  const [offline, setOffline] = useState(false)
  const [dead, setDead] = useState(false)
  const [busy, setBusy] = useState(false)

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
      {dead && (
        <span className="text-[10px] leading-snug text-muted-foreground/80 bg-background/80 rounded px-1.5 py-1 max-w-[180px]">
          恢复：终端执行 uvicorn server.main:app --port 8000（需带 MAPAOPAO_DEV=1）
        </span>
      )}
    </div>
  )
}
