import { useEffect, useRef, useState } from "react"
import { Bot, X, SendHorizonal } from "lucide-react"
import { api, loadUser } from "../lib/api"

interface Msg {
  role: "user" | "bot"
  text: string
  sources?: { source: string; name: string }[]
  from?: string
  medical?: boolean
}

const SUGGESTIONS = [
  "为什么我的计划里大半是轻松跑？",
  "没有手表怎么判断跑步强度？",
  "最近不想跑，有点厌跑怎么办？",
  "睡眠不足还能照常训练吗？",
  "膝盖有点疼，要不要停跑？",
  "错过一次训练，计划会废吗？",
]

const WELCOME = "Hi，我是跑小助～ 训练为什么这样排、体感怎么判断、心态怎么调、睡眠和伤病，都可以问我，回答都带出处。"

export default function Assistant() {
  const [open, setOpen] = useState(false)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const [showWelcome, setShowWelcome] = useState(true)
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [msgs, open])

  const uid = loadUser()

  // 打开面板时恢复多轮对话历史（后端保留最近 50 条）；有历史就收起欢迎语
  useEffect(() => {
    if (!open || historyLoaded || !uid) return
    setHistoryLoaded(true)
    api.get<{ messages: { role: string; content: string }[] }>(
      `/v1/assistant/history?user_id=${uid}`)
      .then(r => {
        if (!r.messages?.length) return
        setMsgs(r.messages.map(m => ({
          role: m.role === "user" ? ("user" as const) : ("bot" as const),
          text: m.content,
        })))
        setShowWelcome(false)
      })
      .catch(() => {})
  }, [open])

  if (!uid) return null          // 未登录不展示

  async function ask(text: string) {
    const q = text.trim()
    if (!q || busy || !uid) return
    setMsgs(m => [...m, { role: "user", text: q }])
    setInput("")
    setBusy(true)
    setShowWelcome(false)
    try {
      const r = await api.post<{ answer: string; sources: { source: string; name: string }[]; from: string; medical?: boolean }>(
        "/v1/assistant/chat", { user_id: uid, message: q })
      setMsgs(m => [...m, { role: "bot", text: r.answer, sources: r.sources, from: r.from, medical: r.medical }])
    } catch (e) {
      setMsgs(m => [...m, { role: "bot", text: "网络开小差了，稍后再问我一次～" }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {/* 悬浮入口：右下角圆形头像，类似腾讯云 Kiki */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          aria-label="打开 AI 跑步助手"
          className="fixed z-50 right-3 bottom-24 h-12 w-12 rounded-full bg-primary text-primary-foreground shadow-lg shadow-orange-500/30 flex items-center justify-center active:scale-95 transition-transform">
          <Bot className="h-6 w-6" />
        </button>
      )}

      {/* 对话面板 */}
      {open && (
        <div className="fixed z-50 inset-x-0 bottom-0 sm:inset-x-auto sm:right-4 sm:bottom-24 sm:w-[380px] sm:rounded-3xl bg-card border shadow-2xl flex flex-col overflow-hidden"
          style={{ height: "min(72vh, 560px)" }}>
          {/* 头部 */}
          <div className="flex items-center gap-2.5 px-4 py-3 bg-primary text-primary-foreground">
            <span className="h-8 w-8 rounded-full bg-white/20 flex items-center justify-center shrink-0">
              <Bot className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <div className="text-sm font-semibold leading-tight">跑小助 · AI 跑步助手</div>
              <div className="text-[11px] opacity-80 leading-tight">回答来自训练原则库 + 跑者经验库</div>
            </div>
            <button onClick={() => setOpen(false)} aria-label="关闭"
              className="ml-auto opacity-80 hover:opacity-100 active:scale-95">
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* 消息区 */}
          <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3">
            {showWelcome && msgs.length === 0 && (
              <div>
                <div className="max-w-[85%] rounded-2xl rounded-tl-sm bg-muted px-3.5 py-2.5 text-sm leading-relaxed">
                  {WELCOME}
                </div>
                <div className="mt-3 space-y-1.5">
                  {SUGGESTIONS.map(s => (
                    <button key={s} onClick={() => ask(s)}
                      className="block w-full text-left text-[13px] text-primary bg-primary/8 border border-primary/20 rounded-xl px-3 py-2 active:scale-[0.98] transition-transform">
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {msgs.map((m, i) => (
              <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
                <div className={m.role === "user"
                  ? "max-w-[85%] rounded-2xl rounded-tr-sm bg-primary text-primary-foreground px-3.5 py-2.5 text-sm leading-relaxed"
                  : "max-w-[88%] rounded-2xl rounded-tl-sm bg-muted px-3.5 py-2.5 text-sm leading-relaxed whitespace-pre-wrap"}>
                  {m.text}
                  {m.medical && (
                    <div className="mt-2 text-[11px] leading-snug text-amber-700 bg-amber-500/10 border border-amber-500/20 rounded-lg px-2.5 py-1.5">
                      ⚠️ 伤病内容仅供参考，不构成医疗建议；症状持续或加重，请及时就医（运动医学科 / 骨科）。
                    </div>
                  )}
                  {m.sources && m.sources.length > 0 && (
                    <div className="mt-1.5 pt-1.5 border-t border-border/60 flex flex-wrap gap-1">
                      {m.sources.map((s, j) => (
                        <span key={j} className="text-[10px] text-muted-foreground bg-background/70 rounded px-1.5 py-0.5">
                          {s.source}·{s.name}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {busy && (
              <div className="flex justify-start">
                <div className="rounded-2xl rounded-tl-sm bg-muted px-4 py-2.5 text-sm text-muted-foreground">
                  正在翻知识库…
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* 输入区 */}
          <div className="flex items-center gap-2 border-t px-3 py-2.5">
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && ask(input)}
              placeholder="问点什么，比如：跑后膝盖疼怎么办"
              className="flex-1 bg-muted rounded-xl px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
            />
            <button onClick={() => ask(input)} disabled={busy || !input.trim()}
              aria-label="发送"
              className="h-9 w-9 rounded-xl bg-primary text-primary-foreground flex items-center justify-center disabled:opacity-40 active:scale-95 transition-transform">
              <SendHorizonal className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}
    </>
  )
}
