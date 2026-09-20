import React, { useState, useMemo } from 'react';
import { api, useJson } from '../../utils/api';
import { fmtDuration, fmtTokens, fmtNum, fmtPct, fmtDateTime, fmtTime, fmtOffset, fmtBytes, blockLabel } from '../../utils/format';
import { Badge, StatusBadge, PassFail, StatTile, BudgetCell, KV, Table, Section, Collapsible, ErrorBanner, Spinner, Empty, LinkButton, LongText } from './common';

function WnsCell({ v }) {
  if (v == null) return <span className="rv-muted">--</span>;
  return <span className={v < 0 ? 'rv-fail-text' : 'rv-ok-text'}>{fmtNum(v, 3)}</span>;
}

function segmentSummary(s) {
  const ex = s.exit || {};
  const parts = [];
  for (const k of ['action', 'decision', 'success', 'passed', 'issues_found', 'new_tier_index', 'completed_so_far', 'passed_so_far', 'test_count', 'category', 'recommended_action', 'blocks_passed', 'blocks_total', 'top_fmax_mhz', 'chip_top_synthesizable', 'lint_clean']) {
    if (ex[k] === undefined || ex[k] === null || ex[k] === '') continue;
    parts.push(`${k.replace(/_/g, ' ')}: ${typeof ex[k] === 'boolean' ? (ex[k] ? 'yes' : 'no') : ex[k]}`);
  }
  return parts.join(' · ');
}

/**
 * Run-level dashboard: totals, block table, tiers, integration/validation,
 * chip-lead decisions, interrupts, engine settings.
 */
export default function RunOverview({ onOpenBlock, onOpenCall, onOpenFile }) {
  const ov = useJson(api.overview(), { pollMs: 10000 });
  const dec = useJson(api.decisions());
  const integ = useJson(api.integration());
  const settings = useJson(api.settings());
  const [decFilter, setDecFilter] = useState('');

  const o = ov.data;
  const decisions = useMemo(() => {
    const list = dec.data?.decisions || [];
    const q = decFilter.trim().toLowerCase();
    if (!q) return list;
    return list.filter((d) => [d.type, d.action, d.reasoning, ...(d.blocks || [])].filter(Boolean).join(' ').toLowerCase().includes(q));
  }, [dec.data, decFilter]);

  if (ov.error) return <div className="rv-page"><ErrorBanner error={ov.error} /></div>;
  if (!o) return <div className="rv-page"><Spinner label="Loading run overview…" /></div>;

  const t = o.totals;
  const u = t.usage || {};
  const passed = t.status_counts?.passed || 0;
  const signoff = o.signoff;
  const st = settings.data;
  const ig = integ.data;

  const blockCols = [
    { key: 'name', label: 'Block', render: (b) => <span className="rv-block-link">{blockLabel(b.name)}</span> },
    { key: 'tier', label: 'Tier', align: 'right' },
    { key: 'subsystem', label: 'Subsystem' },
    { key: 'status', label: 'Status', render: (b) => <StatusBadge status={b.status} /> },
    { key: 'rounds', label: 'Rounds', align: 'right', title: 'Init Block re-entries (integration-review revisions)' },
    { key: 'rtl_attempts', label: 'RTL att.', align: 'right', title: 'max Generate RTL attempt within a round' },
    { key: 'llm_calls', label: 'LLM calls', align: 'right', render: (b) => `${b.llm_calls} (${fmtDuration(b.llm_time_s)})` },
    { key: 'dv', label: 'DV', render: (b) => (b.dv?.tests_total != null ? <span className={b.dv.passed ? 'rv-ok-text' : 'rv-fail-text'}>{b.dv.tests_passed}/{b.dv.tests_total}</span> : <PassFail value={b.dv?.passed} none="--" />) },
    { key: 'coverage_pct', label: 'Cov', align: 'right', render: (b) => (b.coverage_pct != null ? <span className={b.coverage_passed === false ? 'rv-fail-text' : ''}>{fmtPct(b.coverage_pct)}</span> : '--') },
    { key: 'throughput', label: 'Thr', render: (b) => (b.throughput?.applicable ? <PassFail value={b.throughput.passed} yes={`${fmtNum(b.throughput.measured, 0)} cyc`} no={`${fmtNum(b.throughput.measured, 0)} cyc`} /> : <span className="rv-muted">n/a</span>) },
    { key: 'cells', label: 'Cells', align: 'right', render: (b) => <BudgetCell measured={b.cells} budget={b.budgets?.estimated_gates} /> },
    { key: 'ff', label: 'FF', align: 'right', render: (b) => <BudgetCell measured={b.ff} budget={b.budgets?.flip_flop_budget} /> },
    { key: 'area_um2', label: 'Area µm²', align: 'right', render: (b) => <BudgetCell measured={b.area_um2} budget={b.budgets?.area_budget_um2} /> },
    { key: 'wns_ns', label: 'WNS ns', align: 'right', render: (b) => <WnsCell v={b.wns_ns} /> },
    { key: 'ppa_ok', label: 'PPA', render: (b) => <PassFail value={b.ppa_ok == null ? null : !!b.ppa_ok} none="--" /> },
    { key: 'gate_sim', label: 'Gate sim', render: (b) => (b.gate_sim ? <Badge tone={b.gate_sim === 'passed' ? 'ok' : b.gate_sim === 'failed' ? 'fail' : 'muted'}>{b.gate_sim.replace(/_/g, ' ')}</Badge> : '--') },
    { key: 'conformance_ok', label: 'Contract', render: (b) => <PassFail value={b.conformance_ok} none="--" /> },
    { key: 'mem_price_ok', label: 'Mem', render: (b) => <PassFail value={b.mem_price_ok} none="--" /> },
    { key: 'open', label: '', render: (b) => <LinkButton onClick={(e) => { e.stopPropagation(); onOpenBlock(b.name, 'trajectory'); }}>trajectory</LinkButton> },
  ];

  return (
    <div className="rv-page">
      <div className="rv-run-head">
        <div className="rv-run-title">
          <h2>{o.design_name || 'Run'} <span className="rv-muted">review</span></h2>
          {o.is_live ? <Badge tone="running">LIVE</Badge> : <Badge tone="muted">historical</Badge>}
          {signoff?.status && <Badge tone={signoff.status === 'PASS' ? 'ok' : 'fail'}>signoff {signoff.status}</Badge>}
        </div>
        <div className="rv-run-meta">
          <span>engine <code>{o.engine_sha || '?'}</code></span>
          <span>{o.model || '?'} via {o.provider || '?'}</span>
          <span>started {fmtDateTime(o.pipeline_start)}</span>
          <span>last event {fmtDateTime(o.pipeline_end)}</span>
          <span>wall time <b>{fmtDuration(o.wall_time_s)}</b></span>
          <span className="rv-mono rv-muted" title="run directory">{o.project_root}</span>
        </div>
      </div>

      <div className="rv-tiles">
        <StatTile label="Blocks passed" value={`${passed}/${t.blocks}`} tone={passed === t.blocks && t.blocks ? 'ok' : 'warn'} sub={Object.entries(t.status_counts || {}).map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`).join(' · ')} />
        <StatTile label="LLM calls" value={fmtNum(t.llm_calls)} sub={`${fmtDuration(t.llm_time_s)} summed · ${t.timeouts} timeouts · ${t.errors} errors`} tone={t.timeouts || t.errors ? 'warn' : undefined} />
        <StatTile label="Input tokens" value={fmtTokens(u.input_tokens)} sub={`${fmtTokens(u.cached_input_tokens)} cached`} />
        <StatTile label="Output tokens" value={fmtTokens(u.output_tokens)} sub={`${fmtTokens(u.reasoning_output_tokens)} reasoning`} />
        <StatTile label="Agent commands" value={fmtNum(t.codex_commands)} sub={`${fmtNum(t.codex_file_changes)} file writes · ${t.codex_sessions} sessions`} />
        <StatTile label="Rounds" value={fmtNum(t.rounds)} sub="block loop re-entries" />
        <StatTile label="Chip-lead decisions" value={fmtNum(t.decisions)} sub={`${t.interrupts} interrupts`} />
        <StatTile label="Events" value={fmtNum(t.events)} sub="pipeline_events.jsonl" />
      </div>

      <Section id="ov-blocks" title="Blocks" subtitle="click a row for the design review; 'trajectory' for what the LLM did">
        <Table
          columns={blockCols}
          rows={o.blocks}
          rowKey={(b) => b.name}
          onRowClick={(b) => onOpenBlock(b.name, 'design')}
          empty="No blocks recorded."
        />
        <div className="rv-tier-strip">
          {o.tiers.map((tier) => (
            <div key={tier.tier} className="rv-tier">
              <div className="rv-tier-head">Tier {tier.tier}</div>
              <div className="rv-tier-blocks">
                {tier.blocks.map((b) => {
                  const row = o.blocks.find((x) => x.name === b);
                  return (
                    <button key={b} type="button" className={`rv-tier-block rv-tier-block-${row?.status || 'unknown'}`} onClick={() => onOpenBlock(b, 'design')} title={row?.status}>
                      {blockLabel(b)}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </Section>

      <Section id="ov-integration" title="Integration & validation" subtitle="tier-level nodes, chip_top integration, validation DV, final signoff">
        {integ.error && <ErrorBanner error={integ.error} />}
        {!ig && !integ.error && <Spinner />}
        {ig && (
          <>
            {signoff && (
              <div className="rv-tiles">
                <StatTile label="Signoff" value={signoff.status} tone={signoff.status === 'PASS' ? 'ok' : 'fail'} sub={signoff.status_reason || ''} />
                <StatTile label="Blocks" value={`${signoff.blocks_passed}/${signoff.blocks_total}`} />
                <StatTile label="Integration DV" value={signoff.integration_dv || '--'} tone={signoff.integration_dv === 'pass' ? 'ok' : signoff.integration_dv ? 'fail' : undefined} />
                <StatTile label="Validation DV" value={signoff.validation_dv || '--'} tone={signoff.validation_dv === 'pass' ? 'ok' : signoff.validation_dv ? 'fail' : undefined} />
                <StatTile label="Coverage" value={fmtPct(signoff.coverage_aggregate_pct)} sub={`min ${fmtPct(signoff.coverage_min_pct)} · floor ${signoff.coverage_floor}%`} />
                <StatTile label="Top WNS / Fmax" value={signoff.top_wns_ns != null ? `${fmtNum(signoff.top_wns_ns, 3)} ns` : 'n/a'} sub={signoff.top_fmax_mhz != null ? `${signoff.top_fmax_mhz} MHz` : 'not measured'} />
                <StatTile label="chip_top synthesizable" value={signoff.chip_top_synthesizable == null ? '--' : signoff.chip_top_synthesizable ? 'yes' : 'no'} tone={signoff.chip_top_synthesizable ? 'ok' : undefined} />
              </div>
            )}
            {ig.integration_result && (
              <KV rows={[
                ['Top module', ig.integration_result.top_module],
                ['Blocks integrated', ig.integration_result.block_count],
                ['Lint clean', ig.integration_result.lint_clean],
                ['Errors / warnings', `${ig.integration_result.error_count} / ${ig.integration_result.warning_count}`],
                ['Mismatches', ig.integration_result.mismatches?.length ? ig.integration_result.mismatches : 'none'],
                ['Top RTL', ig.integration_result.top_rtl_rel_path ? <LinkButton onClick={() => onOpenFile(ig.integration_result.top_rtl_rel_path, { lang: 'verilog' })}>{ig.integration_result.top_rtl_rel_path}</LinkButton> : null],
                ['Lint log', ig.integration_result.lint_log_rel_path ? <LinkButton onClick={() => onOpenFile(ig.integration_result.lint_log_rel_path, { lang: 'log' })}>open</LinkButton> : null],
              ]} />
            )}
            {ig.chip_throughput && (
              <KV rows={[
                ['Chip throughput gate', ig.chip_throughput.applicable ? <PassFail value={ig.chip_throughput.passed} /> : `not applicable — ${ig.chip_throughput.reason || ''}`],
                ['Measured cyc/op', ig.chip_throughput.measured_cyc_per_op_chip],
                ['Budget cyc/op', ig.chip_throughput.budget_cyc_per_op],
              ]} />
            )}
            <h4>Tier-level nodes</h4>
            <Table dense columns={[
              { key: 'enter_ts', label: 'Time', render: (s) => `${fmtTime(s.enter_ts)} (${fmtOffset(s.enter_ts, o.pipeline_start)})` },
              { key: 'node', label: 'Node' },
              { key: 'tier', label: 'Tier' },
              { key: 'status', label: 'Status', render: (s) => <StatusBadge status={s.status} /> },
              { key: 'duration_s', label: 'Duration', render: (s) => fmtDuration(s.duration_s) },
              { key: 'summary', label: 'Outcome', render: (s) => <span className="rv-clip" title={JSON.stringify(s.exit)}>{segmentSummary(s)}</span> },
              { key: 'calls', label: 'LLM', render: (s) => (s.calls || []).map((c) => <LinkButton key={c.call_id} onClick={() => onOpenCall(c.call_id)} title={c.run_name}>call #{c.call_id}</LinkButton>) },
            ]} rows={ig.segments} rowKey={(s) => s.seg_id} empty="No tier-level nodes recorded." />
            {ig.logs?.length > 0 && (
              <p className="rv-muted">Integration logs: {ig.logs.map((l) => (
                <LinkButton key={l.rel_path} onClick={() => onOpenFile(l.rel_path, { lang: 'log', tail: true })}>{l.name} ({fmtBytes(l.size)})</LinkButton>
              ))}</p>
            )}
            {ig.carried_forward_defects?.length > 0 && (
              <Collapsible title="Carried-forward defects" subtitle={`${ig.carried_forward_defects.length}`} defaultOpen={false}>
                <Table dense columns={[
                  { key: 'gate', label: 'Gate' }, { key: 'kind', label: 'Kind' },
                  { key: 'first_divergence_block', label: 'Block', render: (d) => (d.first_divergence_block ? <LinkButton onClick={() => onOpenBlock(d.first_divergence_block, 'design')}>{blockLabel(d.first_divergence_block)}</LinkButton> : '') },
                  { key: 'advisory', label: 'Advisory', render: (d) => (d.advisory ? 'yes' : 'no') },
                  { key: 'detail', label: 'Detail', render: (d) => <span className="rv-clip" title={d.detail}>{d.detail || d.unmodeled}</span> },
                ]} rows={ig.carried_forward_defects} rowKey={(d, i) => i} />
              </Collapsible>
            )}
            {ig.final_report?.md_rel_path && (
              <p><LinkButton onClick={() => onOpenFile(ig.final_report.md_rel_path, { lang: 'markdown' })}>open final_report.md</LinkButton></p>
            )}
          </>
        )}
      </Section>

      <Section id="ov-decisions" title="Chip-lead decisions" subtitle={`${dec.data?.decisions?.length ?? 0} decisions (in-graph chip lead)`} right={<input className="rv-input" placeholder="Filter decisions…" value={decFilter} onChange={(e) => setDecFilter(e.target.value)} />}>
        {dec.error && <ErrorBanner error={dec.error} />}
        {dec.data && decisions.length === 0 && <Empty>No chip-lead decisions recorded.</Empty>}
        {decisions.map((d) => (
          <div key={d.decision_index} className="rv-decision">
            <div className="rv-decision-head">
              <span className="rv-decision-idx">#{d.decision_index}</span>
              <Badge tone={/approve|override|fix|retry/.test(d.action || '') ? 'ok' : 'warn'}>{d.action}</Badge>
              <Badge tone="muted">{d.type}</Badge>
              <span className="rv-muted">{d.ts ? `${fmtDateTime(d.ts)} (${fmtOffset(d.ts, o.pipeline_start)})` : 'no timestamp'}</span>
              {d.blocks?.map((b) => <LinkButton key={b} onClick={() => onOpenBlock(b, 'design')}>{blockLabel(b)}</LinkButton>)}
              {d.call_id && <LinkButton onClick={() => onOpenCall(d.call_id)} title="Open the chip-lead LLM call">call #{d.call_id}{d.duration_s ? ` · ${fmtDuration(d.duration_s)}` : ''}</LinkButton>}
            </div>
            <LongText text={d.reasoning || ''} initialChars={500} mono={false} searchable={false} />
          </div>
        ))}
      </Section>

      <Section id="ov-interrupts" title="Interrupt history" subtitle={`${dec.data?.interrupts?.length ?? 0} human-in-the-loop / escalation points`}>
        <Table dense columns={[
          { key: 'ts', label: 'Time', render: (r) => `${fmtDateTime(r.ts)} (${fmtOffset(r.ts, o.pipeline_start)})` },
          { key: 'node', label: 'Node / event', render: (r) => r.node || r.event },
          { key: 'block', label: 'Block', render: (r) => (r.block ? <LinkButton onClick={() => onOpenBlock(r.block, 'trajectory')}>{blockLabel(r.block)}</LinkButton> : <span className="rv-muted">run</span>) },
          { key: 'round', label: 'Round', align: 'right' },
          { key: 'action', label: 'Resolution', render: (r) => r.action || r.detail?.reason || r.detail?.action || '' },
          { key: 'status', label: 'Status', render: (r) => <StatusBadge status={r.status} /> },
          { key: 'waited', label: 'Waited', render: (r) => (r.end_ts ? fmtDuration(r.end_ts - r.ts) : '') },
        ]} rows={dec.data?.interrupts || []} rowKey={(r, i) => i} empty="No interrupts recorded." />
      </Section>

      <Section id="ov-engine" title="Engine & settings">
        {settings.error && <ErrorBanner error={settings.error} />}
        {st && (
          <>
            <KV rows={[
              ['Engine SHA', st.engine_sha, { mono: true }],
              ['Engine SHA first seen', st.engine_sha_first_seen ? fmtDateTime(Number(st.engine_sha_first_seen)) : null],
              ['Engine changed mid-run', st.engine_sha_changed],
              ['Project state', st.has_sqlite ? 'project.sqlite (canonical) + JSON views' : 'JSON files only (pre-sqlite engine)'],
              ['Recorded under', st.original_roots?.join(', ') || null, { mono: true }],
              ['Daemon', st.daemon?.pid ? `pid ${st.daemon.pid} on port ${st.daemon.port}, started ${fmtDateTime(st.daemon.started_at)}` : null],
              ['Target clock', st.target_clock_mhz ? `${st.target_clock_mhz} MHz` : null],
            ]} />
            {Object.keys(st.settings || {}).length > 0 && (
              <Collapsible title="settings table" defaultOpen={false}>
                <KV rows={Object.entries(st.settings).map(([k, v]) => [k, v, { mono: true }])} />
              </Collapsible>
            )}
            {st.env?.length > 0 && (
              <Collapsible title="Run environment (.coresmith/env)" subtitle={`${st.env.length} variables · secrets redacted`} defaultOpen={false}>
                <KV rows={st.env.map((e) => [e.key, e.value, { mono: true }])} />
              </Collapsible>
            )}
          </>
        )}
      </Section>
    </div>
  );
}
