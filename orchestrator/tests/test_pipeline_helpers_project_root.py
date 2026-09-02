# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""DV artifacts must be anchored to the RUN's project_root, not to the module
constant ``PROJECT_ROOT``.

In the daemon ``PROJECT_ROOT`` happens to equal the run dir (it is read from
``CORESMITH_PROJECT_ROOT``). In any process that does NOT set that env var --
tests, an operator's live-test script -- it resolves to the ENGINE CHECKOUT, so
``run_simulation`` built ``sim_build/`` and dropped ``tb/cocotb/`` wrappers into
the source tree (observed: a live-test run dirtied the checkout).

These tests keep the two roots DISTINCT (a "checkout" dir and a "run" dir) so
"landed under project_root" and "did not land under PROJECT_ROOT" are both
observable.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from orchestrator.langgraph import pipeline_helpers as ph

_BLOCK = {"name": "pr_probe_blk"}
_RTL = "module pr_probe_blk(input clk);\nendmodule\n"


class _Stop(Exception):
    """Abort run_simulation at the make invocation -- no EDA tools needed."""


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """(checkout, run): the module constant points at 'checkout'."""
    checkout = tmp_path / "checkout"
    run = tmp_path / "run"
    checkout.mkdir()
    run.mkdir()
    monkeypatch.setattr(ph, "PROJECT_ROOT", checkout)
    return checkout, run


def _stub_sim(monkeypatch, captured):
    monkeypatch.setattr(ph, "apply_build_fingerprint", lambda *a, **k: None)
    monkeypatch.setattr(ph, "_normalize_cocotb_timing_keywords",
                        lambda *a, **k: None)

    def _fake_popen(cmd, *a, **k):
        captured["sim_dir"] = cmd[2]            # [make, "-C", <sim_dir>]
        raise _Stop()

    monkeypatch.setattr(ph.subprocess, "Popen", _fake_popen)


class TestRunSimulationProjectRoot:
    def _run(self, run_root, monkeypatch, **kwargs):
        captured: dict = {}
        _stub_sim(monkeypatch, captured)
        rtl = run_root / "blk.v"
        rtl.write_text(_RTL)
        tb = run_root / "test_blk.py"
        tb.write_text("# tb\n")
        with pytest.raises(_Stop):
            ph.run_simulation(_BLOCK, str(rtl), str(tb), **kwargs)
        return Path(captured["sim_dir"])

    def test_builds_under_the_explicit_project_root(self, roots, monkeypatch):
        checkout, run = roots
        sim_dir = self._run(run, monkeypatch, project_root=run)

        assert sim_dir == run / "sim_build" / "pr_probe_blk"
        assert (sim_dir / "Makefile").exists()
        assert not (checkout / "sim_build").exists(), (
            "the engine checkout must stay clean")

    def test_accepts_a_string_project_root(self, roots, monkeypatch):
        _checkout, run = roots
        sim_dir = self._run(run, monkeypatch, project_root=str(run))
        assert sim_dir == run / "sim_build" / "pr_probe_blk"

    def test_defaults_to_the_module_constant(self, roots, monkeypatch):
        """Backward compat: callers that do not pass project_root (today: the
        pipeline_graph nodes) behave exactly as before."""
        checkout, run = roots
        sim_dir = self._run(run, monkeypatch)
        assert sim_dir == checkout / "sim_build" / "pr_probe_blk"

    def test_sim_pythonpath_points_at_the_run_root(self, roots, monkeypatch):
        checkout, run = roots
        captured: dict = {}
        monkeypatch.setattr(ph, "apply_build_fingerprint", lambda *a, **k: None)
        monkeypatch.setattr(ph, "_normalize_cocotb_timing_keywords",
                            lambda *a, **k: None)

        def _fake_popen(cmd, *a, **k):
            captured["pythonpath"] = k["env"]["PYTHONPATH"]
            raise _Stop()

        monkeypatch.setattr(ph.subprocess, "Popen", _fake_popen)
        rtl = run / "blk.v"
        rtl.write_text(_RTL)
        tb = run / "test_blk.py"
        tb.write_text("# tb\n")
        with pytest.raises(_Stop):
            ph.run_simulation(_BLOCK, str(rtl), str(tb), project_root=run)

        parts = captured["pythonpath"].split(os.pathsep)
        assert str(run) in parts
        assert str(checkout) not in parts


class TestGoldenModelWrapperProjectRoot:
    def test_wrapper_lands_under_the_explicit_project_root(self, roots,
                                                           monkeypatch):
        checkout, run = roots
        monkeypatch.setenv("CORESMITH_BLOCK_GOLDENS", "1")
        models = run / "arch" / "block_models"
        models.mkdir(parents=True)
        (models / "blk.py").write_text("VALUE = 42\n")

        ph.create_golden_model_wrapper("blk", "", project_root=run)

        wrapper = run / "tb" / "cocotb" / "blk_model.py"
        assert wrapper.exists()
        assert 'import_module("arch.block_models.blk")' in wrapper.read_text()
        assert not (checkout / "tb").exists()

    def test_defaults_to_the_module_constant(self, roots, monkeypatch):
        checkout, _run = roots
        monkeypatch.setenv("CORESMITH_BLOCK_GOLDENS", "1")
        models = checkout / "arch" / "block_models"
        models.mkdir(parents=True)
        (models / "blk.py").write_text("VALUE = 42\n")

        ph.create_golden_model_wrapper("blk", "")

        assert (checkout / "tb" / "cocotb" / "blk_model.py").exists()
