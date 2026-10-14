import { useEffect, useRef, useState } from "react"

const ROW_H = 36
const HALF_PAD = 72 // 上下各空出 2 行，让首尾项也能停在中心

/** 竖向滚轮选择器：固定 5 行可视，滚动停止后吸附并回调中心项 */
export function WheelPicker({ items, value, onChange }: {
  items: string[]; value: string; onChange: (v: string) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const timer = useRef<number | null>(null)
  const [active, setActive] = useState(() => Math.max(0, items.indexOf(value)))

  // 外部值变化（含初次挂载）时滚动到目标行；已对齐则不动，避免滚动事件回环
  useEffect(() => {
    const idx = items.indexOf(value)
    if (idx < 0 || !ref.current) return
    setActive(idx)
    if (Math.abs(ref.current.scrollTop - idx * ROW_H) > 1) {
      ref.current.scrollTop = idx * ROW_H
    }
  }, [value, items])

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])

  function handleScroll() {
    if (!ref.current) return
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      const el = ref.current
      if (!el) return
      const clamped = Math.min(Math.max(Math.round(el.scrollTop / ROW_H), 0), items.length - 1)
      el.scrollTop = clamped * ROW_H // 对齐吸附
      setActive(clamped)
      if (items[clamped] !== value) onChange(items[clamped])
    }, 120)
  }

  /** 点击行：滚到该行居中并选中（等效于滑到该处） */
  function pick(i: number) {
    const el = ref.current
    if (el) el.scrollTop = i * ROW_H
    setActive(i)
    if (items[i] !== value) onChange(items[i])
  }

  return (
    <div className="relative h-[180px] overflow-hidden">
      <div ref={ref} onScroll={handleScroll}
        className="h-full overflow-y-auto snap-y snap-mandatory [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        <div style={{ height: HALF_PAD }} />
        {items.map((it, i) => (
          <button type="button" key={it} style={{ height: ROW_H }} onClick={() => pick(i)}
            className={`snap-center w-full flex items-center justify-center text-sm transition-colors cursor-pointer
              ${i === active ? "font-semibold text-foreground" : "text-muted-foreground/50"}`}>
            {it}
          </button>
        ))}
        <div style={{ height: HALF_PAD }} />
      </div>
      {/* 中心高亮带 */}
      <div className="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-9 pointer-events-none border-y bg-primary/5" />
      {/* 上下渐隐 */}
      <div className="absolute inset-x-0 top-0 h-[72px] pointer-events-none bg-gradient-to-b from-background to-transparent" />
      <div className="absolute inset-x-0 bottom-0 h-[72px] pointer-events-none bg-gradient-to-t from-background to-transparent" />
    </div>
  )
}
