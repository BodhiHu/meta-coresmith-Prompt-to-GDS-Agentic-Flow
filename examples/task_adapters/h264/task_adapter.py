# coresmith-python: /home/ubuntu/coresmith-venv/bin/python
# coresmith-timeout-s: 7200
"""h264 (ppabench stream_core) task adapter: the PUBLISHED StreamHarness verbatim
plus grade_h264.grade with the task's per-content PSNR floors.

Acceptance subset (declared here, task-owned): one 6-frame clip per content class
at seed 0 with continuous input, plus the gap-stress case (random, seed 42,
in_gap_prob 0.10) -- 4 of the 19 cases of the external campaign, same harness,
same cfg {0: n_frames, 1: width, 2: height, 3: qp}, same 600,000-cycle timeout,
bp_prob 0.15, max_out_words 6*65536. The full 19-case campaign remains the
external grade.

Runs under the coresmith venv (cocotb 2.x runner) with Verilator; the project's
inputs/ (ROM images for $readmemh) is linked into the build directory.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

COMMON = "/home/ubuntu/ppabench/benchmarks/stream_core/common"          # stream_tb.py
GRADER = "/home/ubuntu/h264-ppabench/materials/grade_h264.py"

TOP = None                      # the candidate's own top (chip_top / ppab_dut ...)
LABEL = "published stream_core StreamHarness + grade_h264 (4-case acceptance subset)"
CASE_SPECS = [("gradients", 0, 0.0), ("moving_box", 0, 0.0), ("random", 0, 0.0),
              ("random", 42, 0.10)]
CASES = [f"{c}_s{s}_{'gap' if g else 'cont'}" for c, s, g in CASE_SPECS]

_TB = '''
import importlib.util, json, os, sys
from pathlib import Path
import cocotb
sys.path.insert(0, %(common)r)
from stream_tb import StreamHarness

def _grader():
    spec = importlib.util.spec_from_file_location("grade_h264_adapter", %(grader)r)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

SPECS = %(specs)r
NAMES = %(names)r

@cocotb.test()
async def h264_acceptance(dut):
    g = _grader()
    out = {}
    harness = StreamHarness(dut, clk_period_ns=20, seed=0)
    await harness.start_clock()
    for (content, seed, gap), name in zip(SPECS, NAMES):
        source = g.gen_clip(content, seed, 6).astype("uint8")
        harness.rng.seed(seed)
        await harness.reset()
        try:
            output, cycles = await harness.run_op({0: 6, 1: 64, 2: 48, 3: 28}, source,
                                                  max_out_words=6 * 65536, bp_prob=0.15,
                                                  in_gap_prob=gap, timeout_cycles=600000)
        except RuntimeError as err:
            out[name] = {"ok": False, "kind": "functional_fail", "cycles": harness.cycles,
                         "detail": "published sampler timeout: " + str(err)}
            continue
        stream = bytes(int(w) & 0xFF for w in output)
        r = g.grade(stream, source.tobytes(), 64, 48, g.PSNR_FLOORS[content])
        bad = [k for k in ("container_ok", "intra_only", "decode_ok", "decode_clean", "psnr_ok") if not r.get(k, True)]
        out[name] = {"ok": bool(r["pass"]), "kind": None if r["pass"] else "functional_fail",
                     "cycles": cycles, "cycles_per_frame": cycles / 6.0, "output_bytes": len(stream),
                     "min_psnr_db": r.get("min_psnr_db"), "psnr_floor_db": g.PSNR_FLOORS[content],
                     "detail": ("grade_h264 fails: " + ",".join(bad)) if bad else
                               "decode ok, min PSNR %%s dB >= floor %%s" %% (r.get("min_psnr_db"), g.PSNR_FLOORS[content])}
    Path(os.environ["ADAPTER_RESULT"]).write_text(json.dumps(out, indent=1))
'''


def grade(candidate, workdir):
    try:
        from cocotb_tools.runner import get_runner   # cocotb >= 2.0
    except ImportError:
        from cocotb.runner import get_runner          # cocotb 1.x
    wd = Path(workdir)
    build = wd / "build"
    test_dir = wd / "tb"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    test_dir.mkdir(exist_ok=True)
    (test_dir / "h264_adapter_tb.py").write_text(_TB % {
        "common": COMMON, "grader": GRADER, "specs": CASE_SPECS, "names": CASES})
    inputs = candidate.get("inputs_dir")
    # $readmemh images resolve relative to the simulator's cwd, which the cocotb
    # runner sets to test_dir; link inputs/ there (and beside it) so the
    # normative ROMs load exactly as in the published campaign.
    for d in (test_dir, build, wd):
        link = d / "inputs"
        if inputs and Path(inputs).exists() and not link.exists():
            os.symlink(inputs, link)
    result = wd / "result.json"
    env = dict(os.environ)
    env["ADAPTER_RESULT"] = str(result)
    env["PATH"] = "/usr/bin:" + env.get("PATH", "")
    runner = get_runner("verilator")
    runner.build(verilog_sources=candidate["sources"], hdl_toplevel=candidate["top"],
                 build_dir=str(build), always=True,
                 build_args=["-Wno-fatal", "-Wno-lint", "-Wno-style", "--assert"])
    detail = ""
    try:
        runner.test(hdl_toplevel=candidate["top"], test_module="h264_adapter_tb",
                    test_dir=str(test_dir), build_dir=str(build), extra_env=env)
    except Exception as exc:  # the receipt below tells whether cases were produced
        detail = f"sim error: {type(exc).__name__}: {exc}"
    if not result.exists():
        raise RuntimeError("published harness run produced no result: " + detail)
    cases = json.loads(result.read_text())
    return {"cases": cases, "detail": detail}
