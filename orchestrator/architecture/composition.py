# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Composition gate -- deterministic, LLM-free.

This module wires every per-block *block-level golden model* together per the
block diagram, drives the FRD ``FUNC-NNN`` functional vectors through the
composed chip, and asserts the composed output equals the **reference
implementation**'s output. It is the "shift golden-fidelity LEFT" gate: it
catches a block whose golden math diverges from the reference implementation
(closing the ``QS-OPEN-001`` placeholder class) BEFORE end-of-pipeline DV.

Everything here is pure + deterministic and unit-testable without an LLM or any
EDA tooling.

Public surface:

- :func:`parse_func_vectors` -- tolerant markdown/regex parse of the FRD
  ``## Functional Vectors`` section into structured dicts.
- :func:`resolve_reference_implementation` -- locate the input software golden.
The feature is gated by ``CORESMITH_BLOCK_GOLDENS``: when off (the default),
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BLOCK_GOLDENS_DIRNAME = "block_goldens"  # v1, retired (under <project_root>/arch/)


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


def functional_blocks() -> set[str]:
    """Block names whose PER-BLOCK DV uses FUNCTIONAL-QUALITY acceptance instead
    of byte/bit-exact-vs-block-model.

    Read from ``CORESMITH_FUNCTIONAL_BLOCKS`` (comma/space separated). Default
    empty -> every block keeps the strict bit-exact block-DV comparison.

    Motivation (authorised 2026-06-23): a RATE-DISTORTION encoder block makes
    valid mode/quant/trellis choices that need NOT be byte-identical to one
    reference, so byte-exact-vs-block-model is the wrong block-DV bar for it. A
    listed block instead gets a FUNCTIONAL block testbench that decodes its
    output through the block-model's own inverse/reconstruction reference and
    asserts a real reconstruction-quality (PSNR) bound + structural validity +
    a sane rate bound -- a gate that still genuinely fails a garbage encoder
    (NOT a relaxed/always-pass assert). DETERMINISTIC blocks stay bit-exact by
    simply not being listed. Synth + PPA/FF-budget gates are UNAFFECTED for all
    blocks.
    """
    raw = os.environ.get("CORESMITH_FUNCTIONAL_BLOCKS", "")
    names: set[str] = set()
    for tok in raw.replace(",", " ").split():
        tok = tok.strip()
        if tok:
            names.add(tok)
    return names


def is_functional_block(block_name: str) -> bool:
    """True when ``block_name`` is in :func:`functional_blocks`."""
    return bool(block_name) and block_name in functional_blocks()


def gate_allow_nondegenerate_enabled() -> bool:
    """True when ``CORESMITH_GATE_ALLOW_NONDEGENERATE`` is set truthy.

    Default OFF. The model-integration gate's functional default (no declared
    acceptance predicate, ``CORESMITH_BIT_EXACT`` off) requires the composed
    chip model's output to MATCH THE REFERENCE (the FRD behaviour). Setting this
    flag reverts to the old, too-weak check that accepted any *non-degenerate*
    output without comparing it to the reference -- which let a composition
    streaming a handful of garbage bytes pass. Use only when there is genuinely
    no usable oracle comparison; intentionally non-bit-exact (lossy) designs
    should declare an acceptance_fn (see :func:`resolve_functional_acceptance`)
    instead. Mirrors :func:`bit_exact_enabled`.

    A-Fix 5(b): under the STRICT profile the escape hatch is DEPRECATED. If the
    flag is set while strict, log an ERROR and return ``False`` (the functional
    gate keeps its reference-equivalence requirement) -- a deliberate
    env>profile exception, because honoring a "pass any non-degenerate output"
    knob defeats the whole anti-gaming fix. The LEGACY profile still honors it.
    """
    flag_set = os.environ.get(
        "CORESMITH_GATE_ALLOW_NONDEGENERATE", ""
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not flag_set:
        return False
    try:
        from orchestrator.profile import resolve_profile
        profile = resolve_profile()
    except Exception:  # noqa: BLE001 - a profile hiccup must not weaken the gate
        profile = "strict"
    if profile != "legacy":
        logger.error(
            "CORESMITH_GATE_ALLOW_NONDEGENERATE is set but the STRICT profile "
            "DEPRECATES this escape hatch (it let a wrong composed model pass on "
            "mere non-degeneracy). IGNORING it -- the model-integration gate "
            "keeps its reference-equivalence requirement. Set "
            "CORESMITH_PROFILE=legacy to honor the flag."
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Functional acceptance + throughput floor resolution (criterion plumbing)
# ---------------------------------------------------------------------------

def _load_validation_kpis(project_root: str) -> list[dict]:
    """Best-effort load of the ERS ``validation_kpis`` list.

    Looks in ``.coresmith/ers_spec.json`` (the structured ERS) for a
    ``validation_kpis`` array of ``{acceptance_fn?, metric?, threshold?,
    cycles?/throughput?, ...}`` dicts. Returns ``[]`` when unavailable or
    malformed (the caller then falls back to env / non-degenerate tiers).
    """
    root = Path(project_root)
    for name in ("ers_spec.json", "prd_spec.json"):
        p = root / ".coresmith" / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        doc = data.get("ers", data.get("prd", data)) if isinstance(data, dict) else {}
        kpis = doc.get("validation_kpis") if isinstance(doc, dict) else None
        if isinstance(kpis, list) and kpis:
            return [k for k in kpis if isinstance(k, dict)]
    return []


def resolve_functional_acceptance(
    project_root: str,
) -> Callable[[Any, Any], bool] | None:
    """Resolve a declared functional-acceptance predicate ``accept(expected, observed)``.

    Resolution order (first hit wins):

    1. ``CORESMITH_FUNCTIONAL_ACCEPTANCE`` env var -- a path to a ``.py``
       file exposing a module-level ``accept(expected, observed) -> bool``
       (mirrors ``CORESMITH_MODEL_STIMULUS``). For a codec this is typically
       decode + PSNR>=thr; for an MCU a result-match; for math an objective
       value.
    2. The ERS ``validation_kpis`` declaring an ``acceptance_fn`` entry of the
       form ``"path.py"`` or ``"path.py:accept"``; the named (or ``accept``)
       callable is loaded.

    Returns the callable, or ``None`` when no acceptance predicate is declared
    (the gate then uses the non-degenerate Tier-B fallback).
    """
    # 1. env override
    env_path = os.environ.get("CORESMITH_FUNCTIONAL_ACCEPTANCE", "").strip()
    if env_path:
        fn = _load_acceptance_callable(env_path)
        if fn is not None:
            return fn
        logger.warning(
            "model integration gate: CORESMITH_FUNCTIONAL_ACCEPTANCE=%r did "
            "not resolve to a callable accept(expected, observed)",
            env_path,
        )

    # 2. declared in ERS validation_kpis
    for kpi in _load_validation_kpis(project_root):
        decl = kpi.get("acceptance_fn")
        if isinstance(decl, str) and decl.strip():
            fn = _load_acceptance_callable(decl.strip(), project_root)
            if fn is not None:
                return fn
            logger.warning(
                "model integration gate: validation_kpis acceptance_fn %r did "
                "not resolve",
                decl,
            )
    return None


def _load_acceptance_callable(
    spec: str,
    project_root: str | None = None,
    default_func: str = "accept",
) -> Callable[[Any, Any], Any] | None:
    """Load an ``<default_func>(expected, observed)`` callable from ``path.py[:name]``.

    ``spec`` is a filesystem path to a ``.py`` (optionally suffixed
    ``:funcname``; defaults to ``default_func``, normally ``accept``). Relative
    paths are resolved against ``project_root`` when given. Returns ``None`` on
    any failure. Reused for the fidelity-metric loader (``default_func``
    ``"fidelity"``) -- see ``orchestrator.architecture.fidelity``.
    """
    spec = spec.strip()
    if not spec:
        return None
    func_name = default_func
    path_part = spec
    # Only split a trailing ":name" (avoid splitting a Windows drive letter).
    if ":" in spec:
        head, _, tail = spec.rpartition(":")
        if head and tail and not tail.endswith(".py"):
            path_part, func_name = head, tail
    p = Path(path_part)
    if not p.is_absolute() and project_root:
        cand = Path(project_root) / p
        if cand.exists():
            p = cand
    if not p.is_file():
        return None
    try:
        mod = _import_module_from_path(p, "_coresmith_functional_acceptance")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "model integration gate: acceptance module %s failed import: %s",
            p,
            exc,
        )
        return None
    fn = getattr(mod, func_name, None)
    if callable(fn):
        return fn
    return None


def resolve_throughput_floor(project_root: str) -> float | None:
    """Resolve the maximum allowed cycle count (the throughput *floor*).

    The gate compares ``observed_cycles`` against this value; a run that takes
    MORE cycles than the floor is too slow and a throughput violation.

    Resolution order (first hit wins):

    1. ``CORESMITH_THROUGHPUT_FLOOR_CYCLES`` env var (an int/float cycle count).
    2. The ERS ``validation_kpis``: an explicit ``cycles`` / ``max_cycles``
       budget, else a ``throughput`` (samples/clk) combined with a declared
       ``stimulus_len`` -> cycles = stimulus_len / throughput.

    Returns the cycle floor as a float, or ``None`` when no throughput budget
    is declared (the gate then skips the throughput check).
    """
    env_floor = os.environ.get("CORESMITH_THROUGHPUT_FLOOR_CYCLES", "").strip()
    if env_floor:
        try:
            return float(env_floor)
        except ValueError:
            logger.warning(
                "model integration gate: CORESMITH_THROUGHPUT_FLOOR_CYCLES=%r "
                "not numeric",
                env_floor,
            )

    for kpi in _load_validation_kpis(project_root):
        for key in ("max_cycles", "cycles", "cycle_budget"):
            val = kpi.get(key)
            if isinstance(val, (int, float)) and val > 0:
                return float(val)
        thr = kpi.get("throughput")
        slen = kpi.get("stimulus_len")
        if (
            isinstance(thr, (int, float))
            and thr > 0
            and isinstance(slen, (int, float))
            and slen > 0
        ):
            return float(slen) / float(thr)
    return None


# ---------------------------------------------------------------------------
# FRD FUNC vector parsing
# ---------------------------------------------------------------------------

# A FUNC entry is a markdown bullet list under an `### FUNC-NNN ...` heading or
# a `- **ID**: FUNC-NNN` style bullet. We tolerate both layouts. We anchor on
# the FUNC-NNN id token and then collect the labelled fields that follow it
# until the next FUNC-NNN id.
_FUNC_ID_RE = re.compile(r"FUNC-(\d+)", re.IGNORECASE)
_FIELD_RE = re.compile(
    r"\*{0,2}(ID|Block(?:\s*/\s*I-?O\s*ports?)?|Stimulus|Expected\s*output|"
    r"Priority)\*{0,2}\s*[:\-]\s*(.*)",
    re.IGNORECASE,
)


def _func_section(frd_text: str) -> str:
    """Return only the ``## Functional Vectors`` section of the FRD, if present.

    Falls back to the whole document so a FRD that uses a slightly different
    heading still gets scanned.
    """
    if not frd_text:
        return ""
    # Match the heading, then up to the next same-or-higher level heading.
    # Stop only at a level-1/2 heading ("# " or "## ") so that "### FUNC-NNN"
    # subheadings inside the section do NOT terminate the capture.
    m = re.search(
        r"^#{1,2}\s*Functional\s+Vectors\s*$(.*?)(?=^#{1,2}\s|\Z)",
        frd_text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    if m:
        return m.group(1)
    return frd_text


def _coerce_scalar(value: str) -> Any:
    """Best-effort coercion of a field value to a Python literal.

    Tries ``ast.literal_eval`` (so ``[1, 2, 3]``, ``0x4F``, ``"foo"`` parse),
    then a bare-int parse, else returns the trimmed string. This is best-effort
    metadata; ``compose_and_run`` reads structured stimulus/expected when the
    caller supplies it, so an un-coercible value simply stays a string.
    """
    text = value.strip().strip("`").strip()
    if not text:
        return ""
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError):
        pass
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"0x[0-9a-fA-F]+", text):
        return int(text, 16)
    return text


# Fenced ```json ... ``` block inside a FUNC entry (the machine-readable form).
_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\})\s*```",
    re.IGNORECASE | re.DOTALL,
)
# Inline `Stimulus (structured): {...}` JSON form.
_INLINE_STIM_RE = re.compile(
    r"\*{0,2}Stimulus\s*\(structured\)\*{0,2}\s*[:\-]\s*(\{.*?\})\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_structured(chunk: str) -> tuple[dict | None, Any | None]:
    """Pull a machine-readable stimulus/expected pair out of a FUNC chunk.

    Recognises two shapes:

    1. A fenced json block (```json {"stimulus": {...}, "expected": {...}}```)
       where ``expected`` is optional.
    2. An inline ``Stimulus (structured): {...}`` line whose JSON is itself the
       stimulus dict.

    Returns ``(stimulus_struct, expected_struct)`` where each may be ``None``.
    """
    stimulus_struct: dict | None = None
    expected_struct: Any | None = None

    for m in _JSON_FENCE_RE.finditer(chunk):
        try:
            obj = json.loads(m.group(1))
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        if "stimulus" in obj:
            stim = obj.get("stimulus")
            if isinstance(stim, dict):
                stimulus_struct = stim
            if "expected" in obj:
                expected_struct = obj.get("expected")
            break
        # A bare JSON object with no "stimulus" key is treated as the stimulus
        # struct itself (lenient: lets a FUNC entry inline just the stimulus).
        if stimulus_struct is None:
            stimulus_struct = obj
            break

    if stimulus_struct is None:
        im = _INLINE_STIM_RE.search(chunk)
        if im:
            try:
                obj = json.loads(im.group(1))
                if isinstance(obj, dict):
                    stimulus_struct = obj
            except (ValueError, TypeError):
                pass

    return stimulus_struct, expected_struct


def parse_func_vectors(frd_text: str) -> list[dict]:
    """Parse the FRD ``## Functional Vectors`` section into structured dicts.

    Tolerant markdown/regex parse. Each returned dict has keys:
    ``id`` (e.g. ``"FUNC-001"``), ``block``, ``stimulus``, ``expected_output``,
    ``priority``, plus the machine-readable ``stimulus_struct`` (``dict|None``)
    and ``expected_struct`` (``Any|None``) extracted from a fenced ```json``
    block or an inline ``Stimulus (structured):`` line. Unknown / missing
    fields default to ``""`` (prose) or ``None`` (structured). The parser is
    line-oriented: it splits the section on FUNC-NNN id tokens and reads the
    labelled fields belonging to each.
    """
    section = _func_section(frd_text)
    if not section:
        return []

    # Split the section into chunks, one per FUNC-NNN occurrence. We find each
    # id position and slice up to the next id.
    ids = list(_FUNC_ID_RE.finditer(section))
    vectors: list[dict] = []
    for i, m in enumerate(ids):
        start = m.start()
        end = ids[i + 1].start() if i + 1 < len(ids) else len(section)
        chunk = section[start:end]
        func_id = f"FUNC-{int(m.group(1)):03d}"

        rec = {
            "id": func_id,
            "block": "",
            "stimulus": "",
            "expected_output": "",
            "priority": "",
            "stimulus_struct": None,
            "expected_struct": None,
        }
        stim_struct, exp_struct = _extract_structured(chunk)
        rec["stimulus_struct"] = stim_struct
        rec["expected_struct"] = exp_struct
        for line in chunk.splitlines():
            fm = _FIELD_RE.search(line)
            if not fm:
                continue
            label = fm.group(1).lower()
            raw = fm.group(2).strip()
            if label.startswith("block"):
                rec["block"] = raw
            elif label == "stimulus":
                rec["stimulus"] = _coerce_scalar(raw)
            elif label.startswith("expected"):
                rec["expected_output"] = _coerce_scalar(raw)
            elif label == "priority":
                rec["priority"] = raw
            elif label == "id":
                # The explicit ID field; keep the normalised one we derived.
                pass
        vectors.append(rec)

    # De-dup by id (a `- **ID**: FUNC-001` line plus a heading would otherwise
    # double-count); keep the first, richest occurrence.
    seen: dict[str, dict] = {}
    for v in vectors:
        if v["id"] not in seen:
            seen[v["id"]] = v
        else:
            # Merge: fill any blank fields from the later occurrence.
            for k, val in v.items():
                if not seen[v["id"]].get(k) and val:
                    seen[v["id"]][k] = val
    return list(seen.values())


# ---------------------------------------------------------------------------
# Reference implementation resolution
# ---------------------------------------------------------------------------

def resolve_generator_reference(project_root: str) -> str | None:
    """Reference source for the block-model / chip-model GENERATORS.

    The generators transcribe per-block MATH from the reference, so they need the
    FULL software golden (e.g. ``codec_golden.py``). The GATE, by contrast, needs
    a value it can compare to the chip's egress -- often a bytes-only WRAPPER
    pointed at by ``CORESMITH_SOURCE_ROOT``. Conflating the two starves the
    generators (a bytes-only wrapper has no per-block math -> empty stub blocks).

    Resolution: ``CORESMITH_GENERATOR_SOURCE`` (a file/dir) wins; otherwise fall
    back to :func:`resolve_reference_implementation` (so single-reference designs
    and existing setups are unchanged).
    """
    env_src = os.environ.get("CORESMITH_GENERATOR_SOURCE", "").strip()
    if env_src:
        p = Path(env_src)
        if p.is_file():
            return str(p.resolve())
        if p.is_dir():
            hits = sorted(p.glob("*_golden.py")) or sorted(p.glob("**/*_golden.py"))
            if hits:
                return str(hits[0].resolve())
    return resolve_reference_implementation(project_root)


def resolve_reference_implementation(project_root: str) -> str | None:
    """Locate the design's **reference implementation** (input software golden).

    Search order (first hit wins):

    0. ``CORESMITH_SOURCE_ROOT`` env var (a file, or a dir to scan) -- an
       EXPLICIT operator override always wins over auto-discovery, so a
       bitstream-only reference WRAPPER can be pointed at even when the PRD
       text names the raw ``*_golden.py`` (whose richer return type the gate
       cannot compare).
    1. An explicit path recorded in the PRD/requirements (a line naming a
       ``*_golden.py``) under ``<root>/arch`` or ``<root>/inputs``.
    2. ``examples/<design>/*_golden.py`` relative to the project root (a run
       dir that copied an example design).
    3. ``<root>/inputs/*_golden.py`` then ``<root>/inputs/*.py``.
    4. Any ``*_golden.py`` anywhere under the project root.

    Returns an absolute path string, or ``None`` when nothing is found (the
    gate then becomes a logged no-op).
    """
    root = Path(project_root)

    # 0. Explicit env override -- highest priority.
    env_src = os.environ.get("CORESMITH_SOURCE_ROOT", "").strip()
    if env_src:
        p = Path(env_src)
        if p.is_file():
            return str(p.resolve())
        if p.is_dir():
            hits = sorted(p.glob("*_golden.py")) or sorted(p.glob("**/*_golden.py"))
            if hits:
                return str(hits[0].resolve())

    # 1. Explicit reference cited in PRD / requirements text.
    for doc in (
        root / "arch" / "prd_spec.md",
        root / "inputs" / "requirements.md",
        root / "requirements.md",
    ):
        if doc.exists():
            try:
                text = doc.read_text(encoding="utf-8")
            except OSError:
                continue
            m = re.search(r"([^\s`'\"]+_golden\.py)", text)
            if m:
                cand = (root / m.group(1)).resolve()
                if cand.exists():
                    return str(cand)
                # Also try as an absolute / cwd-relative path.
                cand2 = Path(m.group(1))
                if cand2.exists():
                    return str(cand2.resolve())

    # 2. examples/<design>/*_golden.py under the project root.
    examples_dir = root / "examples"
    if examples_dir.is_dir():
        hits = sorted(examples_dir.glob("*/*_golden.py"))
        if hits:
            return str(hits[0].resolve())

    # 3. inputs/.
    inputs_dir = root / "inputs"
    if inputs_dir.is_dir():
        hits = sorted(inputs_dir.glob("*_golden.py")) or sorted(
            inputs_dir.glob("*.py")
        )
        if hits:
            return str(hits[0].resolve())

    # 5. Anywhere under the root.
    hits = sorted(root.glob("**/*_golden.py"))
    if hits:
        return str(hits[0].resolve())

    return None


# Regex for a declared reference entry point in PRD/FRD prose.
_REF_ENTRY_DECL_RE = re.compile(
    r"reference[_ ]entry[_ ]point[:=]\s*([A-Za-z_][\w.]*)",
    re.IGNORECASE,
)

# Public callable names we prefer when discovering an entry point.
#
# ``main`` is deliberately ABSENT. Discovery is a heuristic over whatever the
# reference module happens to export, and ``main`` is the one name every
# UNRELATED utility script also uses: a live run auto-selected a ROM-image
# generator's zero-argument ``main()`` as the WHOLE-CHIP oracle and every
# vector died on "main() takes 0 positional arguments but 1 was given" -- a
# category error that consumed 11 chip-lead decisions. What is removed is the
# GUESS: an explicit CORESMITH_REFERENCE_ENTRY or a declared
# reference_entry_point may still name ``main`` (it is then ABI-preflighted).
_ENTRY_NAME_RE = re.compile(
    r"^(encode|decode|run|process|top|encode_image\w*|chip_top)\b",
    re.IGNORECASE,
)

# Preference order among conventional entry names. ``dir()`` returns names
# ALPHABETICALLY, so without an explicit intent ranking a codec golden that
# exposes both ``encode`` and ``decode`` resolved to ``decode`` ('d' < 'e') --
# the wrong oracle for an ENCODER design. Rank by the design's primary
# transform: encode before decode, generic drivers last. (Override always
# available via CORESMITH_REFERENCE_ENTRY or a declared reference_entry_point.)
_ENTRY_PRIORITY = (
    "encode_image", "encode", "decode", "chip_top", "top", "run", "process",
)

# Names DISCOVERY must never guess -- not as a conventional name, and not as
# the module's sole public callable either. ``main`` is a script convention, so
# the fact that a module exposes one says nothing about whether it models the
# CHIP: the live regression selected a ROM generator's ``main`` as the
# whole-chip oracle. Naming it explicitly (CORESMITH_REFERENCE_ENTRY /
# reference_entry_point) still works -- that is an operator asserting intent,
# not the engine inferring it.
_ENTRY_NAME_DENY = frozenset({"main"})


def _entry_priority(name: str) -> tuple[int, str]:
    """Rank a conventional entry name by INTENT, not alphabetically.

    Lower sorts first. Only names that already matched ``_ENTRY_NAME_RE`` reach
    here, so the prefix check is safe. Unknown-but-matching names sort after the
    known set, tie-broken by name for determinism.
    """
    low = name.lower()
    for i, key in enumerate(_ENTRY_PRIORITY):
        if low == key or low.startswith(key):
            return (i, low)
    return (len(_ENTRY_PRIORITY), low)


def _public_callables(module) -> list[tuple[str, Callable]]:
    """Public (non-underscore) top-level callables defined in ``module``.

    Only includes functions/callables whose ``__module__`` is the module itself
    (so imported helpers like ``json.loads`` are not mistaken for entries).
    """
    out: list[tuple[str, Callable]] = []
    mod_name = getattr(module, "__name__", None)
    for name in dir(module):
        if name.startswith("_"):
            continue
        obj = getattr(module, name, None)
        if not callable(obj):
            continue
        # Prefer functions defined in this module; tolerate callables without a
        # __module__ (e.g. some builtins) by excluding them.
        obj_mod = getattr(obj, "__module__", None)
        if obj_mod is not None and mod_name is not None and obj_mod != mod_name:
            continue
        if inspect.isfunction(obj) or inspect.ismethod(obj):
            out.append((name, obj))
    return out


class ReferenceEntryPointError(RuntimeError):
    """An EXPLICITLY configured reference entry cannot be the design's oracle.

    Raised by :func:`resolve_reference_entrypoint` when
    ``CORESMITH_REFERENCE_ENTRY`` / a declared ``reference_entry_point`` names a
    callable that takes no positional argument, and by the model-integration
    gate's stimulus derivation for the same callable. Explicit config is an
    OPERATOR DECISION: quietly falling back to a heuristic guess hides the typo
    (and hands the gate a different oracle than the operator asked for), so the
    run fails fast with the callable's real signature instead.
    """


# Resolution tiers, reported as PROVENANCE so a log shows which one won.
ENTRY_SOURCE_ENV = "env"
ENTRY_SOURCE_DECLARED = "declared"
ENTRY_SOURCE_DISCOVERED = "discovered"
ENTRY_SOURCE_NONE = "none"


def _entry_signature(entry_callable) -> str:
    """``name(signature)`` for error messages; degrades, never raises."""
    name = (
        getattr(entry_callable, "__qualname__", None)
        or getattr(entry_callable, "__name__", None)
        or repr(entry_callable)
    )
    try:
        return f"{name}{inspect.signature(entry_callable)}"
    except (TypeError, ValueError):
        return f"{name}(<signature unavailable>)"


def _entry_accepts_stimulus(entry_callable) -> bool:
    """ABI preflight: can this callable be CALLED WITH A STIMULUS?

    True iff it accepts at least one positional argument -- a POSITIONAL_ONLY /
    POSITIONAL_OR_KEYWORD parameter, or ``*args``. Every oracle invocation goes
    through :func:`_run_reference`, which calls ``entry(stimulus)`` (or
    ``entry(**stimulus)``, which still needs those parameters), so a
    zero-positional callable is not an oracle -- it is a script entry point
    that merely happens to be public.

    Callables whose signature cannot be introspected (C builtins, exotic
    ``__call__``) are ACCEPTED: "unknown" is not proof of impossibility, and a
    real invocation failure is still reported as ``reference_uninvokable``.
    """
    if entry_callable is None or not callable(entry_callable):
        return False
    try:
        sig = inspect.signature(entry_callable)
    except (TypeError, ValueError):
        return True
    return any(
        p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        )
        for p in sig.parameters.values()
    )


def _entry_abi_message(entry_callable, spec: str = "", source: str = "") -> str:
    """The actionable message for an entry that cannot accept a stimulus."""
    origin = f" (from {source})" if source else ""
    named = f"{spec!r}{origin} -> " if spec else ""
    return (
        f"reference entry {named}{_entry_signature(entry_callable)} takes NO "
        "positional argument, so it cannot be the design's oracle -- the gate "
        "invokes it as entry(stimulus). Point CORESMITH_REFERENCE_ENTRY (or the "
        "PRD/FRD 'reference_entry_point:') at the callable that CONSUMES the "
        "stimulus, e.g. 'encode' or 'my_module:run'."
    )


def resolve_reference_entrypoint(
    project_root: str,
    ref_module,
    *,
    with_provenance: bool = False,
) -> tuple[Callable | None, str] | tuple[Callable | None, str, str]:
    """Resolve the callable that IS the design's executable oracle.

    Resolution order (first hit wins):

    1. ``CORESMITH_REFERENCE_ENTRY`` env var, formatted ``"func"`` or
       ``"module:func"``. ``"func"`` is ``getattr`` on ``ref_module``;
       ``"module:func"`` imports ``module`` (a submodule / dotted path) and
       reads ``func`` from it (falling back to ``getattr(ref_module, func)``).
    2. A declared entry in ``arch/prd_spec.md`` or ``arch/frd_spec.md`` matching
       ``reference_entry_point: <name>`` (the name may be dotted/attr-pathed and
       is resolved against ``ref_module``).
    3. Discovery on ``ref_module``: among its public top-level functions, prefer
       a name matching ``^(encode|decode|run|process|top|encode_image*|
       chip_top)``; else, if there is exactly one public top-level function,
       use it. ``main`` is never GUESSED at either step (``_ENTRY_NAME_DENY``)
       -- tiers 1 and 2 can still name it.

    ABI PREFLIGHT (every tier, :func:`_entry_accepts_stimulus`): the oracle is
    invoked as ``entry(stimulus)``, so a callable with no positional parameter
    cannot be one. DISCOVERY never selects such a callable -- a live run picked
    a ROM utility's zero-argument ``main()`` as the whole-chip reference and
    burned 11 chip-lead decisions on "main() takes 0 positional arguments but 1
    was given". An EXPLICIT entry (env var / declared) that fails the preflight
    raises :class:`ReferenceEntryPointError` naming the callable and its
    signature, rather than silently degrading to the heuristics.

    Returns ``(callable_or_None, dotted_name_str)`` -- or, with
    ``with_provenance=True``, ``(callable_or_None, dotted_name_str, source)``
    where ``source`` is ``"env"`` / ``"declared"`` / ``"discovered"`` /
    ``"none"``, so the daemon log shows WHICH tier chose the oracle.
    ``dotted_name_str`` is a best-effort human-readable name for logging even
    when the callable is None.
    """
    fn, name, source = _resolve_reference_entrypoint_tiered(
        project_root, ref_module
    )
    if with_provenance:
        return fn, name, source
    return fn, name


def _resolve_reference_entrypoint_tiered(
    project_root: str,
    ref_module,
) -> tuple[Callable | None, str, str]:
    """The tiered resolution behind :func:`resolve_reference_entrypoint`.

    Returns ``(callable_or_None, name, source)``; see the public wrapper for
    the tier order and the ABI preflight contract.
    """
    # 1. env override -- EXPLICIT: honour it or fail loudly, never guess past it.
    env_entry = os.environ.get("CORESMITH_REFERENCE_ENTRY", "").strip()
    if env_entry:
        fn = _resolve_dotted_entry(env_entry, ref_module)
        if fn is not None:
            if not _entry_accepts_stimulus(fn):
                raise ReferenceEntryPointError(
                    _entry_abi_message(fn, env_entry, "CORESMITH_REFERENCE_ENTRY")
                )
            logger.info(
                "composition gate: reference entry %r resolved via %s",
                env_entry, ENTRY_SOURCE_ENV,
            )
            return fn, env_entry, ENTRY_SOURCE_ENV
        logger.warning(
            "composition gate: CORESMITH_REFERENCE_ENTRY=%r did not resolve",
            env_entry,
        )

    # 2. declared in PRD / FRD prose -- also EXPLICIT.
    root = Path(project_root)
    for doc in (root / "arch" / "prd_spec.md", root / "arch" / "frd_spec.md"):
        if not doc.exists():
            continue
        try:
            text = doc.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _REF_ENTRY_DECL_RE.search(text)
        if m:
            decl = m.group(1)
            fn = _resolve_dotted_entry(decl, ref_module)
            if fn is not None:
                if not _entry_accepts_stimulus(fn):
                    raise ReferenceEntryPointError(
                        _entry_abi_message(
                            fn, decl, f"{doc.name} reference_entry_point"
                        )
                    )
                logger.info(
                    "composition gate: reference entry %r resolved via %s (%s)",
                    decl, ENTRY_SOURCE_DECLARED, doc.name,
                )
                return fn, decl, ENTRY_SOURCE_DECLARED
            logger.warning(
                "composition gate: declared reference_entry_point %r "
                "did not resolve",
                decl,
            )

    # 3. discovery on the ref module
    if ref_module is not None:
        publics = _public_callables(ref_module)
        # ABI preflight as a HARD FILTER: a zero-positional callable is never
        # DISCOVERED as the oracle (that is the ROM-utility `main()` bug).
        usable: list[tuple[str, Callable]] = []
        rejected: list[str] = []
        for name, fn in publics:
            if _entry_accepts_stimulus(fn):
                usable.append((name, fn))
            else:
                rejected.append(name)
        if rejected:
            logger.info(
                "composition gate: discovery skipped %s -- no positional "
                "parameter, so they cannot be called with a stimulus",
                rejected,
            )
        denied = [name for name, _ in usable if name.lower() in _ENTRY_NAME_DENY]
        if denied:
            usable = [
                (name, fn)
                for name, fn in usable
                if name.lower() not in _ENTRY_NAME_DENY
            ]
            logger.warning(
                "composition gate: discovery will not GUESS %s as the chip "
                "oracle -- set CORESMITH_REFERENCE_ENTRY=%s (or a declared "
                "reference_entry_point) if that really is the reference entry",
                denied, denied[0],
            )
        # Prefer a conventionally-named entry, RANKED BY INTENT -- not by the
        # alphabetical dir() order, which made an encoder golden exposing both
        # `encode` and `decode` resolve to `decode` ('d' < 'e').
        conventional = [
            (name, fn) for name, fn in usable if _ENTRY_NAME_RE.match(name)
        ]
        if conventional:
            conventional.sort(key=lambda item: _entry_priority(item[0]))
            if len(conventional) > 1:
                logger.warning(
                    "composition gate: multiple conventional reference entries "
                    "%s -- chose %r by intent priority (encode before decode); "
                    "set CORESMITH_REFERENCE_ENTRY or a declared "
                    "reference_entry_point to override",
                    [n for n, _ in conventional],
                    conventional[0][0],
                )
            name, fn = conventional[0]
            logger.info(
                "composition gate: reference entry %r resolved via %s",
                name, ENTRY_SOURCE_DISCOVERED,
            )
            return fn, name, ENTRY_SOURCE_DISCOVERED
        # Else the single public top-level function, if unambiguous.
        if len(usable) == 1:
            name, fn = usable[0]
            logger.info(
                "composition gate: reference entry %r resolved via %s "
                "(sole public function)",
                name, ENTRY_SOURCE_DISCOVERED,
            )
            return fn, name, ENTRY_SOURCE_DISCOVERED

    return None, env_entry or "", ENTRY_SOURCE_NONE


def _resolve_dotted_entry(spec: str, ref_module) -> Callable | None:
    """Resolve a ``"func"`` / ``"module:func"`` / ``"a.b.func"`` entry spec.

    ``module:func`` imports ``module`` (a dotted import path) and reads ``func``
    off it. A bare ``func`` or ``attr.path`` is resolved against ``ref_module``
    via successive ``getattr``. Returns the callable or ``None``.
    """
    spec = spec.strip()
    if not spec:
        return None

    if ":" in spec:
        mod_path, _, attr = spec.partition(":")
        target = None
        # Try importing as a submodule of the ref module first, then absolute.
        candidates = []
        ref_name = getattr(ref_module, "__name__", None)
        if ref_name:
            candidates.append(f"{ref_name}.{mod_path}")
        candidates.append(mod_path)
        for cand in candidates:
            try:
                target = importlib.import_module(cand)
                break
            except Exception:  # noqa: BLE001
                target = None
        if target is None:
            # Fall back: treat the whole thing as an attr path on ref_module.
            return _getattr_path(ref_module, attr)
        fn = _getattr_path(target, attr)
        if fn is None and ref_module is not None:
            fn = _getattr_path(ref_module, attr)
        return fn

    return _getattr_path(ref_module, spec)


def _getattr_path(obj, dotted: str) -> Callable | None:
    """``getattr`` along a dotted path; return the callable or None."""
    if obj is None or not dotted:
        return None
    cur = obj
    for part in dotted.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur if callable(cur) else None


# ---------------------------------------------------------------------------
# Block golden loading
# ---------------------------------------------------------------------------

def _import_module_from_path(path: Path, mod_name: str):
    """Import a module from a file path under a private module name."""
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Composition (topological wiring + execution)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def _load_reference_module(path: str):
    """Import the reference implementation module from a file path."""
    return _import_module_from_path(Path(path), "_coresmith_reference_impl")


def _flatten_numbers(obj: Any) -> list:
    """Flatten a nested output into a flat list of leaves (numbers kept as
    numbers; non-numeric leaves kept for strict !=)."""
    out: list = []

    def _rec(x: Any) -> None:
        if isinstance(x, bool):
            out.append(int(x))
        elif isinstance(x, (int, float)):
            out.append(x)
        elif hasattr(x, "tolist") and not isinstance(x, (str, bytes)):
            _rec(x.tolist())  # numpy array/scalar
        elif isinstance(x, (list, tuple)):
            for y in x:
                _rec(y)
        elif isinstance(x, dict):
            for y in x.values():
                _rec(y)
        else:
            out.append(x)

    _rec(obj)
    return out


def _normalize_ref_output(value: Any) -> Any:
    """Normalise a reference return into a comparable structure.

    Tuples become lists (JSON-comparable); everything else is returned as-is.
    Nested tuples are handled recursively.
    """
    if isinstance(value, tuple):
        return [_normalize_ref_output(v) for v in value]
    if isinstance(value, list):
        return [_normalize_ref_output(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize_ref_output(v) for k, v in value.items()}
    return value


class ReferenceInvocationError(RuntimeError):
    """The reference entry callable could not be invoked on the stimulus.

    Raised by :func:`_run_reference` when ``reraise=True`` and the call fails
    (bad signature, wrong stimulus type, exception inside the reference). The
    model-integration gate treats this as a HARD failure: a reference that
    cannot be invoked provides no oracle, so the run must NOT pass vacuously.
    """


def _run_reference(
    entry_callable: Callable | None,
    stimulus_struct: Any,
    *,
    reraise: bool = False,
) -> Any:
    """Run the reference entry callable on a structured stimulus.

    ``stimulus_struct`` is normally a ``{chip_port: value-or-list}`` dict. It is
    mapped to the callable's signature by parameter NAME where keys match
    declared parameters; otherwise the values are passed positionally in port
    order. A non-dict stimulus is passed as a single positional argument.

    On failure (bad signature, wrong stimulus type, exception inside the
    reference): when ``reraise`` is False (legacy/v1 path) returns ``None``;
    when True (the model-integration gate) raises
    :class:`ReferenceInvocationError` so the caller surfaces a HARD violation
    instead of silently treating "no oracle" as a pass.
    The successful return is normalised via :func:`_normalize_ref_output`.
    """
    if entry_callable is None:
        return None
    try:
        if isinstance(stimulus_struct, dict):
            try:
                sig = inspect.signature(entry_callable)
                param_names = [
                    p.name
                    for p in sig.parameters.values()
                    if p.kind
                    in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        inspect.Parameter.KEYWORD_ONLY,
                    )
                ]
                accepts_var_kw = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                )
            except (TypeError, ValueError):
                param_names = []
                accepts_var_kw = False

            keys = list(stimulus_struct.keys())
            # By-name mapping when the param names line up with stimulus keys.
            if param_names and (
                accepts_var_kw or set(keys).issubset(set(param_names))
            ) and all(k in param_names for k in keys if not accepts_var_kw):
                if set(keys) & set(param_names) or accepts_var_kw:
                    result = entry_callable(**stimulus_struct)
                    return _normalize_ref_output(result)

            # Single-parameter callable that wants the whole dict (e.g.
            # ``def run(stim): ...``) -- pass the dict as one positional arg.
            if len(param_names) == 1 and param_names[0] not in keys:
                result = entry_callable(stimulus_struct)
                return _normalize_ref_output(result)

            # Otherwise pass values positionally in port (insertion) order.
            result = entry_callable(*[stimulus_struct[k] for k in keys])
            return _normalize_ref_output(result)

        # Non-dict stimulus: single positional argument.
        result = entry_callable(stimulus_struct)
        return _normalize_ref_output(result)
    except Exception as exc:
        logger.warning(
            "composition gate: reference entry call failed: %s: %s",
            type(exc).__name__,
            exc,
        )
        if reraise:
            raise ReferenceInvocationError(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return None


