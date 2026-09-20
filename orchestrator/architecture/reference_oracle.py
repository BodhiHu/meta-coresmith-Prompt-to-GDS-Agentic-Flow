# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Golden-reference oracle helpers shared by the DV gates.

Stimulus resolution (env file, FRD vectors, seeded default), reference-module
loading, degeneracy and divergence probes, and the oracle ABI preflight. These
were the reusable parts of the removed Amaranth model-integration gate; nothing
here builds, composes or simulates a model.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _import_module_from_path(path: Path, mod_name: str):
    """Import a module from a file path under a private module name.

    Audit F2: the module's OWN directory goes on ``sys.path`` for the duration
    of the import. Project reference implementations and stimulus files live in
    ``inputs/`` and import sibling helpers by plain name (a reference
    implementation's ``import <its>_vectors``) -- without the parent dir on the
    path that import raises ``No module named ...`` and the gate used to
    swallow it as a no-op.
    """
    import sys

    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    parent = str(Path(path).resolve().parent)
    inserted = False
    if parent not in sys.path:
        sys.path.insert(0, parent)
        inserted = True
    try:
        spec.loader.exec_module(module)
    finally:
        if inserted:
            try:
                sys.path.remove(parent)
            except ValueError:
                pass
    return module


def _load_reference_module(path: str):
    """Import the reference implementation module from a file path."""
    return _import_module_from_path(Path(path), "_coresmith_reference_impl")


def _acceptance_stimulus_path(project_root: str):
    """Resolve the FRD acceptance-stimulus artifact ('' if none declared).

    Order: CORESMITH_ACCEPTANCE_STIMULUS env (a .py exposing module-level
    ``stimulus`` -- or ``cases``, a list of (name, stimulus) tuples) ->
    <root>/inputs/acceptance_stimulus.py -> <root>/arch/acceptance_stimulus.py.
    """
    envp = os.environ.get("CORESMITH_ACCEPTANCE_STIMULUS", "").strip()
    if envp and Path(envp).exists():
        return envp
    for cand in ("inputs/acceptance_stimulus.py", "arch/acceptance_stimulus.py"):
        c = Path(project_root) / cand
        if c.exists():
            return str(c)
    # WP-15: nothing in the flow authors acceptance_stimulus.py, so the
    # Acceptance DV gate was silently SKIPPED on every run. Fall back to
    # the operator-supplied model stimulus (the same fallback
    # bfm_lib.stimulus.load_acceptance_cases already applies).
    envm = os.environ.get("CORESMITH_MODEL_STIMULUS", "").strip()
    if envm and Path(envm).exists():
        return envm
    ms = Path(project_root) / "inputs" / "model_stimulus.py"
    if ms.exists():
        return str(ms)
    return ""


