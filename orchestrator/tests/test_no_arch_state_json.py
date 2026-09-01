# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Guard test: no production code should reference architecture_state.json.

Tier 4: Consumer migration verification. This test greps the production
code to ensure the monolithic architecture_state.json has been fully
replaced by per-document files (prd_spec.json, block_diagram.json, etc.).

The migration is still in flight, so each guard asserts the post-migration
condition and carries ``xfail(strict=True)``: it is expected-fail today, and
the moment the last reference goes away the XPASS turns the suite red so the
marker gets dropped and the guard starts enforcing for real. Reporting the
violation with an imperative ``pytest.xfail()`` instead would have made the
guard unfailable in both directions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Drop the xfail markers below (one at a time) as each consumer is migrated --
# strict=True makes the suite tell you when one is ready to be dropped.
_PENDING = "architecture_state.json migration is not complete yet"


@pytest.mark.doc_persistence
class TestNoArchStateJsonReferences:
    """Guard: architecture_state.json must not be referenced in production code."""

    def _project_root(self) -> Path:
        """Find the project root (directory containing orchestrator/)."""
        return Path(__file__).resolve().parents[2]

    @pytest.mark.xfail(strict=True, reason=_PENDING)
    def test_no_references_in_production_code(self):
        """Grep production code for architecture_state.json references.

        Excludes:
        - Test files (orchestrator/tests/**)
        - Plan files (.cursor/plans/**)
        - Documentation (*.md outside orchestrator/)
        - This test file itself

        Uses ripgrep (rg) if available, falls back to grep.
        """
        root = self._project_root()
        orchestrator_dir = root / "orchestrator"

        if not orchestrator_dir.exists():
            pytest.skip("orchestrator/ directory not found")

        try:
            result = subprocess.run(
                [
                    "rg",
                    "--type=py",
                    "--glob=!orchestrator/tests/**",
                    "--glob=!**/conftest.py",
                    "architecture_state",
                    str(orchestrator_dir),
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            try:
                result = subprocess.run(
                    [
                        "grep", "-r", "--include=*.py",
                        "--exclude-dir=tests",
                        "architecture_state",
                        str(orchestrator_dir),
                    ],
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                pytest.skip("Neither ripgrep (rg) nor grep available")

        if result.returncode == 2:
            pytest.skip("Search tool returned error")

        matches = result.stdout.strip()
        assert not matches, (
            f"architecture_state.json references remain in production code:\n{matches}"
        )

    @pytest.mark.xfail(strict=True, reason=_PENDING)
    def test_no_load_state_save_state_in_architecture_graph(self):
        """After migration, architecture_graph.py should not import load_state/save_state.

        The graph should use per-document persist helpers instead.
        """
        root = self._project_root()
        arch_graph = root / "orchestrator" / "langgraph" / "architecture_graph.py"

        if not arch_graph.exists():
            pytest.skip("architecture_graph.py not found")

        content = arch_graph.read_text()
        imports_state = "from orchestrator.architecture.state import" in content
        assert not (imports_state and "save_state" in content), (
            "save_state still imported in architecture_graph.py -- the graph "
            "should use the per-document persist helpers instead"
        )

    @pytest.mark.xfail(strict=True, reason=_PENDING)
    def test_state_py_does_not_write_monolithic_file(self):
        """state.py should not contain save_state() that writes architecture_state.json.

        After migration, state.py either:
        - Has no save_state() at all, or
        - save_state() is a no-op / deprecated wrapper
        """
        root = self._project_root()
        state_py = root / "orchestrator" / "architecture" / "state.py"

        if not state_py.exists():
            pytest.skip("state.py not found")

        content = state_py.read_text()

        assert not ("def save_state" in content and "architecture_state.json" in content), (
            "state.py still has save_state() writing architecture_state.json"
        )
