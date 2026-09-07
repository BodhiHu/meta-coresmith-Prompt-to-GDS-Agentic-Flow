# coresmith-python: /home/ubuntu/ppabench-venv/bin/python
# coresmith-timeout-s: 1800
"""mcu3 task adapter: the author's external smoke testbench.

NOT an independent published grader (there is none for mcu3 in ppabench): one
hard-coded program (LDI R1=5; LDI R2=3; ADD R1,R2; OUT R1; JMP 0), OUT==8 within
ten cycles of reset, a two-cycle stall check, no early halt. Labelled as such.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

TB = "/home/ubuntu/task-seeds/mcu3/test_mcu3_external.py"
TOP = "mcu3"
LABEL = "author smoke testbench (NOT an independent published grader)"
CASES = ["reference_program"]


def grade(candidate, workdir):
    wd = Path(workdir) / "sim"
    if wd.exists():
        shutil.rmtree(wd)
    wd.mkdir(parents=True)
    shutil.copy(TB, wd / "test_mcu3_external.py")
    result = wd / "result.json"
    (wd / "Makefile").write_text(
        "SIM ?= icarus\nTOPLEVEL_LANG ?= verilog\n"
        f"VERILOG_SOURCES := {' '.join(candidate['sources'])}\n"
        f"TOPLEVEL := {candidate['top']}\nMODULE := test_mcu3_external\n"
        f"export PYTHONPATH := {wd}:$(PYTHONPATH)\nexport PPAB_RESULT := {result}\n"
        "include $(shell cocotb-config --makefiles)/Makefile.sim\n")
    env = dict(os.environ)
    env["PATH"] = "/home/ubuntu/ppabench-venv/bin:/usr/bin:" + env.get("PATH", "")
    r = subprocess.run(["make", "-s"], cwd=str(wd), env=env, capture_output=True, text=True,
                       timeout=1500)
    (wd / "make.log").write_text((r.stdout or "") + (r.stderr or ""))
    if not result.exists():
        raise RuntimeError(f"smoke TB produced no result (make rc={r.returncode}): "
                           + (r.stdout or "")[-400:] + (r.stderr or "")[-400:])
    res = json.loads(result.read_text())
    ok = bool(res.get("functional_ok"))
    return {"cases": {"reference_program": {"ok": ok, "kind": None if ok else "functional_fail",
                                            "cycles": res.get("cycles"),
                                            "detail": str(res.get("detail", ""))[:600]}},
            "detail": "smoke test only; no published grader exists for mcu3"}
