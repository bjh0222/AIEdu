"""经 Tailscale Serve 的显式放行：Host/Origin 白名单环境变量（默认仍仅本机）。"""
from __future__ import annotations

from app.config import settings

HOST = "laptop-phb7rj77.tailc7867a.ts.net"


def _headers() -> dict[str, str]:
    return {"host": HOST, "origin": f"https://{HOST}"}


def test_default_denies_tailnet_host(client):
    r = client.get("/api/v2/capabilities", headers=_headers())
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "ORIGIN_DENIED"


def test_explicit_allowlist_admits_tailnet_origin(client, monkeypatch):
    monkeypatch.setattr(settings, "v2_allowed_hosts",
                        frozenset({"127.0.0.1", "localhost", "testserver", HOST}))
    monkeypatch.setattr(settings, "v2_allowed_origin_hosts",
                        frozenset({"127.0.0.1", "localhost", "::1", HOST}))
    monkeypatch.setattr(settings, "cors_origins", f"{settings.cors_origins},https://{HOST}")
    # 通过 Host/Origin/CORS 三层校验后，未带会话应到达 SESSION_REQUIRED（401），而非 ORIGIN_DENIED。
    r = client.get("/api/v2/capabilities", headers=_headers())
    assert r.status_code == 401, r.text
    assert r.json()["code"] == "SESSION_REQUIRED"
    r2 = client.post("/api/v2/session", headers=_headers())
    assert r2.status_code == 200, r2.text
    assert "csrf_token" in r2.json()


def test_allowlist_without_cors_origin_still_denies(client, monkeypatch):
    monkeypatch.setattr(settings, "v2_allowed_hosts",
                        frozenset({"127.0.0.1", "localhost", "testserver", HOST}))
    monkeypatch.setattr(settings, "v2_allowed_origin_hosts",
                        frozenset({"127.0.0.1", "localhost", "::1", HOST}))
    r = client.get("/api/v2/capabilities", headers=_headers())
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "ORIGIN_DENIED"