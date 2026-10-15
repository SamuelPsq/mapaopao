import { useRef, useState } from "react"
import { useNavigate } from "react-router"
import { api, saveUser, savePlan } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Slider } from "@/components/ui/slider"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { WheelPicker } from "@/components/WheelPicker"
import { REGIONS, findCity } from "@/lib/cities"
import { UserRound, PartyPopper, ShieldCheck } from "lucide-react"

interface Answers {
  recent_km: number
  recent_min: number
  avail_days: number
  goal_race: string
  ability: string
  injury_flag: string
  height_cm: number
  weight_kg: number
}

const STEPS = [
  { key: "run", title: "最近一次跑步", desc: "距离和用时，用来估算你的能力值（VDOT）" },
  { key: "days", title: "每周能练几天", desc: "计划会按可用天数收敛课次，不给你的生活添堵" },
  { key: "goal", title: "目标赛事", desc: "第一次挑战，选保守一点的目标更聪明" },
  { key: "ability", title: "你的跑步基础", desc: "零基础用户会自动套用 0.92 的保守系数" },
  { key: "body", title: "身高和体重", desc: "用来算 BMI：体重较大时计划自动保守（周增幅减半），保护关节" },
  { key: "injury", title: "3 个月内有没有较重伤痛", desc: "有的话我们会先劝你康复，而不是给你计划" },
  { key: "city", title: "你在哪个城市", desc: "用来判断天气对训练的影响（体感温度口径）" },
] as const

export default function Onboarding() {
  const nav = useNavigate()
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState(false)
  const [reject, setReject] = useState<{ reason: string; suggest: string } | null>(null)
  const [a, setA] = useState<Answers>({
    recent_km: 5, recent_min: 30, avail_days: 4, goal_race: "full",
    ability: "B_base", injury_flag: "N", height_cm: 170, weight_kg: 65,
  })
  const [province, setProvince] = useState("上海")
  const [city, setCity] = useState("上海")
  // 身高体重确认：填后不可修改，下一步前先二次确认
  const [bodyConfirm, setBodyConfirm] = useState(false)
  // 防误触：生成计划前确认参数摘要（单向：生成后计划重建、进度清零）
  const [genConfirm, setGenConfirm] = useState(false)
  // 体测结束：计划已生成，进入上传头像环节（可跳过）
  const [generated, setGenerated] = useState(false)
  const [genUid, setGenUid] = useState("")
  const [avatarData, setAvatarData] = useState("")
  const [avatarBusy, setAvatarBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  // 选图后压到 128px JPEG 再转 base64，控制体积（与指标页编辑同一套压缩）
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

  async function enterApp(skipAvatar: boolean) {
    if (!skipAvatar && avatarData) {
      setAvatarBusy(true)
      try {
        await api.post("/v1/user/profile", { user_id: genUid, avatar: avatarData })
      } catch {
        // 头像保存失败不阻断进入，指标页还能再传
      } finally {
        setAvatarBusy(false)
      }
    }
    nav("/", { replace: true })
  }

  function pickProvince(name: string) {
    setProvince(name)
    const r = REGIONS.find((x) => x.name === name)!
    setCity(r.cities[0].name)
  }

  const stepDef = STEPS[step]

  async function finish(city: string) {
    setBusy(true)
    try {
      const r = await api.post<{ rejected: boolean; user_id?: string; reason?: string; suggest?: string }>(
        "/v1/profile",
        {
          user_id: loadUid(),
          recent_km: a.recent_km,
          recent_sec: Math.round(a.recent_min * 60),
          avail_days: a.avail_days,
          goal_race: a.goal_race,
          ability: a.ability,
          injury_flag: a.injury_flag,
          height_cm: a.height_cm,
          weight_kg: a.weight_kg,
          city,
          ...cityCoords(city),
        },
      )
      if (r.rejected) {
        setReject({ reason: r.reason || "", suggest: r.suggest || "" })
        return
      }
      const uid = r.user_id!
      saveUser(uid)
      const g = await api.post<{ plan_id: string; vdot: number; weeks: number }>(
        `/v1/plan/generate?user_id=${uid}`)
      savePlan(g.plan_id)
      setGenUid(uid)
      setGenerated(true)            // 体测结束：先上传头像（可跳过），再进今日卡
    } catch (e) {
      alert("生成失败：" + (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (generated) {
    return (
      <Shell>
        <PartyPopper className="h-12 w-12 text-primary mb-4" />
        <h2 className="text-xl font-bold mb-2">16 周计划已生成！</h2>
        <p className="text-sm text-muted-foreground mb-6">
          上传一张头像，让计划更有你的印记；也可以跳过，之后随时在指标页补传。
        </p>
        <div className="flex flex-col items-center gap-4 mb-8">
          <input ref={fileRef} type="file" accept="image/*" className="hidden"
            onChange={e => onPickAvatar(e.target.files?.[0])} />
          <button onClick={() => fileRef.current?.click()}
            className="relative h-24 w-24 rounded-full overflow-hidden border-2 border-primary/30 active:scale-95 transition-transform">
            {avatarData ? (
              <img src={avatarData} alt="头像" className="h-full w-full object-cover" />
            ) : (
              <span className="h-full w-full bg-primary/10 text-primary flex items-center justify-center">
                <UserRound className="h-10 w-10" />
              </span>
            )}
            <span className="absolute inset-x-0 bottom-0 bg-black/45 text-white text-[11px] py-1">
              {avatarData ? "重新选择" : "选择头像"}
            </span>
          </button>
        </div>
        <Button className="w-full" size="lg" disabled={avatarBusy}
          onClick={() => enterApp(false)}>
          {avatarBusy ? "正在保存头像…" : avatarData ? "保存头像，开始训练" : "开始训练"}
        </Button>
        <Button variant="ghost" className="w-full mt-2" disabled={avatarBusy}
          onClick={() => enterApp(true)}>跳过，直接开始</Button>
      </Shell>
    )
  }

  if (reject) {
    return (
      <Shell>
        <ShieldCheck className="h-12 w-12 text-primary mb-4" />
        <h2 className="text-xl font-bold mb-2">现在生成计划还太早</h2>
        <p className="text-muted-foreground mb-2">{reject.suggest}</p>
        <p className="text-xs text-muted-foreground/70 mb-6">
          这不是拒绝你，是护栏前置：训练计划的缺口是沉没成本，一开始就脱离现实的计划没人跑得完。
        </p>
        <Button variant="outline" onClick={() => { setReject(null); setStep(0) }}>修改画像重新评估</Button>
      </Shell>
    )
  }

  return (
    <Shell>
      <div className="flex gap-1.5 mb-6">
        {STEPS.map((_, i) => (
          <div key={i} className={`h-1 flex-1 rounded-full ${i <= step ? "bg-primary" : "bg-muted"}`} />
        ))}
      </div>
      <h2 className="text-xl font-bold">{stepDef.title}</h2>
      <p className="text-sm text-muted-foreground mt-1 mb-6">{stepDef.desc}</p>

      {stepDef.key === "run" && (
        <div className="space-y-8">
          <div>
            <Label>距离：{a.recent_km} km</Label>
            <Slider value={[a.recent_km]} min={1} max={21} step={1}
              onValueChange={([v]) => setA({ ...a, recent_km: v })} />
          </div>
          <div>
            <Label>用时：{a.recent_min} 分钟</Label>
            <Slider value={[a.recent_min]} min={10} max={150} step={1}
              onValueChange={([v]) => setA({ ...a, recent_min: v })} />
          </div>
          <p className="text-xs text-muted-foreground">
            对应配速约 {(a.recent_min / a.recent_km).toFixed(2)} min/km
          </p>
        </div>
      )}

      {stepDef.key === "days" && (
        <RadioGroup value={String(a.avail_days)} onValueChange={(v) => setA({ ...a, avail_days: Number(v) })}>
          {[
            ["2", "≤ 2 天（会先被劝退哦）"],
            ["3", "3 天"],
            ["4", "4 天"],
            ["5", "5 天"],
            ["6", "6 天及以上"],
          ].map(([v, label]) => (
            <label key={v} className="flex items-center gap-3 py-3 border-b last:border-0 cursor-pointer">
              <RadioGroupItem value={v} /><span>{label}</span>
            </label>
          ))}
        </RadioGroup>
      )}

      {stepDef.key === "goal" && (
        <RadioGroup value={a.goal_race} onValueChange={(v) => setA({ ...a, goal_race: v })}>
          {[["full", "全程马拉松 42.195 km"], ["half", "半程马拉松 21.1 km"]].map(([v, label]) => (
            <label key={v} className="flex items-center gap-3 py-3 border-b last:border-0 cursor-pointer">
              <RadioGroupItem value={v} /><span>{label}</span>
            </label>
          ))}
        </RadioGroup>
      )}

      {stepDef.key === "ability" && (
        <RadioGroup value={a.ability} onValueChange={(v) => setA({ ...a, ability: v })}>
          {[
            ["A_zero_base", "零基础：连续跑不满 3km"],
            ["B_base", "有基础：能跑 5km，跑过欢乐跑"],
            ["C_advanced", "进阶：10km+ 或有半马经历"],
          ].map(([v, label]) => (
            <label key={v} className="flex items-center gap-3 py-3 border-b last:border-0 cursor-pointer">
              <RadioGroupItem value={v} /><span>{label}</span>
            </label>
          ))}
        </RadioGroup>
      )}

      {stepDef.key === "body" && (
        <div className="space-y-8">
          <div>
            <Label>身高：{a.height_cm} cm</Label>
            <Slider value={[a.height_cm]} min={140} max={200} step={1}
              onValueChange={([v]) => setA({ ...a, height_cm: v })} />
          </div>
          <div>
            <Label>体重：{a.weight_kg} kg</Label>
            <Slider value={[a.weight_kg]} min={40} max={120} step={1}
              onValueChange={([v]) => setA({ ...a, weight_kg: v })} />
          </div>
          <p className="text-xs text-muted-foreground">
            BMI = {(a.weight_kg / ((a.height_cm / 100) ** 2)).toFixed(1)}
            {a.weight_kg / ((a.height_cm / 100) ** 2) >= 28
              ? "，属于偏高区间——计划会自动启用保守模式：周跑量增幅减半，给关节留适应时间"
              : "，在正常区间内，按标准周期模板排课"}
          </p>
        </div>
      )}

      {stepDef.key === "injury" && (
        <RadioGroup value={a.injury_flag} onValueChange={(v) => setA({ ...a, injury_flag: v })}>
          {[["N", "没有，状态正常"], ["M", "有，最近受过伤"]].map(([v, label]) => (
            <label key={v} className="flex items-center gap-3 py-3 border-b last:border-0 cursor-pointer">
              <RadioGroupItem value={v} /><span>{label}</span>
            </label>
          ))}
        </RadioGroup>
      )}

      {stepDef.key === "city" && (
        <div className="space-y-6">
          <div className="flex gap-3">
            <div className="flex-1">
              <Label className="block text-center mb-1">省 / 直辖市</Label>
              <WheelPicker items={REGIONS.map((r) => r.name)} value={province}
                onChange={pickProvince} />
            </div>
            <div className="flex-1">
              <Label className="block text-center mb-1">城市</Label>
              <WheelPicker
                items={(REGIONS.find((r) => r.name === province) ?? REGIONS[0]).cities.map((x) => x.name)}
                value={city} onChange={setCity} />
            </div>
          </div>
          <p className="text-sm text-center text-muted-foreground">
            已选：<b className="text-foreground">{province} · {city}</b>
          </p>
          <Button className="w-full" size="lg" disabled={busy} onClick={() => setGenConfirm(true)}>
            {busy ? "正在生成 16 周计划…" : "生成我的训练计划"}
          </Button>
          <p className="text-xs text-muted-foreground text-center">
            计划由规则引擎生成（VDOT 查表 + 周期模板），LLM 只负责解释，不参与任何计算。
          </p>
        </div>
      )}

      {stepDef.key !== "city" && (
        <Button className="w-full mt-8" size="lg"
          onClick={() => stepDef.key === "body" ? setBodyConfirm(true) : setStep(step + 1)}>
          下一步
        </Button>
      )}
      {step > 0 && (
        <Button variant="ghost" className="w-full mt-2" onClick={() => setStep(step - 1)}>上一步</Button>
      )}

      {/* 生成计划前确认：参数摘要，单向操作 */}
      <AlertDialog open={genConfirm} onOpenChange={setGenConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认生成 16 周训练计划？</AlertDialogTitle>
            <AlertDialogDescription>
              目标赛事：<b>{a.goal_race === "full" ? "全程马拉松" : "半程马拉松"}</b>；
              每周训练 <b>{a.avail_days} 天</b>；
              身高 <b>{a.height_cm} cm</b> / 体重 <b>{a.weight_kg} kg</b>（身高之后不可修改）；
              城市 <b>{city}</b>。
              生成后计划按 VDOT 规则引擎排定；后续改训练天数/强度会重建计划、进度清零。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>点错了</AlertDialogCancel>
            <AlertDialogAction onClick={() => { setGenConfirm(false); finish(city) }}>
              确认生成
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 身高体重二次确认：填后不可修改 */}
      <AlertDialog open={bodyConfirm} onOpenChange={setBodyConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>身高数据填写后不可修改，请再次检查</AlertDialogTitle>
            <AlertDialogDescription>
              当前填写：身高 <b>{a.height_cm} cm</b>，体重 <b>{a.weight_kg} kg</b>
              （BMI {(a.weight_kg / ((a.height_cm / 100) ** 2)).toFixed(1)}）。
              身高是计划保守模式的判定依据之一，之后只能修改体重，请确认无误。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>返回修改</AlertDialogCancel>
            <AlertDialogAction onClick={() => { setBodyConfirm(false); setStep(step + 1) }}>
              确认无误，继续
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Shell>
  )
}

function loadUid() { return localStorage.getItem("mpaopao_uid") || undefined }

/** 城市中心近似坐标，查不到时回退上海 */
function cityCoords(name: string): { lat: number; lon: number } {
  return findCity(name) ?? { lat: 31.23, lon: 121.47 }
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-background flex justify-center">
      <div className="w-full max-w-md px-5 py-8">
        <div className="mb-8">
          <div className="text-3xl font-black tracking-tight">马跑跑</div>
          <div className="text-sm text-muted-foreground">马拉松训练规划助手 · 16 周</div>
        </div>
        {children}
      </div>
    </div>
  )
}
