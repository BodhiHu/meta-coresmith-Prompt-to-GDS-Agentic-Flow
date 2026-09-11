# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""One validated candidate manifest: explicit top, sources, assets and configuration."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

RECEIPT_REL = Path(".coresmith") / "candidate.json"


class CandidateError(ValueError):
    def __init__(self, reason: str, kind: str = "candidate_mismatch"):
        super().__init__(reason)
        self.kind = kind


def declared_top(project_root) -> str:
    env = os.environ.get("CORESMITH_TOP_MODULE", "").strip()
    if env:
        return env
    import yaml
    path = Path(project_root) / "inputs/task.yaml"
    if not path.exists():
        return ""
    try:
        data = yaml.safe_load(path.read_text()) or {}
        return str(data.get("top") or "")
    except (OSError, ValueError, AttributeError, yaml.YAMLError) as exc:
        raise CandidateError(f"Cannot read task top declaration: {exc}", "infrastructure_error") from exc


def _code(text: str, defines=()) -> str:
    from orchestrator.langgraph.contract_conformance import strip_preprocessor
    text = strip_preprocessor(text, defines=[d.split("=", 1)[0] for d in defines])
    return re.sub(r"//[^\n]*", " ", re.sub(r"/\*.*?\*/", " ", text, flags=re.S))


def module_declared_in(path, name: str, defines=()) -> bool:
    if not path or not name:
        return False
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(rf"\bmodule\s+{re.escape(name)}\b", _code(text, defines)) is not None


def candidate_sources(project_root, top_rtl: str, block_rtls: Any) -> list[str]:
    """Select sources at adoption only. Missing inputs are never filtered away."""
    blocks = block_rtls.values() if isinstance(block_rtls, dict) else (block_rtls or [])
    paths = list(dict.fromkeys(str(Path(p).resolve()) for p in [top_rtl, *blocks] if p))
    if not top_rtl or any(not Path(p).is_file() for p in paths):
        raise CandidateError("Candidate source is missing", "infrastructure_error")
    from orchestrator.langgraph.sram_wrapper import uses_wrapper, wrapper_lib_path
    if any(uses_wrapper(Path(p).read_text()) for p in paths):
        lib = str(Path(wrapper_lib_path()).resolve())
        if not Path(lib).is_file():
            raise CandidateError("Candidate SRAM library is missing", "infrastructure_error")
        if lib not in paths:
            from orchestrator.harness.candidate_library import library_sources
            paths.extend(library_sources(project_root, paths, lib))
    return paths


def _dependencies(sources: list[str], project_root, *, top_module="", parameters=None) -> list[str]:
    """Bind exactly the literals staged for hierarchy elaboration."""
    from orchestrator.harness.readmem_assets import bind_assets
    assets = bind_assets(sources, project_root, top_module=top_module, parameters=parameters)
    return sorted(str(p) for p in assets.dependencies - {Path(p).resolve() for p in sources})


def candidate_sha(top_module: str, sources: list[str], *, dependencies=(), defines="none",
                  parameters="none") -> str:
    h = hashlib.sha256(json.dumps({"top": top_module, "sources": sources,
        "dependencies": list(dependencies), "defines": defines, "parameters": parameters},
        sort_keys=True, separators=(",", ":")).encode())
    for path in [*sources, *dependencies]:
        data = Path(path).read_bytes()
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def _atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".candidate-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def invalidate_candidate(project_root) -> None:
    """A rejected replacement must not leave an older success discoverable."""
    root = Path(project_root)
    (root / RECEIPT_REL).unlink(missing_ok=True)
    (root / ".coresmith/integration_result.json").unlink(missing_ok=True)


def write_candidate_receipt(project_root, top_module: str, top_rtl_path: str,
                            block_rtls: Any, note: str = "", *, defines=(), parameters=None,
                            expected_blocks=None, integration_result=None) -> dict:
    """The sole adoption operation; publish success only after every check passes."""
    from orchestrator.harness.hierarchy import HierarchyFailure, elaborate_hierarchy, missing_blocks
    root = Path(project_root).resolve()
    receipt_path = root / RECEIPT_REL
    record_path = root / ".coresmith/integration_result.json"
    invalidate_candidate(root)
    defines, parameters = sorted(defines or []), dict(parameters or {})
    declared = declared_top(root)
    if declared and top_module != declared:
        raise CandidateError(f"the task declares top {declared!r} but candidate top is {top_module!r}")
    if not module_declared_in(top_rtl_path, top_module, defines):
        raise CandidateError(f"{top_rtl_path} does not declare module {top_module!r}")
    sources = candidate_sources(root, top_rtl_path, block_rtls)
    dependencies = _dependencies(sources, root, top_module=top_module, parameters=parameters)
    before_sha = candidate_sha(top_module, sources, dependencies=dependencies,
                               defines=defines or "none", parameters=parameters or "none")
    expected = sorted(expected_blocks if expected_blocks is not None else
                      (block_rtls.keys() if isinstance(block_rtls, dict) else []))
    cells = elaborate_hierarchy(sources, top_module, defines=defines, parameters=parameters, project_root=root)
    failure = cells if isinstance(cells, HierarchyFailure) else missing_blocks(cells, expected, top_module)
    if failure:
        raise CandidateError(str(failure), failure.kind)
    if candidate_sha(top_module, sources, dependencies=dependencies,
                     defines=defines or "none", parameters=parameters or "none") != before_sha:
        raise CandidateError("Candidate changed during hierarchy elaboration")
    from orchestrator.harness.candidate_library import retain_elaborated
    sources = retain_elaborated(sources, root, cells | {top_module})
    dependencies = _dependencies(sources, root, top_module=top_module, parameters=parameters)
    before_sha = candidate_sha(top_module, sources, dependencies=dependencies,
                               defines=defines or "none", parameters=parameters or "none")
    rec = {"version": 2, "project_root": str(root), "top_module": top_module,
           "top_rtl_path": str(Path(top_rtl_path).resolve()), "sources": sources,
           "dependencies": dependencies, "defines": defines or "none", "parameters": parameters or "none",
           "expected_blocks": expected, "elaborated_cells": sorted(cells),
           "written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "note": note}
    rec["candidate_sha"] = candidate_sha(top_module, sources, dependencies=dependencies,
                                         defines=rec["defines"], parameters=rec["parameters"])
    if (rec["candidate_sha"] != before_sha or _dependencies(
            sources, root, top_module=top_module, parameters=parameters) != dependencies):
        raise CandidateError("Candidate changed during hierarchy elaboration")
    _atomic_json(receipt_path, rec)
    if integration_result is not None:
        try:
            _atomic_json(record_path, {**integration_result, "candidate_sha": rec["candidate_sha"]})
        except OSError:
            receipt_path.unlink(missing_ok=True)
            raise
    return rec


def receipt_is_current(rec: dict) -> bool:
    try:
        if rec.get("version") != 2 or not rec.get("sources"):
            return False
        dependencies = _dependencies(rec["sources"], rec["project_root"],
                                     top_module=rec["top_module"],
                                     parameters=rec["parameters"] if rec["parameters"] != "none" else {})
        return dependencies == rec["dependencies"] and candidate_sha(
            rec["top_module"], rec["sources"], dependencies=dependencies,
            defines=rec["defines"], parameters=rec["parameters"]) == rec["candidate_sha"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def read_candidate_receipt(project_root) -> dict | None:
    try:
        rec = json.loads((Path(project_root) / RECEIPT_REL).read_text())
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def validated_candidate(project_root) -> dict:
    rec = read_candidate_receipt(project_root)
    if not rec:
        raise CandidateError("Validated candidate manifest is missing", "infrastructure_error")
    sources = rec.get("sources") or []
    if any(not Path(p).is_file() for p in [*sources, *rec.get("dependencies", [])]):
        raise CandidateError("Recorded candidate file is missing", "infrastructure_error")
    declared = declared_top(project_root)
    if declared and declared != rec.get("top_module"):
        raise CandidateError(f"task declares top {declared!r}, contradicting recorded candidate")
    if (not receipt_is_current(rec) or rec.get("top_rtl_path") not in sources
            or str(Path(project_root).resolve()) != rec.get("project_root")):
        raise CandidateError("Candidate manifest is stale or inconsistent")
    return rec


def candidate_for_inputs(project_root, top_rtl: str, block_rtls=None) -> dict:
    rec = validated_candidate(project_root)
    blocks = block_rtls.values() if isinstance(block_rtls, dict) else (block_rtls or [])
    supplied = {str(Path(p).resolve()) for p in [top_rtl, *blocks] if p}
    if (not top_rtl or str(Path(top_rtl).resolve()) != rec["top_rtl_path"]
            or not supplied.issubset(set(rec["sources"]))):
        raise CandidateError("Supplied sources are not the recorded candidate (extra source or different top)")
    return rec


def resolve_top(project_root) -> tuple[str, str]:
    rec = validated_candidate(project_root)
    return rec["top_module"], rec["top_rtl_path"]
