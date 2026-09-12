# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Property invariants under fault injection (Package C, C4).

Drives the block subgraph to terminal under a persistent (all-calls) fault and
asserts P1 (never hangs), P2 (never fails open), P3 (bounded attempts). A
representative subset of the fault taxonomy is used to keep the default gate
fast; the full taxonomy behavior is covered per-class in test_fault_taxonomy.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.testing import fault_provider as fp
from orchestrator.testing.eda_stubs import stub_eda
from orchestrator.testing.faults import FaultClass

pytestmark = pytest.mark.fault_injection

# Representative subset spanning the distinct code paths (content-string,
# empty, prose, absent-file, wrong-path, 0-char-disk, fabricated-pass, raise).
PROPERTY_FAULTS = [
    FaultClass.EMPTY_RESPONSE,
    FaultClass.TIMEOUT_STRING,
    FaultClass.ERROR_TEXT_AS_CONTENT,
    FaultClass.NO_FILE_WRITTEN,
    FaultClass.WRONG_PATH_WRITTEN,
    FaultClass.JSON_DISK_MISMATCH,
    FaultClass.FABRICATED_PASS,
    FaultClass.PROVIDER_EXCEPTION,
]

_BLOCK = {
    "name": "adder8",
    "rtl_target": "rtl/adder8.v",
    "tier": 1,
    "python_source": "golden/adder8.py",
    "testbench": "tb/test_adder8.py",
    "description": "8-bit adder",
}


@pytest.fixture
def fault_env(monkeypatch, tmp_path):
    """Select the fault provider + EDA stubs + frozen PROJECT_ROOT."""
    monkeypatch.setenv("CORESMITH_PROFILE", "legacy")
    monkeypatch.setenv("CORESMITH_LLM_PROVIDER", "fault")
    monkeypatch.setenv("CORESMITH_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("CORESMITH_LLM_LOG_ROOT", raising=False)
    # Keep watchdog/backoff sub-second and avoid the pre-existing _composition
    # NameError on the (block-goldens-only) equiv branch of generate_testbench_node.
    monkeypatch.setenv("CORESMITH_TIMEOUT_MULTIPLIER", "0.01")
    monkeypatch.setenv("CORESMITH_RTL_MODEL_EQUIV", "0")
    import orchestrator.langgraph.pipeline_helpers as ph
    monkeypatch.setattr(ph, "PROJECT_ROOT", Path(tmp_path))
    stub_eda(monkeypatch)
    b = fp.get_backend()
    b.reset()
    yield b
    b.reset()


