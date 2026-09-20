import React, { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { escHtml } from '../../utils/highlight';
import { fmtNum, statusTone } from '../../utils/format';

/* ── Badges / tiles ─────────────────────────────────────── */

export function Badge({ tone = 'muted', children, title, className = '' }) {
  return (
    <span className={`rv-badge rv-badge-${tone} ${className}`} title={title}>{children}</span>
  );
}

export function StatusBadge({ status }) {
  if (!status) return <Badge tone="muted">--</Badge>;
  return <Badge tone={statusTone(status)}>{String(status).replace(/_/g, ' ')}</Badge>;
}

/** Tri-state pass/fail badge: true / false / null (not run). */
export function PassFail({ value, yes = 'pass', no = 'fail', none = 'n/a' }) {
  if (value === true || value === 1) return <Badge tone="ok">{'✓'} {yes}</Badge>;
  if (value === false || value === 0) return <Badge tone="fail">{'✗'} {no}</Badge>;
  return <Badge tone="muted">{'—'} {none}</Badge>;
}

export function StatTile({ label, value, sub, tone, title, onClick }) {
  return (
    <div
      className={`rv-stat ${tone ? `rv-stat-${tone}` : ''} ${onClick ? 'rv-stat-clickable' : ''}`}
      title={title}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === 'Enter' || e.key === ' ') onClick(); } : undefined}
    >
      <div className="rv-stat-value">{value ?? '--'}</div>
      <div className="rv-stat-label">{label}</div>
      {sub && <div className="rv-stat-sub">{sub}</div>}
    </div>
  );
}

/** "measured vs budget" cell with over-budget colouring. */
export function BudgetCell({ measured, budget, unit = '', digits = 0, tolerance = 1.0 }) {
  if (measured == null) return <span className="rv-muted">--</span>;
  const m = Number(measured);
  const b = budget != null ? Number(budget) : null;
  const ratio = b ? m / b : null;
  const tone = ratio == null ? 'muted' : ratio > tolerance ? 'fail' : ratio > 0.9 * tolerance ? 'warn' : 'ok';
  return (
    <span className={`rv-budget rv-budget-${tone}`} title={b != null ? `${fmtNum(m, digits)} / ${fmtNum(b, digits)} ${unit}` : undefined}>
      <span className="rv-budget-measured">{fmtNum(m, digits)}{unit ? ` ${unit}` : ''}</span>
      {b != null && (
        <span className="rv-budget-of"> / {fmtNum(b, digits)} ({Math.round(ratio * 100)}%)</span>
      )}
    </span>
  );
}

/* ── Key/value table ────────────────────────────────────── */

export function KV({ rows, className = '' }) {
  const items = (rows || []).filter((r) => r && r.length >= 2 && r[1] !== undefined && r[1] !== null && r[1] !== '');
  if (!items.length) return null;
  return (
    <table className={`rv-kv ${className}`}>
      <tbody>
        {items.map(([k, v, opts], i) => (
          <tr key={`${k}-${i}`}>
            <th>{k}</th>
            <td className={opts?.mono ? 'rv-mono' : ''}>{renderValue(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function renderValue(v) {
  if (React.isValidElement(v)) return v;
  if (typeof v === 'boolean') return <PassFail value={v} yes="yes" no="no" />;
  if (Array.isArray(v)) {
    if (!v.length) return <span className="rv-muted">(none)</span>;
    if (v.every((x) => typeof x !== 'object')) return v.join(', ');
    return <pre className="rv-pre-inline">{JSON.stringify(v, null, 2)}</pre>;
  }
  if (v && typeof v === 'object') return <pre className="rv-pre-inline">{JSON.stringify(v, null, 2)}</pre>;
  if (typeof v === 'number') return fmtNum(v, Number.isInteger(v) ? 0 : 4);
  return String(v);
}

/** Generic table from column specs: [{key, label, render?, align?}] */
export function Table({ columns, rows, rowKey, onRowClick, selectedKey, empty = 'No rows.', className = '', dense }) {
  if (!rows || !rows.length) return <div className="rv-empty">{empty}</div>;
  return (
    <div className={`rv-table-wrap ${className}`}>
      <table className={`rv-table ${dense ? 'rv-table-dense' : ''}`}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.align ? `rv-align-${c.align}` : ''} title={c.title}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const k = rowKey ? rowKey(r, i) : i;
            return (
              <tr
                key={k}
                className={`${onRowClick ? 'rv-row-clickable' : ''} ${selectedKey != null && selectedKey === k ? 'rv-row-selected' : ''}`}
                onClick={onRowClick ? () => onRowClick(r) : undefined}
              >
                {columns.map((c) => (
                  <td key={c.key} className={c.align ? `rv-align-${c.align}` : ''}>
                    {c.render ? c.render(r) : renderValue(r[c.key])}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* ── Collapsible section ────────────────────────────────── */

export function Collapsible({ title, subtitle, right, defaultOpen = false, open: controlled, onToggle, children, className = '', id, level = 3 }) {
  const [openState, setOpen] = useState(defaultOpen);
  const open = controlled != null ? controlled : openState;
  const toggle = () => {
    if (onToggle) onToggle(!open);
    if (controlled == null) setOpen(!open);
  };
  // The header is a div (not a <button>) because the `right` slot may hold
  // interactive controls such as filter chips; nested buttons are invalid.
  return (
    <section className={`rv-collapsible ${open ? 'rv-open' : ''} ${className}`} id={id}>
      <div
        className="rv-collapsible-header"
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={toggle}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } }}
      >
        <span className="rv-chev">{open ? '▾' : '▸'}</span>
        <span className={`rv-collapsible-title rv-h${level}`}>{title}</span>
        {subtitle && <span className="rv-collapsible-sub">{subtitle}</span>}
        {right && <span className="rv-collapsible-right" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>{right}</span>}
      </div>
      {open && <div className="rv-collapsible-body">{children}</div>}
    </section>
  );
}

/** Non-collapsible titled section with an anchor id for the mini TOC. */
export function Section({ id, title, subtitle, right, children, className = '' }) {
  return (
    <section className={`rv-section ${className}`} id={id}>
      <div className="rv-section-header">
        <h3 className="rv-section-title">{title}</h3>
        {subtitle && <span className="rv-section-sub">{subtitle}</span>}
        {right && <span className="rv-section-right">{right}</span>}
      </div>
      <div className="rv-section-body">{children}</div>
    </section>
  );
}

/* ── Misc ───────────────────────────────────────────────── */

export function Empty({ children, icon }) {
  return (
    <div className="rv-empty">
      {icon && <span className="rv-empty-icon">{icon}</span>}
      <span>{children}</span>
    </div>
  );
}

export function ErrorBanner({ error, prefix = 'Error' }) {
  if (!error) return null;
  return <div className="rv-error-banner">{prefix}: {String(error)}</div>;
}

export function Spinner({ label = 'Loading…' }) {
  return (
    <div className="rv-loading">
      <span className="rv-spinner" />
      <span>{label}</span>
    </div>
  );
}

export function CopyButton({ text, getText, label = 'Copy', className = '' }) {
  const [state, setState] = useState('idle');
  const onClick = async (e) => {
    e.stopPropagation();
    const value = getText ? getText() : text;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(value ?? '');
      } else {
        const ta = document.createElement('textarea');
        ta.value = value ?? '';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setState('done');
    } catch {
      setState('fail');
    }
    setTimeout(() => setState('idle'), 1200);
  };
  return (
    <button type="button" className={`rv-btn rv-btn-small ${className}`} onClick={onClick} title="Copy to clipboard">
      {state === 'done' ? 'Copied' : state === 'fail' ? 'Copy failed' : label}
    </button>
  );
}

export function LinkButton({ onClick, children, className = '', title }) {
  return (
    <button type="button" className={`rv-link ${className}`} onClick={onClick} title={title}>{children}</button>
  );
}

/* ── Searchable long text (prompts, responses, error dumps) ─ */

function buildHighlightedHtml(text, query, highlighter) {
  const base = highlighter ? highlighter(text) : escHtml(text);
  if (!query) return { html: base, count: 0 };
  // Highlight matches inside text nodes only (never inside our own tags).
  const q = query.toLowerCase();
  let count = 0;
  const parts = base.split(/(<[^>]+>)/g);
  const out = parts.map((p) => {
    if (p.startsWith('<')) return p;
    const lower = p.toLowerCase();
    if (!lower.includes(q)) return p;
    let res = '';
    let i = 0;
    while (true) {
      const j = lower.indexOf(q, i);
      if (j < 0) { res += p.slice(i); break; }
      res += p.slice(i, j) + `<mark class="rv-mark" data-m="${count}">${p.slice(j, j + q.length)}</mark>`;
      count++;
      i = j + q.length;
    }
    return res;
  });
  return { html: out.join(''), count };
}

/**
 * Long text with search (with match navigation), copy, and progressive
 * expansion so 100 KB prompts don't lock up the page.
 */
export function LongText({ text, highlighter, initialChars = 8000, mono = true, className = '', searchable = true, wrap = true }) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState('');
  const [current, setCurrent] = useState(0);
  const ref = useRef(null);
  const full = text || '';
  const isLong = full.length > initialChars;
  const shown = expanded || !isLong ? full : full.slice(0, initialChars);
  // A search implies the whole text.
  const effective = query ? full : shown;
  const { html, count } = useMemo(() => buildHighlightedHtml(effective, query, highlighter), [effective, query, highlighter]);

  useEffect(() => {
    setCurrent(0);
  }, [query]);

  useEffect(() => {
    if (!query || !ref.current) return;
    const marks = ref.current.querySelectorAll('mark.rv-mark');
    marks.forEach((m) => m.classList.remove('rv-mark-current'));
    const m = marks[current];
    if (m) {
      m.classList.add('rv-mark-current');
      m.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [current, html, query]);

  if (!full) return <span className="rv-muted">(empty)</span>;
  return (
    <div className={`rv-longtext ${className}`}>
      <div className="rv-longtext-bar">
        {searchable && (
          <span className="rv-search">
            <input
              className="rv-input"
              placeholder="Search…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && count) setCurrent((c) => (e.shiftKey ? (c - 1 + count) % count : (c + 1) % count));
                if (e.key === 'Escape') setQuery('');
              }}
            />
            {query && (
              <span className="rv-search-nav">
                <span className="rv-search-count">{count ? `${current + 1}/${count}` : '0'}</span>
                <button type="button" className="rv-btn rv-btn-small" disabled={!count} onClick={() => setCurrent((c) => (c - 1 + count) % count)}>{'▲'}</button>
                <button type="button" className="rv-btn rv-btn-small" disabled={!count} onClick={() => setCurrent((c) => (c + 1) % count)}>{'▼'}</button>
              </span>
            )}
          </span>
        )}
        <span className="rv-longtext-meta">{fmtNum(full.length)} chars</span>
        <CopyButton text={full} />
      </div>
      <pre
        ref={ref}
        className={`rv-longtext-body ${mono ? 'rv-mono' : 'rv-prose'} ${wrap ? 'rv-wrap' : ''}`}
        dangerouslySetInnerHTML={{ __html: html }}
      />
      {isLong && !query && (
        <button type="button" className="rv-btn rv-btn-small rv-expand" onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'Show less' : `Show all (${fmtNum(full.length)} chars)`}
        </button>
      )}
    </div>
  );
}

/** Simple tab strip. */
export function Tabs({ tabs, active, onChange, className = '' }) {
  return (
    <div className={`rv-tabs ${className}`} role="tablist">
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={active === t.key}
          className={`rv-tab ${active === t.key ? 'rv-tab-active' : ''}`}
          onClick={() => onChange(t.key)}
          title={t.title}
        >
          {t.label}
          {t.count != null && <span className="rv-tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** Sticky mini table-of-contents for long review pages. */
export function MiniToc({ items, containerRef }) {
  const [active, setActive] = useState(items[0]?.id);
  const onJump = useCallback((id) => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
    setActive(id);
  }, []);
  useEffect(() => {
    const root = containerRef?.current;
    if (!root) return undefined;
    const onScroll = () => {
      let best = items[0]?.id;
      for (const it of items) {
        const el = document.getElementById(it.id);
        if (!el) continue;
        const top = el.getBoundingClientRect().top - root.getBoundingClientRect().top;
        if (top <= 80) best = it.id;
      }
      setActive(best);
    };
    root.addEventListener('scroll', onScroll, { passive: true });
    return () => root.removeEventListener('scroll', onScroll);
  }, [items, containerRef]);
  return (
    <nav className="rv-toc" aria-label="Sections">
      {items.map((it) => (
        <button
          key={it.id}
          type="button"
          className={`rv-toc-item ${active === it.id ? 'rv-toc-active' : ''}`}
          onClick={() => onJump(it.id)}
        >
          {it.label}
          {it.badge && <span className="rv-toc-badge">{it.badge}</span>}
        </button>
      ))}
    </nav>
  );
}

/** Right-side drawer overlay. */
export function Drawer({ title, onClose, children, width = 'min(900px, 70vw)' }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="rv-drawer-mask" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="rv-drawer" style={{ width }} role="dialog" aria-label={typeof title === 'string' ? title : 'Details'}>
        <div className="rv-drawer-header">
          <div className="rv-drawer-title">{title}</div>
          <button type="button" className="rv-btn rv-btn-small" onClick={onClose} title="Close (Esc)">{'✕'}</button>
        </div>
        <div className="rv-drawer-body">{children}</div>
      </div>
    </div>
  );
}
