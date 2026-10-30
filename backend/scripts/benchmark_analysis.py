"""Synthetic CPU benchmark only; no provider requests and no profitability claim."""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.analysis.structure import analyze_structure
from app.indicators.engine import calculate
from app.schemas.platform import IndicatorSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iterations', type=int, default=25)
    args = parser.parse_args()
    if not 1 <= args.iterations <= 1000:
        parser.error('iterations must be between 1 and 1000')
    x = np.arange(400)
    close = 100+x*.01+8*np.sin(x/9)
    sample = pd.DataFrame({'time': 1_700_006_400_000+x*3600000, 'open': close-.1,
                           'high': close+1, 'low': close-1, 'close': close,
                           'volume': 100+x%20, 'quote_volume': (100+x%20)*close})
    timings = []
    for _ in range(args.iterations):
        start = time.perf_counter()
        calculated, catalog = calculate(sample, IndicatorSettings(), '1h')
        analyze_structure(calculated, '1h')
        timings.append((time.perf_counter()-start)*1000)
    print(json.dumps({'source': 'synthetic CPU benchmark; excludes network, storage and browser',
                      'iterations': args.iterations, 'bars_per_iteration': len(sample),
                      'indicator_series': len(catalog), 'median_ms': round(statistics.median(timings), 2),
                      'p95_ms': round(float(np.percentile(timings, 95)), 2),
                      'total_seconds': round(sum(timings)/1000, 2)}, indent=2))


if __name__ == '__main__':
    main()
