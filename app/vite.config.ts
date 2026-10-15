import path from "path"
import { spawn } from "child_process"
import { fileURLToPath } from "url"
import react from "@vitejs/plugin-react"
import { defineConfig, type Plugin } from "vite"
import { inspectAttr } from 'kimi-plugin-inspect-react'

// 一键重启后端的开关必须装在前端 dev server 上：后端被挂起后，后端自己的 /dev/restart
// 接口已经不可达，重启按钮会变成摆设。这里用中间件拦截 POST /dev/restart（先于 proxy），
// 以分离进程拉起 server/dev_restart.py kill（杀掉占用 8000 端口的进程 → 等端口释放 →
// 启动新 uvicorn），立即返回 200，前端继续轮询探活。仅 dev server 存在，build 产物无此逻辑。
function devRestartBackend(): Plugin {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
  return {
    name: "dev-restart-backend",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (req.method !== "POST" || req.url !== "/dev/restart") return next()
        const py = path.join(root, ".venv", "Scripts", "python.exe")
        const helper = path.join(root, "server", "dev_restart.py")
        try {
          const child = spawn(py, [helper, "kill"], {
            cwd: root,
            detached: true,
            stdio: "ignore",
            env: { ...process.env, MAPAOPAO_DEV: "1" },
          })
          child.unref()
        } catch {
          /* 拉起失败也返回 200——前端会轮询探活，起不来会在界面上持续显示「重启中…」 */
        }
        res.statusCode = 200
        res.setHeader("Content-Type", "application/json")
        res.end(JSON.stringify({ ok: true }))
      })
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  base: './',
  plugins: [inspectAttr(), react(), devRestartBackend()],
  server: {
    port: 7100,
    host: true,
    proxy: {
      "/v1": "http://127.0.0.1:8000",
      "/jobs": "http://127.0.0.1:8000",
      "/dev": "http://127.0.0.1:8000",
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
