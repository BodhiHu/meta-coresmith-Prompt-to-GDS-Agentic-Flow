# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""A chassis is the fixed chip boundary a task locks: its top module name, the
pad ports the task mandates, the clock and reset names. The engine reads it
from the TASK'S declaration and never infers it from port names (WP-51).

Declaration, first hit wins:
  * ``CORESMITH_CHASSIS=<name>`` (``none`` disables),
  * ``inputs/task.yaml``: ``chassis: <name>`` or a ``top:`` equal to a known
    chassis top module.
Built-in profiles are the chassis plugins the engine ships (today: Caravel /
OpenFrame MPW harness). A task without a declaration has no locked boundary.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Profile:
    name: str
    top_module: str
    locked_ports: tuple[str, ...]
    clk: str
    rst: str
    rst_active_high: bool


CARAVEL = Profile("caravel", "user_project_wrapper", ("io_in", "io_out", "io_oeb"),
                  "wb_clk_i", "wb_rst_i", True)

BUILTIN: dict[str, Profile] = {"caravel": CARAVEL, "accel": CARAVEL, "openframe": CARAVEL}


def _task_yaml_fields(project_root) -> dict:
    out: dict = {}
    ty = Path(project_root) / "inputs" / "task.yaml"
    if not ty.exists():
        return out
    try:
        for line in ty.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\s*(chassis|top)\s*:\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)", line)
            if m and m.group(1) not in out:
                out[m.group(1)] = m.group(2)
    except OSError:
        pass
    return out


def resolve_profile(project_root) -> Profile | None:
    """The task's declared chassis, or None when it declares none."""
    env = (os.environ.get("CORESMITH_CHASSIS", "") or "").strip().lower()
    if env:
        return None if env in ("none", "0", "off") else BUILTIN.get(env)
    fields = _task_yaml_fields(project_root)
    if fields.get("chassis") and fields["chassis"].lower() in BUILTIN:
        return BUILTIN[fields["chassis"].lower()]
    top = fields.get("top")
    if top:
        for p in BUILTIN.values():
            if p.top_module == top:
                return p
    return None


def locked_boundary_ports(project_root) -> tuple[str, ...]:
    """Pad ports the task's chassis mandates (exempt from 'undeclared' reports).
    With no declaration, every built-in chassis's pad ports are exempt -- a
    design that does not use them is unaffected."""
    p = resolve_profile(project_root)
    if p is not None:
        return p.locked_ports
    if (os.environ.get("CORESMITH_CHASSIS", "") or "").strip().lower() in ("none", "0", "off"):
        return ()            # WP-55: an explicit none means none
    seen: list[str] = []
    for prof in BUILTIN.values():
        for port in prof.locked_ports:
            if port not in seen:
                seen.append(port)
    return tuple(seen)


def chassis_top(project_root) -> str:
    """The chassis top module the task declares, or ""."""
    p = resolve_profile(project_root)
    return p.top_module if p else ""
