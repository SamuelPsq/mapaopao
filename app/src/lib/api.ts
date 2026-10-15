const BASE = ""

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = localStorage.getItem("mpaopao_token")
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (token) headers.Authorization = `Bearer ${token}`
  const r = await fetch(BASE + path, { headers, ...init })
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`${r.status}: ${text.slice(0, 200)}`)
  }
  return r.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => req<T>(path),
  post: <T>(path: string, body?: unknown) =>
    req<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
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
  week_progress: { key_done: number; key_total: number; done: number; total: number }
  total_km: number
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
export function savePlan(id: string) { localStorage.setItem("mpaopao_plan", id) }
export function loadPlan(): string | null { return localStorage.getItem("mpaopao_plan") }
export function clearUser() {
  localStorage.removeItem("mpaopao_uid")
  localStorage.removeItem("mpaopao_plan")
  localStorage.removeItem("mpaopao_username")
  localStorage.removeItem("mpaopao_token")
}
