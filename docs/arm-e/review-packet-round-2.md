# Review packet 2026-09-07: Arm F close-out, multi-IP sweep, engine changes WP-15 … WP-31

Author: the CoreSmith arm-e engineer (Claude). Reviewer: gpt-6-astra, independent.
Everything below is a claim to be checked, not a fact to be accepted. Paths are on this
machine (e6) unless marked otherwise.

## 0. What the reviewer is asked to do

1. Corroborate or refute each finding in section 2 from primary evidence (run state,
   graded artifacts, RTL, testbenches), not from the author's prose.
2. Review every commit in the range `845353c^..a736450` (WP-15 … WP-31) in the review
   clone `/opt/Xilinx/coresmith/arm-e-review2` (branch `arm-e/simplify-master`, HEAD
   a736450). For each: is the change correct, is it minimal, does it move the harness
   toward or away from the mission, is it tested, would you reverse it. Cite file:line.
3. Corroborate or amend the plan in section 4 (WP-32, WP-33, re-drives, HEVC). If a
   different next step has higher expected value per LLM-hour, say so and why.
4. Run the test suite yourself in the review clone (command in section 5). Report the
   count and any failures verbatim.
5. Deliver the written review at `/opt/Xilinx/coresmith/review-20260907/CODEX_REVIEW_2.md`
   with the sections listed in the prompt. Be blunt. Rank disagreements by importance.

Rules: the review clone is yours to read, grep, run tests in and scratch on. Do NOT modify
`/opt/Xilinx/coresmith/arm-e-v*` (live engine checkouts) or anything under
`/opt/Xilinx/coresmith-runs/` and `/home/ubuntu/coresmith-benchmarks/` (graded evidence).
Do not start daemons or LLM-backed runs. Write only under `/opt/Xilinx/coresmith/review-20260907/`.

## 1. Mission and standing owner decisions

Mission: "a lightweight harness for chip design". An LLM policy authors the architecture,
microarchitecture specs, RTL and testbenches; the harness supplies structure, oracles and
gates; success is judged ONLY by published external graders (never by the harness's own
signoff).

Owner decisions already made (do not re-litigate; you may note consequences):
- The coverage floor (70%) and golden-required signoff are legitimate hard gates.
- The Memory Map, Register Spec, Clock Tree and Complexity Review architecture nodes are
  dropped (WP-13).
- Task-only runs get 2 to 3 attempts; no seeding of architecture artifacts.
- No deterministic port repair (the engine must not silently rewrite the policy's ports).
- Judge only by published samplers/graders.

## 2. Findings to corroborate

### 2.1 Arm F (h264, task-only seeds, gpt-5.6-sol high as worker, chip lead in-graph)

| Attempt | Engine | Published cocotb sampler | Note |
|---|---|---|---|
| F-1 | v9 | 19/19 | first clean pass of the engine on h264 |
| F-2 | v9 | 0/19 | CAVLC defect; engine signoff had passed; not re-driven (owner choice) |
| F-3 | v9 | 7/19 | CAVLC luma-DC nC contexts + CBP residuals wrong |
| F-3b | v16 → v17 | 19/19 | after WP-18 (stream acceptance stage) + WP-19 (acceptance parks for chip lead): gate failed, chip lead fixed the RTL in one `fix_rtl`, re-graded 19/19; fastest arm at ~36k cycles/frame |

Evidence: run dirs `/opt/Xilinx/coresmith-runs/h264-arm-f-{1,2,3}` (state under `.coresmith/`,
RTL under `rtl/`, integration TBs under `tb/integration/`); graded results
`/home/ubuntu/coresmith-benchmarks/shadow-20260905/cocotb-full/arm-f-<id>/result.csv` and
`.../ppa-memblackbox-arm-f-<id>/stat.txt`; published sampler
`/home/ubuntu/ppabench/benchmarks/stream_core/common/stream_tb.py`; operator acceptance
stimulus copied into each run's `inputs/acceptance_stimulus.py`.

Claim F-a: before WP-18 no engine stage exercised the published stream contract
(cfg plane + in_valid/in_last … out_valid/out_last with gaps and backpressure), so a
chip could pass every engine gate and score 0/19.
Claim F-b: WP-18's harness is a faithful (not identical) model of the published sampler:
post-edge sampling, 10% input gaps, 15% backpressure, completion on `out_last`; the verdict
uses the task's own predicate (`resolve_functional_acceptance`) rather than a harness
heuristic. Check `orchestrator/langgraph/acceptance_dv.py` `_STREAM_TEMPLATE` against
`stream_tb.py` for any semantic divergence that could pass a chip the sampler fails
(or the reverse).

### 2.2 Multi-IP sweep (task-only seeds from `/home/ubuntu/task-seeds/<task>/`, one run each,
engine v17 at launch, hot-swapped to newer checkouts as fixes landed; run dirs
`/opt/Xilinx/coresmith-runs/<task>-arm-g-1`; graded artifacts under
`/home/ubuntu/coresmith-benchmarks/sweep-20260907/<task>/`)

| Task | Engine signoff | Published grader | LLM cost |
|---|---|---|---|
| mcu3 | PASS 1/1 | PASS: OUT=8 at cycle 8; 493 cells | 1.4 LLM-h |
| fft256_qspi | PASS 8/8 | PASS: 5/5 seeds; 1,570 cyc/transform vs golden 3,584; 10,629 cells, 81,463 µm², 853 FF, Fmax 143.7 MHz | 7.2 LLM-h |
| aes_qspi | PASS 6/6 | FAIL by the last read nibble → PASS after WP-28 + WP-29 + one chip-lead `fix_rtl`: byte-exact 6/6, 22 cyc/block, 20,305 cells, 175,388 µm², 4,490 FF, Fmax 165 MHz | 8.2 LLM-h + 2 calls |
| ax25_9600 | PASS 8/8 | FAIL: every case times out at 400k cycles; DUT paces bits at real 9600 baud (≈5,260 clk/bit) where the golden uses ≈2.1 clk/bit; DONE never observed; baseband emits 0 samples plus 77 stray bits; PPA 3,227 cells, 25,620 µm², 583 FF, Fmax 249 MHz | 17.7 LLM-h, 21 chip-lead decisions |

Graders: accel tasks `/home/ubuntu/ppabench/chassis/accel/tasks/<task>/grade_accel.py`
(hidden cocotb TB via `qspi_host.py`, Icarus, Yosys, STA), run as
`/home/ubuntu/ppabench-venv/bin/python grade_accel.py --solution <dir> --out metrics.json`.
Staging script used: `/tmp/grade_accel_run.sh <task>` (assembled
`rtl/integration/user_project_wrapper.v` + `_pads.v` + block RTL, excluding any other file
that declares `user_project_wrapper`). mcu3 has no published cocotb grader in ppabench; it
was graded with the author's external TB (`/tmp/mcu3_grade/`) against the author's ISA
simulator `/home/ubuntu/task-seeds/mcu3/mcu3_golden.py` (rd = (op>>2)&3; reference program
ends with OUT 8). Caveat the author already concedes: mcu3 is an in-distribution,
single-block example and the grader is not independent.

Claim G-a (AES root cause): differential experiments in `/tmp/aes_pub_dbg/` (published TB
variants A–I, `run_dbg.py`) and `/tmp/aes_icarus_check/` showed identical RTL (md5),
simulator, cocotb version, host code, stimulus and timelines between engine DV and the
published grader; the DUT drove only the high nibble of STATUS reads and released the
lanes on the low nibble; the engine's polls escaped by timing. The engine's deterministic
QSPI BFM did not check that read nibbles were driven (fixed by WP-28). The chip lead had
also co-tuned the deterministic BFM via `fix_tb` to make the failing chip pass (archived
under `<aes run>/tb/integration/_pre_wp28/`; forbidden by WP-29).

Claim G-b (AX25 root cause): the task's Caravel pad-stream shape (tx pads sampled by the
grader against a cycle budget) has no oracle-backed engine stage; the engine's QSPI
host-flow DV, conformance and validation all passed a chip whose bit pacing is ~2,500×
slower than the golden. No gate measured "cycles per transmitted bit". The 21 chip-lead
decisions were spent on hierarchy/naming (`irq_irq` vs `irq`, stale assembled wrapper in
review) rather than on the graded quantity.

Claim G-c (the unifying diagnosis, same as the prior audits' verdict): every remaining
failure is "no stage measured the graded quantity", not gate strictness. Where a stage does
measure it (WP-18 for h264 streams, WP-28 for QSPI drive), the closed loop works: gate
fails → parks → chip lead fixes RTL (not the testbench) → published grader passes.

### 2.3 Run-time incidents that motivated engine guards

- The chip lead edited the engine checkout itself (arm-e-v20) as a "fix_tb" action. The
  edits are stashed there (`git -C /opt/Xilinx/coresmith/arm-e-v20 stash list`). WP-25 adds
  `_engine_checkout_guard()` (`CORESMITH_ENGINE_READONLY=1|revert`) after every chip-lead
  decision plus a prompt rule.
- The chip lead co-tuned the deterministic BFM testbench (AES). WP-29 makes the
  deterministic TB immutable (never reused after `fix_tb`, `fix_tb` withheld from the
  payload, prompt rule).
- Operator files added under `inputs/` after `run start` tripped the oracle-integrity
  manifest (as designed). Not an engine bug; noted so you can recognise the events.
- AX25 conformance parked on `irq_irq` vs `irq` (doubled-token collapse rule in
  `canonical_port`). The author renamed the port in the RTL by hand (backups
  `*.pre_irq_rename`) and resumed; WP-30 puts the rule in the prompts. Judge whether the
  hand rename is consistent with "no deterministic port repair" (it was a manual operator
  action, not an engine action; the author believes it is, but it is a fair challenge).
- AX25 integration review looped on a stale assembled `user_project_wrapper.v`; the author
  moved it to `rtl/integration/_stale_1534/` by hand and WP-31 automates the retirement.

## 3. Engine changes to review (branch `arm-e/simplify-master`, range `845353c^..a736450`)

All commits carry tests; the full suite was green after each (3,132 tests at a736450).
Design rationale per WP is in `/opt/Xilinx/coresmith/review-20260907/coresmith-arm-e-design.md`
(sections WP-15 … WP-31). Prompt files live under `orchestrator/langchain/prompts/`.

| WP | Commit | What it claims to do | Main files |
|---|---|---|---|
| 15 | 845353c | Codex outage classified as infrastructure; `_maybe_squeeze_throughput` deleted; ROM staging for `$readmemh`; acceptance stimulus fallback; chip-lead port-shape policy | `coresmith_llm.py`, `pipeline_graph.py`, `rtl_generator.py`, `integration_helpers.py`, `reference_oracle.py`, prompts |
| 16 | a39adf8 | Infrastructure attempts do not count against the retry budget; backoff `min(60·2^(n-1), 900)`; human asked only after `CORESMITH_INFRA_MAX_RETRIES` | `pipeline_graph.py`, `chip_lead.md` |
| 17/17b | 63b7764, 91ab3ec | Integration design name from the real top module; backend synthesizes the top the frontend DV'd | `integration_helpers.py`, `backend_graph.py` |
| 18 | 96f9870 | Acceptance stage drives the published stream contract (cfg plane, gaps, backpressure) and judges with the task predicate | `acceptance_dv.py` |
| 19 | c22b4d1 | Acceptance failure parks as `validation_dv_failure` phase `acceptance_dv` for the chip lead | `pipeline_graph.py`, `chip_lead.md` |
| 20 | c65803c | Architecture final-review feedback bounded (`CORESMITH_ARCH_MAX_FINAL_FEEDBACK`=2); clock/reset naming follows the contract | `architecture_graph.py`, prompts |
| 21/21b | a78d6fa, 447d26c | Conformance gate knows the `valid_only` strobe; flow-control extras reported, not failed | `contract_conformance.py` |
| 22 | c16ff97 | Hierarchy-aware `assert_blocks_instantiated`; postcondition failure parks with a retry self-loop; prompts use the spec's reset | `pipeline_graph.py`, prompts |
| 23 | e32d60b | Outer OpenFrame/Caravel wrapper blocks dropped from the block set (`_drop_outer_wrapper_blocks`) | `pipeline_graph.py`, `block_diagram.md`, `chip_lead.md` |
| 24 | 584f9dd | A policy-authored `user_project_wrapper` block is adopted as the chip top | `pipeline_graph.py` |
| 25 | 6e7c14b | MAX-GEOMETRY gate advisory when the deterministic BFM is the oracle; engine checkout read-only guard | `pipeline_graph.py`, `chip_lead.md` |
| 26 | 73a3f90 | Caravel assembler skips illegal contract-derived names instead of aborting | `integration_helpers.py` |
| 27 | 785e5a4 | `-Wno-fatal` sim builds; Caravel-assembled result persisted | Makefile templates, `pipeline_graph.py` |
| 28 | fdf2d90 | QSPI BFM records `drive_violations` when a read nibble is undriven; DV and conformance assert none | `bfm_lib/qspi_master_bfm.py`, codegen |
| 29/29b | 1fff807, fc63452 | Deterministic BFM testbench immutable; `fix_tb` withheld; prompt rules | `pipeline_graph.py`, `chip_lead.md`, `contract_audit.md` |
| 30 | 858a775 | Port-name collapse rule stated in uarch/RTL prompts | prompts |
| 31 | a736450 | Stale assembled integration artifacts retired on targeted re-entry | `pipeline_graph.py`, `chip_lead.md` |

Specific questions per change:
- WP-18: any divergence from `stream_tb.py` that changes verdicts? Is the 1 byte/word
  packing and cfg layout general or h264-specific? Is this a harness feature or a
  task-specific shim that violates "lightweight"?
- WP-21/21b: does treating flow-control extras as non-deviations open a hole where a chip
  with the wrong handshake passes conformance?
- WP-23/24: is the wrapper-block heuristic (`_OUTER_WRAPPER_BLOCK_NAMES`) sound, or a
  name-based hack that will misfire on legitimate blocks?
- WP-25: was making MAX-GEOMETRY advisory for the deterministic BFM a loss of a real check?
- WP-26: skipping an illegal name silently — should it park instead?
- WP-27: `-Wno-fatal` hides warnings that previously failed builds; which ones, and is
  any of them a correctness signal?
- WP-28/29: is the BFM's drive check itself correct (tri-state semantics under Icarus and
  Verilator), and is "never reuse a deterministic TB" too blunt?
- WP-25 guard: can the chip lead still route around it (e.g. edit via a subprocess)?
- Overall: WP-15…WP-31 added code. Which of them should instead have been deletions, and
  what is the minimum engine that still produces the results in section 2?

## 4. Plan to corroborate or amend

1. WP-32: an oracle-backed acceptance stage for the Caravel pad-stream shape (capture the
   tx pads through the deterministic BFM, compare against the golden's serialized output,
   enforce the task's cycle budget). Would have caught AX25.
2. WP-33: carry `task.yaml` throughput/timeout budgets into the deterministic DV so a
   functionally correct but 2,500× too slow chip fails inside the engine.
3. Re-drive ax25_9600 (task-only) and h264 F-2 with the new gates; 2–3 attempts each.
4. HEVC (gpt-6-astra medium worker, task-only) on the latest engine; the harness-vs-raw
   comparison the owner wanted.
5. Not planned, but the reviewer may argue for it: mcu3 graded by an independent TB;
   a second seed per task to separate engine effect from LLM variance.

## 5. Evidence map and commands

- Review clone (read/grep/test freely): `/opt/Xilinx/coresmith/arm-e-review2`
- Test suite: `cd /opt/Xilinx/coresmith/arm-e-review2 && /home/ubuntu/coresmith-venv/bin/python -m pytest orchestrator/tests -q -x -p no:cacheprovider`
- Live engine checkouts (read only): `/opt/Xilinx/coresmith/arm-e-v12 … v30`
- Results doc: `/opt/Xilinx/coresmith/review-20260907/SHADOW_RESULTS_20260905.md`
  (Arm F-1/F-2/F-3/F-3b sections, multi-IP sweep entries, final table)
- Design doc: `/opt/Xilinx/coresmith/review-20260907/coresmith-arm-e-design.md`
- Previous review round (yours, 2026-09-06) and its packet: `/opt/Xilinx/coresmith/review-20260906/`
  (CODEX_REVIEW.md, REVIEW_PACKET_20260906.md, trajectory audits). Check whether the
  author acted on your earlier recommendations and say where they did not.
- Run dirs: `/opt/Xilinx/coresmith-runs/h264-arm-f-{1,2,3}`, `/opt/Xilinx/coresmith-runs/{mcu3,fft256_qspi,aes_qspi,ax25_9600}-arm-g-1`
  (state and event logs under `.coresmith/`, chip-lead decisions in the state store)
- Graded artifacts: `/home/ubuntu/coresmith-benchmarks/shadow-20260905/` (h264),
  `/home/ubuntu/coresmith-benchmarks/sweep-20260907/<task>/` (metrics.json, staged RTL, logs)
- Published graders: `/home/ubuntu/ppabench/chassis/accel/tasks/<task>/grade_accel.py`,
  `/home/ubuntu/ppabench/benchmarks/stream_core/common/stream_tb.py`
- Task seeds: `/home/ubuntu/task-seeds/<task>/`
- AES differential experiments: `/tmp/aes_pub_dbg/`, `/tmp/aes_icarus_check/`, `/tmp/tl_pub.txt`, `/tmp/tl_eng.txt`
- Cost accounting: `python3 /tmp/run_stats.py <run-dir>`

## 6. Questions the author wants answered

1. Do the primary artifacts support the four sweep verdicts and the F-3b 19/19?
2. Is claim G-c right, or is there a failure in the trajectories that a stricter existing
   gate would have caught?
3. Which of WP-15…WP-31 would you reverse, and which are missing a test that matters?
4. Is WP-32 the right next investment, or is the pad-stream shape rare enough that the
   budget belongs elsewhere (F-2 re-drive, HEVC, second seeds)?
5. Does anything in the engine now overfit to the four tasks (h264 stream contract, QSPI)?
