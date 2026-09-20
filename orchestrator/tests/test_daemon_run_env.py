# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""The run's persisted ``.coresmith/env`` is authoritative -- and re-read.

Two live failures, one loader:

* The CLI loaded the file with ``env.setdefault``, so a stale value already
  exported in the daemon-launching shell silently WON. Observed: the persisted
  file said ``CORESMITH_REFERENCE_ENTRY=oracle`` while the composition gate ran
  with the ambient ``run``.
* The daemon read its environment only at process start, so ``.coresmith/env``
  edits made mid-run (a chip lead appended several to a parked run) were
  invisible to ``/run/restart-node`` and the resume flows.

``orchestrator.run_env`` is the single implementation both use.
"""

from __future__ import annotations

import os

import pytest

from orchestrator.run_env import (
    apply_persisted_env,
    format_override_notice,
    load_persisted_env,
    persisted_env_path,
)

PERSISTED = """\
# the run's frozen config
CORESMITH_REFERENCE_ENTRY=oracle

CORESMITH_LLM_PROVIDER=codex
CORESMITH_SKIP_SYNTH = 1
malformed line without an equals sign
"""


@pytest.fixture()
def isolated_environ(monkeypatch):
    """A throwaway ``os.environ`` so applying the file cannot leak."""
    fake = dict(os.environ)
    monkeypatch.setattr(os, "environ", fake)
    return fake


def _write_env(root, text: str = PERSISTED):
    path = persisted_env_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadPersistedEnv:
    def test_parses_keys_skipping_comments_blanks_and_junk(self, tmp_path):
        _write_env(tmp_path)
        assert load_persisted_env(tmp_path) == {
            "CORESMITH_REFERENCE_ENTRY": "oracle",
            "CORESMITH_LLM_PROVIDER": "codex",
            "CORESMITH_SKIP_SYNTH": "1",
        }

    def test_value_may_contain_equals_signs(self, tmp_path):
        _write_env(tmp_path, "CORESMITH_MODEL=gpt=5.6-sol\n")
        assert load_persisted_env(tmp_path) == {"CORESMITH_MODEL": "gpt=5.6-sol"}

    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        assert load_persisted_env(tmp_path) == {}
        assert load_persisted_env(tmp_path / "nope") == {}


class TestApplyPersistedEnv:
    def test_persisted_value_beats_stale_ambient(self, tmp_path):
        """THE BUG: setdefault let the stale ambient value win."""
        _write_env(tmp_path)
        env = {"CORESMITH_REFERENCE_ENTRY": "run"}

        changes = apply_persisted_env(tmp_path, env)

        assert env["CORESMITH_REFERENCE_ENTRY"] == "oracle"
        assert ("CORESMITH_REFERENCE_ENTRY", "run", "oracle") in changes

    def test_keys_absent_from_the_target_are_reported_as_changes(self, tmp_path):
        _write_env(tmp_path)
        env: dict[str, str] = {}

        changes = apply_persisted_env(tmp_path, env)

        assert env["CORESMITH_LLM_PROVIDER"] == "codex"
        assert ("CORESMITH_LLM_PROVIDER", None, "codex") in changes

    def test_matching_values_are_not_reported(self, tmp_path):
        _write_env(tmp_path, "CORESMITH_LLM_PROVIDER=codex\n")
        env = {"CORESMITH_LLM_PROVIDER": "codex"}
        assert apply_persisted_env(tmp_path, env) == []

    def test_ambient_keys_the_file_does_not_mention_are_untouched(self, tmp_path):
        _write_env(tmp_path, "CORESMITH_SKIP_SYNTH=1\n")
        env = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "secret"}
        apply_persisted_env(tmp_path, env)
        assert env["PATH"] == "/usr/bin"
        assert env["ANTHROPIC_API_KEY"] == "secret"

    def test_defaults_to_os_environ(self, tmp_path, isolated_environ):
        _write_env(tmp_path, "CORESMITH_REFERENCE_ENTRY=oracle\n")
        isolated_environ["CORESMITH_REFERENCE_ENTRY"] = "run"

        changes = apply_persisted_env(tmp_path)

        assert os.environ["CORESMITH_REFERENCE_ENTRY"] == "oracle"
        assert [c[0] for c in changes] == ["CORESMITH_REFERENCE_ENTRY"]

    def test_missing_file_changes_nothing(self, tmp_path):
        env = {"CORESMITH_REFERENCE_ENTRY": "run"}
        assert apply_persisted_env(tmp_path, env) == []
        assert env == {"CORESMITH_REFERENCE_ENTRY": "run"}


class TestOverrideNotice:
    def test_names_overridden_keys_only(self):
        notice = format_override_notice([
            ("CORESMITH_REFERENCE_ENTRY", "run", "oracle"),
            ("CORESMITH_SKIP_SYNTH", None, "1"),
        ])
        assert "CORESMITH_REFERENCE_ENTRY" in notice
        # A key that was simply unset before was not "overridden".
        assert "CORESMITH_SKIP_SYNTH" not in notice

    def test_values_are_not_echoed(self):
        notice = format_override_notice([("CORESMITH_MODEL", "old", "new")])
        assert "old" not in notice and "new" not in notice

    def test_no_overrides_is_empty(self):
        assert format_override_notice([]) == ""
        assert format_override_notice([("K", None, "v")]) == ""


class TestDaemonRefresh:
    """Bug C: a restart-node / resume must see mid-run edits to the file."""

    def test_refresh_applies_persisted_env_to_the_daemon_process(
        self, tmp_path, isolated_environ, monkeypatch
    ):
        import orchestrator.daemon.server as srv

        monkeypatch.setattr(srv, "_PROJECT_ROOT", str(tmp_path))
        isolated_environ["CORESMITH_REFERENCE_ENTRY"] = "run"
        _write_env(tmp_path, "CORESMITH_REFERENCE_ENTRY=oracle\nSKIP_SYNTH=1\n")

        changed = srv._apply_run_env("test/restart-node")

        assert os.environ["CORESMITH_REFERENCE_ENTRY"] == "oracle"
        assert os.environ["SKIP_SYNTH"] == "1"
        assert set(changed) == {"CORESMITH_REFERENCE_ENTRY", "SKIP_SYNTH"}

    def test_refresh_is_a_noop_without_a_persisted_file(
        self, tmp_path, isolated_environ, monkeypatch
    ):
        import orchestrator.daemon.server as srv

        monkeypatch.setattr(srv, "_PROJECT_ROOT", str(tmp_path))
        assert srv._apply_run_env("test/resume") == []

    def test_refresh_never_raises(self, isolated_environ, monkeypatch):
        """A project root that cannot exist must not fail the handler."""
        import orchestrator.daemon.server as srv

        monkeypatch.setattr(srv, "_PROJECT_ROOT", "/nonexistent/coresmith-root")
        assert srv._apply_run_env("test/start") == []
