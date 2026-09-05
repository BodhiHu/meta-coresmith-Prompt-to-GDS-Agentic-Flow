import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { api, getJson } from '../../utils/api';
import { highlightFor } from '../../utils/highlight';
import { fmtBytes, fmtNum, extOf } from '../../utils/format';
import { CopyButton, ErrorBanner, Spinner } from './common';

const PAGE = 400;

/**
 * Paged, syntax-highlighted file viewer backed by /api/text.
 *
 * Large files (multi-MB synthesis logs) are never fetched whole: the viewer
 * loads a window of lines and lets the user page forward/back, jump to the
 * head or tail, and search server-side (matches are listed; clicking one
 * loads the window around it).
 */
export default function CodeView({ relPath, lang, tail = false, pageSize = PAGE, title, maxHeight, highlightLine, onLoaded }) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [window_, setWindow] = useState({ offset: 0, tail });
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState(null);
  const [hitLine, setHitLine] = useState(highlightLine ?? null);
  const bodyRef = useRef(null);
  const ext = extOf(relPath || '');
  const language = lang || ext;

  useEffect(() => {
    setWindow({ offset: 0, tail });
    setQuery('');
    setHits(null);
    setHitLine(highlightLine ?? null);
  }, [relPath, tail, highlightLine]);

  useEffect(() => {
    if (!relPath) return undefined;
    let cancelled = false;
    setState((s) => ({ ...s, loading: true, error: null }));
    getJson(api.text(relPath, { offset: window_.offset, limit: pageSize, tail: window_.tail }))
      .then((data) => {
        if (cancelled) return;
        setState({ data, error: null, loading: false });
        if (onLoaded) onLoaded(data);
      })
      .catch((err) => { if (!cancelled) setState({ data: null, error: err.message, loading: false }); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [relPath, window_.offset, window_.tail, pageSize]);

  const data = state.data;
  const html = useMemo(() => {
    if (!data) return '';
    return highlightFor(language, data.lines.join('\n'));
  }, [data, language]);

  const htmlLines = useMemo(() => (html ? html.split('\n') : []), [html]);

  useEffect(() => {
    if (hitLine == null || !bodyRef.current || !data) return;
    const el = bodyRef.current.querySelector(`[data-line="${hitLine}"]`);
    if (el) el.scrollIntoView({ block: 'center' });
  }, [hitLine, data]);

  const runSearch = useCallback(() => {
    if (!query || !relPath) { setHits(null); return; }
    getJson(api.textSearch(relPath, query))
      .then((res) => setHits(res))
      .catch((err) => setHits({ error: err.message, hits: [] }));
  }, [query, relPath]);

  const gotoLine = (line) => {
    const offset = Math.max(0, line - Math.floor(pageSize / 2));
    setWindow({ offset, tail: false });
    setHitLine(line);
  };

  if (!relPath) return null;
  const total = data?.total_lines ?? 0;
  const offset = data?.offset ?? 0;
  const end = offset + (data?.count ?? 0);
  const canPrev = offset > 0;
  const canNext = data ? !data.eof : false;

  return (
    <div className="rv-code" style={maxHeight ? { maxHeight } : undefined}>
      <div className="rv-code-bar">
        <span className="rv-code-title" title={relPath}>{title || relPath}</span>
        {data && (
          <span className="rv-code-meta">
            {fmtBytes(data.size)} · {fmtNum(total)} lines · showing {fmtNum(offset + 1)}–{fmtNum(end)}
          </span>
        )}
        <span className="rv-code-actions">
          <input
            className="rv-input"
            placeholder="Search file…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') runSearch(); if (e.key === 'Escape') { setQuery(''); setHits(null); } }}
          />
          <button type="button" className="rv-btn rv-btn-small" onClick={runSearch} disabled={!query}>Find</button>
          <button type="button" className="rv-btn rv-btn-small" disabled={!canPrev} onClick={() => setWindow({ offset: Math.max(0, offset - pageSize), tail: false })} title="Previous page">{'◀'}</button>
          <button type="button" className="rv-btn rv-btn-small" disabled={!canNext} onClick={() => setWindow({ offset: end, tail: false })} title="Next page">{'▶'}</button>
          <button type="button" className="rv-btn rv-btn-small" onClick={() => setWindow({ offset: 0, tail: false })} title="Jump to start">Head</button>
          <button type="button" className="rv-btn rv-btn-small" onClick={() => setWindow({ offset: 0, tail: true })} title="Jump to end">Tail</button>
          <CopyButton getText={() => (data ? data.lines.join('\n') : '')} label="Copy page" />
          <a className="rv-btn rv-btn-small" href={api.artifact(relPath)} target="_blank" rel="noreferrer">Raw</a>
        </span>
      </div>
      {hits && (
        <div className="rv-code-hits">
          {hits.error && <ErrorBanner error={hits.error} />}
          {!hits.error && (
            <>
              <span className="rv-code-hits-count">{hits.hits.length}{hits.truncated ? '+' : ''} match{hits.hits.length === 1 ? '' : 'es'} for “{hits.query}”</span>
              <div className="rv-code-hits-list">
                {hits.hits.map((h) => (
                  <button key={h.line} type="button" className={`rv-code-hit ${hitLine === h.line ? 'rv-code-hit-active' : ''}`} onClick={() => gotoLine(h.line)}>
                    <span className="rv-code-hit-line">{h.line + 1}</span>
                    <span className="rv-code-hit-text">{h.text}</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      )}
      <ErrorBanner error={state.error} />
      {state.loading && !data && <Spinner />}
      {data && (
        <div className={`rv-code-body ${state.loading ? 'rv-dim' : ''}`} ref={bodyRef}>
          <table className="rv-code-table">
            <tbody>
              {htmlLines.map((ln, i) => {
                const lineNo = offset + i;
                return (
                  <tr key={lineNo} data-line={lineNo} className={hitLine === lineNo ? 'rv-code-line-hit' : ''}>
                    <td className="rv-code-gutter">{lineNo + 1}</td>
                    <td className="rv-code-line"><code dangerouslySetInnerHTML={{ __html: ln || ' ' }} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {canNext && (
            <button type="button" className="rv-btn rv-btn-small rv-code-more" onClick={() => setWindow({ offset: end, tail: false })}>
              Load next {fmtNum(Math.min(pageSize, total - end))} lines
            </button>
          )}
        </div>
      )}
    </div>
  );
}
