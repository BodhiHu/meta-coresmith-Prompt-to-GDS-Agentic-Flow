# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""WP-72: a policy-authored ``python_source`` ref resolves against the owner's
``inputs/`` directory (and the declared source root), not only the project root."""
from __future__ import annotations

from orchestrator.langgraph.pipeline_helpers import (
    python_source_file,
    resolve_python_source,
)

GOLDEN = "def run(x):\n    return x + 1\n\n\ndef helper():\n    return 2\n"


def test_bare_filename_resolves_under_inputs(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_SOURCE_ROOT", raising=False)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "ax25_golden.py").write_text(GOLDEN)
    text = resolve_python_source("ax25_golden.py:run", tmp_path)
    assert "def run" in text and "def helper" not in text
    assert python_source_file("ax25_golden.py", tmp_path) == tmp_path / "inputs" / "ax25_golden.py"


def test_project_root_still_wins_and_inputs_prefix_still_works(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_SOURCE_ROOT", raising=False)
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "g.py").write_text("INPUTS = 1\n")
    (tmp_path / "g.py").write_text("ROOT = 1\n")
    assert resolve_python_source("g.py", tmp_path) == "ROOT = 1\n"
    assert resolve_python_source("inputs/g.py", tmp_path) == "INPUTS = 1\n"


def test_source_root_directory_is_the_last_resort(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "ref.py").write_text("ELSEWHERE = 1\n")
    monkeypatch.setenv("CORESMITH_SOURCE_ROOT", str(elsewhere / "ref.py"))
    project = tmp_path / "project"
    project.mkdir()
    assert resolve_python_source("ref.py", project) == "ELSEWHERE = 1\n"
    monkeypatch.delenv("CORESMITH_SOURCE_ROOT")
    assert resolve_python_source("ref.py", project) == ""
    assert python_source_file("ref.py", project) is None
