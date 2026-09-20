import React, { useEffect, useMemo } from 'react';
import { api, useJson } from '../../utils/api';
import { fmtDuration, fmtNum, blockLabel, statusTone } from '../../utils/format';
import { Badge, StatusBadge, Tabs, ErrorBanner, Spinner, Empty } from './common';
import BlockTrajectory from './BlockTrajectory';
import BlockDesign from './BlockDesign';

function StatusDot({ status }) {
  return <span className={`rv-dot rv-dot-${statusTone(status)}`} title={status} />;
}

/** Tier-grouped block navigator (left rail). */
export function BlockNavigator({ blocks, selected, onSelect, loading }) {
  const groups = useMemo(() => {
    const g = new Map();
    for (const b of blocks || []) {
      const t = b.tier ?? '?';
      if (!g.has(t)) g.set(t, []);
      g.get(t).push(b);
    }
    return [...g.entries()].sort((a, b) => (Number(a[0]) || 99) - (Number(b[0]) || 99));
  }, [blocks]);
  return (
    <nav className="rv-nav" aria-label="Blocks">
      <div className="rv-nav-head">
        <span>Blocks</span>
        <span className="rv-muted">{blocks ? blocks.length : ''}{loading ? ' …' : ''}</span>
      </div>
      {groups.map(([tier, list]) => (
        <div key={tier} className="rv-nav-tier">
          <div className="rv-nav-tier-head">Tier {tier} <span className="rv-muted">· {list.length}</span></div>
          {list.map((b) => (
            <button
              key={b.name}
              type="button"
              className={`rv-nav-item ${selected === b.name ? 'rv-nav-item-active' : ''}`}
              onClick={() => onSelect(b.name)}
              title={b.description || b.name}
            >
              <StatusDot status={b.status} />
              <span className="rv-nav-name">{blockLabel(b.name)}</span>
              <span className="rv-nav-meta">
                {b.rounds > 1 ? `${b.rounds}r ` : ''}{b.rtl_attempts > 1 ? `${b.rtl_attempts}a ` : ''}
                {b.dv?.tests_total != null ? `${b.dv.tests_passed}/${b.dv.tests_total}` : ''}
              </span>
            </button>
          ))}
        </div>
      ))}
      {blocks && blocks.length === 0 && <Empty>No blocks recorded yet.</Empty>}
    </nav>
  );
}

/**
 * Blocks view: navigator + per-block Trajectory / Design & Results pages.
 * Everything shown is scoped to exactly one block.
 */
export default function BlocksView({ selectedBlock, onSelectBlock, subTab, onSubTab, onOpenFile, onOpenCall, initialCallId, isLive }) {
  const { data, error, loading } = useJson(api.blocks(), { pollMs: isLive ? 10000 : 0 });
  const blocks = data?.blocks || null;

  useEffect(() => {
    if (!selectedBlock && blocks && blocks.length) onSelectBlock(blocks[0].name);
  }, [blocks, selectedBlock, onSelectBlock]);

  const cur = blocks?.find((b) => b.name === selectedBlock) || null;

  return (
    <div className="rv-blocks">
      <BlockNavigator blocks={blocks} selected={selectedBlock} onSelect={onSelectBlock} loading={loading} />
      <div className="rv-blocks-main">
        {error && <ErrorBanner error={error} />}
        {!blocks && !error && <Spinner label="Loading blocks…" />}
        {blocks && !selectedBlock && <Empty>Select a block on the left.</Empty>}
        {selectedBlock && (
          <>
            <div className="rv-block-head">
              <div className="rv-block-title">
                <h2>{blockLabel(selectedBlock)}</h2>
                {cur && <Badge tone="muted">tier {cur.tier ?? '?'}</Badge>}
                {cur?.subsystem && <Badge tone="muted">{cur.subsystem}</Badge>}
                {cur && <StatusBadge status={cur.status} />}
                {cur?.open_node && <Badge tone="running">at {cur.open_node}</Badge>}
              </div>
              {cur?.description && <div className="rv-block-desc">{cur.description}</div>}
              {cur && (
                <div className="rv-block-stats">
                  <span><b>{cur.rounds}</b> round{cur.rounds === 1 ? '' : 's'}</span>
                  <span><b>{cur.rtl_attempts}</b> RTL attempt{cur.rtl_attempts === 1 ? '' : 's'}</span>
                  <span><b>{cur.llm_calls}</b> LLM calls · {fmtDuration(cur.llm_time_s)}</span>
                  {cur.dv?.tests_total != null && <span>DV <b>{cur.dv.tests_passed}/{cur.dv.tests_total}</b></span>}
                  {cur.coverage_pct != null && <span>cov <b>{cur.coverage_pct}%</b></span>}
                  {cur.cells != null && <span><b>{fmtNum(cur.cells)}</b> cells · <b>{fmtNum(cur.ff)}</b> FF · <b>{fmtNum(cur.area_um2)}</b> µm²</span>}
                  {cur.wns_ns != null && <span>WNS <b className={cur.wns_ns < 0 ? 'rv-fail-text' : ''}>{fmtNum(cur.wns_ns, 3)} ns</b></span>}
                </div>
              )}
              <Tabs
                tabs={[
                  { key: 'trajectory', label: 'Trajectory', title: 'What the LLM did, node by node' },
                  { key: 'design', label: 'Design & results', title: 'Spec, RTL, testbench, sim, synthesis, timing, issues' },
                ]}
                active={subTab}
                onChange={onSubTab}
              />
            </div>
            <div className="rv-blocks-content">
              {subTab === 'trajectory' ? (
                <BlockTrajectory block={selectedBlock} onOpenFile={onOpenFile} onOpenBlock={(b, tab) => { onSelectBlock(b); if (tab) onSubTab(tab); }} initialCallId={initialCallId} isLive={isLive} />
              ) : (
                <BlockDesign block={selectedBlock} onOpenFile={onOpenFile} onOpenCall={onOpenCall} isLive={isLive} />
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
