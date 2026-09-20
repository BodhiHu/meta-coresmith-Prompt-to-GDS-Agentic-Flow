"""Candidate hierarchy evidence from Yosys elaboration, never source matching."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


class HierarchyFailure(str):
    """A truthy, serializable postcondition failure with a machine-readable kind."""

    def __new__(cls, reason: str, kind: str = "hierarchy_error"):
        obj = super().__new__(cls, reason)
        obj.kind = kind
        return obj


def _reachable_hierarchy(design: dict, top_module: str) -> set[str]:
    modules = design["modules"]
    if top_module not in modules:
        raise ValueError(f"elaborator did not return top {top_module!r}")
    reached, pending, instantiated = set(), [top_module], set()
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        reached.add(name)
        for cell in modules[name]["cells"].values():
            child = cell["type"]
            if child not in modules:
                if child.startswith("$"):
                    continue  # Yosys primitive, not a design block.
                raise ValueError(f"elaborator returned unresolved cell {child!r}")
            instantiated.add(child)
            # Parameter specialization records the original HDL module name.
            origin = modules[child].get("attributes", {}).get("hdlname", "")
            instantiated.update(origin.split())
            pending.append(child)
    return instantiated


def _stage_sources(paths, stage: Path, project_root=None):
    """Stage the same include/data literals that candidate adoption binds."""
    from orchestrator.harness.readmem_assets import bind_assets
    return bind_assets(paths, project_root or paths[0].parent).stage(paths, stage)


def elaborate_hierarchy(source_paths, top_module: str, *, defines=(), parameters=None,
                        project_root=None) -> set[str] | HierarchyFailure:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top_module or ""):
        return HierarchyFailure("Integration postcondition failed: an explicit valid top is required")
    yosys = shutil.which("yosys")
    if not yosys:
        return HierarchyFailure("Hierarchy elaborator yosys is unavailable", "infrastructure_error")
    # Only simple tokens enter Yosys commands; paths are quoted separately.
    defines = list(defines or ())
    parameters = dict(parameters or {})
    if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:=[A-Za-z0-9_'hHbBdDxX+-]+)?", d)
           for d in defines) or any(
               not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
               or not re.fullmatch(r"[A-Za-z0-9_'hHbBdD+-]+", str(v))
               for k, v in parameters.items()):
        return HierarchyFailure("Unsupported elaboration define/parameter token")
    try:
        paths = [Path(p).resolve(strict=True) for p in source_paths]
        if not paths:
            return HierarchyFailure("Hierarchy has no selected sources")
        with tempfile.TemporaryDirectory(prefix="coresmith-hierarchy-") as td:
            output = Path(td) / "design.json"
            script = Path(td) / "elaborate.ys"
            include_dirs = sorted({str(p.parent) for p in paths})
            if project_root:
                include_dirs.append(str(Path(project_root).resolve() / "inputs"))
            opts = " ".join([*("-D" + d for d in defines),
                             *("-I" + json.dumps(d) for d in include_dirs)])
            staged = _stage_sources(paths, Path(td), project_root)
            commands = [f"read_verilog -sv -nosynthesis {opts} " + " ".join(json.dumps(str(p)) for p in staged)]
            for key, value in sorted(parameters.items()):
                commands.append(f"chparam -set {key} {value} {top_module}")
            commands += [f"hierarchy -check -top {top_module}", "proc", f"write_json {json.dumps(str(output))}"]
            script.write_text("\n".join(commands) + "\n")
            result = subprocess.run([yosys, "-Q", "-T", "-s", str(script)],
                                    capture_output=True, text=True, timeout=60,
                                    cwd=str(project_root or paths[0].parent))
            if result.returncode:
                return HierarchyFailure("Integration postcondition failed: elaboration rejected candidate: "
                                        + (result.stderr or result.stdout)[-2000:])
            return _reachable_hierarchy(json.loads(output.read_text()), top_module)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return HierarchyFailure(f"Hierarchy elaboration unavailable: {exc}", "infrastructure_error")
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return HierarchyFailure(f"Invalid elaborator evidence: {exc}", "infrastructure_error")


def missing_blocks(instantiated: set[str], expected, top_module: str) -> HierarchyFailure | None:
    # The assembler's explicit top/block collision rename is part of its file contract.
    missing = sorted(name for name in expected if name not in instantiated
                     and not (name == top_module and name + "_pads" in instantiated))
    if missing:
        return HierarchyFailure(f"Integration postcondition failed: top {top_module!r} does NOT "
                                f"instantiate expected block(s): {missing}")
    return None
