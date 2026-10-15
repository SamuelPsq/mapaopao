import { useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router"
import { api, loadUser, clearUser, loadUsername, savePlan } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import ConfirmButton from "@/components/ConfirmButton"
import { LogOut, UserRound, UserRoundX, SlidersHorizontal, Scale, Minus, Plus, ChevronUp, ChevronDown, PencilLine } from "lucide-react"
import { Shell, NavBar } from "./Today"

interface Metrics {
  weeks: { plan_id: string; week_no: number; planned: number; done: number; rate: number | null; key_planned: number; key_done: number }[]
  guard: { total: number; blocked: number; effectiveness: number }
  ai_decision: { tune_total: number; tune_applied: number; tune_rate: number; recommend_total: number; recommend_llm: number; kb_injections: number; kb_blocks_used: number; cases_recalled: number }
  llm_usage: { llm: number; template: number; rate: number }
  user: { avail_days: number; vdot: number; goal_race: string; height_cm: number | null; weight_kg: number | null; bmi: number | null } | null
}

interface Me {
  username: string
  nickname: string
  avatar: string
  vdot: number
  height_cm: number | null
  weight_kg: number | null
  city: string
}

export default function MetricsPage() {
  const nav = useNavigate()
  const uid = loadUser()
  const [m, setM] = useState<Metrics | null>(null)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [password, setPassword] = useState("")
  const [deleteErr, setDeleteErr] = useState("")
  const [deleting, setDeleting] = useState(false)
  // 修改训练计划弹窗
  const [adjustOpen, setAdjustOpen] = useState(false)
  const [adjDays, setAdjDays] = useState<number | null>(null)
  const [adjShift, setAdjShift] = useState(0)
  const [adjBusy, setAdjBusy] = useState(false)
  const [adjConfirm, setAdjConfirm] = useState(false)     // 防误触：确认重建计划
  const [weightOpen, setWeightOpen] = useState(false)
  const [weightInput, setWeightInput] = useState("")
  const [weightBusy, setWeightBusy] = useState(false)
  const [weightConfirm, setWeightConfirm] = useState(false) // 防误触：确认更新体重
  const [toast, setToast] = useState("")
  // 用户卡片 / 头像昵称编辑
  const [me, setMe] = useState<Me | null>(null)
  const [profileOpen, setProfileOpen] = useState(false)
  const [nickInput, setNickInput] = useState("")
  const [avatarData, setAvatarData] = useState("")
  const [avatarBusy, setAvatarBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  // 选图后压到 128px JPEG 再转 base64，控制体积 ≤150KB（后端还有一层校验）
  function onPickAvatar(file: File | undefined) {
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => {
      const img = new Image()
      img.onload = () => {
        const S = 128
        const canvas = document.createElement("canvas")
        canvas.width = S; canvas.height = S
        const g = canvas.getContext("2d")!
        const side = Math.min(img.width, img.height)
        g.drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side,
          0, 0, S, S)
        setAvatarData(canvas.toDataURL("image/jpeg", 0.8))
      }
      img.src = String(reader.result)
    }
    reader.readAsDataURL(file)
  }

  async function submitProfile() {
    const nick = nickInput.trim()
    if (!nick && !avatarData) { setToast("改点啥再保存～"); return }
    setAvatarBusy(true)
    try {
      await api.post("/v1/user/profile",
        { user_id: uid, nickname: nick || undefined, avatar: avatarData || undefined })
      const fresh = await api.get<Me>(`/v1/user/me?user_id=${uid}`)
      setMe(fresh)
      setProfileOpen(false)
      setToast("头像和昵称已更新")
    } catch (e) {
      setToast((e as Error).message.replace(/^\d+:\s*/, ""))
    } finally {
      setAvatarBusy(false)
    }
  }

  async function submitWeight() {
    const w = Number(weightInput)
    if (!Number.isFinite(w) || w <= 0) { setToast("请输入有效体重"); return }
    setWeightBusy(true)
    try {
      const r = await api.post<{ ok: boolean; bmi: number | null; advice?: string }>("/v1/user/weight",
        { user_id: uid, weight_kg: w })
      setWeightOpen(false)
      setToast(r.advice || `体重已更新为 ${w} kg${r.bmi ? `，BMI ${r.bmi}` : ""}`)
      const fresh = await api.get<Metrics>(`/v1/metrics?user_id=${uid}`)
      setM(fresh)
    } catch (e) {
      setToast("更新失败：" + (e as Error).message)
    } finally {
      setWeightBusy(false)
    }
  }

  useEffect(() => {
    if (!uid) { nav("/login", { replace: true }); return }
    api.get<Metrics>(`/v1/metrics?user_id=${uid}`).then(setM).catch(console.error)
    api.get<Me>(`/v1/user/me?user_id=${uid}`).then(setMe).catch(console.error)
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

  async function submitAdjust() {
    if (!m?.user) return
    setAdjBusy(true)
    try {
      const r = await api.post<{ ok: boolean; changed: boolean; plan_id?: string; message?: string }>(
        "/v1/plan/adjust",
        { user_id: uid, avail_days: adjDays, intensity_shift: adjShift })
      if (r.changed && r.plan_id) savePlan(r.plan_id)
      setAdjustOpen(false)
      setToast(r.message || "已保存")
    } catch (e) {
      setToast((e as Error).message.replace(/^\d+:\s*/, ""))
    } finally {
      setAdjBusy(false)
    }
  }

  const done = m.weeks.reduce((a, w) => a + w.done, 0)
  const planned = m.weeks.reduce((a, w) => a + w.planned, 0)
  const overall = planned ? Math.round((done / planned) * 100) : 0
  const R = 44
  const CIRC = 2 * Math.PI * R

  return (
    <Shell>
      <h2 className="text-xl font-bold mb-5">指标看板</h2>

      {/* 第一排：累计执行率（60% 宽，主指标）+ 用户卡片（40% 宽），同高 */}
      <div className="flex gap-3 mb-4 items-stretch">
        <div className="rounded-3xl border-2 bg-card shadow-sm flex-[3] min-w-0">
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

        {/* 用户卡片：点击编辑头像 / 昵称（落库） */}
        <button
          onClick={() => { setNickInput(me?.nickname || ""); setAvatarData(""); setProfileOpen(true) }}
          className="rounded-3xl border bg-card shadow-sm flex-[2] min-w-0 flex flex-col items-center justify-center gap-1.5 py-4 active:scale-[0.98] transition-transform">
          {me?.avatar ? (
            <img src={me.avatar} alt="头像" className="h-14 w-14 rounded-full object-cover border-2 border-primary/30" />
          ) : (
            <span className="h-14 w-14 rounded-full bg-primary/10 text-primary flex items-center justify-center">
              <UserRound className="h-7 w-7" />
            </span>
          )}
          <span className="text-sm font-semibold truncate max-w-[92%]">{me?.nickname || loadUsername() || "跑者"}</span>
          <span className="text-[11px] text-muted-foreground flex items-center gap-1">
            <PencilLine className="h-3 w-3" />点头像改资料
          </span>
        </button>
      </div>

      {/* 次级指标：护栏 / AI 决策 / AI 文案 三列紧凑排布 */}
      <div className="grid grid-cols-3 gap-2 mb-6">
        <div className="rounded-2xl border bg-card p-3 shadow-sm">
          <div className="text-[11px] text-muted-foreground">护栏有效性</div>
          <div className="text-xl font-black mt-0.5">{Math.round(m.guard.effectiveness * 100)}%</div>
          <div className="text-[10px] text-muted-foreground/80 mt-1 leading-snug">
            拦截 {m.guard.blocked} / 校验 {m.guard.total} 次 · 留痕才可审计
          </div>
        </div>
        <div className="rounded-2xl border bg-card p-3 shadow-sm">
          <div className="text-[11px] text-muted-foreground">AI 决策</div>
          <div className="text-xl font-black mt-0.5">{Math.round(m.ai_decision.tune_rate * 100)}%</div>
          <div className="text-[10px] text-muted-foreground/80 mt-1 leading-snug">
            微调 {m.ai_decision.tune_applied}/{m.ai_decision.tune_total} · 推荐 {m.ai_decision.recommend_total} 次
          </div>
          <div className="text-[10px] text-muted-foreground/60 mt-0.5 leading-snug">
            知识库 {m.ai_decision.kb_blocks_used} 块 · 案例召回 {m.ai_decision.cases_recalled} 条
          </div>
        </div>
        <div className="rounded-2xl border bg-card p-3 shadow-sm">
          <div className="text-[11px] text-muted-foreground">AI 文案使用率</div>
          <div className="text-xl font-black mt-0.5">{Math.round(m.llm_usage.rate * 100)}%</div>
          <div className="text-[10px] text-muted-foreground/80 mt-1 leading-snug">
            LLM {m.llm_usage.llm} 次 · 模板兜底 {m.llm_usage.template} 次 · 未配密钥自动降级
          </div>
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

      {/* 修改训练计划：训练天数 / 训练强度档位 */}
      <Button variant="outline" className="w-full mt-6 bg-card"
        onClick={() => { setAdjDays(m.user?.avail_days ?? null); setAdjShift(0); setAdjustOpen(true) }}>
        <SlidersHorizontal className="h-4 w-4 mr-1.5" />修改训练计划
      </Button>

      {/* 修改体重：实时生效，BMI≥28 时建议重新生成保守计划 */}
      <Button variant="outline" className="w-full mt-2 bg-card"
        onClick={() => { setWeightInput(m.user?.weight_kg ? String(m.user.weight_kg) : ""); setWeightOpen(true) }}>
        <Scale className="h-4 w-4 mr-1.5" />修改体重
        {m.user?.bmi ? <span className="ml-auto text-xs text-muted-foreground">BMI {m.user.bmi}</span> : null}
      </Button>

      {/* 账号：退出登录 / 切换账号（数据保留在服务端，重新登录可继续） */}
      <div className="mt-6 grid grid-cols-2 gap-2">
        <ConfirmButton variant="outline" className="bg-card" title="确认退出登录？"
          description="退出后需要重新登录才能查看训练计划；训练数据都保留在账号下，不会丢失。"
          confirmText="退出登录"
          onConfirm={() => { api.post("/v1/account/logout").catch(() => {}); clearUser(); nav("/login") }}>
          <LogOut className="h-4 w-4 mr-1.5" />退出登录
        </ConfirmButton>
        <ConfirmButton variant="outline" className="bg-card" title="切换账号？"
          description="将退出当前账号并回到登录页，用另一个账号登录即可；原账号的训练数据保留在服务端。"
          confirmText="切换账号"
          onConfirm={() => { api.post("/v1/account/logout").catch(() => {}); clearUser(); nav("/login") }}>
          <UserRound className="h-4 w-4 mr-1.5" />切换账号
        </ConfirmButton>
      </div>
      <Button className="w-full mt-2 bg-orange-500 hover:bg-orange-600 text-white"
        onClick={() => { setPassword(""); setDeleteErr(""); setDeleteOpen(true) }}>
        <UserRoundX className="h-4 w-4 mr-1.5" />注销账号
      </Button>
      <p className="text-xs text-muted-foreground/70 text-center mt-2">
        训练数据保留在账号下，重新登录后进度不丢
      </p>

      {/* 修改训练计划弹窗：每周训练天数 + 训练强度档位 */}
      <Dialog open={adjustOpen} onOpenChange={setAdjustOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>修改训练计划</DialogTitle>
            <DialogDescription>
              修改后训练计划会按新参数重新生成，当前进度清零。强度只影响目标配速，VDOT 算法的课表结构不变。
            </DialogDescription>
          </DialogHeader>
          {m.user ? (
            <div className="space-y-5 py-2">
              <div>
                <Label>每周训练天数</Label>
                <div className="flex items-center gap-3 mt-2">
                  <Button variant="outline" size="icon" disabled={(adjDays ?? m.user.avail_days) <= 2}
                    onClick={() => setAdjDays((adjDays ?? m.user!.avail_days) - 1)}>
                    <Minus className="h-4 w-4" />
                  </Button>
                  <span className="flex-1 text-center text-2xl font-black">{adjDays ?? m.user.avail_days} 天</span>
                  <Button variant="outline" size="icon" disabled={(adjDays ?? m.user.avail_days) >= 7}
                    onClick={() => setAdjDays((adjDays ?? m.user!.avail_days) + 1)}>
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <div>
                <Label>训练强度（当前 VDOT {m.user.vdot}{adjShift ? ` → ${m.user.vdot + adjShift * 2}` : ""}）</Label>
                <div className="grid grid-cols-2 gap-2 mt-2">
                  <Button variant={adjShift === -1 ? "default" : "outline"}
                    disabled={m.user.vdot - 2 < 30}
                    onClick={() => setAdjShift(adjShift === -1 ? 0 : -1)}>
                    <ChevronDown className="h-4 w-4 mr-1" />减少一级训练强度
                  </Button>
                  <Button variant={adjShift === 1 ? "default" : "outline"}
                    disabled={m.user.vdot + 2 > 70}
                    onClick={() => setAdjShift(adjShift === 1 ? 0 : 1)}>
                    <ChevronUp className="h-4 w-4 mr-1" />增加一级训练强度
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground mt-1.5">一级 = VDOT ±2，目标配速随之放慢 / 加快</p>
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setAdjustOpen(false)}>取消</Button>
            <Button disabled={adjBusy ||
                (adjDays === m.user?.avail_days && adjShift === 0)}
              onClick={() => setAdjConfirm(true)}>
              {adjBusy ? "正在重新生成…" : "确认修改"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 防误触：确认修改训练计划（单向：按新参数重建，当前进度清零） */}
      <AlertDialog open={adjConfirm} onOpenChange={setAdjConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认按新参数重建计划？</AlertDialogTitle>
            <AlertDialogDescription>
              每周训练 {adjDays ?? m.user?.avail_days} 天，强度档位
              {adjShift === 0 ? "不变" : adjShift === 1 ? " +1 级（VDOT +2）" : " -1 级（VDOT -2）"}。
              计划会按新参数重新生成，当前进度清零；已完成的打卡记录保留。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>点错了</AlertDialogCancel>
            <AlertDialogAction onClick={() => { setAdjConfirm(false); submitAdjust() }}>确认重建</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 修改体重弹窗 */}
      <Dialog open={weightOpen} onOpenChange={setWeightOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>修改体重</DialogTitle>
            <DialogDescription>
              体重实时生效：BMI 会在下次生成计划时参与规划修正（BMI≥28 自动启用保守模式，周增幅减半）。
              {m.user?.height_cm ? <>当前身高 {m.user.height_cm} cm。</> : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="py-2">
            <Label>体重（kg）</Label>
            <Input type="number" inputMode="decimal" min={30} max={300} step={0.5} autoFocus
              placeholder="例如 65"
              value={weightInput}
              onChange={(e) => setWeightInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") submitWeight() }} />
            {(() => {
              const w = Number(weightInput)
              if (m.user?.height_cm && Number.isFinite(w) && w > 0) {
                const bmi = w / ((m.user.height_cm / 100) ** 2)
                return <p className="text-xs text-muted-foreground mt-2">
                  BMI = {bmi.toFixed(1)}{bmi >= 28 ? "，达到保守规划阈值" : "，正常区间"}
                </p>
              }
              return null
            })()}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setWeightOpen(false)}>取消</Button>
            <Button disabled={weightBusy} onClick={() => setWeightConfirm(true)}>
              {weightBusy ? "正在保存…" : "确认修改"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 防误触：确认修改体重 */}
      <AlertDialog open={weightConfirm} onOpenChange={setWeightConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认更新体重？</AlertDialogTitle>
            <AlertDialogDescription>
              体重将更新为 <b>{weightInput} kg</b>
              {m.user?.height_cm && Number(weightInput) > 0
                ? <>（BMI {(Number(weightInput) / ((m.user.height_cm / 100) ** 2)).toFixed(1)}）
                    ，会影响后续计划的保守模式判定</>
                : ""}，之后随时可以再次修改。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>点错了</AlertDialogCancel>
            <AlertDialogAction onClick={() => { setWeightConfirm(false); submitWeight() }}>确认更新</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 编辑头像 / 昵称：头像压缩成 128px base64 后落库 */}
      <Dialog open={profileOpen} onOpenChange={setProfileOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑个人资料</DialogTitle>
            <DialogDescription>
              头像和昵称会显示在指标页，并保存到账号数据里。
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col items-center gap-3 py-2">
            <input ref={fileRef} type="file" accept="image/*" className="hidden"
              onChange={e => onPickAvatar(e.target.files?.[0])} />
            <button onClick={() => fileRef.current?.click()}
              className="relative h-20 w-20 rounded-full overflow-hidden border-2 border-primary/30 active:scale-95 transition-transform">
              {avatarData || me?.avatar ? (
                <img src={avatarData || me?.avatar} alt="头像" className="h-full w-full object-cover" />
              ) : (
                <span className="h-full w-full bg-primary/10 text-primary flex items-center justify-center">
                  <UserRound className="h-9 w-9" />
                </span>
              )}
              <span className="absolute inset-x-0 bottom-0 bg-black/45 text-white text-[10px] py-0.5">换头像</span>
            </button>
            <div className="w-full">
              <Label>昵称（1-20 字）</Label>
              <Input value={nickInput} maxLength={20} placeholder="给自己起个跑名"
                onChange={e => setNickInput(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setProfileOpen(false)}>取消</Button>
            <Button disabled={avatarBusy || (!nickInput.trim() && !avatarData)}
              onClick={submitProfile}>
              {avatarBusy ? "正在保存…" : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

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
      {toast ? <div className="fixed bottom-20 left-1/2 -translate-x-1/2 bg-foreground text-background text-sm px-4 py-2 rounded-full shadow-lg max-w-xs text-center z-50"
        onClick={() => setToast("")}>{toast}</div> : null}
    </Shell>
  )
}
