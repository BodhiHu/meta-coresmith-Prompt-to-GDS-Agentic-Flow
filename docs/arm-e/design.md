# Arm E: simplify remote master without demolishing it

Date: 2026-09-05. Branch `arm-e/simplify-master` in `/home/ubuntu/coresmith-arm-e`
(worktree of `/home/ubuntu/coresmith`). Base: remote master `7ef5ce0` (unchanged
since the audit) plus the 19 validated audit-fix commits (`5861f75..b04e3f7`,
cherry-picked as `HEAD~18..HEAD`, 3,841 tests collect).

## Goal

Reliability with the simplest engine that still has the state machines. Take the
ideas that made Arm C small (one SQLite project state, a CLI over it, no on-disk
versioning) and apply them to master, while keeping what master does well: the
LangGraph architecture / frontend / backend state machines, the park-and-resume
interrupt protocol with an outer agent, and the deterministic gates (Verilator
lint, cocotb DV, Yosys synthesis, storage lints, PPA checks, integration and
validation DV, gate-level sim).

## What changes

| Area | Master today | Arm E |
|---|---|---|
| uArch exploration / modeling | Amaranth block models, `microarch_exp` graph (2,866 lines), `model_integration` and `uarch_integration_gate` / `begin_rtl_pass` two-pass topology, composition audit, RTL-vs-model equivalence, derate ledger, chip-model anticheat, block golden and model-integration generators | **Removed.** The frontend loop is spec -> RTL -> lint -> TB -> sim -> synth. `generate_uarch_spec` / `review_uarch_spec` stay (a plan, not a model). |
| Project state | ~40 JSON file kinds under `.coresmith/` and `arch/` (block_diagram, block_specs, interface_contracts, best_result, attempt_history, diagnosis, constraints, prd/ers/frd specs, block_queue, throughput, ppa_report, ...), sha1 sidecars and mtime freshness checks, plus a best-effort SQLite scoreboard | **One SQLite database** `.coresmith/project.sqlite` is the canonical state: blocks, interfaces, contracts, specs, attempts, gate results, diagnoses, constraints, measurements, events, settings. `bin/coresmith` reads and writes it. Agents read state through the CLI, not by opening JSON files. Reports that are evidence (logs, synth reports, waveforms) stay as files and are referenced by path. |
| Synthesis | `CORESMITH_SKIP_SYNTH=1` disables the gate (both h264 A/B runs used it) | **Yosys always runs.** No skip flag. With a PDK configured it maps to the library; without one it runs generic synthesis. Missing Yosys refuses to start the frontend run with a clear error instead of passing silently. |
| Configuration | 291 `CORESMITH_*` environment flags | Flags for removed features deleted; remaining knobs move to a `settings` table with defaults, written once at run start (the audit fix already made the persisted run env authoritative). Target under 60. |
| Daemon / CLI / outer agent | FastAPI daemon, `bin/coresmith` HTTP client, interrupts parked for an outer agent | **Kept.** CLI gains state subcommands (`blocks`, `block NAME`, `specs`, `attempts`, `results`, `constraints`, `metrics`, `settings`). |
| Checkpointing | LangGraph SQLite checkpointer | Kept. |
| Gates | lint, sim, synth (skippable), storage lint, stage lint, mem_price, ppa_check, gate_sim, integration_check/dv, validation_dv, contract conformance | Kept, synth mandatory. |

## Work packages

1. **WP-1 Remove the modeling stage.** Delete `langgraph/microarch_exp.py`,
   `architecture/model_integration.py`, `langgraph/rtl_model_equiv.py`,
   composition audit and chip-model anticheat, derate ledger, the block-golden and
   model-integration generator agents and prompts, and the `two_pass`,
   `model_integration`, `uarch_integration_gate`, `begin_rtl_pass`,
   `write_contract_request` nodes and routes in `pipeline_graph.py`. Keep
   `architecture/composition.py` only where it serves the golden *reference*
   (oracle) used by DV, not block models. Delete the 13 model/microarch test files
   and update wiring tests. Suite green against the shrunk baseline ledger.
2. **WP-2 One SQLite state.** Grow `state_store` into the project database
   (schema below), port the writers (47 sites in `pipeline_graph.py`, 10 in
   `pipeline_helpers.py`, 9 in `architecture/constraints.py`, 8 in
   `architecture_graph.py`, 4 each in `backend_graph.py` and `daemon/server.py`)
   and their readers, replace `harness/blocks.py`'s four-way file resolution with a
   single query, drop sha1 sidecars and mtime freshness in favour of attempt rows,
   and add the CLI subcommands. Prompts that name `.coresmith/*.json` are
   rewritten to name the CLI command.
3. **WP-3 Yosys mandatory.** Remove `CORESMITH_SKIP_SYNTH`, add a startup
   preflight, keep generic synthesis when no PDK is configured.
4. **WP-4 Flag diet.** Delete flags of removed features; move the rest to
   `settings` with documented defaults.
5. **WP-5 Docs and validation.** Update `CLAUDE.md`, `README.md`; run the h264
   task on e6 as Arm E with the matched worker, graded by the same common
   checker and memory-blackboxed synthesis flow as A/B/C/D; then the HEVC task.

## SQLite schema (WP-2)

```
project(id=1, name, requirements, created_at)
settings(name PRIMARY KEY, value)                       -- replaces env flags
blocks(name PRIMARY KEY, tier, description, status)      -- replaces block_diagram/block_specs/block_queue
interfaces(block, name, kind, direction, width, protocol, peer_block, peer_name)  -- block_diagram edges
contracts(id, src_block, src_port, dst_block, dst_port, spec_json_text, version)  -- interface_contracts
specs(stage, block, content)                              -- prd/ers/frd/uarch/block spec markdown
attempts(id, block, node, attempt, status, exit_code, error, log_path, started_at, finished_at)  -- attempt_history + diagnosis
results(id, block, gate, attempt_id, verdict, metrics_json_text, report_path, created_at)         -- best_result, synth/ppa/coverage/dv rows (absorbs scoreboard tables)
constraints(id, block, rule, source, created_at)          -- constraints.json
measurements(id, stage, block, name, value, unit, report, run_id, created_at)
events(id, ts, kind, block, payload_text)                 -- events/escalations
```

Immutability is enforced the Arm C way, with triggers on completed stages, not
with checksum ledgers.

## Non-goals

No new IDL. No change to the LangGraph topology beyond removing the modeling
nodes. No change to the outer-agent decision contract except that the removed
interrupt types disappear. Backend and tapeout graphs are kept as they are apart
from state access.

## WP-7 (2026-09-05): targeted review revise + single-context uArch

What the h264 Arm E run showed: tier 3 went through 6 integration-review
rounds (13 h of the 25 LLM-hours) because a chip-level `revise` re-fanned out
the WHOLE tier from spec generation, the reviewer's fixes were made on
`arch/uarch_specs_review/` copies that nothing consumed, and the chip lead's
feedback never reached the spec author (per-block state starts with
`human_response=None`). Every round re-drew every spec from scratch, the same
naming drift reappeared, the reviewer edited the copies again, the chip lead
revised again.

Change 1, targeted revise (always on). `integration_review_node` turns a
`revise` into `revise_blocks = {block: reuse_spec}`:

- blocks the reviewer edited: the reviewed copy is adopted as the canonical
  `arch/uarch_specs/<b>.md` (newer than the RTL/TB, so those regenerate) and
  the block re-enters implementing that spec as-is (`reuse_spec=True`);
- blocks the chip lead names (`affected_blocks`, `block_actions`, or an exact
  block name in `feedback`/`reasoning`): the findings are written to
  `.coresmith/blocks/<b>/gate_feedback.txt` and the block re-specs from its
  current spec, exactly the existing per-block revise path;
- blocks that failed their lifecycle: retry against their spec;
- every block in scope drops `best_result` so the RTL skip-regen fast path
  cannot reuse a pass measured against the old spec;
- every other block keeps its completed result (`completed_blocks` dedups by
  name). A revise naming nothing still re-runs the whole tier, now with the
  review summary delivered as feedback instead of dropped.

`fan_out_tier` sends only the planned blocks with `reuse_spec`;
`generate_uarch_spec_node` implements the on-disk spec when `reuse_spec` is
set and no feedback is pending for the block; `advance_tier` clears the plan.
The graph topology is unchanged (revise still routes to `init_tier`).

Change 2, single-context uArch stage (experiment, `CORESMITH_UARCH_SINGLE_CONTEXT=1`).
At the first tier entry `init_tier_node` runs ONE agent session
(`UarchSpecGenerator.generate_many`) that writes every missing spec of the
design against the shared ERS/FRD/block diagram/contracts, so the 12 specs
have one author and one naming scheme; blocks then fan out for RTL only. On a
targeted revise the same single session revises the blocks the chip lead
named. A spec the session did not produce (or a block with pending mem-price /
spec-review feedback) falls back to the per-block author, so nothing is lost
when the mode is off or the session under-delivers.

Files: `langgraph/pipeline_graph.py` (`_plan_targeted_revise`,
`_revise_named_blocks`, `_single_context_uarch_stage`, `reuse_spec` /
`revise_blocks` state), `langgraph/pipeline_helpers.py`
(`generate_uarch_specs_single_context`), `langchain/agents/uarch_spec_generator.py`
(`generate_many`), `langchain/agents/integration_review_agent.py`
(`edited_blocks`, `reviewed_specs`), `langchain/prompts/chip_lead.md`.
Tests: `orchestrator/tests/test_targeted_revise.py` (18 tests).

WP-7b (same day): the DV-failure revise gets the same treatment. The live
h264 run's validation-DV `revise` at 19:39 UTC re-ran all three tiers from
tier 1 (2.5 h, every spec re-drawn) although the contract audit named the
affected blocks. Now `integration_dv` / `validation_dv` return
`revise_blocks = {affected: re-spec}` next to the tier reset, the finding is
also handed to each affected block as `gate_feedback.txt`, `init_tier` skips
tiers with nothing to redo, `advance_tier` keeps the plan while a later tier
still has planned blocks, and `integration_review` merges its own plan with
the entries for later tiers. A plan naming no queued block voids itself, so
the old whole-design re-entry remains the fallback.

## WP-8 (2026-09-06): chip-boundary handshake follows the published sampler

Arm E's grading showed why every engine arm (A, B, E) fails the published cocotb
sampler while passing its own DV: the engine's prompts told TB authors to sample
`tready` BEFORE the edge and told RTL authors never to qualify a handshake with a
registered ready. The published `StreamHarness` samples after the edge: a word is
accepted on edge N iff `in_valid && in_ready` as it reads AFTER N, and the output
beat visible after edge N is consumed by the `out_ready` driven for cycle N. Both
passing designs (C, D) register `out_ready` and retire on `out_valid_q &&
out_ready_q`, and never drop `in_ready` on an accepting edge; Arm E dropped it on
the last byte of every frame, so the grader re-offered that byte and the stream
shifted by one per frame.

Commit f17c5e9 states the contract for the chip-boundary stream ports in the RTL,
uArch, interface-definition, per-block TB, integration TB and validation DV
prompts; internal block-to-block interfaces keep the standard at-the-edge
convention. The Arm E2 relaunch (`h264-arm-e2-20260906`, checkout arm-e-v5) also
appends the contract to the seeded ERS.

## WP-9 (2026-09-06): the handshake pair is part of the canonical port set

Arm E2's first two tier-3 revises asked the same five blocks for missing
`srdy/drdy` ports. Cause: the port_naming skill derived a block's port set from
the contract's `fields[]` (payload only); the handshake pair lives in the
`producer_port`/`consumer_port` strings (`m_residual_srdy/m_residual_data`), so
RTL authors emitted flattened payload fields with no flow control, and the
deterministic contract-port gate searched for a port literally named
`s_residual_drdy/s_residual_data` and recorded the miss as an advisory. Commit
b1e7363: the skill states that every channel exposes `<channel>_srdy/_drdy` (or
`_tvalid/_tready`) by role in addition to `<channel>_<field>`; the gate derives
the channel from the slash spelling, hard-fails a missing handshake pair, and
checks each flattened field port's width. Arm E2's daemon moves to checkout
arm-e-v6 at the end of its second spec revision.

## WP-9c (2026-09-06): the shared port derivation carries the handshake pair

WP-9 fixed `check_rtl_contract_ports` but not its sibling: `contract_conformance.signal_specs()`,
the single union both the conformance gate and the RTL prompt's AUTHORITATIVE PORT NAMES table
read, listed `fields[] + sideband_signals[]` only. So the RTL author was shown a port set without
flow control and told that table beats the uArch spec, and once the contract-port gate demanded
`<channel>_srdy/_drdy` the conformance gate rejected them as "undeclared": three E2 blocks parked
on `contract_conformance_unrepairable` for two hours. Commit 3cd37ab derives the pair from
`handshake_protocol` in `signal_specs()` (unless the contract already lists it) and loads the
`srdy_drdy` skill into the RTL generator (only the uArch author had it). Reports:
`/home/ubuntu/remote/reports/e2-missing-ports-investigation.md` (Sonnet) and
`/home/ubuntu/remote/reports/coresmith-history-deterministic-checks.md` (Opus).

## WP-10 (2026-09-06): the deletion pass

Executive decision: return to the original philosophy (external oracles as the
only hard gates; the LLM policy does the design work), keep the SQLite state,
the single-context uArch stage, targeted revise and the chip lead. WP-10a
(commit 3a75868, -6,340 lines) deletes the WaveKit VCD audit, the regex
storage/stage/ifdef lints with their pre-synthesis storage/memory-tier gate
and chip_top ifdef postcondition, and the deterministic half of the
cross-artifact gate. Interface-family coherence (cheap, well-tested, no loop
history) and the gate_guard rollback knob were left alone. Next: WP-10b
staleness ledger / sha1 sidecars / skip-regen (re-entry uses the targeted
plan instead), WP-10c advisory demotions (PPA budgets, mem-price,
throughput, complexity), WP-10d flag diet. Then Arm F: h264 and HEVC judged
only by the published sampler.

## WP-11 to WP-13 (2026-09-06): review fixes, owner decisions, Arm F

- WP-11 (3193fba): fixes from the independent Codex review (restored helper
  deleted by WP-10a, tier-advance skipping finished tiers on re-entry,
  measured timing hard again via `timing_ok`, chip throughput advisory,
  missing MEM manifest advisory, C7b staleness preflight deleted, malformed
  single-context spec quarantined, explicit keep beats prose, WP-9b heuristic
  removed, in_ready wording made self-consistent). Corrected account of Arm E:
  its OUTPUT side retires on raw next-edge out_ready; the input side matches D.
- WP-12 (f6ed2c9): owner decisions -- coverage floor is a hard gate (70%),
  golden-required signoff (`golden_exempt` + `no_golden_reason` for pure
  storage/IO), deterministic port repair deleted (conformance is report-only).
- WP-13 (5572739): Memory Map, Clock Tree, Register Spec and Complexity Review
  stages dropped from the architecture graph (19 nodes left there).
- Arm F protocol (owner: 3 attempts, task-only seeds): `/tmp/launch_arm_f.sh
  <id>` on e6 seeds only the task materials + oracle adapter files, runs the
  architecture phase from `inputs/requirements.md`, a follower starts the block
  flow when the architecture completes, `CORESMITH_AUTO_BACKEND=1` runs flat
  synthesis + chip-level gate sim. Judged only by the published sampler.

## Result: Arm E2 passes the published sampler (2026-09-06 10:40 UTC)

19/19 on the published cocotb harness, 0 timeouts, cycles/frame within 10% of C and D; memory-blackboxed area
551,708 um2 (E 647,965; D 102,839). Engine cost 13.05 LLM-hours / 152 calls vs Arm E's 32.1 / 242 on the same
seeds. First engine arm to pass. Not a clean experiment (engine hot-swapped twice, Arm B architecture inherited);
Arm F attempts 1-3 (task-only seeds, v9/v10) are running for the clean result.

## WP-15 (2026-09-06): fixes from the Arm F / E2 trajectory audits

Two Sonnet audits (`reports/trajectory-audit-arm-f1-f2.md`,
`reports/trajectory-audit-arm-f3-e2.md`) read every trajectory of F-1, F-2,
F-3 and E2. Verdict: strict deterministic gates are NOT the main churn source
(F-1/F-2: zero lint/conformance/PPA rejections, 9 of 12 blocks passed first
try; F-3: zero gate incidents). E2's ~4h15m of rework came from one gate BUG
(Contract-Port vs Contract-Conformance disagreeing on the srdy/drdy pair,
fixed by WP-9c), not a strict threshold. Engine fixes landed here:

- Codex outage is infrastructure: `_parse_codex_json` turns `{"type":"error"}`
  events into `[ClaudeLLM error: codex CLI reported: ...]`; `_INFRA_MARKERS`
  knows usage-limit / rate-limit text; the RTL generator appends the LLM
  response tail to "Agent did not write RTL" (F-2 filed the quota outage as
  UARCH_SPEC_ERROR confidence 1.0 needing a human).
- `_maybe_squeeze_throughput` deleted (ImportError on every leaf block in the
  deployed builds; throughput is advisory since WP-10c).
- Chip-level DV stages `<root>/inputs` into `sim_build/<scope>` via symlink
  (`_stage_project_inputs`): every task-only run lost ~20 min to "three
  project-relative ROM images missing".
- `gate_result` event logs `passed: null` when the gate did not run (38/38
  leaf invocations were logged passed=false while never having run).
- Acceptance stimulus falls back to `CORESMITH_MODEL_STIMULUS` /
  `inputs/model_stimulus.py` (nothing authored `acceptance_stimulus.py`, so
  Acceptance DV was silently SKIPPED on every run); FRD prompt tells the
  agent to write the artifact or reference the operator's model stimulus.
- Prompt policy (chip_lead.md, integration_review.md): decomposed per-field
  ports are a valid contract realisation (no literal `tdata` demands, no
  alias renames -- 92 min / 35 calls of churn in F-1/F-2); compare the DV
  scorecard before/after a fix (F-3: two confidence-0.99 fixes, three
  identical scorecards); widen shared edges for every endpoint in one edit.

Checked and left alone: MAX-GEOMETRY marker matching is already value-based
(`maxgeo_demand`: a value collision counts, only absent values fail); the
mem-price re-spec cap 0 is lenient, not strict (owner call); coverage floor,
FF budget, SRAM-tier and attempt caps never fired unjustifiably.

## WP-16 (2026-09-06 evening): infrastructure attempts do not count

After the owner reset the Codex limit, F-2's chip lead SKIPPED
rbsp_annexb_assembler: three usage-limit failures had been counted as three
RTL attempts, the router asked the chip lead after two (another LLM call
during the same outage), and the "same category 3+ times -> skip" rule then
fired on an outage. Now an INFRASTRUCTURE_ERROR retry re-runs the same
attempt (budget not consumed, backoff 60 s doubling to 15 min), the router
asks a human only after `CORESMITH_INFRA_MAX_RETRIES` (6) consecutive infra
failures, and chip_lead.md says infra attempts never justify `skip`.

## WP-17 (2026-09-06 late): integration design name from the real top

Arm F-2's second Integration Check (after the tier-3 re-entry) named the
design `h264_normative_rom_arbiter`: `load_architecture_connections` took
the first line-anchored `module` of the existing top file, and the real top
was declared behind a same-line comment on line 166, so the helper arbiter
won. The Integration Lead then wrote the whole chip into
`h264_normative_rom_arbiter.v` (helper renamed `_rr_arbiter`); the chip is
intact but misnamed, and F-2 must be graded with that TOPLEVEL. Now
`_existing_top_module` prefers the PRD-derived name when declared, then the
file stem, then the unique never-instantiated module, then the last one, and
matches `module` anywhere in the text.

## WP-18 (2026-09-06 night): acceptance stage drives the published stream contract

Arm F-2 signed off (validation DV 6/6) and scored 0/19 on the published
harness: ffmpeg rejects the first coded macroblock (CAVLC `total_coeff=-1`).
No engine stage ever decoded the stream. The validation TB marked every
decode/PSNR requirement `deferred_to_golden_sweep` (validation_dv.md allows
it and nothing performs the sweep), and the acceptance stage only knew the
framed s_axis/m_axis shape, so it SKIPPED "honestly". WP-18:

- `classify_contract` recognises the stream_core shape (cfg_valid/cfg_addr/
  cfg_data, start, in_valid/in_ready/in_data/in_last, out_valid/out_ready/
  out_data/out_last, optional busy/done) as contract kind `stream_core`.
- `map_stimulus` carries a `cfg` plane (explicit dict/list, or derived from
  n_frames/width/height/qp) and accepts bytes payloads; a stream_core case
  without any cfg is unmappable (honest skip), never a zero-configured run.
- A native Verilator harness (`_STREAM_TEMPLATE`) mirrors the published
  StreamHarness cycle semantics: drive before the edge, sample after it,
  random input gaps (10%) and output backpressure (15%), one payload byte per
  word, completion on a consumed out_last, watchdog from the case cycle_cap.
- The verdict uses the declared acceptance predicate
  (`CORESMITH_FUNCTIONAL_ACCEPTANCE` / ERS `acceptance_fn`,
  `accept(expected, observed)`) when present -- decode + PSNR for the codec --
  and byte equality only as the fallback for bit-exact IPs. Captured streams
  are kept under `.coresmith/acceptance_dv/<case>.out.bin`.
- Operator seed for the h264 task: `inputs/acceptance_stimulus.py` with three
  6-frame cases (gradients 0, moving_box 1, random 42) and the spec's cfg map
  (0 n_frames, 1 width 64, 2 height 48, 3 qp 28).

## WP-19 (2026-09-07): an acceptance failure parks for the chip lead

The Arm F-3 re-drive on v16 (`run restart-node validation_dv`) showed the
acceptance stage failing 2/3 cases and the run going Validation DV -> Final
Report FAIL in six seconds: the acceptance early-return produced a failed
result with no interrupt, so the chip lead never saw it. Now that branch
builds a `validation_dv_failure` payload (`phase: acceptance_dv`, the per-case
table, the captured-stream directory, a synthetic contract audit
`ACCEPTANCE_DV_FAILURE` recommending fix_rtl) and returns
`pending_decision`, exactly like a simulation failure; chip_lead.md tells the
lead to grade the captured streams offline with the task grader and answer
fix_rtl / revise (never skip, fix_tb is meaningless).

## Multi-IP sweep (2026-09-07, owner request: no bias toward one IP type)

Task-only runs of the other PPABench tasks on the latest engine (v17):
`aes_qspi`, `ax25_9600`, `fft256_qspi` (QSPI-slave accelerators in the locked
Caravel wrapper, graded by `chassis/accel/tasks/<task>/grade_accel.py`:
hidden cocotb TB through the QSPI-master BFM + Yosys caps + STA), and `mcu3`
(single-block 3-stage 8-bit MCU from `examples/mcu3`, graded by an external
cocotb TB against the requirements' reference program + Yosys < 2500 cells).
Seeds live in `e6:/home/ubuntu/task-seeds/<task>/` (published solver view:
spec.md, task.yaml, PROTOCOL.md; requirements.md and the operator-frozen
golden/stimulus/acceptance adapters from the 2026-07/08 runs; mcu3 gets a
new ISA-simulator golden). Launcher `/tmp/launch_task.sh <task> <id>
[checkout]` -> run dir `/opt/Xilinx/coresmith-runs/<task>-arm-g-<id>`, scope
`coresmith-<task>-g<id>`, same worker as Arm F (gpt-5.6-sol high on Codex
CLI 0.144.4), chip lead, single-context uArch, auto backend, 50 MHz.
Known limitation: the WP-18 acceptance stage does not classify the Caravel
`io_in/io_out` shape, so for the QSPI tasks the mission-level check is the
engine's deterministic QSPI conformance DV plus the published grader.

### Sweep observation (2026-09-07 06:30): architecture-phase review churn on aes_qspi

Four block-diagram rounds and ten chip-lead decisions before the block flow: the
final review kept sending the architecture back for document-level
consistency (score-only cycle targets written as mandatory; "replicated
mask-ROM" vs max_macro_count=0; a package/pad-library question re-asked every
round). Same class as the Arm F chip-lead policy churn (audit category I).
Mitigation applied at run time: `inputs/OPERATOR_RULINGS.md`, which the chip
lead reads first. WP-20 candidate: cap final_review feedback rounds (force
accept after two) and stop re-asking questions the rulings already answered.

## WP-20 (2026-09-07): bounded architecture review, contract-driven reset naming

- `final_review` and the block-diagram escalation accept after
  `CORESMITH_ARCH_MAX_FINAL_FEEDBACK` (default 2) feedback rounds each; the
  payload carries `feedback_rounds_used` / `feedback_rounds_cap`; a capped
  feedback is logged as a `feedback_cap` event and its text is not applied.
- chip_lead.md ARCHITECTURE REVIEW DISCIPLINE: accept unless the defect
  would make the RTL wrong; wording-level PRD/SAD/FRD/ERS inconsistencies are
  the uArch/DV stages' job; never re-ask answered questions.
- block_diagram.md / integration_review.md: clock/reset names and polarity
  follow the task's contract (Caravel: wb_clk_i + active-high wb_rst_i);
  `clk`/`rst_n` only when unspecified. On fft256_qspi the twiddle_rom was
  regenerated with rst_n against a spec that said active-high `rst` twice
  before converging.

## WP-21 (2026-09-07): the conformance gate knows the valid_only strobe

ax25_9600-arm-g-1 parked modem_controller as `contract_conformance_unrepairable`
with six "undeclared port *_valid" on valid_only edges whose contract listed
only payload fields (F-1 and FFT contracts happened to enumerate `valid`,
AX25's did not). Same defect class as WP-9c for another handshake family.
`signal_specs` now adds `valid` on valid_only edges (so the RTL prompt's port
table shows it too), and `check_block` records a channel-prefixed pure
flow-control signal (valid/ready/req/ack/strobe/srdy/drdy/tvalid/tready/last)
as `handshake_extra` (reported, not a deviation) unless it is the collapsed
form of a declared signal (the `host_write_enable` class is still caught).

## WP-22 (2026-09-07): Caravel hierarchy postcondition, and it parks

fft256_qspi-arm-g-1 passed all 8 blocks, the deterministic Caravel assembly
had 21 wiring hazards (the LLM-written openframe/user_project_wrapper blocks
did not expose the pad signals the contract declared), the Integration Lead
wrote a chip top instantiating openframe_project_wrapper -> the assembled
user_project_wrapper -> the seven blocks, and `assert_blocks_instantiated`
(top file only) declared 7 blocks missing; the node returned skipped and the
run ended "done" with no interrupt. Now the postcondition sees the whole
rtl/integration hierarchy, and a real failure parks as `integration_failure`
phase `postcondition` (retry / fix_rtl re-run the integration check through
a self-loop edge; abort ends the run).

Prompt bias removed in the same WP: rtl_generator.md/.py rule 3/4,
uarch_spec_generator.md rule 5 and testbench_generator.md rule 6 now follow
the spec's clock/reset name and polarity (Caravel: wb_clk_i + active-high
wb_rst_i). ax25_9600 spent two re-spec rounds on three blocks regenerated
with `clk/rst_n` against specs mandating `wb_clk_i/wb_rst_i`; fft256_qspi's
twiddle_rom took three.

## WP-23 (2026-09-07): outer OpenFrame wrapper blocks are backend shells

fft256_qspi and ax25_9600 decomposed with an `openframe_project_wrapper`
block next to the `user_project_wrapper` pad adapter (the FRD's MPW wording
mentions OpenFrame; aes_qspi did not do this). The deterministic Caravel
assembly then hit 21 wiring hazards on the openframe<->upw edge, fell back to
the Integration Lead whose chip top nests openframe -> upw -> blocks, and the
QSPI pin-boundary gate refused to simulate a top that is not the graded
user_project_wrapper (by design it never retargets). AX25's review looped
four times demanding one wrapper instantiate the other. Now integration
drops outer-wrapper blocks (by name, or chassis-only ports) before assembly
and the postcondition; block_diagram.md forbids adding such a block for a
locked Caravel task; chip_lead.md never revises over one.

## WP-24 (2026-09-07): a self-assembled user_project_wrapper is the chip top

fft256_qspi's `user_project_wrapper` block was written as the complete graded
top (locked Caravel pads and wishbone/LA ports, and it instantiates all six
core blocks). The deterministic assembler re-wrapped it, hit 6 hazards (the
contract's decomposed qspi_gpio_* edges are wired inside the block, not
exposed), fell back to the Integration Lead whose top nested it under a
design-named module, and the QSPI pin-boundary gate rejected that top. The
integration node now detects a wrapper block that declares io_in/io_out/
io_oeb and instantiates every other block, lints it with the others and
adopts it as the chip top (`self_assembled_wrapper: true`). Pad-adapter-only
wrappers (ax25, aes) still go through the deterministic assembly.

## WP-25 (2026-09-07): MAX-GEOMETRY advisory for the deterministic BFM; engine checkout is read-only

fft256_qspi (v22) got past the pin-boundary gate; the engine's own
deterministic, DUT-blind QSPI host-flow testbench then failed the
MAX-GEOMETRY gate because the declared maxima (fft_points=256,
complex_buffer_depth=256, qspi_byte_address=2^24-1) never appear by value in
the `# MAXGEO_CASE` marker (in_bytes=1024 for 256 complex words). A fixed
N=256 FFT attains its maximum on every case. For the engine's deterministic
host-flow BFM (full writer record: deterministic_bfm + contract, not the
conformance-only TB, which keeps its own scoped path) the gate is now an
advisory with the uncovered dims recorded loudly; LLM-authored testbenches
still fail hard.

Also found: the FFT chip lead, told to fix the DV, patched the ENGINE
CHECKOUT (bfm_lib/__init__.py, classifier.py, pipeline_graph.py and a test in
arm-e-v20) as its "fix_tb". chip_lead.md now says the engine checkout is
read-only (project-root files only), and `_engine_checkout_guard` runs after
every chip-lead decision: `CORESMITH_ENGINE_READONLY` 1 = log + event
`engine_modified`, `revert` = also git checkout/clean (launchers set revert).
The v20 edits were stashed.

## WP-26 (2026-09-07): the Caravel assembler skips illegal contract names

aes_qspi (v23): the interface-definition stage emitted an edge with
producer_port `status.done`. The conformance gate drops such a signal, but
the deterministic Caravel assembler reported it as its single wiring hazard,
refused its wrapper, fell back to the Integration Lead's design-named top,
and the QSPI pin-boundary gate would have rejected it (the FFT loop). The
assembler now skips a signal whose derived port name is not a legal
identifier (logged; the contract is what is wrong) and keeps the graded
user_project_wrapper. Root-cause follow-up: sanitize channel names at
interface definition.

## WP-27 (2026-09-07): warning-tolerant sim builds; Caravel result persisted

aes_qspi (v24): the deterministic Caravel assembly finally succeeded (6
blocks, 48 wires, lint clean) and the deterministic QSPI BFM ran, but the
integration simulation failed at the Verilator BUILD: `%Warning-UNOPTFLAT`
(circular combinational logic in qspi_slave_frontend) is fatal without
`-Wno-fatal`. The block-level sims and the published harness both build
with `-Wno-fatal`; the integration/validation Makefiles now do too (both
templates), so a style warning is reported, not a DV failure. The
Caravel-assembled branch also never wrote `.coresmith/integration_result.json`,
so the backend (WP-17b) and the graders read a stale Integration-Lead record
naming the wrong top; it is persisted now.

## WP-28 (2026-09-07): QSPI read nibbles must be driven

aes_qspi signed off (deterministic QSPI BFM 3/3, validation 7/7) and failed the
published grader on every case by the LAST NIBBLE of every read, with
STATUS.DONE never visible to the grader's host. Differential runs (same RTL
files by md5, same Icarus, same cocotb 1.9 vs 2.0, same host code, same SCK
speed, same write widths, same reset, IRQ-wait or not) showed the DUT drives
only the HIGH nibble of a STATUS read and releases the io lanes on the LOW
nibble; the engine's polls escaped it by timing on the third poll (io_oeb
traces in both flows show the released nibble). The BFM now records a
drive violation whenever a read data nibble is sampled with any io lane
released (`QSPIMasterBFM._note_drive`), and both generated QSPI testbenches
(host-flow DV and protocol conformance) assert none occurred. The grader's
verdict on AES stands until the chip is fixed: PPA and throughput within
caps, functional byte-exact FAIL on all 6 cases.

## WP-29 (2026-09-07): the deterministic BFM testbench is immutable

With WP-28 the engine's QSPI DV failed the AES chip; the chip lead answered
`fix_tb`, rewrote the engine's contract-derived DUT-blind BFM (sampling point
and SCK period) and the reused testbench passed 15 seconds later while the
published grader still fails the chip. Integration DV now never reuses a
testbench whose writer record says `deterministic_bfm` (it regenerates from
the contract and logs `deterministic_tb_regenerated`), the simulation-failure
payload drops `fix_tb` and carries `deterministic_bfm: true` with guidance,
and chip_lead.md says a deterministic-BFM failure is never a testbench
problem.

## WP-30 / WP-31 (2026-09-07 evening): port-name collapse rule; stale derived artifacts

- WP-30 (prompts): a contract signal that already starts with its channel
  prefix or equals the channel name is not prefixed again (channel `irq` +
  signal `irq` -> port `irq`, never `irq_irq`). ax25_9600's regenerated
  regmap_buffers parked twice on conformance because its uArch spec said
  "keep both tokens"; operator rename applied at run time.
- WP-31: on a targeted tier re-entry, init_tier moves the deterministic
  assembler's outputs (`rtl/integration/user_project_wrapper.v` when the
  record says caravel_wrapper_assembled, and `_pads.v`) into
  `rtl/integration/_stale/<ts>/`; chip_lead.md says derived integration
  artifacts are regenerated at the integration check and are not evidence at
  review. ax25_9600's chip lead had revised blocks three rounds in a row
  citing nets "missing" from a wrapper that had not been rebuilt.

## Independent review round 2 (2026-09-07, gpt-6-astra xhigh)

Packet: `arm-c-whitepaper/REVIEW_PACKET_20260907.md`. Review: `arm-c-whitepaper/review-2/CODEX_REVIEW_2.md`
(e6: `/opt/Xilinx/coresmith/review-20260907/`, with executable probes `review_probes.py`, `sim_probes.py`,
`ax25_probe.py` and their logs). Verdict: **request changes**. The F-3b, FFT and repaired-AES external passes
are corroborated from the graded artifacts; the general diagnosis, the WP-18 sampler equivalence, the green
test suite and several of the new guards are not.

### Reviewer claims verified by the author afterwards (no LLM involved)
- WP-18 accepts an incomplete receipt: `_read_results` stops at EOF, the `zip` over cases drops the unmatched
  case, and `passed` never compares returned vs requested cardinality (`acceptance_dv.py` ~686, ~900, ~998).
- WP-21b: a block with a missing required `go_valid` and a stray `go_ready` returns `ok=True` from
  `check_block` (probe log line "WP21 missing valid plus extra ready").
- WP-25 `revert` uses `git checkout -- .`, which restores from the index; a staged mutation survives.
- WP-28 `_note_drive` converts the whole `io_oeb` vector; an X on any unrelated pad makes all four lanes
  "released" (false violation under Icarus), while Verilator resolves X to 0 and misses real X on the lanes.
- AX25: 8 of 10 cases time out at 400k cycles; `seed2_baseband` and `seed5_baseband` finish with DONE
  (261,571 and 115,998 cycles); the "every case times out / DONE never seen" wording in the results doc is wrong.
- AES: the published and engine host timelines differ (80 ns vs 100 ns SCK half-period), so the earlier
  "identical timelines" statement is withdrawn; the low-nibble drive defect itself stands.
- WP-23 name/port heuristic drops a legitimate Wishbone-only peripheral (`wishbone_uart` probe).
- WP-22 postcondition concatenates neighbouring files; an instance inside an unreferenced module passes.

### Test-suite correction
The packet claimed "full suite green, 3,132 tests". With the packet's own command the suite is **not** green.
At a736450 with `-m "not live_llm"`: 16 failed / 3,191 passed / 26 skipped / 5 xfailed. The same 16 fail at
845353c^ (before WP-15). At 5572739^ (before WP-13) only `test_backend_helpers::test_pdk_files_exist` fails
(missing Tech LEF in this environment). The other 15 (`test_architecture_integration.py`,
`test_param_schema.py`) patch the removed `specialists.memory_map` and are WP-13 fixture fallout that should
have been fixed with WP-13. The per-WP "green" runs excluded these files without saying so.

### Reviewer's disposition list (owner decision pending)
| Item | Reviewer asks | Author position |
|---|---|---|
| WP-20 `_cap_feedback` forced accept | delete; park on budget exhaustion | agree |
| WP-21b missing-strobe exemption + suffix allowlist | reverse; require the declared strobe | agree; keep valid-only derivation |
| WP-23 runtime wrapper-block dropping | delete; explicit submission boundary | agree; keep the prompt distinction |
| WP-26 skip illegal contract name | park with both endpoint names instead | agree |
| WP-25 git revert/clean | delete destructive path; hash-check + park; launch workers without write access | agree |
| WP-29b "BFM is never wrong" prose; unconditional TB regeneration | keep ownership rule, drop infallibility, reuse by content hash | agree |
| WP-18 as a second general sampler | keep as h264 adapter; run the published driver/checker where possible; fix cardinality/32-bit/output-bound/per-class floors | agree on the fixes; keep native path only with differential tests |
| WP-28 X handling + per-BFM assertion coverage | fix | agree |
| WP-17/22/24 top/hierarchy heuristics | one declared top + elaborated source receipt | agree in principle; larger change |
| WP-13 test fixtures | fix, do not restore nodes | agree |
| mcu3 in the "published PASS" column | relabel smoke-only | agree |

### Plan amendment accepted from the review
WP-32 becomes a task-owned acceptance adapter that runs the published driver/checker with the task's
budgets (absorbing WP-33), writes a per-candidate receipt, and parks typed failures; it must replay the retained
AX25, F-2, AES and FFT candidates before any LLM re-drive. Order: engine fixes above → WP-32 → AX25 re-drive
and F-2 replay → HEVC matched comparison → second seeds.

## Post-review changes: WP-32 … WP-42 (2026-09-07/08)

Owner instruction: "do it" on the order proposed after review round 2 (fix the WP-13 fixtures,
reverse the four gate-loosening changes, build the adapter boundary and migrate task specifics
behind it, re-run the sweep). All on `arm-e/simplify-master`; e6 checkout `arm-e-v31`.

| WP | Commit | Change |
|---|---|---|
| 32 | 307830f | Test fixtures stop patching the specialists WP-13 removed (15 collection errors gone). |
| 33 | d3a5641 | Reverses WP-20: an exhausted feedback budget parks for a human with the unresolved feedback; never rewrites `feedback` into `accept`. |
| 34 | a4b9c36 | Reverses WP-21b: the synthesized valid_only strobe is required; undeclared flow-control ports are deviations; `_FLOW_CONTROL_SUFFIXES` and the report-only fields are gone. Keeps WP-21's `valid` derivation. |
| 35 | 30056dd | Reverses WP-23: no runtime outer-wrapper block dropping. The submission boundary is a prompt rule and is validated by elaboration and DV. |
| 36 | 47712b1 | Reverses WP-26: an illegal contract-derived port name is a hazard naming both endpoints; the connection is never dropped. |
| 37 | 4648f83 | Amends WP-25: the engine-checkout guard only detects (staged, unstaged, untracked); a detected edit discards the chip-lead decision, trips the chip lead and parks. `git checkout -- . && git clean -fdq` is gone. Launch scripts use `CORESMITH_ENGINE_READONLY=1`. |
| 38 | ddb4229 | WP-18 fixes: complete-receipt requirement (record count, truncation), build/exit/predicate-load/undeclared-setting failures are `oracle_incomplete` (kinds oracle_incomplete / adapter_defect / infrastructure_error) and park; the stimulus declares `cfg`, `word_bytes` (no byte truncation of 32-bit words), `max_out_words`, `seed`, `gap_pct`, `bp_pct`; no n_frames/width/height/qp inference; predicate receives the case; candidate sha + per-candidate capture dir + receipt.json; WP-19 payload drops `fix_tb` and separates oracle problems from RTL verdicts. |
| 39 | ef95f64 | WP-28 fix: the BFM inspects the four lane characters only (x/z = violation, unrelated X ignored), reports a missing io_oeb once, and generated testbenches use `strict_drive=True` (every read asserted, max-geometry included). |
| 40 | 00770c1 | WP-29 amended: deterministic TB reuse by sha256 recorded at generation (`deterministic_tb_reused` / `deterministic_tb_modified`); prompts drop "never a testbench bug" -- the policy may report a concrete BFM counterexample, the operator owns the fix. |
| 41 | 3e3689f | Task-adapter boundary: `orchestrator/harness/task_adapter.py` + `task_adapter_runner.py`. `inputs/task_adapter.py` (header: interpreter, timeout; `CASES`, `TOP`, `LABEL`, `grade(candidate, workdir)`) runs the task's own checker; the engine assembles + hashes the candidate, runs the adapter in a subprocess, refuses incomplete receipts, types failures `functional_fail` / `budget_fail` (WP-33 folded in), writes `.coresmith/acceptance/<sha12>/`. Validation DV prefers the adapter; the native stream harness is the fallback. |
| 42 | ff83964 | chip_lead.md: outer-wrapper rule without engine dropping, policy-authored tops are blocks, adapter verdict kinds. |

Task-owned adapters (not engine code), installed on e6 under `/home/ubuntu/task-seeds/<task>/task_adapter.py`
(local copies `remote/task-adapters/`): aes_qspi, ax25_9600, fft256_qspi run the published
`grade_accel.run_functional` (hidden_tb + qspi_host + oracle, system Icarus) and the task.yaml
throughput gate verbatim via `_common/ppabench_accel_adapter.py` (ppabench venv); h264 runs the
published StreamHarness + grade_h264 with per-content PSNR floors on a declared 4-case subset
(coresmith venv, Verilator); mcu3 wraps the author's smoke TB and says so in its LABEL.

Test suite at ff83964 (`-m "not live_llm"`): 1 failed (PDK Tech LEF, environmental) / 3,236 passed / 26 skipped / 5 xfailed.
Replay plan (no LLM): `/tmp/replay_adapters.py` on e6 grades the retained candidates through the
adapters -- expected ax25 FAIL (functional + budget), aes PASS, fft PASS, h264 F-2 FAIL, F-3b PASS,
mcu3 PASS(smoke) -- before any re-drive.
