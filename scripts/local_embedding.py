"""Control this verified, loopback-only Windows Ollama installation.

Uses the project's existing Python runtime; never changes PowerShell execution
policy, system PATH, firewall rules, startup tasks, or AI Edu's database.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".local-models" / "ollama"
EXE = RUNTIME / "bin" / "ollama.exe"
BASE = "http://127.0.0.1:11434"
MODEL = "qwen3-embedding:4b"
VERSION = "0.40.15"
ARCHIVE_SHA256 = "9ca74a7ab66ec86870b1eecac7df160df08dc4e3a291ab5d2d59bcf9310221c6"


def verified_install() -> dict:
    manifest = json.loads((RUNTIME / "install.json").read_text(encoding="utf-8-sig"))
    if manifest["version"] != VERSION or manifest["archive_sha256"] != ARCHIVE_SHA256:
        raise RuntimeError("Unexpected local installation manifest")
    with EXE.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != manifest["executable_sha256"]:
        raise RuntimeError("Ollama executable hash mismatch; do not execute")
    return manifest


def listeners() -> list[dict]:
    # netstat is a Windows system binary, not a shell or PATH-resolved helper.
    command = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "netstat.exe"
    result = subprocess.run([str(command), "-ano"], capture_output=True, check=True,
                            timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
    rows = []
    for line in result.stdout.decode("ascii", errors="replace").splitlines():
        fields = line.split()
        if len(fields) != 5 or fields[0] != "TCP" or fields[3] != "LISTENING":
            continue
        address, _, port = fields[1].rpartition(":")
        if port == "11434":
            rows.append({"address": address.strip("[]"), "port": 11434, "pid": int(fields[4])})
    return rows


def process_path(pid: int) -> Path:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return Path(buffer.value).resolve()
    finally:
        kernel.CloseHandle(handle)


def assert_ownership(rows: list[dict]) -> None:
    recorded_pid = int((RUNTIME / "server.pid").read_text(encoding="ascii").strip())
    for row in rows:
        if row["address"] != "127.0.0.1" or row["pid"] != recorded_pid or process_path(row["pid"]) != EXE.resolve():
            raise RuntimeError("Port 11434 is not this recorded loopback-only Ollama server; nothing was stopped")


def local_json(path: str, body: dict | None = None, timeout: float = 60) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(BASE + path,
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def assert_running_security() -> None:
    if local_json("/api/version")["version"] != VERSION:
        raise RuntimeError("Running Ollama version mismatch")
    if local_json("/api/status").get("cloud", {}).get("disabled") is not True:
        raise RuntimeError("Ollama cloud features are not disabled")


def warmup() -> None:
    # Preload outside AI Edu's per-request 60s deadline. No private text, DB,
    # generation model, automatic model download, or indefinite wait is involved.
    print('Warming local embedding model (up to 180s; synthetic text only)...', flush=True)
    started = time.monotonic()
    result = local_json('/api/embed', {
        'model': MODEL, 'input': 'Local embedding startup readiness check.', 'keep_alive': '2m',
    }, timeout=180)
    vectors = result.get('embeddings', [])
    if len(vectors) != 1 or len(vectors[0]) != 2560 or not any(vectors[0]) or \
       not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
               for value in vectors[0]):
        raise RuntimeError('Embedding warmup returned invalid vectors')
    print(f'Local embedding model ready; warmup={time.monotonic()-started:.3f}s', flush=True)


def start(rows: list[dict], model_ready: bool = False) -> None:
    if rows:
        assert_running_security()
        print(f"Already running: Ollama {VERSION}, {BASE}, cloud disabled")
        if model_ready:
            warmup()
        return
    if ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Run this local service from a normal non-administrator terminal")
    home = RUNTIME / "home"
    environment = dict(os.environ)
    environment.update({
        "OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_MODELS": str(RUNTIME / "models"),
        "OLLAMA_NO_CLOUD": "1", "OLLAMA_ORIGINS": "http://127.0.0.1:3001,http://localhost:3001",
        "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_MAX_QUEUE": "8",
        "OLLAMA_CONTEXT_LENGTH": "4096", "OLLAMA_KEEP_ALIVE": "2m", "OLLAMA_GPU_OVERHEAD": "536870912",
        "OLLAMA_IGPU_ENABLE": "false", "OLLAMA_VULKAN": "false", "OLLAMA_AUTH": "false",
        "OLLAMA_DEBUG": "0", "OLLAMA_DEBUG_LOG_REQUESTS": "false", "OLLAMA_NOHISTORY": "true",
        "NO_PROXY": "127.0.0.1,localhost,::1", "USERPROFILE": str(home), "HOME": str(home),
        "HOMEDRIVE": home.drive, "HOMEPATH": str(home)[len(home.drive):],
        "LOCALAPPDATA": str(home / "AppData" / "Local"), "APPDATA": str(home / "AppData" / "Roaming"),
        "TEMP": str(RUNTIME / "tmp"), "TMP": str(RUNTIME / "tmp"),
    })
    for relative in ("home/AppData/Local", "home/AppData/Roaming", "logs", "tmp"):
        (RUNTIME / relative).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    with (RUNTIME / "logs" / f"server-{stamp}.stdout.log").open("ab") as stdout, \
         (RUNTIME / "logs" / f"server-{stamp}.stderr.log").open("ab") as stderr:
        server = subprocess.Popen([str(EXE), "serve"], cwd=RUNTIME, env=environment,
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
    (RUNTIME / "server.pid").write_text(str(server.pid), encoding="ascii")
    for _ in range(30):
        time.sleep(0.5)
        if server.poll() is not None:
            raise RuntimeError("Ollama exited during startup; inspect the private runtime logs")
        ready = listeners()
        if ready:
            assert_ownership(ready)
            assert_running_security()
            print(f"Started: Ollama {VERSION}, {BASE}, PID {server.pid}; local only, cloud disabled")
            if model_ready:
                warmup()
            return
    raise RuntimeError("Ollama startup timeout; inspect the private runtime logs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status", "test", "warmup"), nargs="?", default="start")
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("This project-local Ollama installation is Windows-only")
    manifest = verified_install()
    rows = listeners()
    if rows:
        assert_ownership(rows)
    if args.action == "start":
        start(rows, model_ready=bool(manifest.get("model_verified")))
        return
    if args.action == "stop":
        for pid in {row["pid"] for row in rows}:
            # Revalidate immediately before stopping; never stop an unrelated executable.
            assert_ownership(listeners())
            command = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            subprocess.run([str(command), "/PID", str(pid), "/T", "/F"], check=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        print("Stopped this project-local Ollama server." if rows else "Local Ollama is not running.")
        return
    if not rows:
        raise RuntimeError("Local Ollama is not running; use start first")
    assert_running_security()
    if args.action == 'warmup':
        if not manifest.get('model_verified'):
            raise RuntimeError('The local model has not completed integrity verification')
        warmup()
        return
    if args.action == "test":
        subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "verify_local_embedding.py")], check=True)
        return
    print(json.dumps({"version": VERSION, "base_url": BASE + "/v1", "model": MODEL,
        "cloud_disabled": True, "listeners": rows, "models": local_json("/api/tags")["models"],
        "loaded": local_json("/api/ps")["models"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
