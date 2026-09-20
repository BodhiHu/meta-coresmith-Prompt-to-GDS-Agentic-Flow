# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""WP-77: the adapter boundary uses the system bubblewrap, not whatever a
toolchain bundle put first on PATH (oss-cad-suite ships a bwrap that cannot
create user namespaces on a normal host: 'No permissions to create a new
namespace' on every adapter attempt of a live run)."""
from __future__ import annotations

import os
import stat
from pathlib import Path

from orchestrator.harness import task_adapter as ta


def _fake_bwrap(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / "bwrap"
    p.write_text("#!/bin/sh\nexit 1\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def test_system_bwrap_beats_a_toolchain_bwrap_on_path(tmp_path, monkeypatch):
    toolchain = _fake_bwrap(tmp_path / "toolchain" / "bin")
    system = _fake_bwrap(tmp_path / "usr" / "bin")
    monkeypatch.setenv("PATH", f"{toolchain.parent}:{os.environ.get('PATH', '')}")
    monkeypatch.delenv("CORESMITH_BWRAP", raising=False)
    monkeypatch.setattr(ta, "_SYSTEM_BWRAP", (str(system),))
    assert ta._find_bwrap() == str(system)
    argv = ta._sandbox_argv(["/bin/true"], tmp_path / "work")
    assert argv[0] == str(system)


def test_path_bwrap_is_the_fallback_when_no_system_one_exists(tmp_path, monkeypatch):
    toolchain = _fake_bwrap(tmp_path / "toolchain" / "bin")
    monkeypatch.setenv("PATH", str(toolchain.parent))
    monkeypatch.delenv("CORESMITH_BWRAP", raising=False)
    monkeypatch.setattr(ta, "_SYSTEM_BWRAP", (str(tmp_path / "nowhere" / "bwrap"),))
    assert ta._find_bwrap() == str(toolchain)


def test_explicit_override_wins(tmp_path, monkeypatch):
    chosen = _fake_bwrap(tmp_path / "chosen")
    monkeypatch.setenv("CORESMITH_BWRAP", str(chosen))
    assert ta._find_bwrap() == str(chosen)
    argv = ta._sandbox_argv(["/bin/true"], tmp_path / "work")
    assert argv[0] == str(chosen) and "--unshare-net" in argv
