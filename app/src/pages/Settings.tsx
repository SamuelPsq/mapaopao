import { useEffect, useState } from "react"
import { useNavigate } from "react-router"
import { api, loadUser } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Shell, NavBar } from "./Today"
import { ArrowLeft, Eye, EyeOff, PlugZap, Sparkles, Trash2 } from "lucide-react"

// AI 引擎设置（v3.3）：用户在界面里自选供应商/模型/Key，不用手改 .env。
// 文本与视觉两条通道独立配置——与全站「文本 DeepSeek、识图 Kimi，缺哪个降哪个」一致。
// api_key 语义：输入新值=覆盖；整行清空后保存=清除（回退 .env/未配置）。

interface ChannelCfg {
  provider: string
  base_url: string
  api_key: string   // 回显为脱敏值；提交时若是脱敏占位则后端视为「不变」
  model: string
}

interface SettingsView {
  providers: Record<string, { label: string; base_url: string; models: string[] }>
  text: ChannelCfg
  vision: ChannelCfg
  fallback: { text: string; vision: string }
}

const CHANNEL_META = {
  text: { title: "文本模型", desc: "跑小助问答 · 周审视点评 · 课表微调 · 拉伸建议" },
  vision: { title: "视觉识别模型", desc: "智能识别成绩（成绩单/手表截图）——必须支持图片的模型" },
} as const

function ChannelCard({ channel, view, onChanged }: {
  channel: "text" | "vision"
  view: SettingsView
  onChanged: (saved: { ok: boolean; msg: string }) => void
}) {
  const uid = loadUser()!
  const presets = view.providers
  const saved = view[channel]
  const [provider, setProvider] = useState(saved.provider || "deepseek")
  const [baseUrl, setBaseUrl] = useState(saved.base_url || "")
  const [apiKey, setApiKey] = useState(saved.api_key || "")   // 脱敏占位，如 ••••sk-12
  const [model, setModel] = useState(saved.model || "")
  const [showKey, setShowKey] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [saving, setSaving] = useState(false)

  const models = presets[provider]?.models ?? []
  // 模型下拉：预设项 + 当前值（兼容自定义/历史值）
  const modelOptions = [...new Set([...models, ...(model ? [model] : [])])]

  const pickProvider = (p: string) => {
    setProvider(p)
    const preset = presets[p]
    if (preset) {
      if (preset.base_url) setBaseUrl(preset.base_url)
      if (preset.models.length) setModel(preset.models[0])
    }
  }

  const payload = () => ({
    provider, base_url: baseUrl.trim(), api_key: apiKey.trim(), model: model.trim(),
  })

  const test = async () => {
    setTesting(true); setTestMsg(null)
    try {
      const r = await api.post<{ ok: boolean; message: string }>("/v1/settings/llm/test",
        { channel, ...payload() })
      setTestMsg({ ok: r.ok, text: r.message })
    } catch (e) {
      setTestMsg({ ok: false, text: e instanceof Error ? e.message : "测试失败，请稍后再试" })
    } finally { setTesting(false) }
  }

  const save = async () => {
    setSaving(true)
    try {
      await api.put(`/v1/settings/llm?user_id=${uid}`, { [channel]: payload() })
      onChanged({ ok: true, msg: "已保存" })
    } catch (e) {
      onChanged({ ok: false, msg: e instanceof Error ? e.message : "保存失败，请稍后再试" })
    } finally { setSaving(false) }
  }

  const clearKey = () => {
    setApiKey("")
    setTestMsg({ ok: true, text: "Key 已清空，保存后该通道将回退默认配置" })
  }

  const fb = view.fallback[channel]

  return (
    <section className="rounded-2xl border bg-card p-4 shadow-sm space-y-3">
      <div>
        <h2 className="font-bold flex items-center gap-1.5">
          {channel === "text" ? <Sparkles className="h-4 w-4 text-primary" /> : <PlugZap className="h-4 w-4 text-primary" />}
          {CHANNEL_META[channel].title}
        </h2>
        <p className="text-xs text-muted-foreground mt-0.5">{CHANNEL_META[channel].desc}</p>
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs">模型供应商</Label>
        <Select value={provider} onValueChange={pickProvider}>
          <SelectTrigger className="bg-background"><SelectValue /></SelectTrigger>
          <SelectContent>
            {Object.entries(presets).map(([k, v]) => (
              <SelectItem key={k} value={k}>{v.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs">接口地址</Label>
        <Input className="bg-background" placeholder="https://…（OpenAI 兼容端点）"
          value={baseUrl} onChange={e => setBaseUrl(e.target.value)} />
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs">模型</Label>
        {models.length > 0 ? (
          <Select value={model} onValueChange={setModel}>
            <SelectTrigger className="bg-background"><SelectValue placeholder="选择模型" /></SelectTrigger>
            <SelectContent>
              {modelOptions.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
              <SelectItem value="__custom__" disabled>—— 自定义请在下方输入 ——</SelectItem>
            </SelectContent>
          </Select>
        ) : null}
        <Input className="bg-background" placeholder={models.length > 0 ? "或输入自定义模型名" : "模型名，如 moonshot-v1-8k"}
          value={model} onChange={e => setModel(e.target.value)} />
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs">API Key</Label>
        <div className="relative">
          <Input className="bg-background pr-10" type={showKey ? "text" : "password"}
            placeholder="sk-…" value={apiKey} onChange={e => setApiKey(e.target.value)} />
          <button type="button" onClick={() => setShowKey(!showKey)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground p-1"
            aria-label={showKey ? "隐藏 Key" : "显示 Key"}>
            {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        <div className="flex items-center justify-between">
          <p className="text-[11px] text-muted-foreground">
            {apiKey ? "已填写（保存后生效，仅本人可用）" : fb === "user" ? "已配置（回显脱敏）" : fb === "env" ? "未填写：使用服务器默认配置" : "未填写：该功能将走规则降级"}
          </p>
          {apiKey ? (
            <button type="button" onClick={clearKey}
              className="text-[11px] text-destructive flex items-center gap-0.5 shrink-0 ml-2">
              <Trash2 className="h-3 w-3" />清除 Key
            </button>
          ) : null}
        </div>
      </div>

      {testMsg ? (
        <p className={`text-xs rounded-lg px-2.5 py-1.5 ${testMsg.ok ? "bg-primary/10 text-primary" : "bg-destructive/10 text-destructive"}`}>
          {testMsg.text}
        </p>
      ) : null}

      <div className="flex gap-2 pt-1">
        <Button variant="outline" className="flex-1" onClick={test} disabled={testing}>
          {testing ? "测试中…" : "测试连接"}
        </Button>
        <Button className="flex-1" onClick={save} disabled={saving}>
          {saving ? "保存中…" : "保存"}
        </Button>
      </div>
    </section>
  )
}

export default function SettingsPage() {
  const nav = useNavigate()
  const uid = loadUser()
  const [view, setView] = useState<SettingsView | null>(null)
  const [err, setErr] = useState("")
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)

  useEffect(() => {
    if (!uid) { nav("/login"); return }
    api.get<SettingsView>(`/v1/settings/llm?user_id=${uid}`)
      .then(setView)
      .catch(e => setErr(e instanceof Error ? e.message : "加载失败"))
  }, [uid, nav])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 2600)
    return () => clearTimeout(t)
  }, [toast])

  return (
    <Shell>
      <div className="flex items-center gap-2 mb-5">
        <button onClick={() => nav(-1)} className="p-1.5 -ml-1.5 rounded-full active:scale-90 transition-transform" aria-label="返回">
          <ArrowLeft className="h-5 w-5" />
        </button>
        <h1 className="text-xl font-black">AI 引擎设置</h1>
      </div>

      {err ? <p className="text-sm text-destructive">{err}</p> : null}
      {!view && !err ? <p className="text-sm text-muted-foreground">加载中…</p> : null}

      {view ? (
        <div className="space-y-4">
          <p className="text-xs text-muted-foreground leading-relaxed">
            填入你自己的模型与 API 后，跑小助、周审视点评等 AI 功能将使用你的 Key 计费；不填则使用服务器默认配置。Key 只保存在你的账号下，接口回显已脱敏。
          </p>
          <ChannelCard channel="text" view={view} onChanged={setToast} />
          <ChannelCard channel="vision" view={view} onChanged={setToast} />
          <p className="text-[11px] text-muted-foreground text-center pb-2">
            视觉识别需选择支持图片的模型（如 Kimi、GPT-4o、Qwen-VL），文本模型无法读图
          </p>
        </div>
      ) : null}

      <NavBar />
      {toast ? (
        <div className={`fixed bottom-20 left-1/2 -translate-x-1/2 text-sm px-4 py-2 rounded-full shadow-lg max-w-xs text-center z-50 ${toast.ok ? "bg-foreground text-background" : "bg-destructive text-destructive-foreground"}`}
          onClick={() => setToast(null)}>
          {toast.msg}
        </div>
      ) : null}
    </Shell>
  )
}
