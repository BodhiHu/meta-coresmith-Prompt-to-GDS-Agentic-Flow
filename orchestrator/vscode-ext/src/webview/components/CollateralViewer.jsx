import React, { useState, useEffect, useRef, useMemo } from 'react';
import { highlightVerilog, highlightJson, highlightTcl, highlightMarkdown, highlightHtml, highlightPython } from '../utils/highlight';
// Note: Python testbenches get the python highlighter as well.

const KIND_META = {
  rtl: { label: 'RTL (Verilog)', icon: '⚡' },
  tb: { label: 'Testbench', icon: '⌭' },
  syn: { label: 'Synthesis', icon: '⚙' },
  pnr: { label: 'PnR', icon: '◫' },
  waveforms: { label: 'Waveforms', icon: '〰' },
  reports: { label: 'Reports / Docs', icon: '📋' },
};

const TEXT_EXTS = new Set([
  '.v', '.sv', '.py', '.sdc', '.ys', '.tcl', '.txt', '.json', '.md',
  '.rpt', '.log', '.def', '.html', '.htm', '.csv', '.map',
]);

function _extOf(name) {
  const i = name.lastIndexOf('.');
  return i < 0 ? '' : name.slice(i).toLowerCase();
}

function _formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// Embed the self-hosted Surfer (Rust→WASM) waveform viewer for .vcd / .fst
// files. The bundle lives under /waveform-demos/surfer-local/ and Surfer's
// integration.js listens for {command: "LoadUrl", url} via postMessage.
function SurferWaveformViewer({ relPath }) {
  const iframeRef = useRef(null);
  const [status, setStatus] = useState('loading');
  const vcdUrl = `${location.origin}/api/artifacts/${relPath}`;
  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;
    const handleLoad = () => {
      // Give Surfer's WASM a beat to register its message listener.
      setTimeout(() => {
        try {
          iframe.contentWindow.postMessage({ command: 'LoadUrl', url: vcdUrl }, '*');
          setStatus('loaded');
        } catch (e) {
          setStatus('error: ' + e.message);
        }
      }, 1500);
    };
    iframe.addEventListener('load', handleLoad);
    return () => iframe.removeEventListener('load', handleLoad);
  }, [vcdUrl]);
  return (
    <div className="collateral-waveform">
      <div className="collateral-waveform-bar">
        <span className="collateral-waveform-engine">Surfer</span>
        <span className="collateral-waveform-status">{status}</span>
      </div>
      <iframe
        ref={iframeRef}
        className="collateral-waveform-frame"
        src="/waveform-demos/surfer-local/index.html"
        title="Surfer waveform viewer"
      />
    </div>
  );
}

function FileViewer({ file }) {
  const [content, setContent] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!file) return;
    const ext = _extOf(file.name);
    // HTML / images / waveforms are rendered via iframe or <img>, not
    // through the fetched text content. Skip the text fetch entirely.
    if (ext === '.html' || ext === '.htm' || ext === '.vcd' || ext === '.fst'
        || ext === '.ghw' || ext === '.png' || ext === '.jpg'
        || ext === '.jpeg' || ext === '.gif' || ext === '.svg'
        || ext === '.webp') {
      setContent(null);
      setError(null);
      setLoading(false);
      return;
    }
    if (!TEXT_EXTS.has(ext)) {
      setContent(null);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    fetch(`/api/artifacts/${file.rel_path}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((txt) => {
        const MAX = 256 * 1024;
        if (txt.length > MAX) {
          setContent(txt.slice(0, MAX) + '\n\n... [truncated]');
        } else {
          setContent(txt);
        }
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message || String(e));
        setLoading(false);
      });
  }, [file?.rel_path]);

  if (!file) {
    return (
      <div className="collateral-empty">
        <div className="collateral-empty-icon">📂</div>
        <div>Select a file on the left to view its content.</div>
      </div>
    );
  }

  const ext = _extOf(file.name);
  const isText = TEXT_EXTS.has(ext);
  const isWaveform = ext === '.vcd' || ext === '.fst' || ext === '.ghw';
  const isHtmlPreview = ext === '.html' || ext === '.htm';
  const isImage = ext === '.png' || ext === '.jpg' || ext === '.jpeg'
                 || ext === '.gif' || ext === '.svg' || ext === '.webp';

  let highlighted = null;
  if (content && !isHtmlPreview) {
    if (ext === '.v' || ext === '.sv') highlighted = highlightVerilog(content);
    else if (ext === '.json' || ext === '.map') highlighted = highlightJson(content);
    else if (ext === '.sdc' || ext === '.tcl' || ext === '.ys') highlighted = highlightTcl(content);
    else if (ext === '.md') highlighted = highlightMarkdown(content);
    else if (ext === '.py') highlighted = highlightPython(content);
    else if (ext === '.html' || ext === '.htm') highlighted = highlightHtml(content);
    else highlighted = content
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  return (
    <div className="collateral-content">
      <div className="collateral-content-header">
        <div className="collateral-file-name">{file.name}</div>
        <div className="collateral-file-meta">
          <span>{file.rel_path}</span>
          <span>·</span>
          <span>{_formatBytes(file.size)}</span>
          <a
            className="collateral-download"
            href={`/api/artifacts/${file.rel_path}`}
            download={file.name}
          >Open raw</a>
        </div>
      </div>
      <div className="collateral-content-body">
        {isWaveform && (
          <SurferWaveformViewer relPath={file.rel_path} key={file.rel_path} />
        )}
        {isHtmlPreview && (
          <iframe
            key={file.rel_path}
            className="collateral-html-frame"
            src={`/api/artifacts/${file.rel_path}`}
            title={file.name}
            sandbox="allow-same-origin allow-popups allow-scripts"
          />
        )}
        {isImage && (
          <div className="collateral-image">
            <img src={`/api/artifacts/${file.rel_path}`} alt={file.name} />
          </div>
        )}
        {!isWaveform && !isHtmlPreview && !isImage && !isText && (
          <div className="collateral-empty">
            <div className="collateral-empty-icon">📦</div>
            <div>Binary file ({ext || 'unknown'}) — open via the link above.</div>
          </div>
        )}
        {!isWaveform && !isHtmlPreview && !isImage && loading && (
          <div className="collateral-loading">Loading…</div>
        )}
        {!isWaveform && !isHtmlPreview && !isImage && error && (
          <div className="collateral-error">Failed to load: {error}</div>
        )}
        {!isWaveform && !isHtmlPreview && !isImage && isText && content != null && !error && (
          <pre className="collateral-code">
            <code dangerouslySetInnerHTML={{ __html: highlighted }} />
          </pre>
        )}
      </div>
    </div>
  );
}

/** Build a nested folder tree from a flat list of {rel_path, ...} files. */
function _buildTree(files) {
  const root = { name: '', children: new Map(), files: [], isDir: true };
  for (const f of files) {
    const parts = f.rel_path.split('/').filter(Boolean);
    let node = root;
    for (let i = 0; i < parts.length - 1; i++) {
      const part = parts[i];
      if (!node.children.has(part)) {
        node.children.set(part, {
          name: part,
          path: parts.slice(0, i + 1).join('/'),
          children: new Map(),
          files: [],
          isDir: true,
        });
      }
      node = node.children.get(part);
    }
    node.files.push({ ...f, displayName: parts[parts.length - 1] });
  }
  // Sort: directories first (alphabetical), then files (alphabetical).
  const sortNode = (n) => {
    n.children = new Map([...n.children.entries()].sort((a, b) => a[0].localeCompare(b[0])));
    n.files.sort((a, b) => a.displayName.localeCompare(b.displayName));
    for (const child of n.children.values()) sortNode(child);
  };
  sortNode(root);
  return root;
}

function TreeNode({ node, depth, selected, onSelect, defaultOpen, filterTokens }) {
  const [open, setOpen] = useState(defaultOpen);
  if (!node) return null;
  const indent = { paddingLeft: 6 + depth * 12 };
  const matchesFilter = (text) => {
    if (!filterTokens || filterTokens.length === 0) return true;
    const t = text.toLowerCase();
    return filterTokens.every((q) => t.includes(q));
  };
  // Recursive count + filter so a search shows the surviving subtree only.
  const visibleFiles = node.files.filter((f) => matchesFilter(f.rel_path));
  const visibleChildren = [...node.children.values()].filter((c) => _treeHasMatches(c, filterTokens));
  if (filterTokens && filterTokens.length > 0 && visibleFiles.length === 0 && visibleChildren.length === 0) {
    return null;
  }
  return (
    <div className="collateral-tree-node">
      {node.path != null && node.name && (
        <div
          className="collateral-tree-folder"
          style={indent}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="collateral-tree-chev">{open ? '▾' : '▸'}</span>
          <span className="collateral-tree-folder-icon">{'📁'}</span>
          <span className="collateral-tree-folder-name">{node.name}</span>
          <span className="collateral-tree-folder-count">
            {visibleFiles.length + visibleChildren.reduce((s, c) => s + _treeFileCount(c, filterTokens), 0)}
          </span>
        </div>
      )}
      {(open || node.path == null) && (
        <>
          {visibleChildren.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={node.path != null ? depth + 1 : depth}
              selected={selected}
              onSelect={onSelect}
              defaultOpen={depth < 1}
              filterTokens={filterTokens}
            />
          ))}
          {visibleFiles.map((f) => (
            <div
              key={f.rel_path}
              className={`collateral-tree-file ${selected?.rel_path === f.rel_path ? 'collateral-tree-file-selected' : ''}`}
              style={{ paddingLeft: indent.paddingLeft + 14 }}
              onClick={() => onSelect(f)}
              title={f.rel_path}
            >
              <span className="collateral-tree-file-icon">{_fileIcon(f.displayName)}</span>
              <span className="collateral-tree-file-name">{f.displayName}</span>
              <span className="collateral-tree-file-size">{_formatBytes(f.size)}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function _treeHasMatches(node, filterTokens) {
  if (!filterTokens || filterTokens.length === 0) return true;
  for (const f of node.files) {
    const t = f.rel_path.toLowerCase();
    if (filterTokens.every((q) => t.includes(q))) return true;
  }
  for (const child of node.children.values()) {
    if (_treeHasMatches(child, filterTokens)) return true;
  }
  return false;
}

function _treeFileCount(node, filterTokens) {
  let n = node.files.filter((f) => {
    if (!filterTokens || filterTokens.length === 0) return true;
    const t = f.rel_path.toLowerCase();
    return filterTokens.every((q) => t.includes(q));
  }).length;
  for (const child of node.children.values()) {
    n += _treeFileCount(child, filterTokens);
  }
  return n;
}

function _fileIcon(name) {
  const ext = _extOf(name);
  if (ext === '.v' || ext === '.sv') return '⚡';
  if (ext === '.vcd' || ext === '.fst' || ext === '.ghw') return '〰';
  if (ext === '.html' || ext === '.htm') return '🌐';
  if (ext === '.json') return '{ }';
  if (ext === '.md') return '📄';
  if (ext === '.rpt' || ext === '.log' || ext === '.txt') return '📋';
  if (ext === '.sdc' || ext === '.tcl' || ext === '.ys') return '⚙';
  if (ext === '.png' || ext === '.jpg' || ext === '.jpeg' || ext === '.svg' || ext === '.gif') return '🖼';
  return '·';
}

export default function CollateralViewer() {
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState('');
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch('/api/collateral')
      .then((r) => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  // Flatten the kind-bucketed response into a single list keyed by rel_path
  // so we can build a real folder tree. Tag each file with its kind so we
  // can colour the icon by category later if we want.
  const { tree, totalFiles } = useMemo(() => {
    if (!data) return { tree: null, totalFiles: 0 };
    const all = [];
    const seen = new Set();
    for (const k of ['rtl', 'tb', 'syn', 'pnr', 'waveforms', 'reports']) {
      for (const f of (data[k] || [])) {
        if (seen.has(f.rel_path)) continue;
        seen.add(f.rel_path);
        all.push({ ...f, kind: k });
      }
    }
    return { tree: _buildTree(all), totalFiles: all.length };
  }, [data]);

  const filterTokens = useMemo(() => {
    return filter
      ? filter.toLowerCase().split(/\s+/).filter(Boolean)
      : [];
  }, [filter]);

  if (error) {
    return (
      <div className="collateral-shell">
        <div className="collateral-empty">Failed to load collateral: {error}</div>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="collateral-shell">
        <div className="collateral-empty">Loading collateral…</div>
      </div>
    );
  }

  return (
    <div className="collateral-shell">
      <div className="collateral-sidebar">
        <div className="collateral-toolbar">
          <input
            className="collateral-filter"
            placeholder={`Filter ${totalFiles} files…`}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <div className="collateral-tree">
          {tree && (
            <TreeNode
              node={tree}
              depth={0}
              selected={selected}
              onSelect={setSelected}
              defaultOpen={true}
              filterTokens={filterTokens}
            />
          )}
          {totalFiles === 0 && (
            <div className="collateral-empty-mini">No collateral yet.</div>
          )}
        </div>
      </div>
      <div className="collateral-viewer">
        <FileViewer file={selected} />
      </div>
    </div>
  );
}
