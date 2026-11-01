import { useEffect, useState } from "react"
import { useNavigate } from "react-router"
import { api, loadUser } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Shell, NavBar } from "./Today"
import { ArrowLeft, Eye, EyeOff, ImageIcon, Sparkles, Trash2 } from "lucide-react"

// AI 引擎设置（v3.3.1 单通道化）：一份 Key + 一个多模态模型，驱动全站所有 AI 功能
// （跑小助问答 / 周审视点评 / 课表微调 / 拉伸建议 / 智能识别成绩）。
// 预设列表只保留确认支持图片输入的视觉模型——保证智能识别可用；
// 选「自定义」时提示用户自行确认模型支持图片。
// api_key 语义：输入新值=覆盖；整行清空后保存=清除（回退 .env/未配置）。

interface SettingsView {
  providers: Record<string, { label: string; base_url: string; models: string[] }>
  config: { provider: string; base_url: string; api_key: string; model: string }
  fallback: "user" | "env" | "none"
}

export default function SettingsPage() {
  const nav = useNavigate()
  const uid = loadUser()
  const [view, setView] = useState<SettingsView | null>(null)
  const [err, setErr] = useState("")
  const [toast, setToast] = useState<{ ok: boolean; msg: string } | null>(null)

  const [provider, setProvider] = useState("kimi")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")     // 回显为脱敏值，如 ••••sk-12
  const [model, setModel] = useState("")
  const [showKey, setShowKey] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!uid) { nav("/login"); return }
    api.get<SettingsView>(`/v1/settings/llm?user_id=${uid}`)
      .then(v => {
        setView(v)
        setProvider(v.config.provider || "kimi")
        setBaseUrl(v.config.base_url || "")
        setApiKey(v.config.api_key || "")
        setModel(v.config.model || "")
      })
      .catch(e => setErr(e instanceof Error ? e.message : "加载失败"))
  }, [uid, nav])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 2600)
    return () => clearTimeout(t)
  }, [toast])

  const presets = view?.providers ?? {}
  const models = presets[provider]?.models ?? []
  // 模型下拉：预设视觉模型 + 当前值（兼容自定义/历史值）
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
        { ...payload(), user_id: uid })
      setTestMsg({ ok: r.ok, text: r.message })
    } catch (e) {
      setTestMsg({ ok: false, text: e instanceof Error ? e.message : "测试失败，请稍后再试" })
    } finally { setTesting(false) }
  }

  const save = async () => {
    setSaving(true)
    try {
      await api.put(`/v1/settings/llm?user_id=${uid}`, payload())
      setToast({ ok: true, msg: "已保存" })
    } catch (e) {
      setToast({ ok: false, msg: e instanceof Error ? e.message : "保存失败，请稍后再试" })
    } finally { setSaving(false) }
  }

  const clearKey = () => {
    setApiKey("")
    setTestMsg({ ok: true, text: "Key 已清空，保存后将回退默认配置" })
  }

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
            填入你自己的模型与 API 后，跑小助、周审视点评、智能识别成绩等所有 AI 功能都走你的 Key 计费；不填则使用服务器默认配置。Key 只保存在你的账号下，回显已脱敏。
          </p>

          <section className="rounded-2xl border bg-card p-4 shadow-sm space-y-3">
            <div>
              <h2 className="font-bold flex items-center gap-1.5">
                <Sparkles className="h-4 w-4 text-primary" />模型与 API
              </h2>
              <p className="text-xs text-muted-foreground mt-0.5">
                一个 Key 通吃全部 AI 功能；模型必须支持图片（智能识别成绩要读图）
              </p>
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
              <p className="text-[11px] text-muted-foreground">模型服务商 API 的网址，选供应商时自动填好</p>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs flex items-center gap-1">
                模型
                <span className="text-[10px] font-normal text-primary flex items-center gap-0.5">
                  <ImageIcon className="h-3 w-3" />均需支持图片
                </span>
              </Label>
              {models.length > 0 ? (
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger className="bg-background"><SelectValue placeholder="选择模型" /></SelectTrigger>
                  <SelectContent>
                    {modelOptions.map(m => <SelectItem key={m} value={m}>{m}</SelectItem>)}
                  </SelectContent>
                </Select>
              ) : null}
              <Input className="bg-background" placeholder={models.length > 0 ? "或输入自定义模型名" : "模型名，如 moonshot-v1-8k-vision-preview"}
                value={model} onChange={e => setModel(e.target.value)} />
              {provider === "custom" ? (
                <p className="text-[11px] text-amber-600">自定义模型请自行确认支持图片输入，否则智能识别成绩不可用</p>
              ) : null}
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
                  {apiKey ? "已填写（保存后生效，仅本人可用）" : view.fallback === "user" ? "已配置（回显脱敏）" : view.fallback === "env" ? "未填写：使用服务器默认配置" : "未填写：AI 功能将走规则降级"}
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

          <p className="text-[11px] text-muted-foreground text-center pb-2">
            推荐：Kimi（moonshot-v1-8k-vision-preview）或 GPT-4o mini——识图好且便宜
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
