/* Shared formatting helpers for the run-review views. */

export function fmtDuration(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return '--';
  const s = Number(seconds);
  if (s < 0.001) return '< 1ms';
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 60) return `${s.toFixed(1)}s`;
  if (s < 3600) {
    const m = Math.floor(s / 60);
    const r = Math.round(s % 60);
    return `${m}m ${r}s`;
  }
  const h = Math.floor(s / 3600);
  const m = Math.round((s % 3600) / 60);
  return `${h}h ${m}m`;
}

export function fmtNum(n, digits = 0) {
  if (n == null || n === '' || Number.isNaN(Number(n))) return '--';
  const v = Number(n);
  if (Number.isInteger(v) && digits === 0) return v.toLocaleString();
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function fmtTokens(n) {
  if (n == null || Number.isNaN(Number(n))) return '--';
  const v = Number(n);
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}G`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}k`;
  return String(v);
}

export function fmtBytes(n) {
  if (n == null) return '--';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function pad2(n) {
  return String(n).padStart(2, '0');
}

export function fmtTime(ts) {
  if (ts == null) return '--';
  const d = new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return '--';
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

export function fmtDateTime(ts) {
  if (ts == null) return '--';
  const d = new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime())) return '--';
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${fmtTime(ts)}`;
}

/** Offset from the pipeline start, e.g. "+1h 12m". */
export function fmtOffset(ts, startTs) {
  if (ts == null || startTs == null) return '--';
  return `+${fmtDuration(Number(ts) - Number(startTs))}`;
}

export function fmtPct(n, digits = 1) {
  if (n == null || Number.isNaN(Number(n))) return '--';
  return `${Number(n).toFixed(digits)}%`;
}

export function blockLabel(name) {
  return (name || '').replace(/_/g, ' ');
}

export function titleCase(s) {
  return (s || '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Compact "measured / budget" annotation with over-budget detection. */
export function budgetRatio(measured, budget) {
  if (measured == null || budget == null || Number(budget) === 0) return null;
  return Number(measured) / Number(budget);
}

export function statusTone(status) {
  switch ((status || '').toLowerCase()) {
    case 'passed': case 'done': case 'ok': case 'approve': case 'approved': case 'pass':
      return 'ok';
    case 'failed': case 'error': case 'fail': case 'timeout': case 'abort':
      return 'fail';
    case 'running': case 'streaming':
      return 'running';
    case 'waiting': case 'incomplete': case 'in_progress': case 'revise': case 'skipped': case 'not_run':
      return 'warn';
    default:
      return 'muted';
  }
}

export function extOf(name) {
  const i = (name || '').lastIndexOf('.');
  return i < 0 ? '' : name.slice(i).toLowerCase();
}
