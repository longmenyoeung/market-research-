"""Build and serve the local dashboard and API without exchange credentials."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', action='store_true', help='Rebuild the React dashboard before starting.')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    frontend = ROOT / 'frontend'
    if args.build or not (frontend / 'dist' / 'index.html').is_file():
        npm = shutil.which('npm.cmd') or shutil.which('npm')
        if npm is None or not (frontend / 'node_modules').is_dir():
            print('Install Node.js, then run `npm ci` inside frontend before starting.', file=sys.stderr)
            return 1
        result = subprocess.run([npm, 'run', 'build'], cwd=frontend, check=False)
        if result.returncode:
            return result.returncode
    print(f'Local analysis dashboard: http://127.0.0.1:{args.port}', flush=True)
    print('Public market data only. Stop with Ctrl+C.', flush=True)
    try:
        return subprocess.call([sys.executable, '-m', 'uvicorn', 'app.main:app',
                                '--app-dir', str(ROOT / 'backend'), '--host', '127.0.0.1',
                                '--port', str(args.port)], cwd=ROOT)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
