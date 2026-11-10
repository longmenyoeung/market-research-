"""Production startup entrypoint for Render (and container hosts)."""

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"


def ensure_render_env() -> None:
    render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")

    if render_host and "MCA_ALLOWED_HOSTS" not in os.environ:
        hosts = ["127.0.0.1", "localhost", "[::1]", render_host]
        os.environ["MCA_ALLOWED_HOSTS"] = json.dumps(hosts)

    if render_url and "MCA_WEBSOCKET_ORIGINS" not in os.environ:
        origins = [
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            render_url,
        ]
        os.environ["MCA_WEBSOCKET_ORIGINS"] = json.dumps(origins)


def main() -> int:
    ensure_render_env()
    # Do not seed synthetic candles: the archive fills only from real public market data.

    port = os.environ.get("PORT", "10000")
    print(f"Starting Render web service on 0.0.0.0:{port}", flush=True)
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--app-dir",
            str(BACKEND_DIR),
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--proxy-headers",
            "--forwarded-allow-ips=*",
        ],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())

