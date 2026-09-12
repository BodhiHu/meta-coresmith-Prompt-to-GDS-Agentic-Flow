import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import RunOverview from '../RunOverview';
import BlockTrajectory from '../BlockTrajectory';
import LLMCallViewer from '../LLMCallViewer';

/* ── Canned API payloads (shape mirrors webview_loaders.py) ── */

const OVERVIEW = {
  project_root: '/runs/x', design_name: 'chip_top', pipeline_start: 1000, pipeline_end: 5000, wall_time_s: 4000,
  is_live: false, engine_sha: 'abc123', model: 'gpt', provider: 'codex_cli',
  totals: {
    blocks: 2, status_counts: { passed: 1, incomplete: 1 }, llm_calls: 3, llm_time_s: 120, timeouts: 0, errors: 0,
    usage: { input_tokens: 1000, cached_input_tokens: 500, output_tokens: 100, reasoning_output_tokens: 10 },
    by_model: { gpt: 3 }, codex_sessions: 3, codex_commands: 9, codex_file_changes: 4, events: 40,
    decisions: 1, interrupts: 1, rounds: 3,
  },
  tiers: [{ tier: '1', blocks: ['alpha_engine', 'beta_fifo'] }],
  blocks: [
    { name: 'alpha_engine', tier: '1', subsystem: 'core', status: 'passed', rounds: 2, rtl_attempts: 2, llm_calls: 2, llm_time_s: 100,
      dv: { passed: true, tests_passed: 5, tests_total: 5 }, coverage_pct: 93.5, coverage_passed: true,
      throughput: { applicable: true, passed: true, measured: 2155 }, gate_sim: 'not_run', conformance_ok: true, mem_price_ok: true,
      cells: 11495, ff: 2688, area_um2: 133740, wns_ns: 14.2, ppa_ok: 1, budgets: { estimated_gates: 40000, flip_flop_budget: 5000, area_budget_um2: 250000 } },
    { name: 'beta_fifo', tier: '1', subsystem: 'mem', status: 'incomplete', rounds: 1, rtl_attempts: 1, llm_calls: 1, llm_time_s: 20,
      dv: { passed: null }, coverage_pct: null, throughput: { applicable: false }, gate_sim: null, conformance_ok: null, mem_price_ok: null,
      cells: null, ff: null, area_um2: null, wns_ns: -0.5, ppa_ok: null, budgets: {} },
  ],
  signoff: null,
};

const DECISIONS = {
  pipeline_start: 1000,
  decisions: [{ decision_index: 1, ts: 2000, type: 'uarch_integration_review', action: 'revise', blocks: ['alpha_engine'],
                reasoning: 'ports mismatch', call_id: 7, duration_s: 50 }],
  interrupts: [{ ts: 2500, end_ts: 2600, kind: 'node', node: 'Ask Human', block: 'alpha_engine', round: 2, action: 'fix_rtl', status: 'done' }],
};

const INTEGRATION = { integration_result: null, chip_throughput: null, final_report: null, carried_forward_defects: [], logs: [],
                      segments: [{ seg_id: 1, node: 'Integration Review', tier: '1', enter_ts: 1500, exit_ts: 2000, duration_s: 500,
                                   status: 'done', exit: { action: 'revise', issues_found: 1 }, calls: [{ call_id: 7, run_name: 'Chip Lead' }] }] };

const SETTINGS = { engine_sha: 'abc123', settings: { engine_sha: 'abc123' }, daemon: {}, env: [{ key: 'K', value: 'v' }], has_sqlite: true, original_roots: [] };

const TRAJ = {
  block: 'alpha_engine', meta: { name: 'alpha_engine' }, status: { rounds: 2, rtl_attempts: 2, llm_calls: 2, llm_time_s: 100 },
  rounds: [
    { round: 1, start_ts: 1000, end_ts: 1500, duration_s: 500, outcome: 'failed', decision: 'escalate', tier: '1', n_calls: 1, llm_time_s: 60,
      segments: [
        { seg_id: 1, node: 'Init Block', enter_ts: 1000, exit_ts: 1001, duration_s: 1, attempt: null, status: 'done', exit: {}, steps: [], n_calls: 0, n_tool_runs: 0 },
        { seg_id: 2, node: 'Generate RTL', enter_ts: 1001, exit_ts: 1100, duration_s: 99, attempt: 1, status: 'done', exit: { lint_clean: true },
          steps: [{ type: 'llm_call', call_id: 1, ts: 1001, run_name: 'Generate Verilog [Alpha Engine]', model: 'gpt', duration_s: 60, status: 'ok',
                    usage: { output_tokens: 1200 }, n_commands: 3, n_file_changes: 1 }], n_calls: 1, n_tool_runs: 0 },
        { seg_id: 3, node: 'Synthesize', enter_ts: 1100, exit_ts: 1110, duration_s: 10, attempt: null, status: 'failed', exit: { success: false, gate_count: 0 },
          steps: [{ type: 'tool_run', name: 'synthesize_attempt1.log', rel_path: '.coresmith/step_logs/alpha_engine/synthesize_attempt1.log', step: 'synthesize', attempt: 1, mtime: 1109, size: 2048 }], n_calls: 0, n_tool_runs: 1 },
      ] },
    { round: 2, start_ts: 2000, end_ts: 2500, duration_s: 500, outcome: 'passed', decision: null, tier: '1', n_calls: 1, llm_time_s: 40,
      segments: [
        { seg_id: 4, node: 'Init Block', enter_ts: 2000, exit_ts: 2001, duration_s: 1, attempt: null, status: 'done', exit: {}, steps: [], n_calls: 0, n_tool_runs: 0 },
        { seg_id: 5, node: 'Generate RTL', enter_ts: 2001, exit_ts: 2100, duration_s: 99, attempt: 1, status: 'done', exit: { lint_clean: true },
          steps: [{ type: 'llm_call', call_id: 2, ts: 2001, run_name: 'Generate Verilog [Alpha Engine] round 2', model: 'gpt', duration_s: 40, status: 'ok', usage: {} }], n_calls: 1, n_tool_runs: 0 },
      ] },
  ],
  unattributed: { calls: [], tool_runs: [] }, decisions: [], is_live: false,
};

const CALL = (id, name) => ({
  call_id: id, run_name: name, status: 'ok', model: 'gpt', provider: 'codex_cli', duration_s: 60, start_ts: 1001, ts: 1061,
  block: 'alpha_engine', node: 'Generate RTL', round: 1, attempt: 1,
  usage: { input_tokens: 100, cached_input_tokens: 50, output_tokens: 20, reasoning_output_tokens: 5 },
  system_prompt: 'SYSTEM PROMPT TEXT', user_prompt: 'USER PROMPT TEXT', response: 'RESPONSE TEXT',
  system_prompt_len: 18, user_prompt_len: 16, response_len: 13, timeout_s: 900, session_id: 'thread-1', files_changed: ['rtl/a.v'],
  session: { key: '1:1', thread_id: 'thread-1', pid: 1, turn_count: 1, n_items: 3 },
  turns: [
    { index: 0, id: 'i0', kind: 'agent_message', text: 'I will write the RTL', state: 'completed' },
    { index: 1, id: 'i1', kind: 'command_execution', command: '/bin/bash -lc "verilator --lint-only a.v"', exit_code: 1, status: 'completed',
      output: 'HEAD... [elided] ...TAIL', output_len: 50000, truncated: true, state: 'completed' },
    { index: 2, id: 'i2', kind: 'file_change', changes: [{ path: '/orig/rtl/a.v', kind: 'add', rel_path: 'rtl/a.v', exists: true }], state: 'completed' },
  ],
  segment: { seg_id: 2, node: 'Generate RTL', status: 'done' },
});

const TEXT = { rel_path: '.coresmith/step_logs/alpha_engine/synthesize_attempt1.log', size: 2048, mtime: 1109, total_lines: 3, offset: 0, count: 3,
               lines: ['=== SYNTHESIZE LOG ===', 'Return code: 1', 'ERROR: syntax error'], header: { command: 'yosys -s x.ys', return_code: 1 }, eof: true };

function mockFetch(routes) {
  return jest.fn((url) => {
    const key = Object.keys(routes).find((k) => (k.endsWith('*') ? url.startsWith(k.slice(0, -1)) : url === k));
    if (!key) return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({ error: `no route ${url}` }) });
    const payload = typeof routes[key] === 'function' ? routes[key](url) : routes[key];
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
  });
}

beforeAll(() => {
  Element.prototype.scrollIntoView = jest.fn();
});

afterEach(() => {
  delete global.fetch;
});

describe('RunOverview', () => {
  test('renders totals, the block table and navigates on click', async () => {
    global.fetch = mockFetch({
      '/api/run/overview': OVERVIEW, '/api/run/decisions': DECISIONS, '/api/run/integration': INTEGRATION, '/api/run/settings': SETTINGS,
    });
    const onOpenBlock = jest.fn();
    const onOpenCall = jest.fn();
    render(<RunOverview onOpenBlock={onOpenBlock} onOpenCall={onOpenCall} onOpenFile={jest.fn()} />);
    await screen.findAllByText('alpha engine');
    expect(screen.getByText('1/2')).toBeInTheDocument(); // blocks passed tile
    expect(screen.getByText('11,495')).toBeInTheDocument();
    expect(screen.getByText('14.2')).toBeInTheDocument();
    expect(document.querySelector('.rv-fail-text').textContent).toBe('-0.5');
    fireEvent.click(document.querySelector('.rv-table .rv-block-link'));
    expect(onOpenBlock).toHaveBeenCalledWith('alpha_engine', 'design');
    await screen.findByText('ports mismatch');
    fireEvent.click(screen.getAllByText(/call #7/)[0]);
    expect(onOpenCall).toHaveBeenCalledWith(7);
    expect(screen.getByText('Ask Human')).toBeInTheDocument();
  });

  test('shows the server error instead of crashing', async () => {
    global.fetch = mockFetch({});
    render(<RunOverview onOpenBlock={jest.fn()} onOpenCall={jest.fn()} onOpenFile={jest.fn()} />);
    await screen.findByText(/no route \/api\/run\/overview/);
  });
});

describe('BlockTrajectory', () => {
  test('lists rounds and nodes in order, scoped to one block, and opens call / tool detail', async () => {
    const calls = { 1: CALL(1, 'Generate Verilog [Alpha Engine]'), 2: CALL(2, 'Generate Verilog [Alpha Engine] round 2') };
    global.fetch = mockFetch({
      '/api/block/alpha_engine/trajectory': TRAJ,
      '/api/llm_call/*': (url) => calls[url.split('/').pop()],
      '/api/text?*': TEXT,
    });
    render(<BlockTrajectory block="alpha_engine" onOpenFile={jest.fn()} onOpenBlock={jest.fn()} />);
    await screen.findByText('Round 1');
    expect(screen.getByText('Round 2')).toBeInTheDocument();
    // Node order inside round 1
    const nodes = Array.from(document.querySelectorAll('.rv-round')[0].querySelectorAll('.rv-seg-node')).map((n) => n.textContent);
    expect(nodes).toEqual(['Init Block', 'Generate RTL', 'Synthesize']);
    expect(screen.getByText('route: escalate')).toBeInTheDocument();
    // Default selection is the last round's first LLM call -> verbose viewer loads it
    await screen.findByText('SYSTEM PROMPT TEXT', { exact: false }).catch(() => null);
    await waitFor(() => expect(global.fetch).toHaveBeenCalledWith('/api/llm_call/2', expect.anything()));
    await screen.findByText('USER PROMPT TEXT');
    // Click the tool run -> log viewer with tail paging
    fireEvent.click(screen.getByText('synthesize_attempt1.log'));
    await screen.findByText('ERROR: syntax error');
    expect(global.fetch.mock.calls.some(([u]) => u.includes('/api/text?path=.coresmith%2Fstep_logs%2Falpha_engine%2Fsynthesize_attempt1.log') && u.includes('tail=1'))).toBe(true);
    // Click a node header -> outcome table
    fireEvent.click(screen.getByText('Synthesize'));
    await screen.findByText('Node outcome (graph_node_exit)');
    expect(screen.getByText('gate count')).toBeInTheDocument();
  });

  test('keyboard navigation moves the selection', async () => {
    global.fetch = mockFetch({ '/api/block/alpha_engine/trajectory': TRAJ, '/api/llm_call/*': (url) => CALL(Number(url.split('/').pop()), 'x'), '/api/text?*': TEXT });
    render(<BlockTrajectory block="alpha_engine" onOpenFile={jest.fn()} />);
    await screen.findByText('Round 1');
    const list = document.querySelector('.rv-traj-list');
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    fireEvent.keyDown(list, { key: 'ArrowDown' });
    await waitFor(() => expect(document.querySelectorAll('.rv-step-selected').length).toBe(1));
  });
});

describe('LLMCallViewer', () => {
  test('shows prompts, transcript with filters, truncated output loading, and file links', async () => {
    const full = { index: 1, kind: 'command_execution', command: 'x', exit_code: 1, output: 'FULL OUTPUT', output_len: 11, truncated: false };
    global.fetch = mockFetch({ '/api/llm_call/1': CALL(1, 'Generate Verilog [Alpha Engine]'), '/api/llm_call/1/turn/1': full });
    const onOpenFile = jest.fn();
    render(<LLMCallViewer callId={1} onOpenFile={onOpenFile} />);
    await screen.findByText('Generate Verilog [Alpha Engine]');
    expect(screen.getByText('USER PROMPT TEXT')).toBeInTheDocument();
    expect(screen.queryByText('SYSTEM PROMPT TEXT')).toBeNull(); // collapsed by default
    fireEvent.click(screen.getByText('System prompt'));
    expect(screen.getByText('SYSTEM PROMPT TEXT')).toBeInTheDocument();
    expect(screen.getByText('rc=1')).toBeInTheDocument();
    // Failed command is expanded by default with the truncated output
    expect(screen.getByText(/HEAD\.\.\. \[elided\]/)).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Load full output/));
    await screen.findByText('FULL OUTPUT');
    // File chip opens the file
    fireEvent.click(screen.getByText('rtl/a.v'));
    expect(onOpenFile).toHaveBeenCalledWith('rtl/a.v');
    // Filter to messages only
    fireEvent.click(screen.getByRole('button', { name: /^message/ }));
    expect(screen.getByText('I will write the RTL')).toBeInTheDocument();
    expect(screen.queryByText('rc=1')).toBeNull();
  });

  test('explains missing transcripts for older runs', async () => {
    const c = { ...CALL(3, 'Generate Testbench [Beta]'), session: null, turns: [] };
    global.fetch = mockFetch({ '/api/llm_call/3': c });
    render(<LLMCallViewer callId={3} />);
    await screen.findByText(/did not persist a Codex turn stream/);
  });
});
