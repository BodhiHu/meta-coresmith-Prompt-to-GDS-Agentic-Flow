import React, { useState, useMemo, useCallback } from 'react';
import { api, useJson, getJson } from '../../utils/api';
import { fmtDuration, fmtTokens, fmtNum, fmtDateTime, blockLabel } from '../../utils/format';
import { highlightMarkdown } from '../../utils/highlight';
import { Badge, StatusBadge, Collapsible, KV, LongText, ErrorBanner, Spinner, CopyButton, LinkButton, Empty } from './common';

const KIND_META = {
  agent_message: { label: 'Message', icon: '💬' },
  reasoning: { label: 'Reasoning', icon: '🧠' },
  command_execution: { label: 'Command', icon: '$' },
  file_change: { label: 'File change', icon: '✎' },
  todo_list: { label: 'Plan', icon: '☑' },
  web_search: { label: 'Web search', icon: '🔍' },
  error: { label: 'Error', icon: '✗' },
};

function shortCommand(cmd) {
  if (!cmd) return '';
  // Codex wraps everything in `/bin/bash -lc "<script>"`; show the script.
  const m = cmd.match(/^\/bin\/(?:ba)?sh\s+-lc\s+(['"])([\s\S]*)\1$/);
  return m ? m[2] : cmd;
}

function TurnCard({ turn, callId, index, onOpenFile, defaultOpen }) {
  const meta = KIND_META[turn.kind] || { label: turn.kind, icon: '·' };
  const [output, setOutput] = useState(turn.output);
  const [truncated, setTruncated] = useState(!!turn.truncated);
  const [loadingFull, setLoadingFull] = useState(false);
  const failed = turn.kind === 'command_execution' && turn.exit_code != null && turn.exit_code !== 0;
  const [open, setOpen] = useState(defaultOpen ?? (failed || (turn.kind === 'command_execution' && (turn.output_len || 0) <= 1500)));

  const loadFull = useCallback(() => {
    setLoadingFull(true);
    getJson(api.turn(callId, turn.index))
      .then((full) => { setOutput(full.output); setTruncated(false); })
      .catch(() => {})
      .finally(() => setLoadingFull(false));
  }, [callId, turn.index]);

  return (
    <div className={`rv-turn rv-turn-${turn.kind} ${failed ? 'rv-turn-failed' : ''} ${turn.state !== 'completed' ? 'rv-turn-inprogress' : ''}`} id={`turn-${index}`}>
      <div className="rv-turn-head">
        <span className="rv-turn-idx">{index + 1}</span>
        <span className="rv-turn-icon" aria-hidden="true">{meta.icon}</span>
        <span className="rv-turn-kind">{meta.label}</span>
        {turn.kind === 'command_execution' && (
          <>
            <code className="rv-turn-cmd" title={turn.command}>{shortCommand(turn.command)}</code>
            <span className="rv-turn-right">
              {turn.exit_code != null && <Badge tone={turn.exit_code === 0 ? 'ok' : 'fail'}>rc={turn.exit_code}</Badge>}
              {turn.state !== 'completed' && <Badge tone="running">in progress</Badge>}
              <span className="rv-muted">{fmtNum(turn.output_len || 0)} chars</span>
              <button type="button" className="rv-btn rv-btn-small" onClick={() => setOpen((v) => !v)}>{open ? 'Hide output' : 'Show output'}</button>
              <CopyButton text={shortCommand(turn.command)} label="Copy cmd" />
            </span>
          </>
        )}
        {turn.kind === 'file_change' && (
          <span className="rv-turn-files">
            {(turn.changes || []).map((c, i) => (
              <span key={i} className="rv-file-chip">
                <Badge tone={c.kind === 'delete' ? 'fail' : c.kind === 'add' ? 'ok' : 'info'}>{c.kind}</Badge>
                {c.exists && onOpenFile ? (
                  <LinkButton onClick={() => onOpenFile(c.rel_path)} title="Open the file as it exists in the run directory now">{c.rel_path}</LinkButton>
                ) : (
                  <span className="rv-muted" title="This path is no longer present in the run directory (codex workspace was cleaned up)">{c.path}</span>
                )}
              </span>
            ))}
          </span>
        )}
        {turn.kind === 'web_search' && <span className="rv-turn-text">{turn.query}</span>}
        {turn.kind === 'error' && <span className="rv-turn-text rv-fail-text">{turn.message}</span>}
      </div>
      {turn.kind === 'agent_message' && (
        <div className="rv-turn-body rv-prose" dangerouslySetInnerHTML={{ __html: highlightMarkdown(turn.text || '') }} />
      )}
      {turn.kind === 'reasoning' && (
        <div className="rv-turn-body rv-prose rv-reasoning">{turn.text}</div>
      )}
      {turn.kind === 'todo_list' && (
        <ul className="rv-todo">
          {(turn.items || []).map((it, i) => (
            <li key={i} className={it.completed ? 'rv-todo-done' : ''}>{it.completed ? '☑' : '☐'} {it.text}</li>
          ))}
        </ul>
      )}
      {turn.kind === 'command_execution' && open && (
        <div className="rv-turn-output">
          {output ? <pre className="rv-mono rv-wrap">{output}</pre> : <span className="rv-muted">(no output)</span>}
          {truncated && (
            <button type="button" className="rv-btn rv-btn-small" onClick={loadFull} disabled={loadingFull}>
              {loadingFull ? 'Loading…' : `Load full output (${fmtNum(turn.output_len)} chars)`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Verbose viewer for one LLM call: prompts, agent transcript (commands with
 * output, file writes, messages), response, usage, and links to the graph
 * node / block it belongs to.
 */
export default function LLMCallViewer({ callId, onOpenFile, onOpenBlock, embedded }) {
  const { data, error, loading } = useJson(callId ? api.call(callId) : null);
  const [filter, setFilter] = useState('all');
  const [q, setQ] = useState('');

  const turns = useMemo(() => {
    if (!data) return [];
    const ql = q.trim().toLowerCase();
    return data.turns.filter((t) => {
      if (filter === 'command' && t.kind !== 'command_execution') return false;
      if (filter === 'file' && t.kind !== 'file_change') return false;
      if (filter === 'message' && t.kind !== 'agent_message' && t.kind !== 'reasoning') return false;
      if (filter === 'failed' && !(t.kind === 'command_execution' && t.exit_code != null && t.exit_code !== 0)) return false;
      if (ql) {
        const hay = [t.command, t.output, t.text, t.query, (t.changes || []).map((c) => c.path).join(' ')].filter(Boolean).join('\n').toLowerCase();
        if (!hay.includes(ql)) return false;
      }
      return true;
    });
  }, [data, filter, q]);

  if (!callId) return <Empty icon={'✦'}>Select an LLM call to inspect it.</Empty>;
  if (loading && !data) return <Spinner label="Loading LLM call…" />;
  if (error) return <ErrorBanner error={error} />;
  if (!data) return null;

  const u = data.usage || {};
  const failedCmds = data.turns.filter((t) => t.kind === 'command_execution' && t.exit_code != null && t.exit_code !== 0).length;
  const counts = {
    all: data.turns.length,
    message: data.turns.filter((t) => t.kind === 'agent_message' || t.kind === 'reasoning').length,
    command: data.turns.filter((t) => t.kind === 'command_execution').length,
    file: data.turns.filter((t) => t.kind === 'file_change').length,
    failed: failedCmds,
  };

  return (
    <div className={`rv-call ${embedded ? 'rv-call-embedded' : ''}`}>
      <div className="rv-call-head">
        <div className="rv-call-title">
          <span className="rv-call-name">{data.run_name || `LLM call #${data.call_id}`}</span>
          <StatusBadge status={data.status} />
          {data.timed_out && <Badge tone="fail">timed out after {fmtDuration(data.timeout_s)}</Badge>}
        </div>
        <div className="rv-call-meta">
          <Badge tone="info" title="call id (line in llm_calls.jsonl)">#{data.call_id}</Badge>
          <span className="rv-meta-item"><b>{data.model}</b> · {data.provider}</span>
          <span className="rv-meta-item">{fmtDuration(data.duration_s)}</span>
          <span className="rv-meta-item" title={`started ${fmtDateTime(data.start_ts)}`}>started {fmtDateTime(data.start_ts)}</span>
          <span className="rv-meta-item" title="input tokens (cached) / output tokens (reasoning)">
            in {fmtTokens(u.input_tokens)}{u.cached_input_tokens != null ? ` (${fmtTokens(u.cached_input_tokens)} cached)` : ''}
            {' · '}out {fmtTokens(u.output_tokens)}{u.reasoning_output_tokens != null ? ` (${fmtTokens(u.reasoning_output_tokens)} reasoning)` : ''}
          </span>
        </div>
        <div className="rv-call-context">
          {data.block && (
            <LinkButton onClick={onOpenBlock ? () => onOpenBlock(data.block, 'trajectory') : undefined} title="Open this block's trajectory">
              block: <b>{blockLabel(data.block)}</b>
            </LinkButton>
          )}
          {data.node && <span className="rv-meta-item">node: <b>{data.node}</b></span>}
          {data.round != null && <span className="rv-meta-item">round {data.round}</span>}
          {data.attempt != null && <span className="rv-meta-item">attempt {data.attempt}</span>}
          {data.segment && <span className="rv-meta-item">node outcome: <StatusBadge status={data.segment.status} /></span>}
        </div>
        {data.error && <ErrorBanner error={data.error} prefix="LLM error" />}
        {data.timed_out && !data.error && (
          <div className="rv-error-banner">The call exceeded its {fmtDuration(data.timeout_s)} timeout; the response below is whatever the engine captured.</div>
        )}
      </div>

      <Collapsible title="System prompt" subtitle={`${fmtNum(data.system_prompt.length)} chars`} defaultOpen={false}>
        <LongText text={data.system_prompt} highlighter={highlightMarkdown} initialChars={6000} />
      </Collapsible>

      <Collapsible title="User prompt" subtitle={`${fmtNum(data.user_prompt.length)} chars`} defaultOpen>
        <LongText text={data.user_prompt} highlighter={highlightMarkdown} initialChars={6000} />
      </Collapsible>

      <Collapsible
        title="Agent transcript"
        subtitle={data.session
          ? `${counts.all} items · ${counts.command} commands${failedCmds ? ` (${failedCmds} failed)` : ''} · ${counts.file} file changes · ${counts.message} messages`
          : 'no codex transcript recorded for this call'}
        defaultOpen
        right={data.session && (
          <span className="rv-filter-bar">
            {['all', 'message', 'command', 'failed', 'file'].map((k) => (
              <button key={k} type="button" className={`rv-chip ${filter === k ? 'rv-chip-active' : ''}`} onClick={() => setFilter(k)}>
                {k} <span className="rv-chip-count">{counts[k]}</span>
              </button>
            ))}
            <input className="rv-input" placeholder="Filter transcript…" value={q} onChange={(e) => setQ(e.target.value)} />
          </span>
        )}
      >
        {!data.session && (
          <Empty>
            This run did not persist a Codex turn stream for this call (older engine, or a non-codex provider),
            so only the prompt and final response are available.
          </Empty>
        )}
        {data.session && turns.length === 0 && <Empty>No transcript items match the current filter.</Empty>}
        {turns.map((t) => (
          <TurnCard key={t.id || t.index} turn={t} callId={data.call_id} index={t.index} onOpenFile={onOpenFile} />
        ))}
      </Collapsible>

      <Collapsible title="Response" subtitle={`${fmtNum(data.response.length)} chars`} defaultOpen>
        {data.response ? (
          <LongText text={data.response} highlighter={highlightMarkdown} initialChars={12000} />
        ) : (
          <Empty>No response text recorded.</Empty>
        )}
      </Collapsible>

      <Collapsible title="Usage & session" defaultOpen={false}>
        <KV rows={[
          ['Model', data.model], ['Provider', data.provider], ['Graph', data.graph],
          ['Duration', fmtDuration(data.duration_s)], ['Timeout', data.timeout_s != null ? fmtDuration(data.timeout_s) : null],
          ['Input tokens', u.input_tokens], ['Cached input tokens', u.cached_input_tokens],
          ['Output tokens', u.output_tokens], ['Reasoning tokens', u.reasoning_output_tokens],
          ['Session id', data.session_id, { mono: true }],
          ['Codex pid', data.session?.pid], ['Codex turns', data.session?.turn_count],
          ['System prompt', `${fmtNum(data.system_prompt_len)} chars`], ['User prompt', `${fmtNum(data.user_prompt_len)} chars`],
          ['Response', `${fmtNum(data.response_len)} chars`],
          ['Files touched', data.files_changed],
        ]} />
      </Collapsible>
    </div>
  );
}
