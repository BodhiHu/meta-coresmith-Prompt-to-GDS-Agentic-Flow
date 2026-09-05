# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""The run's PERSISTED environment (``<project_root>/.coresmith/env``).

One implementation, two consumers: the ``coresmith`` CLI (which builds the
env a daemon is launched with) and the daemon itself (which re-applies the
file every time it is about to launch graph work again). Deliberately
dependency-free -- stdlib only -- so the CLI can import it under whatever
interpreter the operator happens to be running.

SEMANTICS: the file is the run's operator-frozen config (provider + model
selectors, ``CORESMITH_REFERENCE_ENTRY``, ``CORESMITH_SYNTH_GENERIC``, ...), so
for the keys it CONTAINS it is AUTHORITATIVE: it OVERRIDES the ambient
environment rather than deferring to it. The old ``setdefault`` semantics let a
stale value that happened to be exported in the daemon-launching shell win
silently -- observed live with a persisted ``CORESMITH_REFERENCE_ENTRY=oracle``
while the composition gate ran with an ambient ``run``. Every override is
REPORTED back to the caller (which prints/logs it) so an operator can see which
ambient value was replaced instead of discovering it from gate behaviour.

Re-applying also matters mid-run: the daemon reads its environment once at
process start, so ``.coresmith/env`` edits made while a run is parked (the
common way an operator corrects a gate knob) were invisible to a subsequent
node restart / resume until the daemon was bounced.

FORMAT: one ``KEY=VALUE`` per line. Blank lines and ``#`` comments are ignored,
the FIRST ``=`` splits, and key/value are stripped. Later lines win (the CLI
appends auto-persisted selectors, so the file legitimately grows).
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

__all__ = [
    "persisted_env_path",
    "load_persisted_env",
    "apply_persisted_env",
    "format_override_notice",
]


def persisted_env_path(project_root: str | Path) -> Path:
    """``<project_root>/.coresmith/env`` (which may not exist)."""
    return Path(project_root) / ".coresmith" / "env"


def load_persisted_env(project_root: str | Path) -> dict[str, str]:
    """Parse the persisted env file into ``{KEY: VALUE}``.

    Returns ``{}`` when the file is absent or unreadable -- a run without a
    persisted env is the normal case, not an error.
    """
    try:
        text = persisted_env_path(project_root).read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        key, value = entry.split("=", 1)
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def apply_persisted_env(
    project_root: str | Path,
    env: MutableMapping[str, str] | None = None,
) -> list[tuple[str, str | None, str]]:
    """Apply the persisted env to ``env`` (default ``os.environ``); file WINS.

    Returns everything that CHANGED as ``[(key, previous_or_None,
    persisted_value), ...]``: ``previous`` is ``None`` for a key the target did
    not have (a line the operator appended mid-run) and the replaced string for
    a key whose ambient value differed. Keys already equal to the persisted
    value are applied silently -- nothing changed, nothing to report.
    """
    target: MutableMapping[str, str] = os.environ if env is None else env
    changes: list[tuple[str, str | None, str]] = []
    for key, value in load_persisted_env(project_root).items():
        previous = target.get(key)
        if previous != value:
            changes.append((key, previous, value))
        target[key] = value
    return changes


def format_override_notice(changes: list[tuple[str, str | None, str]]) -> str:
    """One operator-readable line naming the OVERRIDDEN keys, or ``""``.

    Only keys whose ambient value was REPLACED are named -- those are the ones
    an operator can be surprised by. Keys that were merely unset before are a
    plain application, not a swap. Names the KEYS only, never the values: the
    file can hold long PATH-like values and log lines are copied around freely.
    """
    overridden = [key for key, previous, _ in changes if previous is not None]
    if not overridden:
        return ""
    return (
        ".coresmith/env is authoritative -- overrode the ambient value of: "
        + ", ".join(overridden)
    )
