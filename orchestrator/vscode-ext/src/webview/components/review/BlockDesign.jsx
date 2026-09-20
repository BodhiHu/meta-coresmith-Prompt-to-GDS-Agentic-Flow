import React, { useState, useMemo, useRef, useEffect } from 'react';
import { api, useJson } from '../../utils/api';
import { fmtDuration, fmtTime, fmtDateTime, fmtNum, fmtPct, fmtBytes, blockLabel } from '../../utils/format';
import { highlightMarkdown } from '../../utils/highlight';
import {
  Badge, StatusBadge, PassFail, StatTile, BudgetCell, KV, Table, Section, Collapsible,
  ErrorBanner, Spinner, Empty, LongText, LinkButton, Tabs, MiniToc,
} from './common';
import CodeView from './CodeView';
import DiffView from './DiffView';

const GATE_LABELS = {
  lint: 'Lint (Verilator)', simulation: 'Simulation (cocotb)', coverage: 'Line coverage',
  throughput: 'Measured throughput', gate_sim: 'Gate-level sim', contract_conformance: 'Contract conformance',
  mem_price: 'Memory price (SRAM/ROM area)', ppa: 'PPA gate', timing: 'Pre-layout timing (WNS ≥ 0)',
  'ppa:flip_flop_count': 'PPA: flip-flops', 'ppa:chip_area_um2': 'PPA: chip area', 'ppa:wns_ns': 'PPA: WNS',
};

function GatesTable({ gates }) {
  return (
    <Table
      dense
      columns={[
        { key: 'gate', label: 'Gate', render: (g) => GATE_LABELS[g.gate] || g.gate },
        { key: 'passed', label: 'Verdict', render: (g) => <PassFail value={g.passed} none="not run" /> },
        { key: 'detail', label: 'Detail' },
      ]}
      rows={gates}
      rowKey={(g) => g.gate}
      empty="No gate results recorded for this block."
    />
  );
}

/* ── Versioned file viewer (spec / RTL) with diff against the previous version ── */
function VersionedFile({ versions, lang, emptyText, defaultIndex }) {
  const [idx, setIdx] = useState(defaultIndex ?? Math.max(0, (versions?.length || 1) - 1));
  const [mode, setMode] = useState('view');
  useEffect(() => { setIdx(defaultIndex ?? Math.max(0, (versions?.length || 1) - 1)); }, [versions, defaultIndex]);
  if (!versions || !versions.length) return <Empty>{emptyText}</Empty>;
  const cur = versions[Math.min(idx, versions.length - 1)];
  const prev = idx > 0 ? versions[idx - 1] : null;
  return (
    <div className="rv-versioned">
      <div className="rv-versioned-bar">
        <label className="rv-muted">Version</label>
        <select className="rv-select" value={idx} onChange={(e) => setIdx(Number(e.target.value))}>
          {versions.map((v, i) => (
            <option key={v.rel_path + i} value={i}>
              {i + 1}. {v.label || v.workspace || v.rel_path} · {fmtDateTime(v.mtime)} · {fmtBytes(v.size)}
            </option>
          ))}
        </select>
        <Tabs
          tabs={[{ key: 'view', label: 'View' }, { key: 'diff', label: prev ? `Diff vs #${idx}` : 'Diff', title: prev ? `Compare with ${prev.label || prev.rel_path}` : 'No earlier version' }]}
          active={mode}
          onChange={(m) => { if (m !== 'diff' || prev) setMode(m); }}
        />
      </div>
      {mode === 'view' && <CodeView relPath={cur.rel_path} lang={lang} maxHeight="70vh" />}
      {mode === 'diff' && prev && <DiffView a={prev.rel_path} b={cur.rel_path} maxHeight="70vh" />}
    </div>
  );
}

function num(v, d = 0) { return v == null ? '--' : fmtNum(v, d); }

/**
 * Design & results review page for one block: spec → RTL → testbench →
 * simulation → synthesis → timing → issues → decisions → contracts.
 */
export default function BlockDesign({ block, onOpenFile, onOpenCall, isLive }) {
  const { data, error, loading } = useJson(block ? api.review(block) : null, { pollMs: isLive ? 15000 : 0 });
  const containerRef = useRef(null);
  const [tbIdx, setTbIdx] = useState(0);
  useEffect(() => { setTbIdx(0); }, [block]);

  const toc = useMemo(() => ([
    { id: 'sec-summary', label: 'Summary' },
    { id: 'sec-spec', label: 'uArch spec' },
    { id: 'sec-rtl', label: 'RTL' },
    { id: 'sec-tb', label: 'Testbench' },
    { id: 'sec-sim', label: 'Simulation' },
    { id: 'sec-synth', label: 'Synthesis' },
    { id: 'sec-timing', label: 'Timing' },
    { id: 'sec-issues', label: 'Issues' },
    { id: 'sec-decisions', label: 'Decisions' },
    { id: 'sec-contracts', label: 'Contracts' },
  ]), []);

  if (!block) return <Empty>Select a block.</Empty>;
  if (loading && !data) return <Spinner label="Loading design review…" />;
  if (error) return <ErrorBanner error={error} />;
  if (!data) return null;

  const { meta, status, files, sim, synth, timing, issues, mem_price: memPrice, decisions, interrupts, contracts, gates, lint, spec_summary: specSummary } = data;
  const best = sim.best || {};
  const cov = sim.coverage || {};
  const thr = sim.throughput || {};
  const gs = sim.gate_sim || {};
  const budgets = synth.budgets || {};
  const failedGates = gates.filter((g) => g.passed === false).length;
  const tb = files.tb || [];
  const memories = (memPrice && memPrice.memories) || [];

  return (
    <div className="rv-design">
      <MiniToc items={toc.map((t) => (t.id === 'sec-summary' && failedGates ? { ...t, badge: `${failedGates} ✗` } : t))} containerRef={containerRef} />
      <div className="rv-design-body" ref={containerRef}>

        {/* ── Summary ── */}
        <Section id="sec-summary" title={blockLabel(block)} subtitle={`tier ${meta.tier ?? '?'} · ${meta.subsystem || ''}`} right={<StatusBadge status={status.status} />}>
          {meta.description && <p className="rv-desc">{meta.description}</p>}
          <div className="rv-tiles">
            <StatTile label="Rounds" value={status.rounds} sub="Init Block re-entries" />
            <StatTile label="RTL attempts" value={status.rtl_attempts} sub="max within a round" />
            <StatTile label="LLM calls" value={status.llm_calls} sub={fmtDuration(status.llm_time_s)} />
            <StatTile label="DV" value={best.tests_total != null ? `${best.tests_passed}/${best.tests_total}` : '--'} tone={best.sim_passed === true ? 'ok' : best.sim_passed === false ? 'fail' : undefined} sub="tests passed" />
            <StatTile label="Coverage" value={fmtPct(cov.pct)} tone={cov.passed === true ? 'ok' : cov.passed === false ? 'fail' : undefined} sub={cov.floor != null ? `floor ${cov.floor}%` : ''} />
            <StatTile label="Cells" value={num(synth.cells)} sub={budgets.estimated_gates != null ? `est. ${fmtNum(budgets.estimated_gates)} gates` : ''} />
            <StatTile label="Flip-flops" value={num(synth.ff)} tone={synth.ff != null && budgets.flip_flop_budget != null ? (synth.ff > budgets.flip_flop_budget ? 'fail' : 'ok') : undefined} sub={budgets.flip_flop_budget != null ? `budget ${fmtNum(budgets.flip_flop_budget)}` : ''} />
            <StatTile label="Area (µm²)" value={num(synth.area_um2)} tone={synth.area_um2 != null && budgets.area_budget_um2 != null ? (synth.area_um2 > budgets.area_budget_um2 ? 'fail' : 'ok') : undefined} sub={budgets.area_budget_um2 != null ? `budget ${fmtNum(budgets.area_budget_um2)}` : ''} />
            <StatTile label="WNS (ns)" value={timing.wns_ns != null ? fmtNum(timing.wns_ns, 3) : '--'} tone={timing.wns_ns != null ? (timing.wns_ns >= 0 ? 'ok' : 'fail') : undefined} sub={timing.period_ns != null ? `@ ${timing.period_ns} ns period` : ''} />
            <StatTile label="Fmax (MHz)" value={timing.fmax_mhz != null ? fmtNum(timing.fmax_mhz, 1) : '--'} sub="1000 / (period − WNS)" />
          </div>
          <h4>Gates</h4>
          <GatesTable gates={gates} />
          <KV rows={[
            ['RTL target', meta.rtl_target, { mono: true }],
            ['Testbench', meta.testbench, { mono: true }],
            ['Golden source', meta.python_source, { mono: true }],
            ['Contract version', meta.spec_contract_version],
            ['First activity', fmtDateTime(status.first_ts)],
            ['Last activity', fmtDateTime(status.last_ts)],
          ]} />
        </Section>

        {/* ── Spec ── */}
        <Section id="sec-spec" title="Microarchitecture spec" subtitle={files.spec ? `${fmtBytes(files.spec.size)} · ${fmtDateTime(files.spec.mtime)}` : 'no spec on disk'}>
          {specSummary?.overview && <p className="rv-desc">{specSummary.overview}</p>}
          {specSummary?.summary && (
            <Collapsible title="Machine-readable summary (JSON block in the spec)" defaultOpen={false}>
              <pre className="rv-pre-inline">{JSON.stringify(specSummary.summary, null, 2)}</pre>
            </Collapsible>
          )}
          <VersionedFile versions={files.spec_versions} lang="markdown" emptyText="No uArch spec was written for this block." />
          {files.spec_review && (
            <Collapsible title="Reviewed spec (arch/uarch_specs_review)" subtitle={fmtDateTime(files.spec_review.mtime)} defaultOpen={false}>
              <CodeView relPath={files.spec_review.rel_path} lang="markdown" maxHeight="60vh" />
            </Collapsible>
          )}
        </Section>

        {/* ── RTL ── */}
        <Section id="sec-rtl" title="RTL" subtitle={files.rtl ? `${files.rtl.rel_path} · ${fmtBytes(files.rtl.size)} · ${fmtDateTime(files.rtl.mtime)}` : 'no RTL on disk'}>
          {lint && (
            <div className="rv-inline-stats">
              <PassFail value={(lint.error_count || 0) === 0} yes="lint clean" no={`${lint.error_count} lint errors`} />
              <span className="rv-muted">{lint.warning_count} warnings{lint.warning_kinds?.length ? ` (${lint.warning_kinds.join(', ')})` : ''}</span>
              {lint.log_rel_path && <LinkButton onClick={() => onOpenFile(lint.log_rel_path, { lang: 'log' })}>open lint log</LinkButton>}
            </div>
          )}
          {lint?.errors?.length > 0 && (
            <ul className="rv-list-fail">{lint.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>
          )}
          <VersionedFile versions={files.rtl_versions} lang="verilog" emptyText="No RTL file exists for this block yet." />
          {issues.provenance && (
            <Collapsible title="Provenance" defaultOpen={false}>
              <pre className="rv-pre-inline">{JSON.stringify(issues.provenance, null, 2)}</pre>
            </Collapsible>
          )}
        </Section>

        {/* ── Testbench ── */}
        <Section id="sec-tb" title="Testbench" subtitle={tb.length ? `${tb.length} file${tb.length === 1 ? '' : 's'}` : 'no testbench on disk'}>
          {sim.dv_summary?.testbenches?.length > 0 && (
            <Table dense columns={[
              { key: 'name', label: 'Testbench' }, { key: 'kind', label: 'Kind' },
              { key: 'tests', label: 'Tests', render: (t) => `${t.tests_passed}/${t.tests_total}` },
              { key: 'passed', label: 'Verdict', render: (t) => <PassFail value={t.passed} /> },
            ]} rows={sim.dv_summary.testbenches} rowKey={(t) => t.path || t.name} />
          )}
          {tb.length > 0 && (
            <>
              <Tabs tabs={tb.map((f, i) => ({ key: i, label: f.rel_path.split('/').pop(), title: f.rel_path }))} active={Math.min(tbIdx, tb.length - 1)} onChange={setTbIdx} />
              <CodeView relPath={tb[Math.min(tbIdx, tb.length - 1)].rel_path} lang="python" maxHeight="70vh" />
            </>
          )}
          {tb.length === 0 && <Empty>No testbench files found under tb/ for this block.</Empty>}
        </Section>

        {/* ── Simulation ── */}
        <Section id="sec-sim" title="Simulation results" right={<PassFail value={best.sim_passed} yes="sim passed" no="sim failed" none="not run" />}>
          <div className="rv-tiles">
            <StatTile label="Tests" value={best.tests_total != null ? `${best.tests_passed}/${best.tests_total}` : '--'} tone={best.sim_passed === true ? 'ok' : best.sim_passed === false ? 'fail' : undefined} sub={best.attempt != null ? `best attempt ${best.attempt}` : ''} />
            <StatTile label="Coverage" value={fmtPct(cov.pct)} tone={cov.passed === true ? 'ok' : cov.passed === false ? 'fail' : undefined} sub={cov.points_total != null ? `${cov.points_hit}/${cov.points_total} points · floor ${cov.floor}%` : ''} />
            <StatTile label="Throughput" value={thr.applicable ? `${fmtNum(thr.measured_cyc_per_op, 1)} cyc/op` : 'n/a'} tone={thr.passed === true ? 'ok' : thr.passed === false ? 'fail' : undefined} sub={thr.applicable ? `declared ${fmtNum(thr.declared_cyc_per_op, 1)} · threshold ${fmtNum(thr.threshold_cyc_per_op, 1)} · ${thr.n_ops} ops` : (thr.reason || '')} />
            <StatTile label="Gate-level sim" value={gs.status ? gs.status.replace(/_/g, ' ') : '--'} tone={gs.ran ? (gs.ok ? 'ok' : 'fail') : undefined} sub={gs.cycles_compared ? `${fmtNum(gs.cycles_compared)} cycles compared` : ''} />
          </div>
          {gs.reason && !gs.ran && <p className="rv-note">Gate sim: {gs.reason}</p>}
          {sim.parsed?.tests?.length > 0 && (
            <>
              <h4>Latest regression ({sim.parsed.log_rel_path?.split('/').pop()})</h4>
              <Table dense columns={[
                { key: 'name', label: 'Test' },
                { key: 'status', label: 'Result', render: (t) => <Badge tone={t.status === 'PASS' ? 'ok' : t.status === 'FAIL' ? 'fail' : 'warn'}>{t.status}</Badge> },
                { key: 'sim_time_ns', label: 'Sim time (ns)', align: 'right', render: (t) => fmtNum(t.sim_time_ns, 2) },
                { key: 'real_time_s', label: 'Wall (s)', align: 'right', render: (t) => fmtNum(t.real_time_s, 2) },
              ]} rows={sim.parsed.tests} rowKey={(t) => t.name} />
              {sim.parsed.first_assertion && <p className="rv-fail-text rv-mono">{sim.parsed.first_assertion}</p>}
              <LinkButton onClick={() => onOpenFile(sim.parsed.log_rel_path, { lang: 'log', tail: true })}>open simulate log</LinkButton>
            </>
          )}
          {sim.dv_results?.length > 0 && (
            <Collapsible title="DV history (dv_results)" subtitle={`${sim.dv_results.length} rows · agent probes + gate verdicts`} defaultOpen={false}>
              <Table dense columns={[
                { key: 'ts', label: 'Time', render: (r) => fmtTime(r.ts) },
                { key: 'attempt', label: 'Att.' }, { key: 'scope', label: 'Scope' }, { key: 'source', label: 'Source' },
                { key: 'passed', label: 'Verdict', render: (r) => <PassFail value={r.passed === 1 || r.passed === true} /> },
                { key: 'tests', label: 'Tests', render: (r) => (r.tests_total != null ? `${r.tests_passed}/${r.tests_total}` : '--') },
                { key: 'duration_s', label: 'Dur', render: (r) => fmtDuration(r.duration_s) },
                { key: 'detail', label: 'Detail', render: (r) => <span className="rv-clip" title={r.detail}>{(r.detail || '').slice(0, 120)}</span> },
                { key: 'log', label: 'Log', render: (r) => (r.log_rel_path ? <LinkButton onClick={() => onOpenFile(r.log_rel_path, { lang: 'log', tail: true })}>log</LinkButton> : '') },
              ]} rows={sim.dv_results} rowKey={(r) => r.id} />
            </Collapsible>
          )}
          {sim.coverage_results?.length > 0 && (
            <Collapsible title="Coverage history" defaultOpen={false}>
              <Table dense columns={[
                { key: 'ts', label: 'Time', render: (r) => fmtTime(r.ts) }, { key: 'scope', label: 'Scope' },
                { key: 'pct', label: '%', align: 'right', render: (r) => fmtPct(r.pct, 2) },
                { key: 'points', label: 'Points', render: (r) => `${r.points_hit}/${r.points_total}` },
                { key: 'passed', label: 'Verdict', render: (r) => <PassFail value={r.uncovered?.passed} /> },
              ]} rows={sim.coverage_results} rowKey={(r) => r.id} />
            </Collapsible>
          )}
          {sim.sim_logs?.length > 1 && (
            <p className="rv-muted">Simulate logs on disk: {sim.sim_logs.map((l) => (
              <LinkButton key={l.rel_path} onClick={() => onOpenFile(l.rel_path, { lang: 'log', tail: true })}>{l.name}</LinkButton>
            ))}</p>
          )}
        </Section>

        {/* ── Synthesis ── */}
        <Section id="sec-synth" title="Synthesis (Yosys, sky130_fd_sc_hd)" right={<PassFail value={synth.ppa_ok == null ? null : !!synth.ppa_ok} yes="PPA ok" no="PPA failed" none="no PPA verdict" />}>
          {synth.cells == null && <Empty>No synthesis result recorded for this block (synthesis skipped or not reached).</Empty>}
          {synth.cells != null && (
            <>
              <div className="rv-tiles">
                <StatTile label="Cells" value={<BudgetCell measured={synth.cells} budget={budgets.estimated_gates} />} sub="std cells vs estimated gates" />
                <StatTile label="Flip-flops" value={<BudgetCell measured={synth.ff} budget={budgets.flip_flop_budget} />} sub="sequential cells vs FF budget" />
                <StatTile label="Total area (µm²)" value={<BudgetCell measured={synth.area_um2} budget={budgets.area_budget_um2} />} sub="ppa_history area (std cells + instantiated SRAM macros) vs budget" />
                <StatTile label="Std-cell area (µm²)" value={num(synth.std_cell_area_um2, 1)} sub={synth.sequential_pct != null ? `${synth.sequential_pct}% sequential` : ''} />
                <StatTile label="Memory macro area (µm²)" value={num(synth.sram_area_um2, 1)} sub={memPrice ? `${memories.length} memories (mem_price)` : 'no mem_price ledger'} />
              </div>
              {synth.reasons?.length > 0 && <ul className="rv-list-fail">{synth.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
              {synth.checks?.length > 0 && (
                <>
                  <h4>PPA checks (ppa_report.json)</h4>
                  <Table dense columns={[
                    { key: 'metric', label: 'Metric' },
                    { key: 'actual', label: 'Actual', align: 'right', render: (c) => fmtNum(c.actual, 3) },
                    { key: 'budget', label: 'Budget', align: 'right', render: (c) => fmtNum(c.budget, 3) },
                    { key: 'limit', label: 'Limit', align: 'right', render: (c) => fmtNum(c.limit, 3) },
                    { key: 'passed', label: 'Verdict', render: (c) => <PassFail value={c.passed} /> },
                  ]} rows={synth.checks} rowKey={(c) => c.metric} />
                </>
              )}
              <h4>Macro inventory</h4>
              {synth.macros?.length ? (
                <Table dense columns={[{ key: 'name', label: 'Macro / blackbox' }, { key: 'count', label: 'Instances', align: 'right' }]} rows={synth.macros} rowKey={(m) => m.name} />
              ) : <p className="rv-muted">No macro / blackbox instances in the netlist.</p>}
              {synth.unknown_area_types?.length > 0 && <p className="rv-note">Yosys had no area for: {synth.unknown_area_types.join(', ')} (macro area comes from mem_price).</p>}
              {memories.length > 0 && (
                <>
                  <h4>Memory ledger (mem_price.json)</h4>
                  <Table dense columns={[
                    { key: 'name', label: 'Memory' }, { key: 'width', label: 'W', align: 'right' }, { key: 'depth', label: 'D', align: 'right' },
                    { key: 'bits', label: 'Bits', align: 'right', render: (m) => fmtNum(m.bits) }, { key: 'ports', label: 'Ports' },
                    { key: 'declared_impl', label: 'Impl' }, { key: 'recommended_impl', label: 'Recommended' },
                    { key: 'area_um2', label: 'Area (µm²)', align: 'right', render: (m) => fmtNum(m.area_um2, 1) },
                    { key: 'estimate_source', label: 'Source' },
                  ]} rows={memories} rowKey={(m) => m.name} />
                  <KV rows={[
                    ['Ledger verdict', <PassFail value={memPrice.ok} />], ['Total memory area', `${fmtNum(memPrice.total_area_um2, 1)} µm² (${memPrice.total_area_mm2} mm²)`],
                    ['Area budget', memPrice.area_budget_um2 != null ? `${fmtNum(memPrice.area_budget_um2)} µm²` : null], ['Trajectory', memPrice.trajectory],
                    ['Deferred', memPrice.deferred ? memPrice.deferred_reason || 'yes' : null],
                  ]} />
                </>
              )}
              <Collapsible title="PPA history (ppa_history)" subtitle={`${synth.history?.length || 0} rows · agent probes + gate synth`} defaultOpen={false}>
                <Table dense columns={[
                  { key: 'ts', label: 'Time', render: (r) => fmtTime(r.ts) }, { key: 'round', label: 'Round' }, { key: 'attempt', label: 'Att.' },
                  { key: 'source', label: 'Source' }, { key: 'probe', label: 'Probe' },
                  { key: 'cells', label: 'Cells', align: 'right', render: (r) => fmtNum(r.cells) },
                  { key: 'ff', label: 'FF', align: 'right', render: (r) => fmtNum(r.ff) },
                  { key: 'area_um2', label: 'Area', align: 'right', render: (r) => fmtNum(r.area_um2, 1) },
                  { key: 'wns_ns', label: 'WNS', align: 'right', render: (r) => (r.wns_ns != null ? fmtNum(r.wns_ns, 3) : '--') },
                  { key: 'budget_ff', label: 'FF budget', align: 'right', render: (r) => fmtNum(r.budget_ff) },
                  { key: 'ppa_ok', label: 'OK', render: (r) => <PassFail value={r.ppa_ok == null ? null : !!r.ppa_ok} /> },
                  { key: 'reasons', label: 'Reasons', render: (r) => (r.reasons ? r.reasons.join('; ') : '') },
                ]} rows={synth.history} rowKey={(r) => r.id} />
              </Collapsible>
              <Collapsible title="Cell types (Yosys stat)" subtitle={`${synth.cell_types?.length || 0} types`} defaultOpen={false}>
                <Table dense columns={[
                  { key: 'name', label: 'Cell' }, { key: 'count', label: 'Count', align: 'right', render: (c) => fmtNum(c.count) },
                  { key: 'area', label: 'Area (µm²)', align: 'right', render: (c) => (c.area != null ? fmtNum(c.area, 1) : '--') },
                ]} rows={[...(synth.cell_types || [])].sort((a, b) => (b.count || 0) - (a.count || 0))} rowKey={(c) => c.name} />
              </Collapsible>
              <div className="rv-actions">
                {synth.report_rel_path && <LinkButton onClick={() => onOpenFile(synth.report_rel_path, { lang: 'log', tail: true, title: 'Yosys report' })}>Yosys report</LinkButton>}
                {synth.script_rel_path && <LinkButton onClick={() => onOpenFile(synth.script_rel_path, { lang: 'tcl' })}>synth script</LinkButton>}
                {synth.netlist_rel_path && <LinkButton onClick={() => onOpenFile(synth.netlist_rel_path, { lang: 'verilog' })}>netlist ({fmtBytes(synth.netlist_size)})</LinkButton>}
                {(synth.synth_logs || []).map((l) => (
                  <LinkButton key={l.rel_path} onClick={() => onOpenFile(l.rel_path, { lang: 'log', tail: true })}>{l.name}</LinkButton>
                ))}
              </div>
            </>
          )}
        </Section>

        {/* ── Timing ── */}
        <Section id="sec-timing" title="Timing (pre-layout OpenSTA)" right={timing.wns_ns != null ? <PassFail value={timing.wns_ns >= 0} yes="timing met" no="timing violated" /> : <Badge tone="muted">not measured</Badge>}>
          <div className="rv-tiles">
            <StatTile label="Clock period (ns)" value={timing.period_ns != null ? fmtNum(timing.period_ns, 2) : '--'} sub={timing.period_ns ? `${fmtNum(1000 / timing.period_ns, 1)} MHz target` : 'from SDC'} />
            <StatTile label="WNS (ns)" value={timing.wns_ns != null ? fmtNum(timing.wns_ns, 3) : '--'} tone={timing.wns_ns != null ? (timing.wns_ns >= 0 ? 'ok' : 'fail') : undefined} sub="worst negative slack, max(base, fan-out buffered)" />
            <StatTile label="TNS (ns)" value={timing.tns_ns != null ? fmtNum(timing.tns_ns, 3) : 'n/a'} sub={timing.tns_ns == null ? 'not persisted by engine' : 'total negative slack'} />
            <StatTile label="Fmax (MHz)" value={timing.fmax_mhz != null ? fmtNum(timing.fmax_mhz, 1) : '--'} sub="1000 / (period − WNS)" />
          </div>
          {timing.wns_check && (
            <KV rows={[
              ['Gate verdict', <PassFail value={timing.wns_check.passed} />],
              ['Measured WNS', `${timing.wns_check.actual} ns`], ['Limit', `${timing.wns_check.limit} ns`],
              ['Gross violation', timing.wns_check.gross], ['Advisory only', timing.wns_check.advisory],
            ]} />
          )}
          {timing.history?.filter((h) => h.wns_ns != null).length > 0 && (
            <>
              <h4>WNS per synthesis</h4>
              <Table dense columns={[
                { key: 'ts', label: 'Time', render: (r) => fmtTime(r.ts) }, { key: 'round', label: 'Round' }, { key: 'attempt', label: 'Att.' },
                { key: 'wns_ns', label: 'WNS (ns)', align: 'right', render: (r) => <span className={r.wns_ns < 0 ? 'rv-fail-text' : ''}>{fmtNum(r.wns_ns, 4)}</span> },
                { key: 'cells', label: 'Cells', align: 'right', render: (r) => fmtNum(r.cells) },
                { key: 'ff', label: 'FF', align: 'right', render: (r) => fmtNum(r.ff) },
                { key: 'ppa_ok', label: 'PPA', render: (r) => <PassFail value={r.ppa_ok == null ? null : !!r.ppa_ok} /> },
              ]} rows={timing.history.filter((h) => h.wns_ns != null)} rowKey={(r, i) => `${r.ts}-${i}`} />
            </>
          )}
          {timing.paths?.length > 0 ? (
            <>
              <h4>Worst paths (report_checks)</h4>
              <Table dense columns={[
                { key: 'startpoint', label: 'Startpoint' }, { key: 'endpoint', label: 'Endpoint' }, { key: 'path_group', label: 'Group' },
                { key: 'arrival_ns', label: 'Arrival', align: 'right' }, { key: 'required_ns', label: 'Required', align: 'right' },
                { key: 'slack_ns', label: 'Slack', align: 'right', render: (p) => <span className={p.violated ? 'rv-fail-text' : ''}>{fmtNum(p.slack_ns, 3)}</span> },
                { key: 'violated', label: 'Verdict', render: (p) => <PassFail value={!p.violated} yes="met" no="violated" /> },
              ]} rows={timing.paths} rowKey={(p, i) => i} />
            </>
          ) : (
            timing.note && <p className="rv-note">{timing.note}</p>
          )}
          {timing.sdc_rel_path && (
            <Collapsible title="Constraints (SDC)" defaultOpen={false}>
              <CodeView relPath={timing.sdc_rel_path} lang="tcl" maxHeight="30vh" />
            </Collapsible>
          )}
        </Section>

        {/* ── Issues ── */}
        <Section id="sec-issues" title="Issues, diagnoses & constraints" subtitle={`${issues.event_diagnoses?.length || 0} diagnoses in the event stream · ${issues.attempts?.length || 0} failed attempts on record · ${issues.constraints?.length || 0} constraints`}>
          {issues.event_diagnoses?.length > 0 && (
            <>
              <h4>Diagnoses by round (pipeline events)</h4>
              <p className="rv-note">The state store keeps only the current round's diagnosis; earlier rounds survive as the (truncated) previews the engine logged at Diagnose Failure exit.</p>
              {issues.event_diagnoses.map((d, i) => (
                <div key={i} className="rv-issue">
                  <div className="rv-issue-head">
                    <Badge tone="muted">round {d.round}</Badge>
                    {d.phase && <Badge tone="muted">{d.phase}</Badge>}
                    <Badge tone="warn">{d.category || 'no category'}</Badge>
                    {d.confidence != null && <Badge tone="muted">confidence {Math.round(Number(d.confidence) * 100)}%</Badge>}
                    {d.needs_human != null && <Badge tone={d.needs_human ? 'fail' : 'ok'}>{d.needs_human ? 'needs human' : 'auto-fixable'}</Badge>}
                    {d.repeat_count != null && <Badge tone="fail">repeat ×{d.repeat_count}</Badge>}
                    <span className="rv-muted">{fmtDateTime(d.ts)}</span>
                    {d.call_ids?.map((cid) => onOpenCall && <LinkButton key={cid} onClick={() => onOpenCall(cid)}>open diagnosis call #{cid}</LinkButton>)}
                  </div>
                  {d.diagnosis_preview && <p className="rv-prose">{d.diagnosis_preview}</p>}
                  {d.suggested_fix && <><b>Suggested fix:</b><p className="rv-prose">{d.suggested_fix}</p></>}
                  {!d.diagnosis_preview && !d.suggested_fix && <p className="rv-muted">Repeated failure signature: no new LLM diagnosis was produced.</p>}
                </div>
              ))}
            </>
          )}
          {issues.route_decisions?.length > 0 && (
            <p className="rv-muted">Route decisions: {issues.route_decisions.map((r, i) => (
              <span key={i}>{i ? ' · ' : ''}round {r.round}{r.attempt != null ? ` attempt ${r.attempt}` : ''} → <b>{r.decision}</b></span>
            ))}</p>
          )}
          {issues.attempts?.length > 0 && (
            <>
              <h4>Failed attempts (attempt_history)</h4>
              {issues.attempts.map((a, i) => (
                <div key={i} className="rv-issue">
                  <div className="rv-issue-head">
                    <Badge tone="fail">attempt {a.attempt}</Badge>
                    {a.phase && <Badge tone="muted">{a.phase}</Badge>}
                    {a.category && <Badge tone="warn">{a.category}</Badge>}
                    {a.ts && <span className="rv-muted">{fmtDateTime(a.ts)}</span>}
                  </div>
                  <LongText text={a.error || ''} initialChars={1500} searchable={false} />
                </div>
              ))}
            </>
          )}
          {issues.diagnoses?.length > 0 && (
            <>
              <h4>Diagnoses</h4>
              {issues.diagnoses.map((d, i) => {
                const dj = d.diagnosis || {};
                return (
                  <div key={i} className="rv-issue">
                    <div className="rv-issue-head">
                      <Badge tone="warn">{d.category || dj.category}</Badge>
                      {d.confidence != null && <Badge tone="muted">confidence {Math.round(Number(d.confidence) * 100)}%</Badge>}
                      {d.attempt != null && <Badge tone="muted">attempt {d.attempt}</Badge>}
                      {dj.needs_human != null && <Badge tone={dj.needs_human ? 'fail' : 'ok'}>{dj.needs_human ? 'needs human' : 'auto-fixable'}</Badge>}
                      {d.ts && <span className="rv-muted">{fmtDateTime(d.ts)}</span>}
                    </div>
                    {dj.diagnosis && <p className="rv-prose">{dj.diagnosis}</p>}
                    {dj.suggested_fix && <><b>Suggested fix:</b><p className="rv-prose">{dj.suggested_fix}</p></>}
                    {dj.constraints?.length > 0 && <ul>{dj.constraints.map((c, j) => <li key={j}>{typeof c === 'string' ? c : c.rule || JSON.stringify(c)}</li>)}</ul>}
                    <Collapsible title="Raw diagnosis JSON" defaultOpen={false}><pre className="rv-pre-inline">{JSON.stringify(dj, null, 2)}</pre></Collapsible>
                  </div>
                );
              })}
            </>
          )}
          {issues.constraints?.length > 0 && (
            <>
              <h4>Constraints</h4>
              <Table dense columns={[
                { key: 'rule', label: 'Rule' }, { key: 'source', label: 'Source' }, { key: 'attempt', label: 'Att.' },
                { key: 'ts', label: 'Time', render: (r) => (r.ts ? fmtTime(r.ts) : '') },
              ]} rows={issues.constraints} rowKey={(r, i) => i} />
            </>
          )}
          <h4>previous_error.txt (what the regenerating agent was told)</h4>
          {issues.previous_error ? <LongText text={issues.previous_error} initialChars={3000} /> : <p className="rv-muted">empty — no pending error for the next attempt.</p>}
          {issues.conformance && (
            <>
              <h4>Contract conformance</h4>
              <KV rows={[
                ['Verdict', issues.conformance.ran ? <PassFail value={issues.conformance.ok} /> : 'not run'],
                ['Edges checked', issues.conformance.checked_edges],
                ['Deviations', issues.conformance.deviations?.length ? issues.conformance.deviations : 'none'],
                ['Renames applied', Object.keys(issues.conformance.renames || {}).length ? issues.conformance.renames : null],
                ['Feedback', issues.conformance.feedback || null],
                ['Testbench changed', issues.conformance.tb?.changed],
              ]} />
            </>
          )}
          {issues.carried_forward_defects?.length > 0 && (
            <>
              <h4>Carried-forward defects</h4>
              {issues.carried_forward_defects.map((d, i) => (
                <div key={i} className="rv-issue">
                  <div className="rv-issue-head">
                    <Badge tone={d.advisory ? 'warn' : 'fail'}>{d.gate}</Badge>
                    <Badge tone="muted">{d.kind}</Badge>
                    {d.advisory && <Badge tone="muted">advisory</Badge>}
                  </div>
                  <p className="rv-prose">{d.detail || d.unmodeled}</p>
                </div>
              ))}
            </>
          )}
          {issues.failure_signature && (
            <p className="rv-muted">Last failure signature: <code>{issues.failure_signature.sig}</code> (seen {issues.failure_signature.run}×)</p>
          )}
        </Section>

        {/* ── Decisions ── */}
        <Section id="sec-decisions" title="Chip-lead decisions & interrupts" subtitle={`${decisions.length} decisions · ${interrupts.length} interrupts`}>
          {decisions.length === 0 && <p className="rv-muted">No chip-lead decision references this block.</p>}
          {decisions.map((d) => (
            <div key={d.decision_index} className="rv-issue">
              <div className="rv-issue-head">
                <Badge tone={/approve|override|fix|retry/.test(d.action || '') ? 'ok' : 'warn'}>{d.action}</Badge>
                <Badge tone="muted">{d.type}</Badge>
                <span className="rv-muted">#{d.decision_index} · {fmtDateTime(d.ts)}</span>
                {d.call_id && onOpenCall && <LinkButton onClick={() => onOpenCall(d.call_id)}>open chip-lead call</LinkButton>}
              </div>
              <p className="rv-prose">{d.reasoning}</p>
            </div>
          ))}
          {interrupts.length > 0 && (
            <Table dense columns={[
              { key: 'ts', label: 'Time', render: (r) => fmtDateTime(r.ts) },
              { key: 'node', label: 'Node / event', render: (r) => r.node || r.event },
              { key: 'round', label: 'Round' },
              { key: 'action', label: 'Resolution', render: (r) => r.action || (r.detail?.reason) || '' },
              { key: 'status', label: 'Status', render: (r) => <StatusBadge status={r.status} /> },
              { key: 'dur', label: 'Waited', render: (r) => (r.end_ts ? fmtDuration(r.end_ts - r.ts) : '') },
            ]} rows={interrupts} rowKey={(r, i) => i} />
          )}
        </Section>

        {/* ── Contracts ── */}
        <Section id="sec-contracts" title="Interface contracts" subtitle={`${contracts.length} edges`}>
          <Table dense columns={[
            { key: 'producer_block', label: 'Producer', render: (c) => `${blockLabel(c.producer_block)}.${c.producer_port}` },
            { key: 'consumer_block', label: 'Consumer', render: (c) => `${blockLabel(c.consumer_block)}.${c.consumer_port}` },
            { key: 'handshake_protocol', label: 'Protocol' },
            { key: 'data_width_bits', label: 'Width', align: 'right' },
            { key: 'version', label: 'v', align: 'right' },
          ]} rows={contracts} rowKey={(c) => c.edge_id} empty="No interface contracts recorded for this block." />
          {meta.interfaces && (
            <Collapsible title="Declared interfaces (block diagram)" defaultOpen={false}>
              <pre className="rv-pre-inline">{JSON.stringify(meta.interfaces, null, 2)}</pre>
            </Collapsible>
          )}
        </Section>
      </div>
    </div>
  );
}
