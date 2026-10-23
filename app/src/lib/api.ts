const BASE = ""

// 网络层失败（后端没启动/断网）时 fetch 直接抛错，浏览器原生文案是英文 "Failed to fetch"，
// 普通用户看不懂——统一成人话；GET 自动重试（递增退避），写操作不自动重试，避免重复提交。
async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = localStorage.getItem("mpaopao_token")
  const headers: Record<string, string> = {}
  // FormData（图片上传）不能手动设 Content-Type——浏览器要带 boundary 自行设置；
  // 其余请求照旧 JSON
  if (!(init?.body instanceof FormData)) headers["Content-Type"] = "application/json"
  if (token) headers.Authorization = `Bearer ${token}`
  const isGet = (init?.method ?? "GET") === "GET"
  const tries = isGet ? 3 : 1
  for (let attempt = 0; ; attempt++) {
    let r: Response
    try {
      r = await fetch(BASE + path, { headers, ...init })
    } catch {
      if (attempt < tries - 1) {
        await new Promise(res => setTimeout(res, 600 * (attempt + 1)))
        continue
      }
      throw Object.assign(new Error("连不上服务器，请检查网络后重试"), { status: 0 })
    }
    if (r.status === 401) {
      // 凭证失效（token 被轮换/过期）：统一清本地会话并回登录页。
      // 不统一处理的话，各页面表现不一致——今日卡显示裸 401 文案，指标页则永远停在「加载中…」。
      clearUser()
      if (!window.location.pathname.startsWith("/login")) {
        window.location.assign("/login")
      }
      throw Object.assign(new Error("登录状态已失效，请重新登录"), { status: 401 })
    }
    if (!r.ok) {
      // 后端统一返回 {error: 人话}；旧结构 {detail}/{message} 兼容；非 JSON（网关错误页）走兜底文案
      let message = "请求失败，请稍后再试"
      try {
        const j = await r.json()
        if (typeof j?.error === "string") message = j.error
        else if (typeof j?.detail === "string") message = j.detail
        else if (typeof j?.message === "string") message = j.message
      } catch { /* 响应体不是 JSON，保留兜底文案 */ }
      throw Object.assign(new Error(message), { status: r.status })
    }
    return r.json() as Promise<T>
  }
}

export const api = {
  get: <T>(path: string) => req<T>(path),
  post: <T>(path: string, body?: unknown) =>
    req<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  postForm: <T>(path: string, form: FormData) =>
    req<T>(path, { method: "POST", body: form }),
}

// ---------- 类型 ----------
export interface MainSession {
  session_id: string
  date: string
  dow: string
  type: string
  type_cn: string
  is_key: boolean
  pace_zone: string
  target_km: number
  target_pace: string
  source: string
  status: string
  log?: { status: string; actual_km: number; actual_pace: string; rpe: number | null }
}

export interface TodayCard {
  user_id: string
  plan_id: string
  week_no: number
  week_phase: string
  is_dropped: boolean
  weekday: string
  days_to_race: number
  vdot: number
  city: string
  today_has_run: boolean
  main_session: MainSession | null
  reason_line: string
  weather_line: string
  hard_days: string[]
  suggestion: { trigger_src: string; message: string } | null
  degrade: { triggered: boolean; message: string } | null
  recovery_alert: { type: string; message: string } | null
  week_progress: { key_done: number; key_total: number; done: number; total: number }
  total_km: number
  week_km: number
  weather?: {
    t_max: number
    apparent_max: number
    precip: number
    aqi: number | null
    condition: string
    hard: boolean
    hot: boolean
    week: { date: string; t_max: number; apparent_max: number; precip: number; condition: string; hard: boolean }[]
  } | null
}

export interface CandSession {
  session_id: string
  date: string
  dow: string
  type: string
  type_cn: string
  is_key: boolean
  pace_zone: string
  target_km: number
  status: string
  note: string
}

export interface PlanOption {
  strategy: string
  description: string
  delta_km: number
  sessions: CandSession[]
  guard_action: string
  guard_note: string
  explain: { reason: string; what_changed: string; cost: string; reassurance: string; source: string }
}

export interface PreviewResp {
  event_id: string
  main: PlanOption | null
  alt: PlanOption | null
  guard_notes: { strategy: string; hit_rule: string | null; action: string; reason_user: string }[]
  ai_recommendation?: { choice: "main" | "alt" | "either"; reason: string; source: "llm" | "template" } | null
  message?: string
}

export interface WeekReview {
  week_no: number
  phase: string
  planned_km: number
  is_dropped: boolean
  bmi_conservative: boolean
  is_current: boolean
  comment: { text: string; source: string; note?: string | null }
  adherence: { done: number; planned_snapshot: number; rate: number }
  sessions: MainSession[]
  next_week_preview: MainSession[]
}

export const STRATEGY_CN: Record<string, string> = {
  compress: "压缩",
  downgrade: "降级",
  replace: "替换",
  postpone: "顺延",
  deload: "强制降载",
  noop: "保持原计划",
}

export function saveUser(id: string) { localStorage.setItem("mpaopao_uid", id) }
export function loadUser(): string | null { return localStorage.getItem("mpaopao_uid") }
export function saveUsername(name: string) { localStorage.setItem("mpaopao_username", name) }
export function loadUsername(): string | null { return localStorage.getItem("mpaopao_username") }
export function saveToken(t: string) { localStorage.setItem("mpaopao_token", t) }
export function saveRole(r: string) { localStorage.setItem("mpaopao_role", r) }
export function loadRole(): string { return localStorage.getItem("mpaopao_role") || "user" }
export function savePlan(id: string) { localStorage.setItem("mpaopao_plan", id) }
export function loadPlan(): string | null { return localStorage.getItem("mpaopao_plan") }
export function clearUser() {
  localStorage.removeItem("mpaopao_uid")
  localStorage.removeItem("mpaopao_plan")
  localStorage.removeItem("mpaopao_username")
  localStorage.removeItem("mpaopao_token")
  localStorage.removeItem("mpaopao_role")
  // 通知挂根部、路由切换不卸载的组件（如跑小助）立即清空本账号状态，防串话
  window.dispatchEvent(new Event("mpaopao:logout"))
}
