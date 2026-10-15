"""【仅开发演示】一键重启的看护进程：等旧后端退出、8000 端口释放后拉起新后端。

进程不能自己重启自己——/dev/restart 接口以分离进程方式拉起本脚本并传入旧进程
PID，本脚本等旧进程死亡、端口释放后再启动新的 uvicorn，然后自行退出。
仅 MAPAOPAO_DEV=1 的演示流程会用到，生产环境不存在这条链路。

用法: python server/dev_restart.py <旧后端进程 PID>
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


def main() -> None:
    old_pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
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
