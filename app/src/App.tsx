import { useEffect } from 'react'
import { Routes, Route, useNavigate } from 'react-router'
import Onboarding from './pages/Onboarding'
import Login from './pages/Login'
import Today from './pages/Today'
import Rearrange from './pages/Rearrange'
import Week from './pages/Week'
import Metrics from './pages/Metrics'
import Degrade from './pages/Degrade'
import Assistant from './components/Assistant'
import DevTools from './components/DevTools'
import { loadUser } from './lib/api'

export default function App() {
  const nav = useNavigate()
  useEffect(() => {
    // 未登录 → 登录页（登录/注册成功后会自己跳走）
    if (!loadUser() && location.pathname !== '/login') nav('/login', { replace: true })
  }, [nav])
  return (
    <>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/onboarding" element={<Onboarding />} />
        <Route path="/" element={<Today />} />
        <Route path="/rearrange" element={<Rearrange />} />
        <Route path="/week/:no?" element={<Week />} />
        <Route path="/metrics" element={<Metrics />} />
        <Route path="/degrade" element={<Degrade />} />
      </Routes>
      {/* AI 跑步助手：全站悬浮入口（登录页除外由组件自行判断未登录不渲染） */}
      <Assistant />
      {/* 开发演示工具：仅 vite dev 模式可见（模拟断网 / 挂起后端），生产构建自动消失 */}
      <DevTools />
    </>
  )
}
