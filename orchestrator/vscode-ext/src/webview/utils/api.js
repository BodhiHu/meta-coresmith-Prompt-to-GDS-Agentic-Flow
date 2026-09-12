import { useState, useEffect, useCallback, useRef } from 'react';

/** fetch() a JSON endpoint, surfacing the server's {error} message. */
export async function getJson(url, opts = {}) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j && j.error) msg = j.error;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  return res.json();
}

/**
 * Small data-fetching hook.  Re-fetches when `url` changes; `reload()` forces
 * a refresh.  Keeps the previous data while reloading so views don't flash.
 */
export function useJson(url, { enabled = true, pollMs = 0 } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: !!url && enabled });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const urlRef = useRef(url);

  useEffect(() => {
    const urlChanged = urlRef.current !== url;
    urlRef.current = url;
    if (!url || !enabled) {
      setState({ data: null, error: null, loading: false });
      return undefined;
    }
    let cancelled = false;
    const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
    // A different URL is a different resource: drop the stale payload so
    // consumers never render the previous block's data under a new header.
    // Same-URL reloads (polling) keep the data to avoid flashing.
    setState((s) => ({ data: urlChanged ? null : s.data, loading: true, error: null }));
    getJson(url, ctrl ? { signal: ctrl.signal } : {})
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((err) => {
        if (cancelled || (err && err.name === 'AbortError')) return;
        setState((s) => ({ data: s.data, error: err.message || String(err), loading: false }));
      });
    let timer = null;
    if (pollMs > 0) {
      timer = setInterval(() => setTick((t) => t + 1), pollMs);
    }
    return () => {
      cancelled = true;
      if (ctrl) ctrl.abort();
      if (timer) clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, enabled, tick]);

  return { ...state, reload };
}

export const api = {
  overview: () => '/api/run/overview',
  blocks: () => '/api/run/blocks',
  decisions: () => '/api/run/decisions',
  settings: () => '/api/run/settings',
  integration: () => '/api/run/integration',
  trajectory: (block) => `/api/block/${encodeURIComponent(block)}/trajectory`,
  review: (block) => `/api/block/${encodeURIComponent(block)}/review`,
  files: (block) => `/api/block/${encodeURIComponent(block)}/files`,
  call: (id) => `/api/llm_call/${id}`,
  turn: (id, idx) => `/api/llm_call/${id}/turn/${idx}`,
  text: (rel, { offset = 0, limit = 400, tail = false } = {}) =>
    `/api/text?path=${encodeURIComponent(rel)}&offset=${offset}&limit=${limit}${tail ? '&tail=1' : ''}`,
  textSearch: (rel, q) => `/api/text_search?path=${encodeURIComponent(rel)}&q=${encodeURIComponent(q)}`,
  diff: (a, b) => `/api/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
  artifact: (rel) => `/api/artifacts/${rel.split('/').map(encodeURIComponent).join('/')}`,
};
