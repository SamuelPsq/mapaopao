#!/usr/bin/env node
/**
 * 开发启动器：一条命令拉起 FastAPI 后端 + Vite 前端。
 * 用法：npm run dev [-- --port 7100 --host 127.0.0.1]
 * host/port 参数会转发给 Vite；后端固定 127.0.0.1:8000（Vite 通过 proxy 转发 /v1、/jobs）。
 */
import { spawn } from "node:child_process"
import { fileURLToPath } from "node:url"
import path from "node:path"

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)))
const args = process.argv.slice(2)

let port = 7100
let host = "127.0.0.1"
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--port" && args[i + 1]) port = Number(args[++i])
  if (args[i] === "--host" && args[i + 1]) host = args[++i]
}

const isWin = process.platform === "win32"
const py = path.join(root, ".venv", isWin ? "Scripts/python.exe" : "bin/python")

const procs = [
  spawn(py, ["-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", "8000"], {
    cwd: root, stdio: "inherit",
  }),
  spawn("npm", ["--prefix", "app", "run", "dev", "--", "--port", String(port), "--host", host], {
    cwd: root, stdio: "inherit", shell: isWin,
  }),
]

const kill = () => { for (const p of procs) p.kill("SIGTERM") }
process.on("SIGINT", () => { kill(); process.exit(0) })
process.on("SIGTERM", () => { kill(); process.exit(0) })
for (const p of procs) p.on("exit", (code) => { if (code) kill() })
