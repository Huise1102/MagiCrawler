"""一键启动：本地模型（Ollama，可选）+ 后端 8000 + 前端 5170。

用法：在项目根目录执行 `python start.py`，按 Ctrl+C 会同时结束后端与前端。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN_DIR = "Scripts" if os.name == "nt" else "bin"
PYTHON_NAME = "python.exe" if os.name == "nt" else "python"
VENV_PYTHON = ROOT / ".venv" / BIN_DIR / PYTHON_NAME
FRONTEND_DIR = ROOT / "frontend"

BACKEND_CMD = [
    str(VENV_PYTHON),
    "-m",
    "uvicorn",
    "backend:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
]
FRONTEND_CMD = "npm run dev"

OLLAMA_URL = "http://127.0.0.1:11434"


def _ollama_reachable(timeout: float = 2.0) -> bool:
    """探测 Ollama 服务是否可用。"""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=timeout):
            return True
    except Exception:
        return False


def _settings_provider() -> tuple[str, str]:
    """读取设置中的模型提供方与接口地址。"""
    path = ROOT / "data" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "ollama", ""
    llm = data.get("llm") or {}
    return str(llm.get("provider") or "ollama"), str(llm.get("base_url") or "")


def _find_ollama() -> str | None:
    """定位 ollama 可执行文件。"""
    found = shutil.which("ollama")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        exe = Path(local) / "Programs" / "Ollama" / ("ollama.exe" if os.name == "nt" else "ollama")
        if exe.exists():
            return str(exe)
    return None


def _ensure_ollama(env: dict[str, str]) -> subprocess.Popen | None:
    """在需要本地模型时确保 Ollama 已启动，返回本次启动的进程（未启动为 None）。"""
    provider, base_url = _settings_provider()
    if provider != "ollama" and "11434" not in base_url:
        return None
    if _ollama_reachable():
        print("检测到 Ollama 已在运行。")
        return None

    exe = _find_ollama()
    if not exe:
        print("[警告] 未找到 Ollama，本地模型将不可用；可在设置页改用云端模型。")
        return None

    print("Ollama 未运行，正在启动本地模型服务…")
    proc = subprocess.Popen(
        [exe, "serve"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        time.sleep(1)
        if _ollama_reachable():
            print("Ollama 已就绪：http://127.0.0.1:11434")
            return proc
        if proc.poll() is not None:
            break
    print("[警告] Ollama 启动失败，请手动执行 `ollama serve` 查看原因。")
    print("        常见原因：OLLAMA_MODELS 指向的目录没有写权限。")
    _terminate(proc)
    return None


def _terminate(proc: subprocess.Popen) -> None:
    """结束进程及其子进程（npm/vite 在 Windows 上需要连同进程树一起结束）。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    if not VENV_PYTHON.exists():
        print(f"[错误] 未找到虚拟环境解释器：{VENV_PYTHON}")
        print("请先创建 .venv 并安装 requirements.txt 中的依赖。")
        return 1
    if not (FRONTEND_DIR / "package.json").exists():
        print(f"[错误] 未找到前端项目：{FRONTEND_DIR}")
        return 1
    if not (FRONTEND_DIR / "node_modules").exists():
        print("[提示] 未检测到 frontend/node_modules，如启动失败请先执行：cd frontend && npm install")

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    ollama = _ensure_ollama(env)
    print("正在启动后端：http://127.0.0.1:8000")
    backend = subprocess.Popen(BACKEND_CMD, cwd=str(ROOT), env=env)
    print("正在启动前端：http://127.0.0.1:5170")
    frontend = subprocess.Popen(
        FRONTEND_CMD,
        cwd=str(FRONTEND_DIR),
        env=env,
        shell=True,
    )
    print("启动完成，按 Ctrl+C 可同时关闭前后端。\n")

    try:
        while True:
            time.sleep(1)
            backend_code = backend.poll()
            frontend_code = frontend.poll()
            if backend_code is not None:
                print(f"后端已退出（退出码 {backend_code}），正在关闭前端…")
                return backend_code
            if frontend_code is not None:
                print(f"前端已退出（退出码 {frontend_code}），正在关闭后端…")
                return frontend_code
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，正在关闭前后端…")
        return 0
    finally:
        _terminate(frontend)
        _terminate(backend)
        if ollama is not None:
            _terminate(ollama)
        print("已全部关闭。")


if __name__ == "__main__":
    sys.exit(main())
