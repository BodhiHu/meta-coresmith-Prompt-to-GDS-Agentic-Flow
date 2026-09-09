# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""A chassis is the fixed chip boundary a task locks: its top module name, the
pad ports the task mandates, the clock and reset names. The engine reads it
from the TASK'S declaration and never infers it from port names (WP-51).

Declaration, first hit wins:
  * ``inputs/task.yaml: chassis: <name>``,
  * ``CORESMITH_CHASSIS=<name>``.
``none``, ``off`` and ``0`` disable the chassis. An absent declaration selects none.
Built-in profiles are the chassis plugins the engine ships (today: Caravel /
OpenFrame MPW harness). A task without a declaration has no locked boundary.
"""
from __future__ import annotations

import logging
import os
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


def declared_chassis(project_root) -> Profile | None:
    """The canonical task-first chassis declaration. A top name is not consent."""
    import yaml
    path = Path(project_root) / "inputs/task.yaml"
    fields = yaml.safe_load(path.read_text()) or {} if path.exists() else {}
    if not isinstance(fields, dict):
        raise ValueError("task.yaml must be a mapping")
    value = fields["chassis"] if "chassis" in fields else os.environ.get("CORESMITH_CHASSIS")
    if value is None or str(value).strip() == "":
        logging.getLogger(__name__).info("No chassis declared; no profile selected")
        return None
    name = str(value).strip().lower()
    if name in ("none", "off", "0", "false"):
        return None
    if name not in BUILTIN:
        raise ValueError(f"Unknown declared chassis {name!r}")
    return BUILTIN[name]


def resolve_profile(project_root) -> Profile | None:
    """Compatibility name for the canonical declaration reader."""
    return declared_chassis(project_root)


def locked_boundary_ports(project_root) -> tuple[str, ...]:
    profile = declared_chassis(project_root)
    return profile.locked_ports if profile else ()


def chassis_top(project_root) -> str:
    profile = declared_chassis(project_root)
    return profile.top_module if profile else ""
