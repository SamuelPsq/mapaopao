import { useState } from "react"
import { useNavigate } from "react-router"
import { api, saveUser, savePlan } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Slider } from "@/components/ui/slider"
import { WheelPicker } from "@/components/WheelPicker"
import { REGIONS, findCity } from "@/lib/cities"

interface Answers {
  recent_km: number
  recent_min: number
  avail_days: number
  goal_race: string
  ability: string
  injury_flag: string
}

const STEPS = [
  { key: "run", title: "最近一次跑步", desc: "距离和用时，用来估算你的能力值（VDOT）" },
  { key: "days", title: "每周能练几天", desc: "计划会按可用天数收敛课次，不给你的生活添堵" },
  { key: "goal", title: "目标赛事", desc: "第一次挑战，选保守一点的目标更聪明" },
  { key: "ability", title: "你的跑步基础", desc: "零基础用户会自动套用 0.92 的保守系数" },
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
    ability: "B_base", injury_flag: "N",
  })
  const [province, setProvince] = useState("上海")
  const [city, setCity] = useState("上海")

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
      nav("/", { replace: true })
    } catch (e) {
      alert("生成失败：" + (e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (reject) {
    return (
      <Shell>
        <div className="text-5xl mb-4">🛡️</div>
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
          <Button className="w-full" size="lg" disabled={busy} onClick={() => finish(city)}>
            {busy ? "正在生成 16 周计划…" : "生成我的训练计划"}
          </Button>
          <p className="text-xs text-muted-foreground text-center">
            计划由规则引擎生成（VDOT 查表 + 周期模板），LLM 只负责解释，不参与任何计算。
          </p>
        </div>
      )}

      {stepDef.key !== "city" && (
        <Button className="w-full mt-8" size="lg" onClick={() => setStep(step + 1)}>下一步</Button>
      )}
      {step > 0 && (
        <Button variant="ghost" className="w-full mt-2" onClick={() => setStep(step - 1)}>上一步</Button>
      )}
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
