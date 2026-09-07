#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""Runs one task adapter in ITS interpreter (WP-41). No engine imports: this
file is executed by whatever python the adapter's header names.

    task_adapter_runner.py <adapter.py> <candidate.json> <receipt.json>

Writes the receipt (always, when the adapter module loads) with the module's
declared CASES / TOP / LABEL alongside grade()'s result; an exception inside
grade() lands in ``error`` (with the traceback) so the engine can classify it.
Exit 3 = the adapter module itself is unusable (import error, missing grade).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: task_adapter_runner.py <adapter.py> <candidate.json> <receipt.json>",
              file=sys.stderr)
        return 2
    apath, cand_path, rpath = Path(argv[1]), Path(argv[2]), Path(argv[3])
    candidate = json.loads(cand_path.read_text(encoding="utf-8"))
    sys.path.insert(0, str(apath.parent))
    try:
        spec = importlib.util.spec_from_file_location("_coresmith_task_adapter", apath)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        grade = getattr(mod, "grade")
        cases = list(getattr(mod, "CASES"))
    except Exception:  # noqa: BLE001
        print("task adapter module unusable:\n" + traceback.format_exc(), file=sys.stderr)
        return 3
    receipt: dict = {
        "declared_cases": [str(c) for c in cases],
        "declared_top": getattr(mod, "TOP", None),
        "label": str(getattr(mod, "LABEL", "")),
        "adapter": str(apath),
        "candidate_sha": candidate.get("candidate_sha"),
    }
    workdir = rpath.parent / "work"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        result = grade(candidate, str(workdir))
        if not isinstance(result, dict):
            raise TypeError(f"grade() returned {type(result).__name__}, not a dict")
        receipt.update({k: v for k, v in result.items() if k not in receipt})
    except Exception:  # noqa: BLE001
        receipt["error"] = traceback.format_exc()
    rpath.write_text(json.dumps(receipt, default=str, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
