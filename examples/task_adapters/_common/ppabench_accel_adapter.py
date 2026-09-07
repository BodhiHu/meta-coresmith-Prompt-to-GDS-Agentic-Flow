"""Shared task adapter for ppabench accel (Caravel QSPI-slave) tasks.

Runs the PUBLISHED grader's functional part VERBATIM -- ``grade_accel.run_functional``
(hidden_tb.py + qspi_host.py + oracle.py under system Icarus) and its
``_throughput`` gate (task.yaml ``throughput.max_cycles_per_op``) -- on the
candidate. Synthesis / STA / power are the backend's and the final grader's
business, not acceptance. Nothing here is engine code; it is task-owned.

Runs in the ppabench venv (see each task's ``task_adapter.py`` header).
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

PPAB_TASKS = Path("/home/ubuntu/ppabench/chassis/accel/tasks")


def _load_grader(task: str):
    d = PPAB_TASKS / task
    for p in (str(PPAB_TASKS), str(d)):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(f"_ppab_grade_{task}", d / "grade_accel.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def grade(task: str, candidate: dict, workdir: str) -> dict:
    import yaml

    g = _load_grader(task)
    sol = Path(workdir) / "solution"
    if sol.exists():
        shutil.rmtree(sol)
    sol.mkdir(parents=True)
    for s in candidate["sources"]:
        shutil.copy(s, sol / Path(s).name)
    inputs = candidate.get("inputs_dir")
    if inputs and Path(inputs).exists() and not (sol / "inputs").exists():
        os.symlink(inputs, sol / "inputs")     # $readmemh images, if any
    os.environ["PATH"] = "/usr/bin:" + os.environ.get("PATH", "")   # SYSTEM iverilog/vvp

    func = g.run_functional(sol)
    task_yaml = yaml.safe_load((PPAB_TASKS / task / "task.yaml").read_text())
    thr = g._throughput(task_yaml, func.get("cases") or {})

    cases = {}
    for name, c in (func.get("cases") or {}).items():
        ok = bool(c.get("pass", c.get("ok", False)))
        cases[name] = {
            "ok": ok,
            "kind": None if ok else "functional_fail",
            "cycles": c.get("cycles"),
            "detail": json.dumps({k: v for k, v in c.items() if k not in ("pass", "ok")},
                                 default=str)[:600],
        }
    if not cases and func.get("detail"):
        # the sim itself failed (build error, crash): no case can be reported
        raise RuntimeError(f"published functional run produced no cases: {func['detail']}")
    budgets = {
        "throughput": {
            "ok": bool(thr.get("throughput_met")),
            "measured_cycles_per_op": thr.get("cycles_per_op"),
            "budget_cycles_per_op": thr.get("throughput_budget"),
            "op_unit": (task_yaml.get("throughput") or {}).get("op_unit"),
            "worst_case": thr.get("worst_case"),
            "detail": (f"worst-case {thr.get('cycles_per_op')} cycles per "
                       f"{(task_yaml.get('throughput') or {}).get('op_unit', 'op')} vs the "
                       f"task cap {thr.get('throughput_budget')} "
                       f"(golden {(task_yaml.get('throughput') or {}).get('golden_cycles_per_op')}): "
                       + str(thr.get("detail", ""))),
        }
    }
    return {"cases": cases, "budgets": budgets, "detail": str(func.get("detail", "")),
            "artifacts": {"build_dir": func.get("build_dir"),
                          "seed_material": func.get("seed_material")}}
