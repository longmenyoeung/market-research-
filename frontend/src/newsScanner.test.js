import { describe, it, expect } from 'vitest';
import { FOCUSED_COINS, formatCoinLabel } from './components/NewsScanner.jsx';

describe('Focused 3-coin universe and labels', () => {
  it('contains only Gold (XAUTUSDT), Bitcoin (BTCUSDT), and Ethereum (ETHUSDT)', () => {
    expect(FOCUSED_COINS.map(c => c.symbol)).toEqual(['XAUTUSDT', 'BTCUSDT', 'ETHUSDT']);
  });

  it('formats Khmer and English labels for the 3 coins', () => {
    expect(formatCoinLabel('XAUTUSDT')).toContain('មាស');
    expect(formatCoinLabel('BTCUSDT')).toContain('Bitcoin');
    expect(formatCoinLabel('ETHUSDT')).toContain('Ethereum');
    expect(formatCoinLabel('TESTUSDT')).toBe('TESTUSDT');
  });
});

