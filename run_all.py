from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT / "react UI"
load_dotenv(ROOT / ".env")


def _resolve_npm_executable() -> str | None:
    # On Windows, npm.cmd is the correct executable for subprocess.Popen.
    if os.name == "nt":
        return shutil.which("npm.cmd") or shutil.which("npm")
    return shutil.which("npm")


def _prepend_pythonpath(env: dict[str, str], path: Path) -> dict[str, str]:
    next_env = dict(env)
    existing = next_env.get("PYTHONPATH", "")
    next_env["PYTHONPATH"] = str(path) if not existing else f"{str(path)}{os.pathsep}{existing}"
    return next_env


def _stream_output(name: str, pipe) -> None:
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            print(f"[{name}] {line}", end="")
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def _spawn(name: str, cmd: list[str], cwd: Path, env: dict[str, str]) -> tuple[subprocess.Popen, threading.Thread]:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    thread = threading.Thread(target=_stream_output, args=(name, proc.stdout), daemon=True)
    thread.start()
    return proc, thread


def _wait_for_http(url: str, proc: subprocess.Popen, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + max(1, timeout_seconds)
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            print(f"[run_all] API process exited before it became ready. code={code}")
            return False
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if 200 <= response.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(0.5)
    print(f"[run_all] API did not become ready within {timeout_seconds}s: {url}")
    return False


def _terminate_process(proc: subprocess.Popen, name: str) -> None:
    if proc.poll() is not None:
        return
    print(f"[run_all] Stopping {name}...")
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        print(f"[run_all] Force-killing {name}...")
        proc.kill()
        proc.wait(timeout=5)


def _ensure_frontend_dependencies(frontend_dir: Path) -> None:
    npm_exe = _resolve_npm_executable()
    if not npm_exe:
        raise RuntimeError("npm is not installed or not available in PATH.")
    if (frontend_dir / "node_modules").exists():
        return
    print("[run_all] Installing frontend dependencies (node_modules missing)...")
    result = subprocess.run([npm_exe, "install"], cwd=str(frontend_dir), check=False)
    if result.returncode != 0:
        raise RuntimeError("npm install failed. Fix frontend dependencies and retry.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run backend API and React frontend together (Gradio excluded)."
    )
    parser.add_argument("--api-host", default="127.0.0.1")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--api-startup-timeout", type=int, default=180)
    parser.add_argument("--open-browser", action="store_true", help="Open frontend URL automatically.")
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip npm dependency install check.",
    )
    args = parser.parse_args()

    if not FRONTEND_DIR.exists():
        raise RuntimeError(f"Frontend folder not found: {FRONTEND_DIR}")

    npm_exe = _resolve_npm_executable()
    if not npm_exe:
        raise RuntimeError("npm is not installed or not available in PATH.")

    if not args.skip_install:
        _ensure_frontend_dependencies(FRONTEND_DIR)

    api_url = f"http://{args.api_host}:{args.api_port}"
    health_url = f"{api_url}/health"
    docs_url = f"{api_url}/docs"
    frontend_url = f"http://{args.web_host}:{args.web_port}"

    print("[run_all] Starting services...")
    print(f"[run_all] Backend API:   {api_url}")
    print(f"[run_all] API Docs:      {docs_url}")
    print(f"[run_all] Frontend URL: {frontend_url}")
    print("[run_all] Open frontend URL in browser to use the app.")

    base_env = _prepend_pythonpath(os.environ.copy(), ROOT)

    api_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "src.api.app:app",
        "--host",
        args.api_host,
        "--port",
        str(args.api_port),
    ]
    web_env = dict(base_env)
    web_env["VITE_PROXY_TARGET"] = api_url
    if base_env.get("SERVICE_API_KEY"):
        web_env["VITE_SERVICE_API_KEY"] = base_env["SERVICE_API_KEY"]
    web_cmd = [
        npm_exe,
        "run",
        "dev",
        "--",
        "--host",
        args.web_host,
        "--port",
        str(args.web_port),
        "--strictPort",
    ]

    api_proc, api_thread = _spawn("api", api_cmd, ROOT, base_env)
    try:
        print(f"[run_all] Waiting for API health check: {health_url}")
        if not _wait_for_http(health_url, api_proc, args.api_startup_timeout):
            _terminate_process(api_proc, "api")
            return 1
        print("[run_all] API is ready. Starting frontend...")
        web_proc, web_thread = _spawn("web", web_cmd, FRONTEND_DIR, web_env)
    except Exception:
        _terminate_process(api_proc, "api")
        raise

    if args.open_browser:
        time.sleep(2)
        webbrowser.open(frontend_url)

    try:
        while True:
            api_code = api_proc.poll()
            web_code = web_proc.poll()

            if api_code is not None:
                print(f"[run_all] API process exited with code {api_code}.")
                _terminate_process(web_proc, "web")
                return int(api_code)

            if web_code is not None:
                print(f"[run_all] Frontend process exited with code {web_code}.")
                _terminate_process(api_proc, "api")
                return int(web_code)

            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\n[run_all] Ctrl+C received.")
        _terminate_process(web_proc, "web")
        _terminate_process(api_proc, "api")
        return 0
    finally:
        api_thread.join(timeout=1)
        web_thread.join(timeout=1)


if __name__ == "__main__":
    raise SystemExit(main())
