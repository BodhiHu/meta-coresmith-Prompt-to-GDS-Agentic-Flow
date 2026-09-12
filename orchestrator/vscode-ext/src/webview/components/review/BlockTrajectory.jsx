import React, { useState, useMemo, useEffect, useCallback, useRef } from 'react';
import { api, useJson } from '../../utils/api';
import { fmtDuration, fmtTime, fmtTokens, fmtBytes, fmtNum, blockLabel } from '../../utils/format';
import { Badge, StatusBadge, KV, ErrorBanner, Spinner, Empty, LinkButton, LongText } from './common';
import LLMCallViewer from './LLMCallViewer';
import CodeView from './CodeView';

const EXIT_SUMMARY_KEYS = [
  'lint_clean', 'sim_passed', 'success', 'passed', 'gate_count', 'chip_area_um2', 'category',
  'confidence', 'decision', 'action', 'tb_fixes_attempted', 'local_fixes_attempted',
  'needs_human', 'repeat_count', 'issues_found', 'new_tier_index',
];

function exitSummary(exit) {
  if (!exit) return [];
  const out = [];
  for (const k of EXIT_SUMMARY_KEYS) {
    if (exit[k] === undefined || exit[k] === null || exit[k] === '') continue;
    out.push([k, exit[k]]);
  }
  return out;
}

function ExitChips({ exit }) {
  const items = exitSummary(exit);
  if (!items.length) return null;
  return (
    <span className="rv-exit-chips">
      {items.map(([k, v]) => {
        let tone = 'muted';
        if (typeof v === 'boolean') tone = v ? 'ok' : 'fail';
        else if (k === 'decision' || k === 'action') tone = /escalate|ask_human|abort|revise/.test(String(v)) ? 'warn' : 'info';
        else if (k === 'category') tone = 'warn';
        const label = typeof v === 'boolean' ? `${k.replace(/_/g, ' ')} ${v ? '✓' : '✗'}` : `${k.replace(/_/g, ' ')}: ${typeof v === 'number' ? fmtNum(v, 2) : String(v)}`;
        return <Badge key={k} tone={tone}>{label}</Badge>;
      })}
    </span>
  );
}

function eventSummary(step) {
  const f = step.fields || {};
  switch (step.event) {
    case 'gate_result':
      return `${step.node || 'gate'}: ${f.status || (f.passed ? 'passed' : 'failed')}${f.reason ? ` — ${String(f.reason).slice(0, 140)}` : ''}`;
    case 'mem_price':
      return `memory price ${f.ok ? 'ok' : 'over budget'} · ${f.n_memories} memories · ${f.total_area_mm2} mm² (${f.trajectory})`;
    case 'lint_retry_bypass':
    case 'ppa_retry_bypass':
      return `${step.event.replace(/_/g, ' ')} (attempt ${f.attempt}) — previous RTL backed up`;
    case 'llm_timed out':
      return `LLM call timed out after ${fmtDuration(f.elapsed_s)}`;
    case 'gate_failed':
      return `gate failed: ${(f.deviations || []).length} deviations`;
    case 'uarch_feasibility_blocked':
      return `uArch feasibility blocked: ${(f.blocking_issues || []).length} issue(s)`;
    case 'uarch_feasibility_resume':
      return `uArch feasibility resumed: ${f.action}`;
    case 'interrupt':
      return `interrupt: ${f.reason || ''}`;
    default:
      return step.event;
  }
}

function StepRow({ step, selected, onSelect, pipelineStart }) {
  const key = stepKey(step);
  const cls = `rv-step rv-step-${step.type} ${selected ? 'rv-step-selected' : ''}`;
  if (step.type === 'llm_call') {
    const u = step.usage || {};
    return (
      <button type="button" className={cls} data-key={key} onClick={() => onSelect(step)}>
        <span className="rv-step-icon">✦</span>
        <span className="rv-step-main">
          <span className="rv-step-title">{step.run_name || `LLM call #${step.call_id}`}</span>
          <span className="rv-step-sub">
            {step.model} · {fmtDuration(step.duration_s)} · out {fmtTokens(u.output_tokens)} tok
            {step.n_commands ? ` · ${step.n_commands} cmds` : ''}{step.n_file_changes ? ` · ${step.n_file_changes} file writes` : ''}
          </span>
        </span>
        <span className="rv-step-right">
          <span className="rv-step-time">{fmtTime(step.ts)}</span>
          <StatusBadge status={step.status} />
        </span>
      </button>
    );
  }
  if (step.type === 'tool_run') {
    return (
      <button type="button" className={cls} data-key={key} onClick={() => onSelect(step)}>
        <span className="rv-step-icon">🔧</span>
        <span className="rv-step-main">
          <span className="rv-step-title">{step.name}</span>
          <span className="rv-step-sub">{step.step} log · {fmtBytes(step.size)}</span>
        </span>
        <span className="rv-step-right"><span className="rv-step-time">{fmtTime(step.mtime)}</span></span>
      </button>
    );
  }
  return (
    <button type="button" className={cls} data-key={key} onClick={() => onSelect(step)}>
      <span className="rv-step-icon">•</span>
      <span className="rv-step-main">
        <span className="rv-step-title rv-step-event">{eventSummary(step)}</span>
      </span>
      <span className="rv-step-right"><span className="rv-step-time">{fmtTime(step.ts)}</span></span>
    </button>
  );
}

function stepKey(step) {
  if (step.type === 'llm_call') return `call:${step.call_id}`;
  if (step.type === 'tool_run') return `tool:${step.rel_path}`;
  return `event:${step.event}:${step.ts}`;
}

function SegmentBlock({ seg, index, selectedKey, onSelect }) {
  const key = `seg:${seg.seg_id}`;
  const hasSteps = seg.steps && seg.steps.length > 0;
  return (
    <div className={`rv-seg rv-seg-${seg.status} ${seg.hitl ? 'rv-seg-hitl' : ''}`}>
      <button type="button" className={`rv-seg-head ${selectedKey === key ? 'rv-step-selected' : ''}`} data-key={key} onClick={() => onSelect({ type: 'segment', ...seg })}>
        <span className="rv-seg-idx">{index + 1}</span>
        <span className="rv-seg-node">{seg.node}</span>
        {seg.attempt != null && <Badge tone="info" title="RTL attempt within this round">attempt {seg.attempt}</Badge>}
        <StatusBadge status={seg.status} />
        <span className="rv-seg-dur">{seg.instant ? '' : fmtDuration(seg.duration_s)}</span>
        <span className="rv-seg-counts">
          {seg.n_calls > 0 && <span title="LLM calls">✦{seg.n_calls}</span>}
          {seg.n_tool_runs > 0 && <span title="tool logs">🔧{seg.n_tool_runs}</span>}
        </span>
        <span className="rv-seg-time">{fmtTime(seg.enter_ts)}</span>
      </button>
      <div className="rv-seg-chips"><ExitChips exit={seg.exit} /></div>
      {hasSteps && (
        <div className="rv-seg-steps">
          {seg.steps.map((st) => (
            <StepRow key={stepKey(st)} step={st} selected={selectedKey === stepKey(st)} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  );
}

function RoundBlock({ round, selectedKey, onSelect, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  const outcomeTone = round.outcome === 'passed' ? 'ok' : round.outcome === 'failed' ? 'fail' : 'warn';
  return (
    <div className={`rv-round ${open ? 'rv-open' : ''}`}>
      <button type="button" className="rv-round-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="rv-chev">{open ? '▾' : '▸'}</span>
        <span className="rv-round-title">Round {round.round}</span>
        {round.tier && <Badge tone="muted">tier {round.tier}</Badge>}
        <Badge tone={outcomeTone}>{round.outcome || 'in progress'}</Badge>
        {round.decision && <Badge tone={/escalate|ask_human|abort/.test(round.decision) ? 'warn' : 'info'}>route: {round.decision}</Badge>}
        <span className="rv-round-meta">
          {fmtTime(round.start_ts)} · {fmtDuration(round.duration_s)} · {round.n_calls} LLM call{round.n_calls === 1 ? '' : 's'} ({fmtDuration(round.llm_time_s)})
        </span>
      </button>
      {open && (
        <div className="rv-round-body">
          {round.segments.map((seg, i) => (
            <SegmentBlock key={seg.seg_id} seg={seg} index={i} selectedKey={selectedKey} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  );
}

function ToolRunDetail({ step, onOpenFile }) {
  const [header, setHeader] = useState(null);
  const isSim = step.step === 'simulate' || /sim/.test(step.step);
  return (
    <div className="rv-detail-tool">
      <div className="rv-detail-head">
        <h3>{step.name}</h3>
        <span className="rv-muted">{step.step} log · attempt {step.attempt ?? '?'} · {fmtBytes(step.size)} · written {fmtTime(step.mtime)}</span>
      </div>
      {header && (
        <KV rows={[
          ['Command', header.command, { mono: true }],
          ['Return code', header.return_code != null ? <Badge tone={header.return_code === 0 ? 'ok' : 'fail'}>{header.return_code}</Badge> : header.return_code_raw],
          ['Timestamp', header.timestamp],
        ]} />
      )}
      <CodeView relPath={step.rel_path} lang="log" tail={isSim || step.step === 'synthesize'} onLoaded={(d) => setHeader(d.header || null)} />
      {onOpenFile && (
        <div className="rv-actions"><LinkButton onClick={() => onOpenFile(step.rel_path, { lang: 'log', title: step.name })}>Open in drawer</LinkButton></div>
      )}
    </div>
  );
}

function SegmentDetail({ seg, onSelect }) {
  const exit = seg.exit || {};
  const toolStdout = exit.tool_stdout;
  const rows = Object.entries(exit).filter(([k]) => k !== 'tool_stdout').map(([k, v]) => [k.replace(/_/g, ' '), v]);
  const enterRows = Object.entries(seg.enter || {}).map(([k, v]) => [k.replace(/_/g, ' '), v]);
  return (
    <div className="rv-detail-seg">
      <div className="rv-detail-head">
        <h3>{seg.node}{seg.attempt != null ? ` · attempt ${seg.attempt}` : ''}</h3>
        <StatusBadge status={seg.status} />
        <span className="rv-muted">{fmtTime(seg.enter_ts)} {seg.exit_ts ? `→ ${fmtTime(seg.exit_ts)} (${fmtDuration(seg.duration_s)})` : '(still open)'}</span>
      </div>
      {rows.length > 0 ? (
        <>
          <h4>Node outcome (graph_node_exit)</h4>
          <KV rows={rows} />
        </>
      ) : (
        <Empty>No exit fields recorded for this node.</Empty>
      )}
      {enterRows.length > 0 && (
        <>
          <h4>Node inputs (graph_node_enter)</h4>
          <KV rows={enterRows} />
        </>
      )}
      {toolStdout && (
        <>
          <h4>Tool stdout (tail)</h4>
          <LongText text={toolStdout} initialChars={4000} />
        </>
      )}
      {seg.steps && seg.steps.length > 0 && (
        <>
          <h4>Steps in this node</h4>
          <div className="rv-seg-steps">
            {seg.steps.map((st) => <StepRow key={stepKey(st)} step={st} selected={false} onSelect={onSelect} />)}
          </div>
        </>
      )}
    </div>
  );
}

function EventDetail({ step }) {
  const rows = Object.entries(step.fields || {}).map(([k, v]) => [k.replace(/_/g, ' '), v]);
  return (
    <div className="rv-detail-event">
      <div className="rv-detail-head">
        <h3>{eventSummary(step)}</h3>
        <span className="rv-muted">{step.event} · {fmtTime(step.ts)}{step.node ? ` · ${step.node}` : ''}</span>
      </div>
      <KV rows={rows} />
    </div>
  );
}

/**
 * Per-block trajectory: rounds → nodes → steps (LLM calls, tool logs, engine
 * events) in chronological order, with a detail pane for the selected item.
 */
export default function BlockTrajectory({ block, onOpenFile, onOpenBlock, initialCallId, isLive }) {
  const { data, error, loading, reload } = useJson(block ? api.trajectory(block) : null, { pollMs: isLive ? 8000 : 0 });
  const [selState, setSelState] = useState(null); // { block, item }
  const sel = selState && selState.block === block ? selState.item : null;
  const setSel = useCallback((item) => setSelState(item ? { block, item } : null), [block]);
  const listRef = useRef(null);

  // Flat, ordered list of selectable items for keyboard navigation.
  const flat = useMemo(() => {
    if (!data) return [];
    const out = [];
    for (const r of data.rounds) {
      for (const s of r.segments) {
        out.push({ type: 'segment', ...s });
        for (const st of s.steps) out.push(st);
      }
    }
    for (const c of data.unattributed?.calls || []) out.push({ type: 'llm_call', ts: c.ts, ...c });
    return out;
  }, [data]);

  const keyOf = (item) => (item.type === 'segment' ? `seg:${item.seg_id}` : stepKey(item));

  // Default selection: an explicitly requested call, else the last round's
  // first LLM call (usually the uArch spec or RTL generation the architect
  // wants to read first).  Guarded on data.block so a stale payload from the
  // previous block can never pick the selection for this one.
  useEffect(() => {
    if (!data || data.block !== block || sel) return;
    if (initialCallId) {
      const c = flat.find((x) => x.type === 'llm_call' && x.call_id === initialCallId);
      if (c) { setSel(c); return; }
    }
    const last = data.rounds[data.rounds.length - 1];
    const first = last?.segments.flatMap((s) => s.steps).find((s) => s.type === 'llm_call');
    if (first) setSel(first);
  }, [data, block, flat, initialCallId, sel, setSel]);

  const onKeyDown = useCallback((e) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    if (!flat.length) return;
    e.preventDefault();
    const cur = sel ? flat.findIndex((x) => keyOf(x) === keyOf(sel)) : -1;
    const next = e.key === 'ArrowDown' ? Math.min(flat.length - 1, cur + 1) : Math.max(0, cur - 1);
    const item = flat[next];
    setSel(item);
    const el = listRef.current?.querySelector(`[data-key="${keyOf(item)}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
  }, [flat, sel]);

  if (!block) return <Empty>Select a block.</Empty>;
  if (loading && !data) return <Spinner label="Loading trajectory…" />;
  if (error) return <ErrorBanner error={error} />;
  if (!data) return null;

  const selKey = sel ? keyOf(sel) : null;
  const totalCalls = data.rounds.reduce((a, r) => a + r.n_calls, 0) + (data.unattributed?.calls?.length || 0);

  return (
    <div className="rv-traj">
      <div className="rv-traj-list" ref={listRef} tabIndex={0} onKeyDown={onKeyDown} aria-label="Trajectory steps (use arrow keys)">
        <div className="rv-traj-summary">
          <span><b>{data.rounds.length}</b> round{data.rounds.length === 1 ? '' : 's'}</span>
          <span><b>{totalCalls}</b> LLM calls</span>
          <span><b>{data.status?.rtl_attempts ?? 0}</b> RTL attempt{data.status?.rtl_attempts === 1 ? '' : 's'} (max per round)</span>
          <span>LLM time <b>{fmtDuration(data.status?.llm_time_s)}</b></span>
          <button type="button" className="rv-btn rv-btn-small" onClick={reload} title="Refresh">{'↻'}</button>
        </div>
        {data.rounds.length === 0 && <Empty>No pipeline events recorded for this block yet.</Empty>}
        {data.rounds.map((r, i) => (
          <RoundBlock key={r.round} round={r} selectedKey={selKey} onSelect={setSel} defaultOpen={i === data.rounds.length - 1 || data.rounds.length <= 2} />
        ))}
        {data.unattributed?.calls?.length > 0 && (
          <div className="rv-round rv-open">
            <div className="rv-round-head rv-round-head-static">
              <span className="rv-round-title">Calls outside any node window</span>
              <span className="rv-round-meta">{data.unattributed.calls.length} (chip-lead / helper calls attributed by name)</span>
            </div>
            <div className="rv-round-body rv-seg-steps">
              {data.unattributed.calls.map((c) => (
                <StepRow key={`call:${c.call_id}`} step={{ type: 'llm_call', ...c }} selected={selKey === `call:${c.call_id}`} onSelect={setSel} />
              ))}
            </div>
          </div>
        )}
        {data.unattributed?.tool_runs?.length > 0 && (
          <div className="rv-round rv-open">
            <div className="rv-round-head rv-round-head-static">
              <span className="rv-round-title">Step logs outside any node window</span>
            </div>
            <div className="rv-round-body rv-seg-steps">
              {data.unattributed.tool_runs.map((l) => (
                <StepRow key={`tool:${l.rel_path}`} step={{ type: 'tool_run', ...l }} selected={selKey === `tool:${l.rel_path}`} onSelect={setSel} />
              ))}
            </div>
          </div>
        )}
        {data.decisions?.length > 0 && (
          <div className="rv-round rv-open">
            <div className="rv-round-head rv-round-head-static">
              <span className="rv-round-title">Chip-lead decisions touching {blockLabel(block)}</span>
            </div>
            <div className="rv-round-body">
              {data.decisions.map((d) => (
                <div key={d.decision_index} className="rv-decision-mini">
                  <Badge tone={/approve|override|fix/.test(d.action || '') ? 'ok' : 'warn'}>{d.action}</Badge>
                  <span className="rv-muted">{d.type} · {fmtTime(d.ts)}</span>
                  {d.call_id && <LinkButton onClick={() => setSel({ type: 'llm_call', call_id: d.call_id, ts: d.ts, run_name: `Chip Lead decision #${d.decision_index}` })}>open call</LinkButton>}
                  <div className="rv-decision-reason">{d.reasoning}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="rv-traj-detail">
        {!sel && <Empty icon={'☜'}>Select a node, LLM call, or tool log on the left. Arrow keys move the selection.</Empty>}
        {sel && sel.type === 'llm_call' && <LLMCallViewer callId={sel.call_id} onOpenFile={onOpenFile} onOpenBlock={onOpenBlock} embedded />}
        {sel && sel.type === 'tool_run' && <ToolRunDetail step={sel} onOpenFile={onOpenFile} />}
        {sel && sel.type === 'segment' && <SegmentDetail seg={sel} onSelect={setSel} />}
        {sel && sel.type === 'event' && <EventDetail step={sel} />}
      </div>
    </div>
  );
}
