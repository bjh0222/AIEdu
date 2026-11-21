"""Windows launcher regressions; local HTTP fixtures only, no app/db/model imports."""
import contextlib
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows desktop launcher")
SOURCE = Path(__file__).resolve().parents[1] / "start-aiedu.ps1"


@contextlib.contextmanager
def server(*, fail_tasks=False):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.reply({"csrf_token": "fixture"}, cookie=True)

        def do_GET(self):
            if self.path == "/api/health":
                self.reply({"app": "AI Edu", "status": "ok"})
            elif self.path.startswith("/api/v2/") and "aiedu_owner=fixture" not in self.headers.get("Cookie", ""):
                self.reply({"code": "SESSION_REQUIRED"}, status=401)
            elif self.path == "/api/v2/capabilities":
                self.reply({"schema_version": "aiedu.task-run.v2", "paid_calls_enabled": True,
                            "role_configuration": {role: {"configured": True} for role in ("generation", "review", "blind_solver")}})
            elif self.path.startswith("/api/v2/tasks"):
                self.reply({"items": []}, status=503 if fail_tasks else 200)
            else:
                self.reply({"homepage": "ready"})

        def reply(self, body, *, status=200, cookie=False):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            if cookie:
                self.send_header("Set-Cookie", "aiedu_owner=fixture; Path=/api; HttpOnly")
            self.end_headers()
            self.wfile.write(data)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def workspace(tmp_path, backend_port, *, array_rewrites=False):
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(SOURCE, scripts / SOURCE.name)
    (scripts / "start-backend-v2.ps1").write_text("throw 'fixture must never start a backend'", encoding="utf-8-sig")
    web = repo / "apps" / "web"
    (web / ".next").mkdir(parents=True)
    (web / ".next" / "BUILD_ID").write_text("fixture")
    cli = web / "node_modules" / "next" / "dist" / "bin" / "next"
    cli.parent.mkdir(parents=True)
    cli.write_text("throw new Error('fixture must never start a web process')")
    rules = [{"source": "/api/:path*", "destination": f"http://127.0.0.1:{backend_port}/api/:path*"}]
    manifest = {"rewrites": rules if array_rewrites else {"beforeFiles": [], "afterFiles": rules, "fallback": []}}
    (web / ".next" / "routes-manifest.json").write_text(json.dumps(manifest))
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path / "private"))
    private = Path(env["LOCALAPPDATA"]) / "AI Edu 2.0"
    private.mkdir(parents=True)
    profile = {"id": "fixture", "api_key": "fake-key-for-local-http-test", "base_url": "http://127.0.0.1", "model": "fixture"}
    (private / "model-settings.json").write_text(json.dumps({"profiles": [profile], "model_roles": {"generation": "fixture"}}))
    (private / "model-roles.private.json").write_text(json.dumps({"profiles": [], "roles": {"review": "fixture", "blind_solver": "fixture"}}))
    return scripts / SOURCE.name, env


def launch(script, env, *args):
    return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                           *map(str, args)], env=env, capture_output=True, timeout=35)


@pytest.mark.parametrize("array_rewrites", [False, True])
def test_preflight_uses_frozen_build_port(tmp_path, array_rewrites):
    script, env = workspace(tmp_path, 18723, array_rewrites=array_rewrites)
    result = launch(script, env, "-CheckOnly")
    assert result.returncode == 0, result.stderr
    assert b"http://127.0.0.1:18723" in result.stdout
    assert not list(Path(env["LOCALAPPDATA"]).rglob("*.pid"))


def test_explicit_port_mismatch_fails_before_starting(tmp_path):
    script, env = workspace(tmp_path, 18723)
    result = launch(script, env, "-BackendPort", 18724, "-CheckOnly")
    assert result.returncode != 0
    log = next(Path(env["LOCALAPPDATA"]).rglob("launcher.log")).read_text(encoding="utf-8-sig")
    assert "conflicts with web build target" in log
    assert not list(Path(env["LOCALAPPDATA"]).rglob("*.pid"))


def test_paid_launch_refuses_missing_private_roles(tmp_path):
    script, env = workspace(tmp_path, 18723)
    (Path(env["LOCALAPPDATA"]) / "AI Edu 2.0" / "model-roles.private.json").unlink()
    result = launch(script, env, "-NoBrowser", "-TimeoutSec", 2)
    assert result.returncode != 0
    assert not list(Path(env["LOCALAPPDATA"]).rglob("*.pid"))


def test_repeated_launch_reuses_healthy_services_without_claiming_pids(tmp_path):
    with server() as backend, server() as web:
        script, env = workspace(tmp_path, backend)
        for _ in range(2):
            result = launch(script, env, "-NoBrowser", "-WebPort", web, "-TimeoutSec", 3)
            assert result.returncode == 0, result.stderr
        log = next(Path(env["LOCALAPPDATA"]).rglob("launcher.log")).read_text(encoding="utf-8-sig")
        assert log.count("=== launch done") == 2
        assert "Starting web" not in log
        assert "Starting backend" not in log
        assert not list(Path(env["LOCALAPPDATA"]).rglob("*.pid"))


def test_homepage_200_does_not_hide_broken_api(tmp_path):
    with server() as backend, server(fail_tasks=True) as web:
        script, env = workspace(tmp_path, backend)
        result = launch(script, env, "-NoBrowser", "-WebPort", web, "-TimeoutSec", 2)
        assert result.returncode != 0
        log = next(Path(env["LOCALAPPDATA"]).rglob("launcher.log")).read_text(encoding="utf-8-sig")
        assert "Task API not ready" in log
        assert "=== launch done" not in log


def test_saved_profile_wins_over_duplicate_private_profile(tmp_path):
    with server() as backend, server() as web:
        script, env = workspace(tmp_path, backend)
        private = Path(env["LOCALAPPDATA"]) / "AI Edu 2.0" / "model-roles.private.json"
        defaults = json.loads(private.read_text())
        defaults["profiles"] = [{"id": "fixture", "base_url": "", "model": "", "api_key": ""}]
        private.write_text(json.dumps(defaults))
        result = launch(script, env, "-NoBrowser", "-WebPort", web, "-TimeoutSec", 3)
        assert result.returncode == 0, result.stderr
        log = next(Path(env["LOCALAPPDATA"]).rglob("launcher.log")).read_text(encoding="utf-8-sig")
        assert "=== launch done" in log
