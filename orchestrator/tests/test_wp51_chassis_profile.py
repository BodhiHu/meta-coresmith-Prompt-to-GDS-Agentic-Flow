"""WP-51: the chassis is declared by the task, never inferred from port names."""
from __future__ import annotations

from types import SimpleNamespace

from orchestrator.chassis.profile import (
    CARAVEL,
    chassis_top,
    locked_boundary_ports,
    resolve_profile,
)
from orchestrator.langgraph.integration_helpers import detect_wrapper_block


def test_profile_from_task_yaml_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_CHASSIS", raising=False)
    assert resolve_profile(tmp_path) is None and chassis_top(tmp_path) == ""
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "task.yaml").write_text("top: user_project_wrapper\nclock: wb_clk_i\n")
    assert resolve_profile(tmp_path) == CARAVEL and chassis_top(tmp_path) == "user_project_wrapper"
    (tmp_path / "inputs" / "task.yaml").write_text("chassis: accel\ntop: something_else\n")
    assert resolve_profile(tmp_path) == CARAVEL
    monkeypatch.setenv("CORESMITH_CHASSIS", "none")
    assert resolve_profile(tmp_path) is None
    assert locked_boundary_ports(tmp_path) == ()           # WP-55: none means none
    monkeypatch.delenv("CORESMITH_CHASSIS", raising=False)
    (tmp_path / "inputs" / "task.yaml").write_text("title: plain\n")
    assert locked_boundary_ports(tmp_path) == ("io_in", "io_out", "io_oeb")   # undeclared: built-in union


def test_wrapper_block_is_matched_by_declared_name_only():
    def m(name, ports):
        return SimpleNamespace(name=name, ports=[SimpleNamespace(name=p) for p in ports])

    mods = {"padsy": m("padsy", ["io_in", "io_out", "io_oeb", "wb_clk_i"]), "core": m("core", ["a"])}
    assert detect_wrapper_block(mods) is None                       # pad-looking ports are not a name
    assert detect_wrapper_block(mods, "padsy") == "padsy"
    mods2 = {"blk": m("user_project_wrapper", ["x"])}
    assert detect_wrapper_block(mods2) is None                     # no declared name, no wrapper (WP-55)
    assert detect_wrapper_block(mods2, "user_project_wrapper") == "blk"   # module name matches the declared top
