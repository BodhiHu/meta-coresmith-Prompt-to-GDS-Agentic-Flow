import { fmtDuration, fmtNum, fmtTokens, fmtBytes, fmtPct, statusTone, budgetRatio, blockLabel, extOf } from '../format';

describe('format helpers', () => {
  test('fmtDuration scales units', () => {
    expect(fmtDuration(null)).toBe('--');
    expect(fmtDuration(0.0004)).toBe('< 1ms');
    expect(fmtDuration(0.25)).toBe('250ms');
    expect(fmtDuration(12.34)).toBe('12.3s');
    expect(fmtDuration(125)).toBe('2m 5s');
    expect(fmtDuration(3 * 3600 + 15 * 60)).toBe('3h 15m');
  });

  test('fmtNum / fmtTokens / fmtBytes / fmtPct', () => {
    expect(fmtNum(11495)).toBe('11,495');
    expect(fmtNum(14.2058, 3)).toBe('14.206');
    expect(fmtNum(undefined)).toBe('--');
    expect(fmtTokens(694372)).toBe('694.4k');
    expect(fmtTokens(296537732)).toBe('296.54M');
    expect(fmtBytes(628689)).toBe('614.0 KB');
    expect(fmtPct(94.16)).toBe('94.2%');
  });

  test('statusTone maps pipeline statuses to badge tones', () => {
    expect(statusTone('passed')).toBe('ok');
    expect(statusTone('done')).toBe('ok');
    expect(statusTone('failed')).toBe('fail');
    expect(statusTone('timeout')).toBe('fail');
    expect(statusTone('running')).toBe('running');
    expect(statusTone('incomplete')).toBe('warn');
    expect(statusTone('skipped')).toBe('warn');
    expect(statusTone('whatever')).toBe('muted');
  });

  test('budgetRatio and labels', () => {
    expect(budgetRatio(50, 200)).toBe(0.25);
    expect(budgetRatio(50, 0)).toBeNull();
    expect(budgetRatio(null, 5)).toBeNull();
    expect(blockLabel('cavlc_macroblock_encoder')).toBe('cavlc macroblock encoder');
    expect(extOf('foo/bar.V')).toBe('.v');
    expect(extOf('noext')).toBe('');
  });
});
