import { useEffect, useState } from "react"
import { useNavigate } from "react-router"
import { api, loadUser } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "@/components/ui/alert-dialog"
import { Card, CardContent } from "@/components/ui/card"
import { Shell } from "./Today"

const OPTIONS = {
  half: {
    title: "A · 调整为半马（21.1 km）",
    desc: "剩余周计划已按半马重新生成，完赛把握高。21.1 km 也是了不起的成就。",
    confirmTitle: "确认调整为半马？",
    confirmDesc: "剩余周的训练计划将按半马目标重新生成，全马目标放弃；之后想再改可以重新调整。",
  },
  defer: {
    title: "B · 延期到下一期",
    desc: "保持全马目标，赛事顺延。用接下来的时间先打有氧基础，下期稳稳完赛。",
    confirmTitle: "确认延期到下一期？",
    confirmDesc: "保持全马目标，当前期计划结束，用接下来的时间打有氧基础，下一期再挑战。",
  },
  insist: {
    title: "C · 我知道风险，仍按原计划",
    desc: "需要你确认：本周自动进入降载模式，受伤风险自担。训练中如不适请立刻停止并就医。",
    confirmTitle: "确认仍按原计划？",
    confirmDesc: "本周将自动进入降载模式，训练受伤风险自担；训练中如不适请立刻停止并就医。",
  },
} as const

export default function Degrade() {
  const nav = useNavigate()
  const uid = loadUser()!
  const [done, setDone] = useState("")
  const [pending, setPending] = useState<null | keyof typeof OPTIONS>(null)

  useEffect(() => {
    if (!loadUser()) nav("/login", { replace: true })
  }, [nav])

  async function choose(to: "half" | "defer" | "insist") {
    const today = await api.get<{ plan_id: string }>(`/v1/today?user_id=${uid}`)
    const r = await api.post<{ message: string }>(`/v1/plan/${today.plan_id}/degrade-goal`, { to })
    setDone(r.message)
  }

  return (
    <Shell>
      <div className="mb-5">
        <h2 className="text-xl font-bold">调整目标</h2>
        <p className="text-xs text-muted-foreground mt-1">
          建议权在产品，决定权在你。竞品不会在你掉队时劝你降目标 —— 我们会。
        </p>
      </div>

      {done ? (
        <Card><CardContent className="pt-6 text-sm">{done}</CardContent></Card>
      ) : (
        <div className="space-y-3">
          {(Object.keys(OPTIONS) as (keyof typeof OPTIONS)[]).map((k) => (
            <Card key={k} className="cursor-pointer" onClick={() => setPending(k)}>
              <CardContent className="pt-5">
                <div className="font-bold">{OPTIONS[k].title}</div>
                <p className="text-sm text-muted-foreground mt-1">{OPTIONS[k].desc}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* 防误触：三个选项都立即改目标，先确认 */}
      <AlertDialog open={!!pending} onOpenChange={(o) => { if (!o) setPending(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{pending ? OPTIONS[pending].confirmTitle : ""}</AlertDialogTitle>
            <AlertDialogDescription>{pending ? OPTIONS[pending].confirmDesc : ""}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>点错了</AlertDialogCancel>
            <AlertDialogAction onClick={() => { const p = pending; setPending(null); if (p) choose(p) }}>
              确认
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Button variant="ghost" className="w-full mt-6" onClick={() => nav("/")}>返回今日卡</Button>
    </Shell>
  )
}
