"""Review round 3 counterexamples, now regressions (WP-52 .. WP-58)."""
from __future__ import annotations

import inspect
import json
import shutil

import pytest

from orchestrator.harness import task_adapter as ta
from orchestrator.harness import top_module as tm
from orchestrator.langchain.agents.integration_lead import assert_blocks_instantiated
from orchestrator.langchain.agents.rtl_generator import _is_qspi_frontend_block
from orchestrator.langgraph import pipeline_graph as pg
from orchestrator.langgraph.integration_helpers import (
    detect_wrapper_block,
    generate_caravel_wrapper_top,
    load_interface_contract_edges,
    parse_verilog_ports,
)
from orchestrator.tests.candidate_fixtures import adopt

TOP = "module chip_top(input wire clk, output wire y);\n  assign y = clk;\nendmodule\n"


def _proj(tmp_path, adapter_src=None):
    root = tmp_path / "p"
    (root / "inputs").mkdir(parents=True)
    (root / "rtl").mkdir()
    top = root / "rtl" / "top.v"
    top.write_text(TOP)
    if adapter_src is not None:
        (root / "inputs" / "task_adapter.py").write_text(adapter_src)
    adopt(root, top)
    return root, top


def _run(root, top, monkeypatch):
    monkeypatch.delenv("CORESMITH_TASK_ADAPTER", raising=False)
    monkeypatch.setenv("CORESMITH_ADAPTER_SANDBOX", "none")
    return ta.run_task_adapter(str(root), str(top), {})


# ---- WP-52 adapter ------------------------------------------------------------
@pytest.mark.parametrize("value", ['"false"', "0", "None", "{}"])
def test_non_boolean_budget_never_passes(tmp_path, monkeypatch, value):
    root, top = _proj(tmp_path, 'CASES=["one"]\ndef grade(c,w):\n return {"cases":{"one":{"ok":True}},'
                                '"budgets":{"speed":{"ok":' + value + '}}}\n')
    res = _run(root, top, monkeypatch)
    assert res["passed"] is False and res["oracle_incomplete"] and "boolean ok" in res["reason"]


def test_exit_code_after_receipt_is_incomplete(tmp_path, monkeypatch):
    root, top = _proj(tmp_path, 'import atexit, os\natexit.register(lambda: os._exit(9))\nCASES=["one"]\n'
                                'def grade(c,w):\n return {"cases":{"one":{"ok":True}}}\n')
    res = _run(root, top, monkeypatch)
    assert res["passed"] is False and res["oracle_incomplete"] and "rc=9" in res["reason"]


def test_malformed_case_row_is_incomplete(tmp_path, monkeypatch):
    root, top = _proj(tmp_path, 'CASES=["one"]\ndef grade(c,w):\n return {"cases":{"one":"broken"}}\n')
    res = _run(root, top, monkeypatch)
    assert res["passed"] is False and res["oracle_incomplete"] and "non-boolean" in res["reason"]


def test_declared_missing_adapter_is_a_defect(tmp_path, monkeypatch):
    root, top = _proj(tmp_path)
    monkeypatch.setenv("CORESMITH_TASK_ADAPTER", str(tmp_path / "nowhere.py"))
    res = ta.run_task_adapter(str(root), str(top), {})
    assert res is not None and res["kind"] == "adapter_defect" and "not found" in res["reason"]


def test_gate_exception_parks_never_skips():
    src = inspect.getsource(pg.validation_dv_node)
    assert "gate error (skipped)" not in src
    assert '"reason": f"acceptance gate raised: {_exc!r}"' in src


# ---- WP-53 loader -------------------------------------------------------------
def _edge(eid, pb, pp, cb, cp, fields):
    return {"edge_id": eid, "producer_block": pb, "producer_port": pp, "consumer_block": cb,
            "consumer_port": cp, "handshake_protocol": "valid_only",
            "fields": [{"name": f} for f in fields], "sideband_signals": [],
            "flow_control_policy": {"semantics": "free_running"}}


def test_loader_keeps_every_declared_key(tmp_path):
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / ".coresmith" / "interface_contracts.json").write_text(json.dumps(
        {"contracts": [_edge("e2", "modem", "stage_start", "hdlc", "start", ["stage_start_length_bytes/start_length_bytes"])]}))
    e = load_interface_contract_edges(str(tmp_path))[0]
    assert e["handshake_protocol"] == "valid_only" and e["flow_control_policy"]["semantics"] == "free_running"


def test_loaded_contract_wires_the_valid_strobe(tmp_path):
    files = {
        "user_project_wrapper": "module user_project_wrapper(input wire wb_clk_i, input wire wb_rst_i, input wire [37:0] io_in, output wire [37:0] io_out, output wire [37:0] io_oeb);\n assign io_out=0; assign io_oeb={38{1'b1}};\nendmodule\n",
        "regmap": "module regmap(input wire wb_clk_i, input wire wb_rst_i, output wire start_valid, output wire [6:0] start_length_bytes);\n assign start_valid=1; assign start_length_bytes=3;\nendmodule\n",
        "modem": "module modem(input wire wb_clk_i, input wire wb_rst_i, input wire start_valid, input wire [6:0] start_length_bytes, output wire stage_start_valid, output wire [6:0] stage_start_length_bytes);\n assign stage_start_valid=start_valid; assign stage_start_length_bytes=start_length_bytes;\nendmodule\n",
        "hdlc": "module hdlc(input wire wb_clk_i, input wire wb_rst_i, input wire start_valid, input wire [6:0] start_length_bytes);\nendmodule\n",
    }
    paths = {}
    for n, src in files.items():
        p = tmp_path / f"{n}.v"
        p.write_text(src)
        paths[n] = str(p)
    modules = {n: parse_verilog_ports(p, n) for n, p in paths.items()}
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / ".coresmith" / "interface_contracts.json").write_text(json.dumps({"contracts": [
        _edge("e1", "regmap", "start", "modem", "start", ["length_bytes"]),
        _edge("e2", "modem", "stage_start", "hdlc", "start", ["stage_start_length_bytes/start_length_bytes"])]}))
    edges = load_interface_contract_edges(str(tmp_path))
    asm = generate_caravel_wrapper_top(modules, edges, paths, str(tmp_path / "out"), "user_project_wrapper", None)
    v = asm["verilog"]
    assert asm.get("wiring_errors") in (None, [])
    body = v[v.index("u_hdlc ("):]
    assert ".start_valid(w_modem_stage_start_valid)" in body[:body.index(");")]
    assert "1'b0" not in body[:body.index(");")]


def test_strobe_only_edge_binds_by_contract():
    from orchestrator.langgraph.integration_helpers import _contract_signal_names
    assert _contract_signal_names(_edge("e3", "a", "go", "b", "go", [])) == ["valid"]
    assert _contract_signal_names({"edge_id": "legacy", "producer_block": "a", "consumer_block": "b",
                                   "producer_port": "", "consumer_port": "", "fields": [], "sideband_signals": []}) == []


# ---- WP-54 top invariants ------------------------------------------------------
def test_stale_receipt_is_not_resolved(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_TOP_MODULE", raising=False)
    root, top = _proj(tmp_path)
    leaf = root / "rtl" / "leaf.v"
    leaf.write_text("module leaf(); endmodule\n")
    adopt(root, top, {"leaf": str(leaf)})
    assert tm.resolve_top(root) == ("chip_top", str(top.resolve()))
    leaf.write_text("module leaf(); wire changed; endmodule\n")
    with pytest.raises(tm.CandidateError):
        tm.resolve_top(root)


def test_receipt_refuses_a_declared_top_mismatch(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_TOP_MODULE", raising=False)
    root, top = _proj(tmp_path)
    (root / "inputs" / "task.yaml").write_text("top: locked_other\n")
    with pytest.raises(ValueError):
        tm.write_candidate_receipt(root, "chip_top", str(top), {})


def test_adapter_refuses_a_candidate_that_is_not_the_recorded_one(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_TOP_MODULE", raising=False)
    root, top = _proj(tmp_path, 'CASES=["one"]\ndef grade(c,w):\n return {"cases":{"one":{"ok":True}}}\n')
    other = root / "rtl" / "other.v"
    other.write_text("module other(); endmodule\n")
    tm.write_candidate_receipt(root, "other", str(other), {})
    res = _run(root, top, monkeypatch)
    assert res["oracle_incomplete"] and "not the recorded candidate" in res["reason"]


def test_synth_probe_takes_the_recorded_top_and_never_a_chassis_name(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_TOP_MODULE", raising=False)
    txt = "module chip_top(); endmodule\nmodule user_project_wrapper(); endmodule\n"
    with pytest.raises(tm.CandidateError):
        pg._resolve_probe_top("chip_top", txt)
    with pytest.raises(tm.CandidateError):
        pg._resolve_probe_top("nothing", "module a(); endmodule\nmodule b(); endmodule\n")
    root, top = _proj(tmp_path)
    top.write_text(txt)
    tm.write_candidate_receipt(root, "chip_top", str(top), {})
    assert pg._resolve_probe_top("user_project_wrapper", txt, project_root=str(root)) == "chip_top"


@pytest.mark.skipif(not shutil.which("yosys"), reason="requires yosys")
def test_hierarchy_starts_at_the_selected_top_and_sees_the_preprocessor():
    orphan = "module chip_top(); endmodule\nmodule orphan(); required_leaf u(); endmodule\n"
    assert assert_blocks_instantiated(orphan, {"required_leaf"}, sources=["module required_leaf(); endmodule\n"],
                                      top_module="chip_top")
    disabled = "module chip_top();\n`ifdef NEVER\nrequired_leaf u();\n`endif\nendmodule\n"
    assert assert_blocks_instantiated(disabled, {"required_leaf"}, sources=["module required_leaf(); endmodule\n"],
                                      top_module="chip_top")
    good = "module chip_top(); required_leaf u(); endmodule\n"
    assert assert_blocks_instantiated(good, {"required_leaf"}, sources=["module required_leaf(); endmodule\n"],
                                      top_module="chip_top") is None
    src = inspect.getsource(assert_blocks_instantiated)
    assert "reference_codec_openframe_pad_adapter" not in src


# ---- WP-55 generality -----------------------------------------------------------
def test_qspi_skill_needs_a_declared_bus_and_an_edge_to_the_top(tmp_path, monkeypatch):
    monkeypatch.delenv("CORESMITH_BUS", raising=False)
    assert _is_qspi_frontend_block("cpu_frontend", "instruction fetch") is False
    root, _ = _proj(tmp_path)
    assert _is_qspi_frontend_block("qspi_regmap_frontend", "", str(root)) is False   # nothing declared
    (root / "inputs" / "task.yaml").write_text("top: user_project_wrapper\ninterface:\n  bus: qspi_slave\n")
    (root / ".coresmith").mkdir(exist_ok=True)
    (root / ".coresmith" / "interface_contracts.json").write_text(json.dumps({"contracts": [
        {"edge_id": "e", "producer_block": "user_project_wrapper", "producer_port": "pads",
         "consumer_block": "bus_frontend", "consumer_port": "pads", "fields": [{"name": "csn"}]}]}))
    assert _is_qspi_frontend_block("bus_frontend", "", str(root)) is True
    assert _is_qspi_frontend_block("cpu_frontend", "instruction fetch", str(root)) is False


def test_chassis_none_means_none(tmp_path, monkeypatch):
    from orchestrator.chassis.profile import locked_boundary_ports
    from orchestrator.langgraph.bfm_lib.classifier import classify_bus_verdict
    monkeypatch.setenv("CORESMITH_CHASSIS", "none")
    root, top = _proj(tmp_path)
    assert locked_boundary_ports(root) == ()
    v = classify_bus_verdict(str(root), "module chip(input csn, input sck, inout [3:0] dq); endmodule\n",
                             [{"protocol": "qspi", "type": "qspi", "name": "qspi"}], "chip", str(top))
    assert v.status == "no_chassis" and not v.contract_enforcing
    assert detect_wrapper_block({"x": parse_verilog_ports(str(top), "chip_top")}) is None


def test_generated_prompt_text_is_general():
    from orchestrator.architecture import constraints as cs
    from orchestrator.langchain.agents import testbench_generator as tg
    for mod in (tg, cs):
        src = open(mod.__file__, encoding="utf-8").read()
        for tok in ("RATE-DISTORTION", "macroblock", "idct4x4", "PSNR", "video_codec", "v7/v8", "v9 "):
            assert tok not in src, (mod.__name__, tok)
    src = open(pg.__file__, encoding="utf-8").read()
    assert "mission-scale stream run" not in src and "validation testbenches all passed" not in src


# ---- WP-56 sandbox on resume / WP-58 adoption --------------------------------------
def test_adoption_and_strict_review_defaults():
    src = inspect.getsource(pg._plan_targeted_revise)
    assert "adopt_failed" in src and "name not in adopt_failed" in src
    src2 = open(pg.__file__, encoding="utf-8").read()
    assert "_adopt_reviewed_specs(pr, edited_blocks, reviewed_specs)" in src2
    from orchestrator.state_store.spec_adoption import adopt_reviewed_specs
    assert "os.replace" in inspect.getsource(adopt_reviewed_specs)
    from orchestrator.langgraph import pipeline_helpers as ph
    assert ".md.rejected-" in inspect.getsource(ph) and "quarantine" in inspect.getsource(ph).lower()


@pytest.fixture(autouse=True)
def _fixture_elaborator(monkeypatch, request):
    if request.node.name == "test_hierarchy_starts_at_the_selected_top_and_sees_the_preprocessor":
        return
    monkeypatch.setattr("orchestrator.harness.hierarchy.elaborate_hierarchy", lambda *a, **k: {"leaf", "syntax_adapter", "rom_arbiter"})
