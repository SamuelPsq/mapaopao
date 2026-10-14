import { useState } from "react"
import { Button } from "@/components/ui/button"
import { REGIONS, findCity, type CityInfo } from "@/lib/cities"

function regionOf(cityName: string): string {
  for (const r of REGIONS) if (r.cities.some((x) => x.name === cityName)) return r.name
  return REGIONS[2].name // 默认上海
}

/** 省-市二级联动选择器：左列省、右列市，底部确认 */
export function CityPicker({ current, onConfirm }: {
  current?: string; onConfirm: (city: CityInfo) => void
}) {
  const cur = current ? findCity(current) : null
  const [province, setProvince] = useState(cur ? regionOf(cur.name) : "上海")
  const [city, setCity] = useState(cur?.name ?? "上海")

  const region = REGIONS.find((r) => r.name === province) ?? REGIONS[0]
  const cities = region.cities
  const sel = cities.find((x) => x.name === city) ?? cities[0]

  function pickProvince(name: string) {
    setProvince(name)
    const r = REGIONS.find((x) => x.name === name)!
    if (!r.cities.some((x) => x.name === city)) setCity(r.cities[0].name)
  }

  return (
    <div>
      <div className="flex h-[300px] rounded-xl border overflow-hidden">
        <div className="w-[132px] shrink-0 overflow-y-auto bg-muted/40 py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {REGIONS.map((r) => (
            <button key={r.name} onClick={() => pickProvince(r.name)}
              className={`w-full px-3 py-2.5 text-left text-sm transition-colors
                ${r.name === province ? "bg-background text-primary font-semibold border-r-2 border-primary" : "text-muted-foreground"}`}>
              {r.name}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {cities.map((x) => (
            <button key={x.name} onClick={() => setCity(x.name)}
              className={`w-full px-4 py-2.5 text-left text-sm transition-colors
                ${x.name === city ? "text-primary font-semibold" : "text-muted-foreground"}`}>
              {x.name}
            </button>
          ))}
        </div>
      </div>
      <Button className="w-full mt-4" onClick={() => onConfirm(sel)}>
        切换到{sel.name}
      </Button>
    </div>
  )
}
