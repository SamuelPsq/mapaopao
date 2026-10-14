import { useEffect, useState } from "react"
import { useNavigate } from "react-router"
import { api, loadUser } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Shell } from "./Today"

export default function Degrade() {
  const nav = useNavigate()
  const uid = loadUser()!
  const [done, setDone] = useState("")

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
          <Card className="cursor-pointer" onClick={() => choose("half")}>
            <CardContent className="pt-5">
              <div className="font-bold">A · 调整为半马（21.1 km）</div>
              <p className="text-sm text-muted-foreground mt-1">
                剩余周计划已按半马重新生成，完赛把握高。21.1 km 也是了不起的成就。
              </p>
            </CardContent>
          </Card>
          <Card className="cursor-pointer" onClick={() => choose("defer")}>
            <CardContent className="pt-5">
              <div className="font-bold">B · 延期到下一期</div>
              <p className="text-sm text-muted-foreground mt-1">
                保持全马目标，赛事顺延。用接下来的时间先打有氧基础，下期稳稳完赛。
              </p>
            </CardContent>
          </Card>
          <Card className="cursor-pointer" onClick={() => choose("insist")}>
            <CardContent className="pt-5">
              <div className="font-bold">C · 我知道风险，仍按原计划</div>
              <p className="text-sm text-muted-foreground mt-1">
                需要你确认：本周自动进入降载模式，受伤风险自担。训练中如不适请立刻停止并就医。
              </p>
            </CardContent>
          </Card>
        </div>
      )}

      <Button variant="ghost" className="w-full mt-6" onClick={() => nav("/")}>返回今日卡</Button>
    </Shell>
  )
}
