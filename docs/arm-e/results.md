# Shadow results: Arms A/B/C/D under the published contract (2026-09-05, written 07:59 UTC)

Evidence root on e6: `/home/ubuntu/coresmith-benchmarks/shadow-20260905/`. Nothing under the main
experiment root `sqlite-h264-20260905/` was modified. Arm D run dir: `~/coresmith-runs/h264-codex-20260905-arm-d`
(meta, logs, timing: `...-arm-d-meta/`).

## Arm D: raw matched Codex, no CoreSmith
Same prompt as A/B/C (`requirements.md`, md5 21db3d8c...), piped on stdin; Codex CLI 0.144.4, gpt-5.6-sol/high,
one session, empty directory, no shared twelve-block architecture. First segment 06:36:49-06:55:45 ended with an
API "Selected model is at capacity" failure (not a worker fault); resumed with `codex exec resume` 07:00:32-07:34:04
(exit 0). Worker time 52.5 min, wall 57.3 min. Usage 7,929,517 input tokens (7,708,288 cached), 56,800 output.
Output: `ppab_dut.sv` (937 lines, single module), generated `h264_tables.vh`, `model_i4.py`, `README.md`.
Strategy: I4x4 DC luma + DC chroma, one-frame source buffer, pauses input while encoding each frame.

Contamination audit (`arm-d-meta/worker*.jsonl`, 50 commands): no reads of any other arm's run dir, RTL, models or
bitstreams. It did read `~/h264-ppabench/AB_REPORT_20260823.md`, `absynth/ARM_V_PPA.md`, `absynth/synth_sta.sh`,
`absynth/coresmith_run.log` at 07:31:31-07:31:41 UTC, after its last RTL edit (ppab_dut.sv mtime 07:27:52).

## Functional: full mission under the ACTUAL published cocotb `StreamHarness` (unmodified)
`cocotb-full/<arm>/result.csv`. 18 clips (gradients/moving_box/random x seeds 0,1,42,101,607,4093), 6 frames,
bp_prob 0.15, continuous input, timeout 600,000 cycles; plus gap stress = random/42 with in_gap_prob 0.10.
Harness rng seeded with the case seed. Verilator 5.020, `--assert`.

| Arm | pass | worst cycles/frame | mean cycles/frame | min PSNR dB | gap stress |
|---|---:|---:|---:|---:|---|
| A | 0/18 | timeout every case | | | timeout |
| B | 0/18 | timeout every case | | | timeout |
| C | 18/18 | 78,204.7 | 66,615.4 | 33.52 | pass, 78,706.7 |
| D | 18/18 | 77,327.0 | 61,900.4 | 33.53 | pass, 77,812.8 |

Common C++ checker (`verification/run_benchmark.py`) on D: FAIL on all cases in both sampling modes with
`PROTOCOL_FAILURE in_ready bubbled within a continuously offered frame` after exactly 4,607 accepted bytes.
The spec (materials/spec.md, protocol rules) explicitly permits pausing mid-stream via refuse-and-drop, and the
published cocotb harness accepts D. The C++ driver's no-bubble rule is stricter than the contract; it did not
affect A/B/C. Do not grade D with that driver alone. `functional-arm-d/<mode>/` holds the failing runs.

## Synthesis: memory-blackboxed variant of the common flow
Common flow (`ppa/measure.sh`) blackboxes only CoreSmith native `cs_sram` wrappers and runs `memory_map` on
inferred arrays. D has no native wrappers; its 29 inferred memories (171,504 bits: 8x8192 rbsp, 2x 8x4608 frame
and recon, CAVLC ROMs, small work arrays) were mapped to 144,432 flops giving 6,131,628 um2, which is an artifact.
The spec says the grader blackboxes inferred memories as SRAM macros. `ppa-memblackbox-<arm>/` reruns the same
Yosys/ABC/Liberty flow with `memory_map` removed (all memories left as unmapped `$mem_v2`) for all four arms:

| Arm | std-cell area um2 | std cells | flip-flops | unmapped inferred mems | native macros (common flow) |
|---|---:|---:|---:|---:|---:|
| A | 1,044,035 | 160,824 | 13,367 | 6 | 9 (146,688 bits) |
| B | 773,665 | 124,162 | 7,112 | 10 | 9 (146,688 bits) |
| C | 1,076,549 | 186,807 | 2,365 | 8 | 7 (103,680 bits) |
| D | 102,839 | 17,916 | 322 | 29 (171,504 bits) | 0 |

Caveats: logic-only; no timing claim (pre-layout STA in the common flow is inconclusive for all arms); D's
sub-100-bit arrays would be flops in silicon (negligible); D is not bound to the shared twelve-block
architecture, so architecture-conformance findings do not apply to it and its storage layout differs.

## Wall time to a passing candidate (different endpoints; see RUNS.md)
A 310.57 min, B 120.47 min, C 91.93 min, D 52.5 min worker / 57.3 min wall.

## HEVC task (Main profile, intra-only, CABAC, 64x48, QP 28), launched 08:18 UTC, gpt-6-astra medium
Materials: `e6:~/hevc-ppabench/materials/` (no constants pack). Checker: `hevc-20260905/verification/run_stream_check.sh`
(shipped cocotb StreamHarness + grade_hevc.py, 18 clips + gap stress, 100k cycles/frame limit).

| Arm | wall time | result under the real harness | worst cycles/frame | min PSNR (grad/box/rand) | tokens |
|---|---:|---|---:|---|---:|
| Raw gpt-6 (one session, prompt only) | 19.9 min (08:18:27-08:38:20) | 19/19 PASS (`shadow-20260905/hevc-raw-full/result.csv`) | 73,916 | 37.75 / 34.93 / 33.43 | 2.10M in (1.99M cached), 29.6K out |
| Thin harness (coresmith-simple, arch->uarch->frontend, gpt-6 medium all stages) | architecture 6 min, uArch 12 min, frontend running since 08:36:54 | pending (frontend gate = full mission) | worker-reported smoke 90,880 | | |

The raw arm's design: single `ppab_dut.v` (1,558 lines), 16x16 CTU / 8x8 CU, DC prediction, one TU per CU,
CABAC; validated against its own Python model (`model/hevc.py`). Independent PPA pending (memory-blackboxed synthesis
in `shadow-20260905/ppa-memblackbox-hevc-raw/`).

### HEVC harness arm result (frontend completed 09:08:22 UTC)
Thin harness (coresmith-simple @ aae6769d), gpt-6-astra medium for all stages, driver budgets 3/3/8, one attempt per stage:
architecture 08:18:58-08:25:06 (6.1 min), uArch 08:25:06-08:36:54 (11.8 min), frontend 08:36:54-09:08:22 (31.5 min incl. the
completion gate). Total 49.4 min. Tokens: 0.41M + 1.23M + 5.32M = 6.96M input (6.71M cached), 57.6K output.
Gate (`hevc-harness-20260905/reports/functional-full/result.csv`, run by `frontend complete`): 19/19 PASS, worst 85,456.7
cycles/frame, mean 69,686, min PSNR 34.7 (random) / 36.21 (moving_box) / 38.72 (gradients). The worker also declared and
passed its own checks: 03-rom-headers, 05-cabac-unit, 15-protocol, 20-synthesis, 30-sta-50mhz (WNS 0.0 ns with a logical
SRAM abstraction; worker-recorded 14,597 cells / 1,942 FF / 134,106 um2 / 77,632 abstract SRAM bits).

| HEVC arm | wall | tokens in | pass | worst cpf | min PSNR |
|---|---:|---:|---|---:|---:|
| raw gpt-6 | 19.9 min | 2.10M | 19/19 | 73,916 | 33.43 |
| thin harness + gpt-6 | 49.4 min | 6.96M | 19/19 | 85,457 | 34.70 |

Memory-blackboxed synthesis, HEVC (all inferred memories left as unmapped $mem_v2; same Yosys/ABC/Liberty flow):

| HEVC arm | std-cell area um2 | std cells | FF | memory bits (arrays) |
|---|---:|---:|---:|---:|
| raw gpt-6 | 127,500 | 20,009 | 678 | 881,904 (33) incl. a 9x65536 bin buffer and 8x16384 output buffer |
| thin harness + gpt-6 | 117,159 | 18,631 | 731 | 107,240 (36) |

## Arm E (simplified master, engine f1f1a85 -> 595e819), graded 2026-09-05 23:44 UTC

Run: `e6:~/coresmith-runs/h264-arm-e-20260905` (Arm B seeds: same ERS / block diagram / 29 contract edges;
gpt-5.6-sol high, in-graph chip lead). Engine signoff: PASS 12/12 blocks, 14 TBs, cov(min) 88.6%,
Fmax 50 MHz, integration DV pass, validation DV pass (9/9). 242 LLM calls, 32.1 LLM-hours,
463M input tokens (438M cached), 4.8M output, 21 chip-lead decisions, 59 interrupts, 55 rounds.

Published cocotb harness (`shadow-20260905/cocotb-full/arm-e/result.csv`, TOPLEVEL=chip_top,
same 19 cases as A-D): **0/19 pass**. All 18 `continuous` cases time out at 600,001 cycles with
3.6k-13k output words; the `gap_stress` case reaches `done` in 432,086 cycles (72,014 cycles/frame,
faster than C 78,707 / D 77,813) but its 15,776-byte stream fails decode_ok / decode_clean / psnr_ok.
Same failure class as A and B (engine DV green, published sampler red). The ERS interface is
correct (one byte per word, 64x48 pinned, 4608 B/frame, matches spec.md), so the defect is inside
the design's stream handling (the spec's refuse-and-drop rule is the prime suspect).

Memory-blackboxed synthesis (`shadow-20260905/ppa-memblackbox-arm-e/stat.txt`, same flow as A-D):
647,965 um2, 7,379 dfxtp flops, 19 unmapped memories (cs_fpmem + ROM tables).
Reference: A 1,044,035 / B 773,665 / C 1,076,549 / D 102,839 um2.

Engine fixes needed to finish: 2.4 GB validation VCD audit wedged the host (OCI reset), then
timed out and vetoed a 9/9 pass -> commit 595e819 (audit skipped >1 GiB; skipped/timed-out audit is advisory).

## Arm E2 (simplified master + targeted revise + single-context uArch + post-edge sampler contract), graded 2026-09-06 10:40 UTC

Run: `e6:/opt/Xilinx/coresmith-runs/h264-arm-e2-20260906` (Arm B seeds; gpt-5.6-sol high; in-graph chip lead;
engine 3cd37ab at signoff, hot-swapped twice during the run: f17c5e9 -> b1e7363 -> 3cd37ab). Engine signoff PASS
12/12, 14 TBs, cov(min) 81.04%, Fmax 50 MHz, integration DV pass (after 5 rounds), validation DV pass (retry).
152 LLM calls, 13.05 LLM-hours, 245M input tokens (231M cached), 1.93M output, 20 chip-lead decisions,
42 interrupts, 30 block rounds; chip-lead hand patches: 2 fix_rtl, 4 fix_tb. Wall 10.0 h incl. ~2 h parked
on the WP-9/9c gate contradiction.

Published cocotb harness (`shadow-20260905/cocotb-full/arm-e2/result.csv`, TOPLEVEL=chip_top, same 19 cases):
**19/19 pass, 0 timeouts.** Cycles/frame: gradients ~58.3k, moving_box ~61.4k, random ~83.8k (C: 59.8k/62.4k/77.9k;
D: 50.6k/58.0k/77.1k); min PSNR 33.5-36.9 dB. First engine arm to pass the published sampler.

Memory-blackboxed synthesis (`shadow-20260905/ppa-memblackbox-arm-e2/stat.txt`, same flow): 551,708 um2,
3,817 dfxtp flops, 20 unmapped memories (E 647,965; B 773,665; A 1,044,035; C 1,076,549; D 102,839).

What changed vs Arm E (same seeds): WP-7 targeted revise + single-context uArch (spec stage 34 min vs 13 LLM-h),
WP-8 post-edge handshake contract, WP-9/9c handshake ports in the shared port derivation, WaveKit audit removed.
Caveat (Codex review): engine hot-swapped mid-run and Arm B architecture inherited -- a debugging trajectory,
not a clean experiment; Arm F (task-only seeds, 3 attempts) is the clean one.

## Arm F-2 (task-only seeds, engine v10 -> v13 mid-run), graded 2026-09-06 22:10 UTC

Run: `e6:/opt/Xilinx/coresmith-runs/h264-arm-f-2` (no Arm B architecture: the engine derived 12 blocks from
`inputs/requirements.md`; gpt-5.6-sol high; in-graph chip lead; engine 40b80b7 (v10) at launch, moved to
845353c (v12) then a39adf8 (v13) after the Codex usage-limit outage). Engine signoff PASS 12/12, 14 TBs,
cov(min) 79.92% (aggregate 92.78%), Fmax 50 MHz, integration DV 8/8, validation DV 6/6 (248 requirements,
decode/PSNR requirements marked `deferred_to_golden_sweep`), Acceptance DV SKIPPED ("chip top is not the framed
s_axis/m_axis + sideband shape this harness covers"). 96 LLM calls, 8.72 LLM-hours, 132M input tokens
(125M cached), 1.27M output, 16 chip-lead decisions (5 approve, 4 accept, 3 revise, 1 fix_rtl, 1 fix_tb, 1 skip,
1 continue). Wall 11:33-21:59 UTC including ~5 h parked on the Codex usage limit.

Published cocotb harness (`shadow-20260905/cocotb-full/arm-f-2/result.csv`, TOPLEVEL=h264_normative_rom_arbiter,
same 19 cases): **0/19 pass, 0 timeouts.** Every case streams to completion (gradients ~67.7k, moving_box ~71.3k,
random ~93.7k cycles/frame; 5.3-16.7 KB per 6 frames), the container is valid (SPS/PPS, 6 intra slices,
frame count OK), but ffmpeg rejects the first non-zero macroblock: "negative number of zero coeffs at 1 0",
"corrupted macroblock 1 0 (total_coeff=-1)" -- a CAVLC entropy-coding defect (dump:
`cocotb-full/arm-f-2-dump/streams/gradients_0.264`). Not the Arm E sampling-contract class: framing and
handshakes are right.

Memory-blackboxed synthesis (`ppa-memblackbox-arm-f-2/stat.txt`): 411,232 um2, 6,181 flops, 18 unmapped memories
(E2 551,708; E 647,965; D 102,839).

Finding: the engine signed the chip off without ever decoding its output. The validation TB deferred every
decode/PSNR requirement to an external "golden sweep" that no stage performs, and the acceptance stage only
drives one interface shape (framed s_axis/m_axis), not the published cfg/start/in/out stream contract. WP-18:
teach the acceptance stage the published StreamHarness shape and grade the real stream with the task's grader.

## Arm F-1 (task-only seeds, engine v9 -> v12), graded 2026-09-06 22:50 UTC -- FIRST CLEAN ENGINE PASS

Run: `e6:/opt/Xilinx/coresmith-runs/h264-arm-f-1` (no Arm B artefacts; the engine derived 12 blocks / 32 contract
edges from `inputs/requirements.md`; gpt-5.6-sol high; in-graph chip lead; engine 5572739 (v9) at launch, moved
to 845353c (v12) after the Codex usage-limit outage). Engine signoff PASS 12/12, 14 TBs, cov(min) 72.31%
(aggregate 92.43%), Fmax 50 MHz, integration DV 7/7 (5 rounds), validation DV 5/5 (223 requirements; one
targeted revise of cavlc_syntax_engine + one fix_tb). 119 LLM calls, 12.64 LLM-hours, 211.6M input tokens
(200.1M cached), 1.87M output, 22 chip-lead decisions (4 approve, 4 accept, 3 revise, 2 revise_interface,
4 fix_tb, 1 fix_rtl, 1 retry, 3 continue). Wall 07:13 (architecture) / 08:55 (block flow) -> 22:02 UTC
including ~5 h parked on the Codex usage limit.

Published cocotb harness (`shadow-20260905/cocotb-full/arm-f-1/result.csv`,
TOPLEVEL=h264_enc_streaming_baseline_profile_intra_only_h_264_encoder_core_64x48_top, same 19 cases):
**19/19 pass, 0 timeouts.** Cycles/frame: gradients 65.1-65.4k, moving_box 70.0-70.6k, random 85.0-86.0k
(E2 58.3k/61.4k/83.8k; C 59.8k/62.4k/77.9k; D 50.6k/58.0k/77.1k); min PSNR 33.5 (random) / 35.2 / 36.2 dB.
The WP-18 acceptance stage (3 six-frame cases, ffmpeg decode + PSNR predicate) passes it in 7.9 s.

Memory-blackboxed synthesis (`ppa-memblackbox-arm-f-1/stat.txt`): 563,956 um2, 7,386 flops, 25 unmapped memories
(E2 551,708; F-2 411,232; E 647,965; D 102,839).

## Arm F-3 (task-only seeds, engine v9 -> v12), graded 2026-09-06 22:50 UTC

Run: `e6:/opt/Xilinx/coresmith-runs/h264-arm-f-3` (12 blocks, different decomposition; same engine path as F-1).
Engine signoff PASS 12/12, cov(min) 87.18% (aggregate 94.85%), Fmax 50 MHz, integration DV 7/7 (after 4 rounds:
1/7 -> 6/7 -> 7/7 via chip-lead fix_rtl x2 + fix_tb x2), validation DV 7/7 (200 requirements, decode/PSNR
deferred). 103 LLM calls, 10.80 LLM-hours, 146.3M input (136.3M cached), 1.57M output, 23 chip-lead decisions
(3 approve, 4 accept, 4 revise_interface, 4 fix_rtl, 3 fix_tb, 1 feedback, 4 continue). Wall 09:21 -> 22:04 UTC
including ~5 h parked on the Codex usage limit.

Published cocotb harness (`cocotb-full/arm-f-3/result.csv`,
TOPLEVEL=h264_enc_streaming_baseline_profile_intra_only_h_264_encoder_core_64_48_top): **7/19 pass, 0 timeouts.**
random 7/7 (41.5-42.1k cycles/frame -- the fastest engine chip so far, min PSNR 33.5 dB); gradients 0/6 and
moving_box 0/6 fail to decode (36.0-37.8k cycles/frame, streams complete) -- a content-dependent entropy/skip
defect the engine's DV never saw, same class as F-2 (dump under `cocotb-full/arm-f-3-dump/streams/`).

Memory-blackboxed synthesis (`ppa-memblackbox-arm-f-3/stat.txt`): 694,818 um2, 7,380 flops, 12 unmapped memories.

## Arm F summary (3 task-only attempts, one engine lineage v9..v13)

| Attempt | Engine signoff | Published sampler | cpf (grad/box/rand) | Area (memblackbox) | LLM-h / calls |
|---|---|---|---|---|---|
| F-1 | PASS 12/12, cov 72.3% | **19/19** | 65.2k / 70.3k / 85.5k | 563,956 um2 / 7,386 FF | 12.64 / 119 |
| F-2 | PASS 12/12, cov 79.9% | 0/19 (CAVLC total_coeff=-1) | 67.7k / 71.3k / 93.7k | 411,232 um2 / 6,181 FF | 8.72 / 96 |
| F-3 | PASS 12/12, cov 87.2% | 7/19 (random only) | 36.0k / 37.8k / 41.8k | 694,818 um2 / 7,380 FF | 10.80 / 103 |

All three signed off green on the engine's own scorecard; only one is right. The gap is exactly the missing
mission-level decode check (WP-18: the acceptance stage now drives the published stream contract and grades
with the task's predicate -- E2 and F-1 pass it, F-2 fails it, in under 10 s each).

## Arm F-3b: F-3 re-driven on v17 with the WP-18/19 acceptance gate, graded 2026-09-07 05:45 UTC

Same run dir (`h264-arm-f-3`), engine moved to c22b4d1 (v17) and re-entered at `validation_dv` via
`run restart-node`. The acceptance stage (3 six-frame cases, ffmpeg + PSNR predicate) FAILED 2/3 and parked as
`validation_dv_failure` (phase acceptance_dv). In one 52-minute session the chip lead graded the captured
streams offline, localized CAVLC (luma-DC nC used the wrong neighbour contexts; residual syntax was still
emitted for CBP-omitted blocks), applied `fix_rtl` to cavlc_slice_engine, and the acceptance stage passed 3/3
(PSNR 36.26 / 35.35 / 33.68 dB). Cost of the fix: 1 chip-lead call.

Published cocotb harness (`cocotb-full/arm-f-3b/result.csv`, same 19 cases): **19/19 pass, 0 timeouts.**
Cycles/frame gradients 36.0k, moving_box 37.7-37.8k, random 41.5-42.1k -- the fastest chip of every arm
(F-1 65.2k/70.3k/85.5k; E2 58.3k/61.4k/83.8k; D 50.6k/58.0k/77.1k); min PSNR 36.15 / 35.19 / 33.52 dB.
Memory-blackboxed synthesis: 696,363 um2, 7,385 flops (unchanged class vs F-3: 694,818).

## Arm F summary (updated)

| Attempt | Engine signoff | Published sampler | cpf (grad/box/rand) | Area (memblackbox) | LLM-h / calls |
|---|---|---|---|---|---|
| F-1 | PASS 12/12, cov 72.3% | **19/19** | 65.2k / 70.3k / 85.5k | 563,956 um2 / 7,386 FF | 12.64 / 119 |
| F-2 | PASS 12/12, cov 79.9% | 0/19 (CAVLC total_coeff=-1) | 67.7k / 71.3k / 93.7k | 411,232 um2 / 6,181 FF | 8.72 / 96 |
| F-3 (v12) | PASS 12/12, cov 87.2% | 7/19 (random only) | 36.0k / 37.8k / 41.8k | 694,818 um2 / 7,380 FF | 10.80 / 103 |
| F-3b (v17 gate) | acceptance 3/3 after one fix_rtl | **19/19** | 36.0k / 37.7k / 41.8k | 696,363 um2 / 7,385 FF | +~1 h chip lead |

With the mission-level acceptance gate in the loop, two of three task-only attempts pass the published sampler
(F-2 not re-driven yet).

## Multi-IP sweep on v17 (task-only seeds, 2026-09-07): mcu3

Run: `e6:/opt/Xilinx/coresmith-runs/mcu3-arm-g-1` (requirements = `examples/mcu3/requirements.md`; single
block by design; my ISA-simulator golden). Engine: architecture 04:15 -> 04:45, block flow -> signoff 05:35 UTC
(1 block, 3 TBs, cov(min) 94.53%, Fmax 380 MHz); 21 LLM calls, 1.37 LLM-hours, 9.0M input tokens, 0.22M output,
3 chip-lead decisions (continue / accept / approve). Acceptance stage skipped honestly (dedicated-pin interface).

External grading (`/tmp/mcu3_grade/grade_mcu3.sh`: the requirements' reference program under Icarus + Yosys
sky130_fd_sc_hd): **functional PASS** -- out_o == 8 at cycle 8 after reset (bar: ~10), stall holds pc_o, no
halted_o over 100 cycles; synthesis 493 cells / 3,900 um2 (cap 2,500 cells) -- PASS.

## Multi-IP sweep: fft256_qspi (task-only seeds, engine v17 -> v25 during the run), graded 2026-09-07 10:33 UTC

Run: `e6:/opt/Xilinx/coresmith-runs/fft256_qspi-arm-g-1` (256-point Q1.15 complex FFT accelerator behind a
QSPI-slave interface in the locked Caravel `user_project_wrapper`; requirements + operator-frozen adapters from
the July run; 8 blocks incl. an OpenFrame wrapper block the engine now excludes). Engine signoff PASS 8/8, 11 TBs,
cov(min) 70.97%, Fmax 50 MHz; integration DV 2/2 with the deterministic DUT-blind QSPI BFM; validation DV 4/4
(197 requirements). 97 LLM calls, 7.20 LLM-hours, 89.4M input tokens (82.9M cached), 0.90M output, 13 chip-lead
decisions. Wall 04:15 -> 10:21 UTC including five engine hot-swaps (v17..v25) and two operator pauses.

Published grader (`chassis/accel/tasks/fft256_qspi/grade_accel.py`, hidden cocotb TB through the QSPI-master BFM,
Icarus, seeds 1-5): **verdict PASS.** Functional: 5/5 seeds, max error 2-3 LSB (tolerance 24), SNR 57.1-57.9 dB
(floor 45); 1,570 cycles/transform worst case (budget 7,168 = golden x2; golden 3,584 -> 2.3x faster than the
reference). PPA: 10,629 cells, 81,463 um2, 853 flops (caps 148,500 / 1,920,000 / 40,000); STA WNS +13.0 ns,
Fmax 143.7 MHz at the 50 MHz target.

Engine findings on the way (all fixed in WP-20..WP-27): reset-polarity prompt bias (rst_n forced against
active-high specs), an invented OpenFrame outer-wrapper block, a self-assembled wrapper re-wrapped into a
design-named top the QSPI pin-boundary gate rejected, the MAX-GEOMETRY value heuristic on a fixed-N design,
and a chip lead that patched the engine checkout as its "fix_tb".

## Multi-IP sweep: aes_qspi (task-only seeds, engine v17 -> v25), graded 2026-09-07 14:10 UTC

Run: `e6:/opt/Xilinx/coresmith-runs/aes_qspi-arm-g-1` (AES-128 ECB accelerator behind a QSPI slave in the
locked Caravel wrapper; 6 blocks incl. the pad-adapter `user_project_wrapper`). Engine signoff PASS 6/6, 8 TBs,
cov(min) 81.82%, Fmax 50 MHz; deterministic Caravel assembly (48 wires), integration DV 3/3 with the
deterministic QSPI BFM after one chip-lead fix_tb, validation DV 7/7 (161 requirements) after one fix_rtl.
126 LLM calls, 8.23 LLM-hours, 78.9M input tokens (68.6M cached), 0.89M output, 19 chip-lead decisions.
Wall 04:15 -> 13:58 UTC, of which ~3.2 h were architecture-review churn (before WP-20) and ~1 h operator pauses.

Published grader (`chassis/accel/tasks/aes_qspi/grade_accel.py`, FIPS-197 KAT + 5 random multi-block seeds through
the QSPI-master BFM, Icarus): **verdict FAIL, functional byte-exact FAIL on all 6 cases -- by the LAST NIBBLE
only** (fips197: expected 69c4e0d8...b4c55a, got ...b4c550; seed1: ...cc68a3 vs ...cc68a0), and STATUS.DONE never
observed by the host. Both symptoms fit one defect: the final nibble of every QSPI read is dropped (STATUS's
BUSY/DONE bits live in the low nibble). PPA within caps: 20,501 cells, 175,958 um2, 4,490 flops (caps 30,000 /
337,000 / 6,400); STA WNS +14.0 ns, Fmax 167.5 MHz; throughput 22 cycles/block worst case (budget 42, golden 21).

The engine's own deterministic DUT-blind QSPI BFM passed this chip 3/3: its read path must tolerate the last
nibble the published host does not (WP-28 investigation).

Update 2026-09-07 15:55 UTC: with WP-28 (v26) the engine's deterministic QSPI DV reproduces the grader's AES
failure on the same RTL (drive violations on the released low nibble of STATUS reads) and the run was re-entered
at integration DV so the chip lead can fix the frontend; re-grade pending.

### aes_qspi after the WP-28/29 loop (v27), re-graded 2026-09-07 17:35 UTC: PASS

With the drive check in the deterministic BFM (WP-28) the engine's own integration DV failed the chip; the
chip lead first co-tuned the BFM (`fix_tb`, sampling point + SCK period), so WP-29 made the deterministic
testbench immutable and removed `fix_tb` from its failures; the next decision was `fix_rtl` on the QSPI frontend
and the regenerated DV passed 3/3. Published grader: **verdict PASS** -- byte-exact 6/6 (FIPS-197 KAT + 5 seeds),
STATUS.DONE observed on every case, 22 cycles/block worst case (budget 42, golden 21); 20,305 cells,
175,388 um2, 4,490 flops (caps 30,000 / 337,000 / 6,400); STA WNS +14.0 ns, Fmax 165.3 MHz. Cost of the fix:
2 chip-lead calls plus one operator restart.

## Multi-IP sweep summary (2026-09-07 17:35 UTC)

| Task | Engine signoff | Published grader | Notes |
|---|---|---|---|
| mcu3 | PASS 1/1 (cov 94.5%) | PASS (out==8 @ cycle 8, 493 cells) | single-block smoke task, 1.4 LLM-h |
| fft256_qspi | PASS 8/8 (cov 71.0%) | PASS (5/5 seeds, 1,570 cyc, 10.6k cells) | 7.2 LLM-h; needed WP-22..25 |
| aes_qspi | PASS 6/6 (cov 81.8%) | FAIL -> PASS after WP-28/29 loop | 8.2 LLM-h + 2 chip-lead calls |
| ax25_9600 | in flight (validation) | pending | WP-21/23 fixes; operator port rename |

## Multi-IP sweep: ax25_9600 (task-only seeds, engine v17 -> v30), graded 2026-09-07 20:50 UTC

Run: `e6:/opt/Xilinx/coresmith-runs/ax25_9600-arm-g-1` (AX.25 9600-baud G3RUH FSK transmit modem behind a QSPI
slave in the locked Caravel wrapper; 8 blocks incl. an OpenFrame wrapper block the engine now excludes). Engine
signoff PASS 8/8, 10 TBs, cov(min) 81.78%, Fmax 50 MHz; deterministic Caravel assembly (7 blocks, 50 wires);
integration DV 1/1 (deterministic QSPI conformance, three chip-lead fix_rtl rounds); validation DV 9/9 (213
requirements) after two validation-stage revises. 206 LLM calls, 17.68 LLM-hours, 184.3M input tokens (170.2M
cached), 1.68M output, 21 chip-lead decisions (8 revise, 3 fix_rtl, 3 approve, 3 accept, 2 retry, 1 feedback,
1 continue) -- the most expensive run of the sweep (wall 05:52 -> 16:37 UTC with six engine hot-swaps and one
operator port rename).

Published grader (`chassis/accel/tasks/ax25_9600/grade_accel.py`, 5 seeds x {bit, baseband}): **verdict FAIL --
every case times out at 400,000 cycles.** In bit mode the chip emits 76 of the 281 expected bits (about 5,260
wb_clk per bit: it paces the stream at a real 9600-baud symbol rate from the 50 MHz clock, while the golden
emits at 2.1 cycles/bit and the grader's window assumes that); STATUS.DONE is never observed inside the window;
baseband cases emit 0 samples with 77 stray bits on the inactive port. PPA: 3,227 cells, 25,620 um2, 583 flops,
Fmax 248.7 MHz (within caps). Throughput is score-only per the spec, but the timeout makes it functional.

What the engine had: no oracle-backed mission check for this interface shape. The acceptance stage skips the
Caravel pad shape, the deterministic QSPI DV checks the bus protocol only (the TX pad streams are unmodeled), and
the LLM validation testbench passed 9/9 against its own reading of "9600 symbol rate". Same class as F-2 before
WP-18 and aes before WP-28: what no stage measures, signoff cannot protect.

## Multi-IP sweep summary (final, 2026-09-07 20:50 UTC)

| Task | Engine signoff | Published grader | Cost | Notes |
|---|---|---|---|---|
| mcu3 | PASS 1/1 (cov 94.5%) | PASS (out==8 @ cycle 8; 493 cells) | 21 calls / 1.4 LLM-h | single-block smoke task |
| fft256_qspi | PASS 8/8 (cov 71.0%) | PASS (5/5 seeds, 1,570 cyc/transform, 10.6k cells) | 97 / 7.2 | needed WP-22..25 |
| aes_qspi | PASS 6/6 (cov 81.8%) | FAIL (last read nibble) -> PASS after WP-28/29 + fix_rtl | 126 / 8.2 (+2 calls) | deterministic BFM now checks bus drive |
| ax25_9600 | PASS 8/8 (cov 81.8%) | FAIL (timeout: real-baud pacing; stray bits) | 206 / 17.7 | no mission-level pad-stream check exists |

Together with h264 (F-1 19/19, F-3b 19/19 after the WP-19 loop, F-2 0/19 not re-driven): six task-only runs,
four pass the published grader as delivered or after one engine-guided fix loop; the two failures are both
"no stage measured the graded quantity" gaps, not gate strictness.

## Post-review replay through the task adapters (2026-09-08, engine v31, no LLM)

Review round 2 required that the new acceptance path reproduce the external verdicts on the
retained candidates before any LLM re-drive. Each retained candidate (the exact file set the
published grade used) was run through its task adapter (`/home/ubuntu/task-seeds/<task>/task_adapter.py`,
the published hidden testbench + throughput gate for the accel tasks) via
`orchestrator.harness.task_adapter.run_task_adapter`; receipts under
`e6:/home/ubuntu/coresmith-benchmarks/replay-20260908/<name>/`.

| Candidate | External verdict | Adapter replay | Detail |
|---|---|---|---|
| aes_qspi (repaired) | PASS 6/6 | PASS 6/6, throughput 22.0 vs cap 42.0 cyc/block | fips197 + seed1..5, DONE seen, byte-exact |
| fft256_qspi | PASS 5/5 | PASS 5/5, throughput 1,570 vs cap 7,168 cyc/transform | max err 2–3 LSB, SNR 57.1–57.9 dB |
| ax25_9600 | FAIL 10/10 | FAIL 10/10 `functional_fail` + throughput `budget_fail` | the failure the engine never saw now parks inside the engine |
| mcu3 | smoke PASS | PASS 1/1, labelled "author smoke testbench (NOT an independent published grader)" | |
| h264 F-3b | 19/19 | PASS 4/4 (published StreamHarness + grade_h264, 4-case subset) | decode ok; min PSNR 36.3 / 35.4 / 33.6 / 33.7 dB vs floors 33 / 30 / 30 / 30; 36.0k–42.1k cycles/frame |
| h264 F-2 | 0/19 | FAIL 4/4 `functional_fail` | grade_h264: decode_ok, decode_clean, psnr_ok all fail (the CAVLC defect) |

Engine state: WP-32 … WP-42 on `arm-e/simplify-master` (ff83964), e6 `arm-e-v31`; full suite
3,236 passed / 1 environmental failure (PDK files) with `-m "not live_llm"`.

First h264 replay attempt failed all four F-3b cases by timeout with `$readmem file not found`: the
cocotb runner executes the simulator from `test_dir`, where the adapter had not linked `inputs/`
(normative ROM images). Fixed in the adapter (link in test_dir, build and work dirs); the rerun above
is the corrected adapter. Lesson recorded: an adapter is validated only by reproducing BOTH a known
pass and a known failure on retained candidates before it judges a live run.

## ax25_9600 attempt 2 (task-only, gpt-5.6-sol high, engine v31 → v38, 2026-09-07 19:10 → 09-08 05:57)

Run `e6:/opt/Xilinx/coresmith-runs/ax25_9600-arm-g-2`. Same seeds as attempt 1 plus `inputs/task_adapter.py`
(the published `hidden_tb` + throughput gate as the acceptance oracle, WP-41). Owner instruction: only the
highest-signal evaluation, no mcu3, no h264 re-drive.

**Frontend outcome:** the task adapter PASSES inside the engine (10/10 cases, 1.191 cycles/bit vs the 4.21
cap; attempt 1 measured 5,263) and the ERS validation testbench passes after the chip lead rewrote its
real-time-pacing assertion to the published cap. 21 chip-lead decisions, 137 LLM calls, 14.8 LLM-h to
`pipeline_done`; auto-backend (flat synthesis + gate sim) followed.

**External published grade (grade_accel.py, e6:`~/coresmith-benchmarks/sweep-20260908/ax25_9600-g2/metrics.json`): PASS.**

| Check | Attempt 1 (v17→v30) | Attempt 2 (v31→v38) | Cap / golden |
|---|---|---|---|
| functional byte-exact (10 cases) | FAIL 0/10 (8 timeouts) | PASS 10/10 | -- |
| Dire Wolf decode / negative control | FAIL / -- | PASS / PASS | -- |
| cycles per bit (worst case) | 5,263 | 1.191 (ratio 0.566 of golden 2.105) | cap 4.21 |
| baseband | 0 samples | max err 1.04 LSB, SNR 65.2–65.4 dB | 4 LSB / 45 dB |
| cells / area / FF | 3,227 / 25,620 µm² / 583 | 2,231 / 18,114 µm² / 411 | 12,800 / 152,500 / 3,000 |
| Fmax | 249 MHz | 270.7 MHz (WNS 16.3 ns @ 50 MHz) | 50 MHz |
| power (real, logic only) | -- | 0.855 mW, 20.0 pJ/bit | reported |
| LLM cost | 17.7 LLM-h, 21 decisions | 14.8 LLM-h, 21 decisions | -- |

**What the run exposed, and the engine fixes it forced (all hot-swapped in while it ran):**
- WP-43: the chip lead demanded the `user_project_wrapper` block instantiate all six children while the
  conformance gate forbids it (the engine assembles the top) -- two identical revise rounds. Prompt rule.
- WP-44: the specialist wrote dotted channel names (`start.valid/start.payload`); `channel_signals` dropped
  38 rows silently, blocks lacked ports, three revise rounds until the chip lead re-froze the contract by
  hand. Now a structural violation at interface definition and a gate failure.
- WP-45: the pad block's `` `ifdef USE_POWER_PINS `` port section was parsed as ports ("Pin not found:
  endif"); the deterministic assembly failed lint and the engine fell back to an LLM top with the wrong
  module name. Now: preprocessor-aware parsers, and a locked Caravel boundary parks instead of falling back.
- WP-46/b/c: the assembler merged nets by bare port name across blocks (`start_valid` on modem_controller
  and hdlc_framer), START resolved to X, the published TB saw nothing; the chip lead hand-edited the
  assembled wrapper. Now: signal_specs-derived binding, no bare-name re-union, one driver per net.
- WP-47: the ERS validation TB demanded 5,208 cycles/bit (attempt 1's fatal reading); the chip lead slowed
  the RTL and the adapter re-failed exactly like attempt 1. Prompt rule: the adapter outranks internal
  requirements; the chip lead then reverted the RTL and fixed the TB.
- The deterministic QSPI DV (WP-28/39 strict drive check) caught two real frontend defects on this run
  (OEB released after each READ byte; output nibble shifted on the wrong SCK phase) before acceptance.

**Reading:** the closed loop (adapter fails → park → chip lead fixes RTL → adapter passes) worked as
designed, and every failure the engine could not see in attempt 1 was visible inside attempt 2. The price
was five engine defects surfaced by one run; four of them were silent-drop or wrong-fallback classes of
the kind review round 2 warned about.
