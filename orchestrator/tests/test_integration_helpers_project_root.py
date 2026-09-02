# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Integration/validation artifacts must be anchored to the RUN's
project_root, not to the module constant ``PROJECT_ROOT``.

``PROJECT_ROOT`` is ``CORESMITH_PROJECT_ROOT`` or, when that is unset, the
engine CHECKOUT. Every helper below wrote its artifacts there unconditionally,
so any process that did not set the env var built the chip inside the source
tree (observed: a live-test run left ``tb/integration/`` + ``sim_build/`` in
the checkout).

Each test keeps the two roots DISTINCT (a "checkout" dir and a "run" dir) so
both halves of the claim are observable: the artifact lands under the passed
project_root, and nothing lands under ``PROJECT_ROOT``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.langgraph import integration_helpers as ih


class _Stop(Exception):
    """Abort the sim at the make invocation -- no EDA tools needed."""


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """(checkout, run): the module constant points at 'checkout'."""
    checkout = tmp_path / "checkout"
    run = tmp_path / "run"
    checkout.mkdir()
    run.mkdir()
    monkeypatch.setattr(ih, "PROJECT_ROOT", checkout)
    return checkout, run


def _top(root: Path, name: str = "chip_top") -> Path:
    p = root / f"{name}.v"
    p.write_text(f"module {name}(input clk);\nendmodule\n")
    return p


class TestLintTopLevelProjectRoot:
    def _stub_verilator(self, monkeypatch):
        monkeypatch.setattr(
            ih.subprocess, "run",
            lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 0, "", ""))

    def test_dedup_scratch_lands_under_the_explicit_project_root(
        self, roots, monkeypatch
    ):
        checkout, run = roots
        self._stub_verilator(monkeypatch)
        ih.lint_top_level(str(_top(run)), [], project_root=run)

        assert (run / "sim_build" / "integration_lint").is_dir()
        assert not (checkout / "sim_build").exists()

    def test_defaults_to_the_module_constant(self, roots, monkeypatch):
        checkout, run = roots
        self._stub_verilator(monkeypatch)
        ih.lint_top_level(str(_top(run)), [])

        assert (checkout / "sim_build" / "integration_lint").is_dir()


class TestGenerateTopLevelRtlProjectRoot:
    def test_rtl_lands_under_the_explicit_project_root(self, roots):
        checkout, run = roots
        result = ih.generate_top_level_rtl(
            "chip_top", [], {}, project_root=run)

        assert Path(result["rtl_path"]) == \
            run / "rtl" / "integration" / "chip_top.v"
        assert Path(result["rtl_path"]).exists()
        assert not (checkout / "rtl").exists()

    def test_defaults_to_the_module_constant(self, roots):
        checkout, _run = roots
        result = ih.generate_top_level_rtl("chip_top", [], {})
        assert Path(result["rtl_path"]) == \
            checkout / "rtl" / "integration" / "chip_top.v"


class TestRunIntegrationSimulationProjectRoot:
    def _run(self, run_root, monkeypatch, **kwargs):
        captured: dict = {}

        def _fake_popen(cmd, *a, **k):
            captured["sim_dir"] = cmd[2]        # [make, "-C", <sim_dir>]
            raise _Stop()

        monkeypatch.setattr(ih.subprocess, "Popen", _fake_popen)
        top = _top(run_root)
        tb = run_root / "test_chip_top.py"
        tb.write_text("import cocotb\n")
        with pytest.raises(_Stop):
            ih.run_integration_simulation(
                "chip_top", str(top), {}, str(tb), **kwargs)
        return Path(captured["sim_dir"])

    def test_builds_under_the_explicit_project_root(self, roots, monkeypatch):
        checkout, run = roots
        sim_dir = self._run(run, monkeypatch, project_root=run)

        assert sim_dir == run / "sim_build" / "integration"
        assert (sim_dir / "Makefile").exists()
        assert (sim_dir / "test_chip_top.py").exists()
        assert not (checkout / "sim_build").exists()

    def test_validation_scope_also_lands_under_the_run(self, roots,
                                                       monkeypatch):
        checkout, run = roots
        sim_dir = self._run(run, monkeypatch, sim_scope="validation",
                            project_root=run)

        assert sim_dir == run / "sim_build" / "validation"
        assert not (checkout / "sim_build").exists()

    def test_defaults_to_the_module_constant(self, roots, monkeypatch):
        checkout, run = roots
        sim_dir = self._run(run, monkeypatch)
        assert sim_dir == checkout / "sim_build" / "integration"


class _FakeGen:
    """Records the output_path the helper computed; writes nothing itself."""

    seen: dict = {}

    def __init__(self, *a, **k):
        pass

    async def generate(self, **kw):
        _FakeGen.seen = dict(kw)
        return {"tb_path": kw.get("output_path", ""), "test_count": 1}


class TestTestbenchGeneratorProjectRoot:
    async def test_integration_tb_dir_under_the_explicit_project_root(
        self, roots, monkeypatch
    ):
        checkout, run = roots
        import orchestrator.langchain.agents.integration_testbench_generator \
            as gen_mod
        monkeypatch.setattr(gen_mod, "IntegrationTestbenchGenerator", _FakeGen)

        result = await ih.generate_integration_testbench(
            "chip_top", str(_top(run)), {}, [], {}, project_root=run)

        assert Path(result["testbench_path"]) == \
            run / "tb" / "integration" / "test_chip_top.py"
        assert (run / "tb" / "integration").is_dir()
        assert not (checkout / "tb").exists()

    async def test_integration_tb_defaults_to_the_module_constant(
        self, roots, monkeypatch
    ):
        checkout, run = roots
        import orchestrator.langchain.agents.integration_testbench_generator \
            as gen_mod
        monkeypatch.setattr(gen_mod, "IntegrationTestbenchGenerator", _FakeGen)

        result = await ih.generate_integration_testbench(
            "chip_top", str(_top(run)), {}, [], {})

        assert Path(result["testbench_path"]) == \
            checkout / "tb" / "integration" / "test_chip_top.py"

    async def test_validation_tb_dir_under_the_explicit_project_root(
        self, roots, monkeypatch
    ):
        checkout, run = roots
        import orchestrator.langchain.agents.validation_dv_generator as gen_mod
        monkeypatch.setattr(gen_mod, "ValidationDVGenerator", _FakeGen)

        result = await ih.generate_validation_testbench(
            "chip_top", str(_top(run)), {}, [], {}, "ers", project_root=run)

        assert Path(result["testbench_path"]) == \
            run / "tb" / "validation" / "test_chip_top_validation.py"
        assert not (checkout / "tb").exists()
