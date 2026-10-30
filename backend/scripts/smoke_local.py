"""Exercise a real local Uvicorn process without contacting a market provider."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[2]


async def check_websocket(port: int, key: str) -> None:
    async with connect(f'ws://127.0.0.1:{port}/api/v1/ws/candles/TESTUSDT?timeframe=invalid', proxy=None,additional_headers={'X-API-Key':key}) as stream:
        message = json.loads(await asyncio.wait_for(stream.recv(), 5))
        assert message['code'] == 'invalid_subscription'


def main() -> None:
    if not (ROOT / 'frontend/dist/index.html').is_file():
        raise SystemExit('Build the frontend before this check: npm --prefix frontend run build')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    opener = build_opener(ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix='mca-local-smoke-') as temporary:
        env = os.environ.copy()
        key = secrets.token_urlsafe(32)
        env.update(MCA_DATABASE_PATH=str(Path(temporary) / 'smoke.db'), MCA_API_KEY=key,
                   MCA_TRADING_ENABLED='false', MCA_LOG_LEVEL='WARNING')
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app',
            '--app-dir', str(ROOT / 'backend'), '--host', '127.0.0.1', '--port', str(port)],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            base = f'http://127.0.0.1:{port}'
            deadline = time.monotonic()+20
            while True:
                try:
                    with opener.open(base+'/health', timeout=1) as response:
                        health = json.load(response)
                    break
                except (URLError, TimeoutError):
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError(f'Local server did not become ready; exit code={process.poll()}. Run Uvicorn in the foreground to inspect logs.')
                    time.sleep(.1)
            assert health['mode'] == 'analysis_only' and health['trading_enabled'] is False
            assert health['execution_endpoints'] == 'none' and health['exchange_connectivity'] == 'not_checked'
            with opener.open(base+'/', timeout=3) as response:
                assert response.status == 200 and '<div id="root">' in response.read().decode()
            with opener.open(base+'/openapi.json', timeout=3) as response:
                paths = json.load(response)['paths']
                assert '/api/v1/kill-switch' not in paths
                assert not any(word in path.lower() for path in paths for word in ('order', 'position', 'execution'))
            body = json.dumps(dict(side='LONG', equity=1000, risk_percent=.25, entry=100, stop=95, target=110)).encode()
            request = Request(base+'/api/v1/planner', data=body,
                              headers={'Content-Type': 'application/json', 'X-API-Key': key})
            with opener.open(request, timeout=3) as response:
                plan = json.load(response)
                assert plan['hypothetical_only'] and float(plan['risk_budget']) == 2.5
                assert float(plan['potential_loss']) <= 2.5
            with opener.open(Request(base+'/api/v1/history/candles?symbol=TESTUSDT&timeframe=1h',headers={'X-API-Key':key}), timeout=3) as response:
                archive = json.load(response)
                assert archive['live_data'] is False and archive['candles'] == []
            asyncio.run(check_websocket(port,key))
        finally:
            if os.name == 'nt' and process.poll() is None:
                # Windows venv python.exe is a launcher with a child interpreter.
                # Stop only this owned tree before deleting its temporary database.
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=10, check=False)
            elif process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print(json.dumps({'uvicorn': 'passed', 'built_dashboard': 'HTTP 200',
        'health': 'permanently analysis_only; provider not checked', 'planner': 'cost-aware risk budget passed',
        'archive': 'historical-only empty temporary database', 'websocket': 'invalid subscription rejected',
        'exchange_requests': 'none', 'server_cleanup': 'owned tree stopped; temporary data removed'}, indent=2))


if __name__ == '__main__':
    main()
