import React from 'react';
import { api, useJson } from '../../utils/api';
import { ErrorBanner, Spinner, Empty, CopyButton } from './common';

/** Unified diff between two files in the run directory (server-side difflib). */
export default function DiffView({ a, b, maxHeight }) {
  const { data, error, loading } = useJson(a && b ? api.diff(a, b) : null);
  if (!a || !b) return null;
  if (loading && !data) return <Spinner label="Computing diff…" />;
  if (error) return <ErrorBanner error={error} />;
  if (!data) return null;
  if (data.identical) return <Empty icon={'='}>Files are identical.</Empty>;
  return (
    <div className="rv-diff" style={maxHeight ? { maxHeight } : undefined}>
      <div className="rv-diff-bar">
        <span className="rv-diff-title"><code>{a}</code> {'→'} <code>{b}</code></span>
        <span className="rv-diff-stats">
          <span className="rv-diff-add">+{data.added}</span>
          <span className="rv-diff-del">−{data.removed}</span>
          {data.truncated && <span className="rv-muted">(truncated)</span>}
        </span>
        <CopyButton text={data.lines.join('\n')} label="Copy diff" />
      </div>
      <pre className="rv-diff-body">
        {data.lines.map((ln, i) => {
          let cls = 'rv-diff-ctx';
          if (ln.startsWith('+++') || ln.startsWith('---')) cls = 'rv-diff-file';
          else if (ln.startsWith('@@')) cls = 'rv-diff-hunk';
          else if (ln.startsWith('+')) cls = 'rv-diff-add';
          else if (ln.startsWith('-')) cls = 'rv-diff-del';
          return <div key={i} className={`rv-diff-line ${cls}`}>{ln || ' '}</div>;
        })}
      </pre>
    </div>
  );
}
