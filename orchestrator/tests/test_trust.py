# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Oracle-manifest tamper detection for files ADDED after run start.

Editing a recorded oracle file is caught by its hash; SHADOWING one is not --
``resolve_golden_path`` prefers ``inputs/golden.py``, so dropping a doctored
golden into a run whose golden resolved elsewhere swaps the scoring oracle
while every recorded hash still matches."""

from pathlib import Path

from orchestrator.state_store.trust import (
    check_oracle_manifest,
    write_oracle_manifest,
)


def _mk(root: Path) -> None:
    (root / "inputs").mkdir()
    (root / "inputs" / "adder_golden.py").write_text("def run(s): return s\n")
    (root / "inputs" / "requirements.md").write_text("do the thing\n")
    (root / "arch").mkdir()
    (root / "arch" / "ers_spec.md").write_text("ERS v1\n")


def test_shadowing_golden_added_after_run_start_is_tamper(tmp_path):
    _mk(tmp_path)
    write_oracle_manifest(tmp_path)
    assert check_oracle_manifest(tmp_path)["ok"]
    # the cheat: a doctored golden that takes precedence at resolve time
    (tmp_path / "inputs" / "golden.py").write_text("def run(s): return 'PASS'\n")
    res = check_oracle_manifest(tmp_path)
    assert res["ok"] is False
    assert "inputs/golden.py" in res["added"]
    assert res["violation"]["category"] == "ORACLE_TAMPER"


def test_arch_specs_written_after_run_start_are_not_tamper(tmp_path):
    # write_oracle_manifest runs at /run/start; the architecture stage writes
    # the FRD/PRD specs afterwards -- that must stay non-blocking.
    _mk(tmp_path)
    write_oracle_manifest(tmp_path)
    (tmp_path / "arch" / "frd_spec.md").write_text("FRD v1\n")
    (tmp_path / "arch" / "prd_spec.md").write_text("PRD v1\n")
    res = check_oracle_manifest(tmp_path)
    assert res["ok"] is True
    assert res["added"] == []
