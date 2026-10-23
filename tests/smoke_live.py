"""Read-only smoke test for a running PereHomeLab instance."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENER = build_opener(ProxyHandler({}))


def fetch(
    base_url: str,
    path: str,
    *,
    expected: int = 200,
    auth: tuple[str, str] | None = None,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[bytes, dict[str, str]]:
    request_headers = dict(headers or {})
    if auth:
        raw = f"{auth[0]}:{auth[1]}".encode("utf-8")
        request_headers["Authorization"] = f"Basic {base64.b64encode(raw).decode('ascii')}"
    request = Request(f"{base_url.rstrip('/')}{path}", headers=request_headers, method=method)
    try:
        with OPENER.open(request, timeout=20) as response:
            status = response.status
            body = response.read()
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except HTTPError as exc:
        status = exc.code
        body = exc.read()
        response_headers = {key.lower(): value for key, value in exc.headers.items()}
    if status != expected:
        raise AssertionError(f"{method} {path}: expected HTTP {expected}, got {status}")
    print(f"OK  {status}  {method:<4} {path}")
    return body, response_headers


def fetch_json(base_url: str, path: str, **kwargs) -> dict:
    body, _ = fetch(base_url, path, **kwargs)
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise AssertionError(f"GET {path}: expected a JSON object")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / "secret.env", override=False, encoding="utf-8-sig")
    username = os.getenv("ADMIN_USERNAME", "")
    password = os.getenv("ADMIN_PASSWORD", "")
    if not username or not password:
        raise SystemExit("ADMIN_USERNAME and ADMIN_PASSWORD are required for the admin smoke checks")
    auth = (username, password)

    health = fetch_json(args.url, "/healthz")
    assert health.get("status") == "ok"
    home, home_headers = fetch(args.url, "/")
    assert "text/html" in home_headers.get("content-type", "") and b'id="regionGrid"' in home
    monitor, _ = fetch(args.url, "/monitor")
    assert b'id="alertAudio"' in monitor and b"/static/app.js?v=" in monitor
    fetch(args.url, "/static/assets/yadro-logo.png")
    fetch(args.url, "/static/assets/fonts/JetBrainsMono-Variable.ttf")
    fetch(args.url, "/spb")

    status = fetch_json(args.url, "/api/status")
    assert "system" in status and "watched_count" in status
    system = fetch_json(args.url, "/api/system-status")
    assert system.get("overall") in {"ok", "warning", "error"}
    checked_system = fetch_json(args.url, "/api/system-status/check", method="POST")
    assert "tuya_gateway_ok" in checked_system and "tuya_integration_loaded" in checked_system
    endpoints = checked_system.get("tuya_endpoints") or {}
    assert "apigw.tuyaeu.com" in endpoints and "apigw.iotbing.com" in endpoints
    areas = fetch_json(args.url, "/api/areas")
    assert isinstance(areas.get("items"), list)
    batteries = fetch_json(args.url, "/api/batteries?limit=5")
    assert isinstance(batteries.get("items"), list)
    history = fetch_json(args.url, "/api/history?page=1&page_size=10")
    assert isinstance(history.get("items"), list)
    config = fetch_json(args.url, "/api/client-config")
    assert config.get("sound_url") and float(config.get("sound_play_seconds", 0)) > 0

    _, sound_headers = fetch(args.url, config["sound_url"], expected=206, headers={"Range": "bytes=0-1023"})
    assert sound_headers.get("content-type", "").startswith("audio/")
    _, download_headers = fetch(args.url, "/download-app")
    assert download_headers.get("content-type") == "application/octet-stream"

    fetch(args.url, "/admin", expected=401)
    admin_page, _ = fetch(args.url, "/admin", auth=auth)
    assert b"admin.js" in admin_page
    for path in (
        "/api/admin/me",
        "/api/admin/areas",
        "/api/admin/sounds",
        "/api/admin/logs",
        "/api/admin/users",
        "/api/admin/login-logs",
    ):
        fetch_json(args.url, path, auth=auth)

    fetch(args.url, "/api/history?page=0", expected=422)
    fetch(args.url, "/api/history?page_size=9999", expected=422)
    fetch(args.url, "/api/batteries?limit=0", expected=422)
    fetch(args.url, "/api/does-not-exist", expected=404)
    print("\nLive smoke test passed.")


if __name__ == "__main__":
    main()
