# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""WP-49: ONE declared top module and ONE candidate receipt.

Before this, five places guessed the chip top independently: the first
``module`` keyword in a file, the file stem, the module nobody instantiates,
the module matching the design name, and a pad-boundary heuristic. They
disagreed (review round 2), and the backend once synthesized a different
candidate than the frontend verified.

Now:

* ``declared_top(project_root)`` is what the TASK says the graded top is
  (``CORESMITH_TOP_MODULE``, else ``inputs/task.yaml: top``), or "".
* The integration check writes ``.coresmith/candidate.json`` -- the top
  module, the file that declares it, the exact source list and a sha256 over
  their bytes -- and refuses a top that contradicts the declared one.
* Every later consumer (acceptance, task adapter, backend synthesis, lint)
  reads the receipt through ``resolve_top`` and never guesses.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

RECEIPT_REL = Path(".coresmith") / "candidate.json"
_log = logging.getLogger(__name__)


def declared_top(project_root) -> str:
    """The top module the task declares, or "" when it declares none."""
    env = (os.environ.get("CORESMITH_TOP_MODULE", "") or "").strip()
    if env:
        return env
    ty = Path(project_root) / "inputs" / "task.yaml"
    if ty.exists():
        try:
            for line in ty.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"^\s*top\s*:\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)", line)
                if m:
                    return m.group(1)
        except OSError:
            pass
    return ""


def _code(text: str) -> str:
    try:
        from orchestrator.langgraph.contract_conformance import strip_preprocessor
        text = strip_preprocessor(text)
    except Exception:  # noqa: BLE001 - layering guard
        pass
    return re.sub(r"//[^\n]*", " ", re.sub(r"/\*.*?\*/", " ", text, flags=re.S))


def module_declared_in(path, name: str) -> bool:
    """True when the file declares ``module <name>`` outside comments and
    outside preprocessor branches that lint/sim do not see."""
    if not path or not name:
        return False
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(rf"\bmodule\s+{re.escape(name)}\b", _code(text)) is not None


def candidate_sources(project_root, top_rtl: str, block_rtls: Any) -> list[str]:
    """The exact source list a candidate elaborates: top, blocks, the pad
    adapter beside a deterministically assembled top, the SRAM library."""
    top_p = Path(top_rtl)
    if isinstance(block_rtls, dict):
        blocks = [str(p) for p in block_rtls.values() if p]
    else:
        blocks = [str(p) for p in (block_rtls or []) if p]
    sources: list[str] = [str(top_p.resolve())] if top_p.exists() else []
    for b in blocks:
        rb = str(Path(b).resolve())
        if Path(rb).exists() and rb not in sources:
            sources.append(rb)
    pads = top_p.with_name(top_p.stem + "_pads.v")
    if pads.exists() and str(pads.resolve()) not in sources:
        sources.append(str(pads.resolve()))
    try:
        from orchestrator.langgraph.sram_wrapper import uses_wrapper, wrapper_lib_path

        if any(uses_wrapper(Path(s).read_text(encoding="utf-8", errors="replace"))
               for s in sources):
            lib = str(wrapper_lib_path())
            if lib and Path(lib).exists() and lib not in sources:
                sources.append(lib)
    except Exception:  # noqa: BLE001 - lib resolution is best-effort
        pass
    return sources


def candidate_sha(top_module: str, sources: list[str]) -> str:
    h = hashlib.sha256()
    h.update(str(top_module).encode())
    for s in sources:
        h.update(b"\0" + Path(s).name.encode() + b"\0")
        h.update(Path(s).read_bytes())
    return h.hexdigest()


def write_candidate_receipt(project_root, top_module: str, top_rtl_path: str,
                            block_rtls: Any, note: str = "") -> dict:
    """Record the candidate. Raises ValueError when ``top_rtl_path`` does not
    declare ``top_module`` -- a receipt never lies about its top."""
    if not module_declared_in(top_rtl_path, top_module):
        raise ValueError(f"{top_rtl_path} does not declare module {top_module!r}")
    _declared = declared_top(project_root)
    if _declared and top_module != _declared:
        raise ValueError(f"the task declares top {_declared!r} but the candidate top is "
                         f"{top_module!r}")
    sources = candidate_sources(project_root, top_rtl_path, block_rtls)
    receipt = {
        "top_module": top_module,
        "top_rtl_path": str(Path(top_rtl_path).resolve()),
        "sources": sources,
        "candidate_sha": candidate_sha(top_module, sources),
        "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": note,
    }
    p = Path(project_root) / RECEIPT_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(receipt, indent=1))
    return receipt


def receipt_is_current(rec: dict) -> bool:
    """True when every recorded source still exists and hashes to the recorded
    candidate sha (WP-54): a receipt whose sources changed is stale."""
    try:
        sources = [str(x) for x in (rec.get("sources") or [])]
        if not sources or any(not Path(s).exists() for s in sources):
            return False
        return candidate_sha(str(rec.get("top_module") or ""), sources) == \
            str(rec.get("candidate_sha") or "")
    except OSError:
        return False


def read_candidate_receipt(project_root) -> dict | None:
    p = Path(project_root) / RECEIPT_REL
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def resolve_top(project_root) -> tuple[str, str]:
    """``(top_module, top_rtl_path)`` from the candidate receipt, else from the
    integration record; ("", "") when neither names a top that its file
    declares. Never a guess from file contents."""
    rec = read_candidate_receipt(project_root)
    if rec:
        mod, path = str(rec.get("top_module") or ""), str(rec.get("top_rtl_path") or "")
        if mod and path and module_declared_in(path, mod) and receipt_is_current(rec):
            return mod, path
        if rec:
            _log.warning("candidate receipt is stale or inconsistent (%s); ignoring it",
                         path or "?")
    try:
        ir = json.loads((Path(project_root) / ".coresmith" / "integration_result.json")
                        .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    mod, path = str(ir.get("top_module") or ""), str(ir.get("top_rtl_path") or "")
    if mod and path and module_declared_in(path, mod):
        return mod, path
    return "", ""
