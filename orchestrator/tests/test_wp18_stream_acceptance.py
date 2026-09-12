"""WP-18: Acceptance DV drives the published stream_core contract."""
from __future__ import annotations

import shutil
import struct
from pathlib import Path

import pytest

from orchestrator.langgraph import acceptance_dv as ad

STREAM_PORTS = """
module dut(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        cfg_valid,
    input  wire [7:0]  cfg_addr,
    input  wire [31:0] cfg_data,
    input  wire        start,
    input  wire        in_valid,
    output wire        in_ready,
    input  wire [31:0] in_data,
    input  wire        in_last,
    output wire        out_valid,
    input  wire        out_ready,
    output wire [31:0] out_data,
    output wire        out_last,
    output wire        busy,
    output wire        done
);
"""


def _contract():
    return ad.classify_contract(ad.discover_ports(STREAM_PORTS))


def test_stream_core_shape_classified():
    c = _contract()
    assert c and c["kind"] == "stream_core" and c["rst_active_low"]
    assert c["in_width"] == 32 and c["out_width"] == 32 and c["has_done"]


def test_axis_shape_still_classified_as_axis():
    src = ("module m(input clk, input rst_n, input s_tvalid, output s_tready, "
           "input [7:0] s_tdata, output m_tvalid, input m_tready, output [7:0] m_tdata);")
    c = ad.classify_contract(ad.discover_ports(src))
    assert c and c["kind"] == "axis"


def test_map_stimulus_explicit_cfg_dict():
    m = ad.map_stimulus({"data": b"\x01\x02", "cfg": {0: 6, 1: 64, 2: 48, 3: 28},
                         "cycle_cap": 1000, "word_bytes": 1}, _contract())
    assert m["payload"] == [1, 2] and m["cfg"] == [(0, 6), (1, 64), (2, 48), (3, 28)]
    assert m["cycle_cap"] == 1000 and m["in_word_bytes"] == 1 and m["out_word_bytes"] == 1
    assert (m["max_out_words"], m["seed"], m["gap_pct"], m["bp_pct"]) == (0, 0, 10, 15)


def test_map_stimulus_never_infers_cfg():
    """WP-38: the n_frames/width/height/qp convention is the task's, not the engine's."""
    assert ad.map_stimulus({"data": [9], "n_frames": 2, "width": 64, "height": 48, "qp": 28,
                            "word_bytes": 1}, _contract()) is None
    assert "no explicit 'cfg'" in ad.stream_case_problem({"data": [9], "n_frames": 2})


def test_map_stimulus_requires_word_packing():
    assert ad.map_stimulus({"data": [9], "cfg": {0: 1}}, _contract()) is None
    assert "word_bytes" in ad.stream_case_problem({"data": [9], "cfg": {0: 1}})


def test_map_stimulus_without_cfg_is_unmappable():
    assert ad.map_stimulus({"data": [1, 2, 3], "n_frames": 1}, _contract()) is None


def test_stream_harness_names_ports_and_protocol():
    src = ad.generate_harness(_contract(), "dut", [])
    for tok in ("cfg_valid", "cfg_addr", "cfg_data", "start", "in_valid", "in_ready",
                "in_data", "in_last", "out_valid", "out_ready", "out_data", "out_last",
                "Vdut", "rst_n"):
        assert tok in src


def test_pack_cases_stream_layout(tmp_path):
    p = tmp_path / "in.bin"
    ad._pack_cases([("c", {"payload": [7, 8], "cfg": [(0, 1), (3, 28)], "cycle_cap": 99,
                           "sidebands": {}, "unmapped": []})], [], p)
    b = p.read_bytes()
    assert struct.unpack("<I", b[:4])[0] == 2
    assert struct.unpack("<II", b[4:12]) == (0, 1)
    assert struct.unpack("<II", b[12:20]) == (3, 28)
    assert struct.unpack("<I", b[20:24])[0] == 99
    assert struct.unpack("<IIIIII", b[24:48]) == (0, 0, 10, 15, 1, 1)   # WP-38 schedule defaults
    assert struct.unpack("<I", b[48:52])[0] == 2 and b[52:] == b"\x07\x08"


# ---- E2E with a real verilator build: a byte echo with configurable offset --
ECHO_RTL = """
module echo_top(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        cfg_valid,
    input  wire [7:0]  cfg_addr,
    input  wire [31:0] cfg_data,
    input  wire        start,
    input  wire        in_valid,
    output wire        in_ready,
    input  wire [31:0] in_data,
    input  wire        in_last,
    output wire        out_valid,
    input  wire        out_ready,
    output wire [31:0] out_data,
    output wire        out_last,
    output wire        busy,
    output wire        done
);
    reg [31:0] offset_q;
    reg        full_q, last_q, busy_q;
    reg [7:0]  byte_q;
    assign in_ready  = !full_q || out_ready;
    assign out_valid = full_q;
    assign out_data  = {24'd0, byte_q + offset_q[7:0]};
    assign out_last  = full_q && last_q;
    assign busy = busy_q;
    assign done = full_q && last_q && out_ready;
    always @(posedge clk) begin
        if (!rst_n) begin
            offset_q <= 0; full_q <= 0; last_q <= 0; busy_q <= 0; byte_q <= 0;
        end else begin
            if (cfg_valid && cfg_addr == 8'd5) offset_q <= cfg_data;
            if (start) busy_q <= 1;
            if (full_q && out_ready) begin
                full_q <= 0;
                if (last_q) busy_q <= 0;
            end
            if (in_valid && in_ready) begin
                full_q <= 1; byte_q <= in_data[7:0]; last_q <= in_last;
            end
        end
    end
endmodule
"""

needs_verilator = pytest.mark.skipif(not shutil.which("verilator"), reason="verilator not on PATH")


def _project(tmp_path, offset: int, cfg_offset: int):
    root = tmp_path / "proj"
    (root / "inputs").mkdir(parents=True)
    (root / "rtl").mkdir()
    top = root / "rtl" / "echo_top.v"
    top.write_text(ECHO_RTL)
    (root / "inputs" / "acceptance_stimulus.py").write_text(
        "cases = [('a', {'data': bytes(range(1, 40)), 'cfg': {5: %d}, 'cycle_cap': 20000, 'word_bytes': 1})]\n" % cfg_offset)
    (root / "inputs" / "golden.py").write_text(
        "def run(stim):\n    return bytes((b + %d) & 0xFF for b in stim['data'])\n" % offset)
    (root / "inputs" / "accept.py").write_text(
        "def accept(expected, observed):\n    return bytes(expected) == bytes(observed)\n")
    return root, top


@needs_verilator
def test_stream_core_echo_passes_with_predicate(tmp_path, monkeypatch):
    root, top = _project(tmp_path, 3, 3)
    monkeypatch.setenv("CORESMITH_SOURCE_ROOT", str(root / "inputs" / "golden.py"))
    monkeypatch.setenv("CORESMITH_REFERENCE_ENTRY", "run")
    monkeypatch.setenv("CORESMITH_FUNCTIONAL_ACCEPTANCE", str(root / "inputs" / "accept.py"))
    monkeypatch.delenv("CORESMITH_ACCEPTANCE_STIMULUS", raising=False)
    res = ad.run_acceptance_dv(str(root), str(top), [])
    assert res["passed"], res
    assert res["cases"][0]["criterion"] == "acceptance_predicate"
    assert (Path(res["captured_dir"]) / "a.out.bin").read_bytes() == bytes((b + 3) & 0xFF for b in range(1, 40))
    assert res["candidate_sha"] and res["requested_cases"] == res["completed_cases"] == 1
    assert (Path(res["captured_dir"]) / "receipt.json").exists()


@needs_verilator
def test_stream_core_wrong_cfg_fails_predicate(tmp_path, monkeypatch):
    root, top = _project(tmp_path, 3, 4)   # RTL configured with offset 4, golden expects 3
    monkeypatch.setenv("CORESMITH_SOURCE_ROOT", str(root / "inputs" / "golden.py"))
    monkeypatch.setenv("CORESMITH_REFERENCE_ENTRY", "run")
    monkeypatch.setenv("CORESMITH_FUNCTIONAL_ACCEPTANCE", str(root / "inputs" / "accept.py"))
    monkeypatch.delenv("CORESMITH_ACCEPTANCE_STIMULUS", raising=False)
    res = ad.run_acceptance_dv(str(root), str(top), [])
    assert not res["passed"] and not res["skipped"], res
    assert res["violations"][0]["criterion"] == "acceptance_dv_predicate"
