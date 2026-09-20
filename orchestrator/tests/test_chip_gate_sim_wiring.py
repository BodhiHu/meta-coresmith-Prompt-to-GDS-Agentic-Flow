# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""The chip_top gate-sim must be WIRED, and its verdict must BITE.

These test the plumbing, not the verdict logic (that is test_chip_top_gate_sim).
They exist because the gate shipped in a state where it could never run on a
real design and could never block one, while its unit tests passed:

  * ``_run_chip_top_gate_sim`` read ``state["top_rtl_path"]``, which no part of
    the backend flow populates -- ``BackendState`` has no such field,
    ``start_backend``'s initial_state omits it, and ``init_design_node`` does
    not return it. Its test passed only because the test injected the value.
    On every real run the gate reported ``not_run``. The one real verdict ever
    produced came from a hand-written driver that read the source list out of
    the integration DV's Makefile.
  * ``run_simulation`` built ``VERILOG_SOURCES`` from ONE path, so even a
    correct single path could not elaborate an assembled chip.
  * Nothing read ``chip_gate_sim_ok``. A FAIL was recorded in state and the
    flow proceeded to P&R anyway.
"""
from __future__ import annotations

import shutil

import pytest

import orchestrator.harness.gate_sim as gs
from orchestrator.harness.top_module import CandidateError, write_candidate_receipt
from orchestrator.langgraph.backend_graph import (
    _run_chip_top_gate_sim,
    route_after_flat_synth,
)
from orchestrator.langgraph.integration_helpers import chip_rtl_sources
from orchestrator.tests.candidate_fixtures import adopt

# ---------------------------------------------------------------------------
# The reference source list
# ---------------------------------------------------------------------------

class TestChipRtlSources:
    def test_exact_manifest_sources_without_rewriting(self, tmp_path):
        top = tmp_path / "chip_top.v"
        top.write_text("module chip_top(); endmodule")
        leaf = tmp_path / "leaf.v"
        leaf.write_text("module leaf(); endmodule")
        blocks = {"leaf": str(leaf)}
        rec = adopt(tmp_path, top, blocks)
        assert chip_rtl_sources(str(top), blocks, top_module="chip_top", project_root=tmp_path,
                                dedup_dir=tmp_path / "scratch") == rec["sources"]
        assert not (tmp_path / "scratch").exists()

    def test_missing_recorded_source_is_fatal(self, tmp_path):
        top = tmp_path / "chip_top.v"
        top.write_text("module chip_top(); endmodule")
        adopt(tmp_path, top)
        top.unlink()
        with pytest.raises(CandidateError):
            chip_rtl_sources(str(top), {}, top_module="chip_top", project_root=tmp_path)


# ---------------------------------------------------------------------------
# The gate is actually fed
# ---------------------------------------------------------------------------

def _run_dir(tmp_path):
    (tmp_path / "sim_build" / "integration").mkdir(parents=True)
    (tmp_path / "sim_build" / "integration" / "test_my_chip.py").write_text("#tb\n")
    top = tmp_path / "rtl" / "integration" / "my_chip.v"
    top.parent.mkdir(parents=True)
    top.write_text("module my_chip(input clk); endmodule\n")
    blk = tmp_path / "rtl" / "blk.v"
    blk.write_text("module blk(); endmodule\n")
    adopt(tmp_path, top, {"blk": str(blk)}, name="my_chip")
    return tmp_path, top, blk


class TestTheGateIsFedARealSourceList:
    def test_sources_come_from_the_backend_state_the_flow_populates(
        self, tmp_path, monkeypatch
    ):
        """``integration_top_path`` + ``block_rtl_paths`` are what
        ``init_design_node`` really returns. Reading a field the flow never
        sets is how this gate self-disabled on every real run."""
        monkeypatch.setenv(gs.GATE_SIM_ENV, "1")
        root, top, blk = _run_dir(tmp_path)
        seen = {}

        def fake_check(*, block, netlist_path, rtl_path, tb_path):
            seen["rtl_path"] = rtl_path
            return gs.GateSimResult(ran=True, ok=True, status=gs.STATUS_PASS,
                                    reason="ok", cycles_compared=10,
                                    output_bits_compared=100)

        monkeypatch.setattr(gs, "check_gate_sim", fake_check)
        ok, status, _ = _run_chip_top_gate_sim({
            "project_root": str(root),
            "design_name": "my_chip",
            "integration_top_path": str(top),
            "block_rtl_paths": {"blk": str(blk)},
        }, "net.v")

        assert ok is True and status == gs.STATUS_PASS
        srcs = seen["rtl_path"]
        assert not isinstance(srcs, str), "an assembled chip is not one file"
        assert str(top) in srcs and str(blk) in srcs
        assert srcs[0] == str(top)

    def test_no_assembled_top_anywhere_is_not_run_never_a_pass(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv(gs.GATE_SIM_ENV, "1")
        root, _top, _blk = _run_dir(tmp_path)
        ok, status, reason = _run_chip_top_gate_sim({
            "project_root": str(root),
            "design_name": "my_chip",
        }, "net.v")
        assert ok is False and status == gs.STATUS_NOT_RUN
        assert reason                            # absence is always explained


# ---------------------------------------------------------------------------
# The verdict bites
# ---------------------------------------------------------------------------

class TestTheVerdictBlocksPnR:
    def test_fail_routes_to_diagnose_not_pnr(self, tmp_path):
        """The flat netlist provably does not reproduce the verified RTL.
        Hardening it would spend hours of P&R on a design that does not work."""
        net = tmp_path / "net.v"
        net.write_text("module chip_top(); endmodule\n")
        assert route_after_flat_synth({
            "flat_netlist_path": str(net), "chip_gate_sim_ok": False,
        }) == "diagnose"

    def test_pass_proceeds(self, tmp_path):
        net = tmp_path / "net.v"
        net.write_text("module chip_top(); endmodule\n")
        assert route_after_flat_synth({
            "flat_netlist_path": str(net), "chip_gate_sim_ok": True,
        }) == "run_pnr"

    def test_not_applicable_does_not_block(self, tmp_path):
        """``None`` means the gate did not APPLY (disabled, no integration TB,
        no toolchain). That is not a verdict, and a gate that cannot run must
        not wall off the flow -- it is already logged with a reason."""
        net = tmp_path / "net.v"
        net.write_text("module chip_top(); endmodule\n")
        assert route_after_flat_synth({
            "flat_netlist_path": str(net), "chip_gate_sim_ok": None,
        }) == "run_pnr"
        assert route_after_flat_synth({
            "flat_netlist_path": str(net),
        }) == "run_pnr"

    def test_missing_netlist_still_diagnoses(self, tmp_path):
        assert route_after_flat_synth({
            "flat_netlist_path": str(tmp_path / "absent.v"),
            "chip_gate_sim_ok": True,
        }) == "diagnose"


class TestModdupHazard:
    @pytest.mark.skipif(not shutil.which("yosys"), reason="requires yosys")
    def test_duplicate_modules_are_rejected_at_adoption(self, tmp_path):
        top = tmp_path / "chip_top.v"
        top.write_text("module chip_top(); endmodule")
        alias = tmp_path / "alias.v"
        alias.write_text("module chip_top(); endmodule")
        with pytest.raises(CandidateError):
            write_candidate_receipt(tmp_path, "chip_top", str(top), {"alias": str(alias)})
        assert not (tmp_path / ".coresmith/candidate.json").exists()
