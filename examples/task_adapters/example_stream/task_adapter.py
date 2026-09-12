# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
#
# coresmith-python: python3
# coresmith-timeout-s: 900
"""A complete, self-contained task adapter.

The task here is deliberately trivial so the file stays readable: the chip must
add a constant to each input word and raise ``out_valid`` for one cycle. What
matters is the shape, which is the same for a real task:

  * declare CASES, TOP and LABEL up front, so a receipt that omits a case is
    recognised as incomplete rather than read as a pass;
  * build and drive the candidate inside ``workdir``, the only writable place;
  * compare against the task's own reference, not against anything the engine
    believes;
  * report a per-case verdict and, optionally, budgets.

A real adapter normally calls the task's published grader instead of the local
reference below. Everything else stays as it is here.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

# Every case the receipt must report. The engine parks if one is missing.
CASES = ["ramp", "saturating", "zeros"]

# The top module this adapter drives. Checked against the task's declared top;
# a mismatch is reported as a boundary mismatch rather than a functional failure.
TOP = "dut"

# Shown verbatim wherever the verdict appears. Say what the verdict does and
# does not establish; an author smoke test should not read like a grader.
LABEL = "example adapter: constant-add reference, 3 cases"

ADDEND = 7
VECTORS = {
    "ramp": list(range(16)),
    "saturating": [250, 251, 252, 253, 254, 255],
    "zeros": [0] * 8,
}
CYCLE_BUDGET_PER_WORD = 4.0


def reference(values: list[int]) -> list[int]:
    """The task's own definition of correct."""
    return [(v + ADDEND) & 0xFF for v in values]


TB = """
`timescale 1ns/1ps
module tb;
  reg clk = 0, rst_n = 0, in_valid = 0;
  reg  [7:0] in_data;
  wire [7:0] out_data;
  wire out_valid;
  integer fd, i, n, v, cycles;
  reg [7:0] stim [0:255];

  {top} dut_i (.clk(clk), .rst_n(rst_n), .in_valid(in_valid),
               .in_data(in_data), .out_data(out_data), .out_valid(out_valid));

  always #5 clk = ~clk;

  initial begin
    $readmemh("stim.hex", stim);
    fd = $fopen("out.txt", "w");
    n = {count};
    repeat (4) @(posedge clk);
    rst_n = 1;
    cycles = 0;
    for (i = 0; i < n; i = i + 1) begin
      @(negedge clk); in_valid = 1; in_data = stim[i];
      @(posedge clk); cycles = cycles + 1;
      @(negedge clk); in_valid = 0;
      v = 0;
      while (v == 0 && cycles < 10000) begin
        @(posedge clk); cycles = cycles + 1;
        if (out_valid) begin $fwrite(fd, "%0d\\n", out_data); v = 1; end
      end
      if (v == 0) begin $fwrite(fd, "TIMEOUT\\n"); i = n; end
    end
    $fwrite(fd, "CYCLES %0d\\n", cycles);
    $fclose(fd);
    $finish;
  end
endmodule
"""


def _run_case(name: str, candidate: dict, work: Path) -> dict:
    case_dir = work / name
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True)

    values = VECTORS[name]
    (case_dir / "stim.hex").write_text("".join(f"{v:02x}\n" for v in values))
    (case_dir / "tb.v").write_text(TB.format(top=candidate["top"], count=len(values)))

    sources = [*candidate["sources"], str(case_dir / "tb.v")]
    build = subprocess.run(
        ["iverilog", "-g2005", "-o", "sim", *sources],
        cwd=case_dir, capture_output=True, text=True, timeout=300,
    )
    if build.returncode != 0:
        # A build failure is not a functional verdict: report it as such so the
        # engine parks for a decision instead of recording a design failure.
        return {"ok": False, "kind": "infrastructure_error",
                "detail": build.stderr.strip()[-400:]}

    run = subprocess.run(["vvp", "sim"], cwd=case_dir,
                         capture_output=True, text=True, timeout=600)
    out_file = case_dir / "out.txt"
    if not out_file.is_file():
        return {"ok": False, "kind": "infrastructure_error",
                "detail": f"no output produced: {run.stdout[-300:]}"}

    got, cycles = [], None
    for line in out_file.read_text().splitlines():
        if line.startswith("CYCLES "):
            cycles = int(line.split()[1])
        elif line == "TIMEOUT":
            return {"ok": False, "kind": "functional_fail",
                    "detail": "DUT never raised out_valid", "cycles": cycles}
        elif line.strip():
            got.append(int(line))

    want = reference(values)
    ok = got == want
    detail = "" if ok else f"expected {want[:8]}, got {got[:8]}"
    return {"ok": ok, "kind": None if ok else "functional_fail",
            "cycles": cycles, "detail": detail}


def grade(candidate: dict, workdir: str) -> dict:
    work = Path(workdir)
    cases = {name: _run_case(name, candidate, work) for name in CASES}

    words = sum(len(VECTORS[n]) for n in CASES)
    spent = sum(c.get("cycles") or 0 for c in cases.values())
    per_word = (spent / words) if words else 0.0
    budgets = {
        "cycles_per_word": {
            "ok": per_word <= CYCLE_BUDGET_PER_WORD,
            "measured": round(per_word, 3),
            "budget": CYCLE_BUDGET_PER_WORD,
        }
    }
    return {
        "cases": cases,
        "budgets": budgets,
        "detail": json.dumps({"addend": ADDEND, "words": words}),
    }
