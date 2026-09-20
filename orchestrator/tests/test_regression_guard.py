# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Regression-guard fix (Bug 1) + budget sizing rule (Bug 3).

Bug 1: when a block already passed sim, re-entering it (e.g. an
integration-review restart) used to FORCE testbench regeneration, which
produced a worse TB that re-failed -> infinite regen/fail loop that wedged
whole runs. The fix REUSES the passing TB by default; the old behavior is
opt-in via CORESMITH_FORCE_TB_REGEN=1.

Bug 3: the uArch storage-budget prompt must forbid `sram_budget = 0` for a
block holding any storage structure >= 2 Kbit (the deadlock case the PPA
gate flags but the fixer can't repair).
"""
from __future__ import annotations

import json


def _passed_block_state(tmp_path):
    """A BlockState re-entering a block that previously PASSED sim."""
    block = {
        "name": "fifo_blk",
        "tier": 1,
        "rtl_target": "rtl/fifo_blk.v",
        "testbench": "tb/cocotb/test_fifo_blk.py",
        "description": "block that already passed sim",
    }
    # On-disk: the RTL exists + best_result.json marks sim_passed.
    rtl = tmp_path / block["rtl_target"]
    rtl.parent.mkdir(parents=True, exist_ok=True)
    rtl.write_text("module fifo_blk(); endmodule\n")
    bdir = tmp_path / ".coresmith" / "blocks" / "fifo_blk"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "best_result.json").write_text(json.dumps(
        {"sim_passed": True, "attempt": 1, "tests_passed": 7, "tests_total": 7}
    ))
    return {
        "project_root": str(tmp_path),
        "target_clock_mhz": 50.0,
        "max_attempts": 3,
        "current_block": block,
        "attempt": 2,                 # re-entry -> guard fires
        "phase": "init",
        "lint_clean": False,
        "force_regen_tb": False,
        "step_log_paths": {},
    }


class TestBudgetSizingRule:
    def test_uarch_prompt_forbids_sram_budget_zero_on_big_storage(self):
        from pathlib import Path

        import orchestrator.langchain.agents.uarch_spec_generator as u
        prompt = u.SYSTEM_PROMPT
        # The mandatory computed sizing rule must be present.
        assert "2048 bits" in prompt or "2 Kbit" in prompt
        assert "SPEC ERROR" in prompt
        assert "depth" in prompt and "width" in prompt
        # sanity: file on disk matches the loaded prompt
        md = (Path(u.__file__).resolve().parent.parent / "prompts"
              / "uarch_spec_generator.md").read_text()
        assert "SPEC ERROR" in md
