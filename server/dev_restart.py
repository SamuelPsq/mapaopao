"""【仅开发演示】一键重启的看护进程：等旧后端退出、8000 端口释放后拉起新后端。

进程不能自己重启自己——/dev/restart 接口以分离进程方式拉起本脚本并传入旧进程
PID，本脚本等旧进程死亡、端口释放后再启动新的 uvicorn，然后自行退出。
仅 MAPAOPAO_DEV=1 的演示流程会用到，生产环境不存在这条链路。

用法: python server/dev_restart.py <旧后端进程 PID>
      python server/dev_restart.py kill    # 不知道/没有旧 PID：先杀掉占用 8000 端口的进程再拉起。
      两种模式最终都保证「端口空闲后才启动新实例」，因此重启按钮在前端 dev server 上也能用
      （后端全挂时 /dev/restart 接口本身已不可达，必须由 vite 中间件调本脚本）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

PORT = 8000
WAIT_ROUNDS = 120          # 最多等 60 秒，等不到也尝试启动（uvicorn 会因端口占用退出，日志留在 backend.log）


def _port_free() -> bool:
    import socket
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", PORT))
        return False
    except OSError:
        return True
    finally:
        s.close()


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259                        # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _port_owner_pid() -> int:
    """返回占用 PORT 的进程 PID（找不到返回 0）。Windows 用 netstat 解析，POSIX 用 lsof。"""
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                                 timeout=10).stdout
            for line in out.splitlines():
                if f":{PORT}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts and parts[-1].isdigit():
                        return int(parts[-1])
            return 0
        out = subprocess.run(["lsof", "-ti", f":{PORT}"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        return int(out.split()[0]) if out else 0
    except Exception:
        return 0


def _kill_pid(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=15)
        else:
            import signal
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "0"
    old_pid = 0
    if arg.lower() == "kill":
        # 「不知道旧 PID」模式：直接清掉占用端口的进程，再走统一的重启流程
        owner = _port_owner_pid()
        if owner and owner != os.getpid():
            _kill_pid(owner)
    else:
        try:
            old_pid = int(arg)
        except ValueError:
            old_pid = 0
    for _ in range(WAIT_ROUNDS):
        if old_pid and _pid_alive(old_pid):
            time.sleep(0.5)
            continue
        if not _port_free():
            time.sleep(0.5)
            continue
        break
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, MAPAOPAO_DEV="1")
    log = open(os.path.join(root, "backend.log"), "ab", buffering=0)
    subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app", "--port", str(PORT)],
                     cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     close_fds=True)


if __name__ == "__main__":
    main()
