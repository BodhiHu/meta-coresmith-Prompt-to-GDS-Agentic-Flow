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
import inspect
import logging
import os
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from orchestrator.architecture.composition import (
    ReferenceEntryPointError,
    _entry_abi_message,
    _entry_accepts_stimulus,
    parse_func_vectors,
)

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


def _default_stimulus(entry_callable: Callable | None) -> Any:
    """Derive a small default stimulus from the reference entry signature.

    Policy (documented): a single short ascending list ``[1, 2, 3, 4]`` is
    produced and bound to the entry's FIRST positional parameter (the common
    "stream of samples / bytes / words" case). This is intentionally minimal:
    designs whose primary input is not a 1-D integer stream MUST supply an
    explicit stimulus via ``CORESMITH_MODEL_STIMULUS`` (a python file exposing
    a module-level ``stimulus``). The default exists only so a simple stream
    design (the common toy / objective-math case) can self-test without
    operator input. Returns ``None`` when no sensible default can be derived
    (the gate then logs + no-ops).

    Raises :class:`ReferenceEntryPointError` when the entry takes NO positional
    argument. Deriving a stimulus for such a callable is meaningless -- it was
    how a zero-argument ROM utility's ``main()`` reached the oracle seat and
    failed every vector with "main() takes 0 positional arguments but 1 was
    given". The ABI error names the callable and its signature instead.
    """
    if entry_callable is None:
        return None
    if not _entry_accepts_stimulus(entry_callable):
        raise ReferenceEntryPointError(_entry_abi_message(entry_callable))
    try:
        sig = inspect.signature(entry_callable)
        positional = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
    except (TypeError, ValueError):
        positional = []
    # A single short ascending stream is the most broadly-valid default.
    default_stream = [1, 2, 3, 4]
    if not positional:
        # ``*args``-only (or an unintrospectable signature -- both passed the
        # preflight above): pass the bare list (simulate() decides).
        return default_stream
    if len(positional) == 1:
        return default_stream
    # Multiple parameters: bind the stream by name to the first param so the
    # reference and simulate() both receive a dict they can map.
    return {positional[0].name: default_stream}


def _stimulus_from_file(p: Path) -> tuple[Any, bool]:
    """Import ``p`` and return ``(p.stimulus, True)`` or ``(None, False)``."""
    if not p.is_file():
        return None, False
    try:
        mod = _import_module_from_path(p, "_coresmith_model_stimulus")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "model integration gate: stimulus file %s failed to import: %s",
            p, exc,
        )
        return None, False
    stim = getattr(mod, "stimulus", None)
    if stim is None:
        logger.warning(
            "model integration gate: stimulus file %s has no `stimulus`", p
        )
        return None, False
    return stim, True


def _load_env_stimulus(project_root: str | None = None) -> tuple[Any, bool]:
    """Resolve the model-integration stimulus.

    Order: (1) ``CORESMITH_MODEL_STIMULUS`` env file, then (2) auto-discover
    ``<project_root>/inputs/model_stimulus.py`` (PR#12 finding #5). Returns
    ``(stimulus, found)``.

    Finding #5: when the env var was unset the gate fell straight through to a
    small DERIVED default (a bare ``[1,2,3,4]`` list) that a real golden's
    ``run()`` rejects -- so byte-exact blocks were marked failed for a pure
    setup reason. A run typically ships a project ``inputs/model_stimulus.py``
    exposing a valid ``stimulus``; auto-discovering it removes the footgun
    without requiring the operator to export the env var.
    """
    path = os.environ.get("CORESMITH_MODEL_STIMULUS", "").strip()
    if path:
        p = Path(path)
        if not p.is_file():
            logger.warning(
                "model integration gate: CORESMITH_MODEL_STIMULUS=%r not a file",
                path,
            )
        else:
            stim, ok = _stimulus_from_file(p)
            if ok:
                return stim, True
    # Auto-discover the project stimulus file.
    if project_root:
        cand = Path(project_root) / "inputs" / "model_stimulus.py"
        stim, ok = _stimulus_from_file(cand)
        if ok:
            logger.info(
                "model integration gate: using auto-discovered stimulus %s", cand
            )
            return stim, True
    return None, False


def _gate_seeded_stimulus_enabled() -> bool:
    """True -> also drive the gate with a FRESH SEEDED stimulus the chip model
    could not have memorized at generation time. Seeded ON in the STRICT
    profile (seeded by profile.STRICT_DEFAULTS); OFF in legacy."""
    try:
        from orchestrator.profile import ensure_applied, flag_enabled
        ensure_applied()
        return flag_enabled("CORESMITH_GATE_SEEDED_STIMULUS", default=False)
    except Exception:  # noqa: BLE001
        raw = (os.environ.get("CORESMITH_GATE_SEEDED_STIMULUS") or "").strip().lower()
        return raw in {"1", "true", "yes", "on"}


def _resolve_gate_seed() -> int:
    """The SEEDED-tier seed: a pinned value (``CORESMITH_DV_SEED_PIN``, for
    reproducible debugging) else a fresh cryptographic 63-bit seed. Delegates to
    the unified seed provider (single source of truth for the dev/gate split)."""
    from orchestrator.harness.seed_provider import gate_seed
    return gate_seed()


def _seeded_stimulus(entry_callable: Callable | None, seed: int) -> Any:
    """A FRESH seeded 1-D int stream for the SEEDED tier.

    Returns ``None`` when the reference is not a 1-D-stream entry (more than one
    positional parameter): the seeded tier then only runs if the reference
    module exports ``stimulus_for_seed(seed)``. This mirrors ``_default_stimulus``
    so a design the default tier could self-test is also seeded-testable.
    """
    if entry_callable is None:
        return None
    try:
        sig = inspect.signature(entry_callable)
        positional = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
    except (TypeError, ValueError):
        positional = []
    if len(positional) > 1:
        # Multi-parameter reference: cannot safely synthesize a stream. The
        # caller falls back to a ``stimulus_for_seed`` export if present.
        return None
    rng = random.Random(seed)
    n = rng.randint(6, 16)
    return [rng.randint(0, 255) for _ in range(n)]


def detect_byte_shift(expected: Any, observed: Any, max_shift: int = 4) -> str:
    """Detect an ALIGNMENT divergence: observed == expected shifted by +/-k
    bytes (with inserted/duplicated head bytes or dropped head bytes).

    armD live: the gate reported 'unlocalized block_math' for
    observed == dup_head_byte + expected[:-1] -- the whole content chain was
    byte-exact and the real defect was an egress FIFO head-duplication (FWFT
    class). A shift detection names that class instantly instead of routing a
    content re-spec. Returns '' when no shift pattern matches.
    """
    try:
        if not (_is_byteseq(expected) and _is_byteseq(observed)):
            return ""
        e = bytes(expected) if isinstance(expected, (bytes, bytearray)) else bytes(
            v & 0xFF for v in expected)
        o = bytes(observed) if isinstance(observed, (bytes, bytearray)) else bytes(
            v & 0xFF for v in observed)
        if not e or not o or e == o:
            return ""
        n = min(len(e), len(o))
        if n < 8:
            return ""
        for k in range(1, max_shift + 1):
            # observed carries k EXTRA leading bytes (dup/inserted head):
            # o[k:] aligns with e
            if len(o) >= len(e) - max_shift and o[k:k + n - k] == e[:n - k]:
                return (
                    f"BYTE-SHIFT DETECTED: the observed stream equals the "
                    f"expected stream shifted by +{k} byte(s) (extra/duplicated "
                    f"head byte(s) {o[:k].hex()}). The CONTENT chain is "
                    f"byte-exact -- this is an egress FIFO / handshake "
                    f"ALIGNMENT bug (FWFT head-duplication / refill class), "
                    f"NOT content math. Inspect the egress-most block's FIFO "
                    f"pop/refill logic under backpressure; do NOT re-spec the "
                    f"datapath blocks."
                )
            # observed MISSING k leading bytes (head dropped): e[k:] aligns
            if o[:n - k] == e[k:k + n - k]:
                return (
                    f"BYTE-SHIFT DETECTED: the observed stream equals the "
                    f"expected stream with the first {k} byte(s) DROPPED "
                    f"(missing head {e[:k].hex()}). The content chain is "
                    f"byte-exact -- an ingress/egress handshake drops the "
                    f"first beat(s) (reset/priming class), not content math."
                )
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _load_frd_func_vectors(project_root: str) -> list[dict]:
    """Machine-readable FRD FUNC vectors that carry an EXPLICIT structured
    stimulus (``stimulus_struct`` present). Prose-only vectors are skipped."""
    frd_path = Path(project_root) / "arch" / "frd_spec.md"
    if not frd_path.exists():
        return []
    try:
        frd_text = frd_path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [
        v for v in parse_func_vectors(frd_text)
        if v.get("stimulus_struct") is not None
    ]


def _split_observed(observed: Any) -> tuple[Any, int | None]:
    """Split a ``simulate()`` return into ``(output, cycles)``.

    The v2 ``simulate(stimulus)`` contract returns a ``(output, cycles)``
    2-tuple. Legacy / on-disk models that still return a bare output (a
    1-tuple-equivalent, i.e. anything that is not a 2-tuple) are tolerated:
    ``cycles`` is ``None`` and the throughput check is skipped (R6).

    A 2-tuple is recognised ONLY when its second element is an int/float cycle
    count; a 2-element list output (e.g. ``[a, b]``) is NOT mistaken for
    ``(output, cycles)``.
    """
    if (
        isinstance(observed, tuple)
        and len(observed) == 2
        and isinstance(observed[1], (int, float))
        and not isinstance(observed[1], bool)
    ):
        return observed[0], int(observed[1])
    return observed, None


def _flatten_scalars(obj: Any) -> list:
    """Recursively flatten ``obj`` to a list of Python scalars.

    Handles nested lists/tuples, numpy arrays (flattened to scalars), and
    bytes/bytearray (expanded to their integer byte values). Other objects are
    appended as-is. This is what makes degeneracy/equality checks numpy-safe:
    a bare ``v == first`` on a numpy array raises "truth value ambiguous", and
    a ``bytes`` bitstream must be treated as its byte sequence (not one opaque
    element that would look constant).
    """
    out: list = []
    stack = [obj]
    while stack:
        x = stack.pop()
        if x is None:
            out.append(None)
        elif isinstance(x, (bytes, bytearray)):
            out.extend(int(b) for b in x)
        elif isinstance(x, (list, tuple)):
            stack.extend(x)
        elif hasattr(x, "flat") and hasattr(x, "shape"):  # numpy ndarray
            try:
                out.extend(x.flatten().tolist())
            except Exception:  # noqa: BLE001
                out.append(x)
        else:
            out.append(x)
    return out


def _is_degenerate(observed: Any) -> bool:
    """True when ``observed`` is degenerate (all-zero / constant / None / empty).

    Used by the functional Tier-B non-degenerate fallback: a flat / collapsed
    output (the class of bug that ships a design with a flat output) is rejected
    even without a declared acceptance predicate. Numpy/bytes-safe.
    """
    if observed is None:
        return True
    if isinstance(observed, (list, tuple, bytes, bytearray)) or (
        hasattr(observed, "flat") and hasattr(observed, "shape")
    ):
        flat = _flatten_scalars(observed)
        if not flat:
            return True
        first = flat[0]
        # All-constant (incl. all-zero) -> degenerate. Scalar `==` only (flat
        # holds Python scalars / ints), so no numpy-array truthiness ambiguity.
        if all(bool(v == first) for v in flat):
            return True
        return False
    if isinstance(observed, (int, float)):
        return observed == 0
    if isinstance(observed, str):
        return observed.strip() == ""
    if isinstance(observed, dict):
        if not observed:
            return True
        return all(_is_degenerate(v) for v in observed.values())
    return False


def _is_byteseq(v: Any) -> bool:
    """True for a byte/int sequence (a bitstream), not a dict/str/scalar."""
    if isinstance(v, (bytes, bytearray)):
        return True
    if isinstance(v, (list, tuple)) and v and all(isinstance(x, int) for x in v):
        return True
    return False


def first_divergence_offset(expected: Any, observed: Any) -> int:
    """Byte index of the first mismatch (-1 if one is a clean prefix of the other
    AND they are equal length; the shorter length otherwise)."""
    try:
        n = min(len(expected), len(observed))
    except TypeError:
        return -1
    for i in range(n):
        if expected[i] != observed[i]:
            return i
    return -1 if len(expected) == len(observed) else n


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
    return ""


