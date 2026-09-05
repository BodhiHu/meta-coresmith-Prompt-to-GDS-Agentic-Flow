# Run-review web UI

`orchestrator/vscode-ext/serve.py` renders a CoreSmith run directory as a
read-only dashboard so an architect can judge **both** the trajectory (what the
LLM did, node by node) and the resulting design (spec, RTL, testbench, DV,
synthesis, timing, gates) without opening files by hand.

```bash
cd orchestrator/vscode-ext && npm install && npm run build      # once
CORESMITH_PROJECT_ROOT=<run dir> python orchestrator/vscode-ext/serve.py --port 3000
```

The server never writes to the run directory: SQLite is opened with
`file:...?mode=ro` (falling back to `immutable=1` for a WAL database copied
without its sidecars) and every other input is read as text. It works on a
live run (it polls) and on a snapshot copied elsewhere (absolute paths inside
the logs are re-anchored onto the served directory).

## Views

| Tab | What it shows | Source |
| --- | --- | --- |
| **Overview** | Run header (design, engine SHA, model/provider, wall time, live/historical), stat tiles (blocks passed, LLM calls + summed LLM time, tokens in/cached/out/reasoning, agent commands + file writes, rounds, chip-lead decisions, interrupts), the **block table** (tier, subsystem, status, rounds, RTL attempts, LLM calls, DV, coverage, throughput, cells/FF/area vs budgets, WNS, PPA, gate-sim, contract, mem-price), tier strip, **integration & validation** (final signoff, `integration_result.json`, chip throughput, every tier-level node with its outcome and chip-lead calls, integration logs, carried-forward defects), **chip-lead decisions** (reasoning, blocks touched, link to the call that produced them), **interrupt history**, engine/settings/env (secrets redacted). | `/api/run/*` |
| **Blocks** | Left rail: blocks grouped by tier with status dots. Everything on the right is scoped to **one block**. | `/api/run/blocks` |
| Blocks → **Trajectory** | Rounds (one per `Init Block` re-entry, i.e. after a tier-level integration review says *revise*) → graph nodes in execution order (`Init Block → Generate Uarch Spec → Review Uarch Spec → Generate RTL (attempt n) → Generate Testbench → Synthesize → Gate Sim → Diagnose Failure → Route Decision → Ask Human → Block Done`) → steps inside each node: LLM calls, tool logs, engine events. Each node shows its outcome chips (`lint clean ✓`, `sim passed ✓`, `success ✗`, `category: LOGIC_ERROR`, `decision: retry_rtl`, …). Selecting a step opens the detail pane: the **verbose call viewer**, a paged log viewer (tail-first for simulate/synth), the node's full `graph_node_exit` payload, or an event's fields. Arrow keys move the selection. | `/api/block/<b>/trajectory` |
| Blocks → **Design & results** | One long review page with a sticky mini-TOC: Summary (tiles + **gates table** with plain pass/fail + budgets), uArch spec (all versions: codex-workspace drafts and the canonical spec, with diff), RTL (current + `rtl_backup_attemptN.v` versions with unified diff, lint summary), Testbench (cocotb test + model), Simulation (test table from the latest simulate log, coverage, throughput, gate-sim verdict, DV / coverage history from sqlite), Synthesis (cells/FF/area vs budgets, std-cell vs memory-macro area, macro inventory, `mem_price` memory ledger, PPA checks and history, Yosys cell-type table, links to report/netlist/script/logs), Timing (period, WNS, TNS, Fmax, per-synthesis WNS, worst paths *when persisted*), Issues (diagnoses per round from the event stream, failed attempts, constraints, `previous_error.txt`, contract conformance, carried-forward defects, failure signature), Decisions & interrupts touching the block, interface contracts. | `/api/block/<b>/review` |
| **Verbose call viewer** (drawer, or the trajectory detail pane) | Model, provider, duration, start time, token usage (input/cached/output/reasoning), block/node/round/attempt links; full **system prompt** and **user prompt** (collapsible, searchable with match navigation, copy); the **agent transcript**: every Codex item in order — messages, commands (`$ …` with exit code and captured output; long outputs are truncated with a *load full output* button), file writes (linked to the file as it exists now in the run), plans, web searches; the **response**; usage/session table. Filters: all / message / command / failed / file + free-text. | `/api/llm_call/<id>`, `/api/llm_call/<id>/turn/<n>` |
| **Timeline / Architecture / Frontend / Backend / Block Diagram / Collateral** | The pre-existing views. The Timeline detail panel is now scoped to the clicked row's block (`?block=`), offers a block selector when a graph node is opened without block context, and links each call to the verbose viewer (*Verbose*) and to the Blocks view (*Open block*). | `/api/timeline`, `/api/node_trajectory/<node>?block=` |

Dark and light themes follow the existing theme toggle.

## Where each number comes from

| Number | Source |
| --- | --- |
| Block identity, tier, subsystem, budgets (`estimated_gates`, `flip_flop_budget`, `area_budget_um2`), interfaces | `project.sqlite` table `blocks` (fallback: `.coresmith/block_diagram.json`, `block_specs.json`) |
| Rounds, node order, attempts, node outcomes, durations | `.coresmith/pipeline_events.jsonl` (`graph_node_enter` / `graph_node_exit`; `Route Decision`, `Block Done`, `Advance Tier` are exit-only and shown as instantaneous nodes). A round starts at every `Init Block` enter. |
| LLM call → block / node / round | `llm_calls.jsonl` record window `[ts − duration_s, ts]` inside the block's node window; the block named in `run_name` (`Generate Verilog [Cavlc Macroblock Encoder]`) must match, which keeps parallel blocks apart. Calls outside any window (chip lead, helpers) are attributed by name only. |
| Agent transcript | `codex_turns.jsonl`, grouped by `(pid, wall_start)` and joined to the call by `usage.session_id == thread.started.thread_id` (fallback: `wall_start` within 6 s of the call start). |
| Tokens, duration, timeout, error | `llm_calls.jsonl` (`usage`, `duration_s`, `timed_out`, `error`) |
| Chip-lead decisions | `.coresmith/chip_lead/decisions.jsonl` (reasoning; no timestamps) paired by `decision_index` with `chip_lead_decision` events (timestamps); the producing call is the `Chip Lead [...]` LLM call that ended just before the event. Blocks touched: explicit `block_name`, the HITL node open at that time, or block names mentioned in the reasoning. |
| Interrupts | `Review Uarch Spec` / `Ask Human` / `PPA Gate Unmeasurable` node segments plus `interrupt`, `uarch_feasibility_*`, `gate_failed`, `escalation_response` events |
| Step logs (lint / simulate / synthesize / dv_seed / sim_timeout) | `.coresmith/step_logs/<block>/<step>_attemptN.log`, attached to the node whose window contains the file mtime. **The engine keeps one file per attempt number**, so a later round overwrites the previous round's log; only the newest survives. |
| Tests passed/total | `best_result.json` / sqlite `results(kind='best')`; the per-test table is parsed from the latest simulate log (`** TESTS=… **`). History: sqlite `dv_results`. |
| Coverage, throughput, gate sim, conformance, memory ledger | `.coresmith/blocks/<b>/{coverage,throughput,gate_sim_report,contract_conformance,mem_price}.json` |
| Cells, FF, area, WNS per synthesis | sqlite `ppa_history` (`source='gate', probe='synth'`; agent probes are shown in the history table). Fallback when there is no sqlite: Yosys `stat` parse of `syn/output/<b>/<b>_report.txt`. |
| Cell types, macro inventory, sequential area | Yosys `stat` section of `syn/output/<b>/<b>_report.txt` (last `Printing statistics` block; macros = cell types that are neither `sky130_*` nor `$…`). |
| Clock period | `syn/output/<b>/<b>.sdc` (`create_clock -period`) or `ppa_report.json` |
| Fmax | `1000 / (period − WNS)` |
| PPA checks (actual / budget / limit) | `.coresmith/blocks/<b>/ppa_report.json` (only written when a check fails) |
| Diagnoses per round | `Diagnose Failure` exit events (`category`, `confidence`, `suggested_fix`, `diagnosis_preview`, `failure_signature`, `repeat_count`); the current round's full diagnosis from sqlite `diagnoses` / `diagnosis.json` |
| Signoff, integration DV, validation DV | `final_report.json`, `.coresmith/integration_result.json`, `chip_throughput.json`, tier-level node exits (`Integration DV`, `Validation DV`, `Contract Audit`, `Final Report`) |
| Engine SHA, settings | sqlite `settings` (`engine_sha`), `.coresmith/engine_sha.json`, `final_report.json` |

## Endpoints

All are `GET`, JSON, read-only. Paths are validated against the run root
(`..`, absolute paths and symlink escapes are rejected with 404).

| Endpoint | Returns |
| --- | --- |
| `/api/run/overview` | totals, tiers, block table, signoff, engine/model |
| `/api/run/blocks` | block table only (cheap; polled by the Blocks rail) |
| `/api/run/decisions` | chip-lead decisions + interrupt history |
| `/api/run/integration` | tier-level nodes, integration/validation artefacts, logs |
| `/api/run/settings` | engine SHA, sqlite settings, daemon.json, env (redacted) |
| `/api/block/<b>/trajectory` | rounds → nodes → steps (call summaries only; details are lazy) |
| `/api/block/<b>/review` | the design-review payload |
| `/api/block/<b>/files` | RTL / TB / spec files and their versions |
| `/api/llm_calls?block=&node=` | call summaries |
| `/api/llm_call/<id>` | full call: prompts, response, transcript (command outputs truncated to 4 KB head+tail) |
| `/api/llm_call/<id>/turn/<n>` | one transcript item in full |
| `/api/text?path=&offset=&limit=&tail=1` | paged lines of any file in the run (max 5000 lines per page) |
| `/api/text_search?path=&q=` | line numbers matching `q` (case-insensitive, first 200) |
| `/api/diff?a=&b=` | unified diff between two files in the run |
| `/api/node_trajectory/<node>?block=` | legacy per-node trajectory used by the Timeline / graph detail panel, now block-scoped |
| `/api/artifacts/<rel>` | raw file |

`<id>` is the 1-based line number of the record in `llm_calls.jsonl`.

## Tests

* `orchestrator/tests/test_webview_loaders.py` — parsers (Yosys stat in both
  layouts, OpenSTA `report_wns/tns/checks`, cocotb, Verilator), the
  event→rounds index, parallel-block isolation, codex-session joins, path
  safety, paging/diff, the HTTP routes, and (when present) an end-to-end walk
  of the staged runs under `/home/ubuntu/remote/webview-work/runs`
  (`CORESMITH_STAGED_RUNS` overrides the location).
* `orchestrator/vscode-ext` — `npm test` (jest): formatting/highlighting
  utilities, review primitives, and the Overview / Trajectory / call viewer
  components against canned API payloads.
* `orchestrator/vscode-ext/scripts/screenshot_views.py` — loads every view in
  headless Chromium, fails on console/page errors, saves screenshots.

## Known gaps (data the engine does not persist)

The viewer can only show what the run directory contains. These are the
gaps an architect will notice, with the engine location to fix:

1. **TNS and the failing-path report.** `ppa_check.run_maxfanout_buffered_sta`
   / `_measure_wns_from_rtl` run Yosys + OpenSTA in a `tempfile.mkdtemp`
   directory that is deleted; only `worst_slack -max` is kept and written to
   `ppa_history.wns_ns`. `run_pre_layout_sta` parses `report_tns` but the value
   is dropped before `_record_ppa_row`. Nothing writes `report_checks`
   (startpoint / endpoint / slack). The Timing section therefore shows TNS as
   *n/a* and no path table. `webview_loaders.parse_sta_report` already
   understands `report_wns` / `report_tns` / `report_checks` text and the
   review looks for `syn/output/<b>/*sta*.rpt|txt|log`, `timing*.rpt`,
   `.coresmith/step_logs/<b>/*sta*.log`; persisting the OpenSTA stdout there
   (and `tns_ns` in `ppa_history`) makes the table appear with no UI change.
2. **Step logs are overwritten across rounds.** `step_logs/<block>/<step>_attemptN.log`
   is keyed by attempt only, so round 5's `lint_attempt1.log` replaces round
   1's. Earlier rounds show the node outcome but no log.
3. **Diagnoses / attempt history are cleared per round.** `Init Block` clears
   `attempts` / `diagnoses` in the state store; only the event-stream previews
   (`diagnosis_preview`, `suggested_fix`, truncated by the engine) survive for
   earlier rounds.
4. **Generated file contents at write time are not captured.** Codex
   `file_change` items carry only path + kind. The viewer links to the file
   as it exists *now* and offers diffs between `rtl_backup_attemptN.v` and the
   current RTL, and between spec drafts left in `codex-call-*/arch/uarch_specs/`.
   Most `codex-call-*` workspaces are empty after the call.
5. **`chip_lead/decisions.jsonl` has no timestamps or call ids**; they are
   recovered by index pairing with `chip_lead_decision` events and by time
   proximity to `Chip Lead [...]` calls.
6. **`llm_calls.jsonl` has no `block` / `node` fields**; attribution relies
   on time windows plus the block name embedded in `run_name`.
7. **No per-block STA on the historical run** (`h264-arm-b` skipped synthesis),
   so its synthesis/timing sections are empty by construction.
