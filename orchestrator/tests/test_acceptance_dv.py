# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""RTL Acceptance DV (dv-hardening-16): native RTL-vs-golden at mission scale.

E2E uses a REAL verilator build of a tiny framed DUT (byte+offset echo with
tuser/tlast + one sideband) -- skipped when verilator is absent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from orchestrator.langgraph.acceptance_dv import (
    classify_contract,
    discover_ports,
    generate_harness,
    map_stimulus,
    run_acceptance_dv,
)
from orchestrator.tests.candidate_fixtures import adopt

FRAMED_HEADER = """
module toy_top (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] s_axis_tdata,
    input  wire s_axis_tvalid,
    output wire s_axis_tready,
    input  wire s_axis_tuser,
    input  wire s_axis_tlast,
    output wire [7:0] m_axis_tdata,
    output wire m_axis_tvalid,
    input  wire m_axis_tready,
    output wire m_axis_tlast,
    input  wire [7:0] cfg_offset
);
endmodule
"""


class TestContractDiscovery:
    def test_framed_shape_classified(self):
        c = classify_contract(discover_ports(FRAMED_HEADER))
        assert c is not None
        assert c["clk"] == "clk" and c["rst"] == "rst_n"
        assert c["rst_active_low"] is True
        assert c["s_axis"]["prefix"] == "s_axis"
        assert c["m_axis"]["prefix"] == "m_axis"
        assert c["s_axis"]["has_tuser"] and c["s_axis"]["has_tlast"]
        assert c["sidebands"] == {"cfg_offset": 8}

    def test_unframed_shape_rejected(self):
        assert classify_contract(discover_ports(
            "module x (input wire clk, input wire rst, input wire [7:0] d);"
            "endmodule")) is None


class TestStimulusMapping:
    _contract = classify_contract(discover_ports(FRAMED_HEADER))

    _mapping = {"payload": "pixels", "input_width": 8, "output_width": 8,
                "packing": "bytes", "byte_order": "little", "sidebands": {"cfg_offset": "offset"}}

    def test_declared_sideband_mapping(self):
        m = map_stimulus({"pixels": [1, 2, 3], "offset": 7}, self._contract, mapping=self._mapping)
        assert m["payload"] == [1, 2, 3]
        assert m["sidebands"] == {"cfg_offset": 7}
        assert m["unmapped"] == []

    def test_flat_list(self):
        assert map_stimulus([9, 8], self._contract) is None

    def test_unmappable(self):
        assert map_stimulus({"qp": 20}, self._contract) is None  # no payload


class TestHarnessGeneration:
    def test_generated_source_names_ports(self):
        c = classify_contract(discover_ports(FRAMED_HEADER))
        src = generate_harness(c, "toy_top", sorted(c["sidebands"]))
        for token in ("Vtoy_top", "s_axis_tvalid", "m_axis_tlast",
                      "cfg_offset = sb[0]", "rst_n = 0", "rst_n = 1"):
            assert token in src, token

    def test_input_gap_never_drops_a_pending_beat(self):
        # AXI-Stream: TVALID must hold until TREADY -- the gap branch is gated
        # on nothing being pending.
        c = classify_contract(discover_ports(FRAMED_HEADER))
        src = generate_harness(c, "toy_top", sorted(c["sidebands"]))
        assert "if (!s_pend && BP" in src
        assert "{ load(idx); s_pend = true; }" in src

    def test_no_tlast_completion_uses_idle_window(self):
        # Without an egress tlast the run ends after the output has been QUIET
        # for 64 cycles, not after 64 total cycles (which truncated capture on
        # the first beat accepted post-drain).
        c = classify_contract(discover_ports(
            FRAMED_HEADER.replace("output wire m_axis_tlast,\n", "")))
        src = generate_harness(c, "toy_top", sorted(c["sidebands"]))
        assert "s_done && wd > 64" not in src
        assert "++m_idle > 64" in src
        assert "m_idle = 0;" in src


# ---------------------------------------------------------------------------
# E2E: real verilator, tiny framed DUT
# ---------------------------------------------------------------------------

TOY_DUT = """
module toy_top (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] s_axis_tdata,
    input  wire s_axis_tvalid,
    output wire s_axis_tready,
    input  wire s_axis_tuser,
    input  wire s_axis_tlast,
    output reg  [7:0] m_axis_tdata,
    output reg  m_axis_tvalid,
    input  wire m_axis_tready,
    output reg  m_axis_tlast,
    input  wire [7:0] cfg_offset
);
    assign s_axis_tready = !m_axis_tvalid || m_axis_tready;
    always @(posedge clk) begin
        if (!rst_n) begin
            m_axis_tvalid <= 1'b0;
            m_axis_tlast <= 1'b0;
        end else begin
            if (m_axis_tvalid && m_axis_tready) m_axis_tvalid <= 1'b0;
            if (s_axis_tvalid && s_axis_tready) begin
                m_axis_tdata <= s_axis_tdata + cfg_offset {BUG};
                m_axis_tvalid <= 1'b1;
                m_axis_tlast <= s_axis_tlast;
            end
        end
    end
endmodule
"""

REFERENCE = '''\
def run(stim):
    off = stim.get("offset", 0)
    return bytes((v + off) & 0xFF for v in stim["pixels"])
'''

ACCEPTANCE = '''\
cases = [
    ("small", {"pixels": [1, 2, 3, 4], "offset": 5}),
    ("mission", {"pixels": list(range(200)), "offset": 9}),
]
'''


# A DUT that presents each output for exactly ONE cycle and clears m_axis_tvalid
# UNCONDITIONALLY on the next edge (not gated on m_axis_tready). Correct when the
# consumer is always ready; loses beats the moment the consumer backpressures --
# the exact handshake-drop class the evaluation harness run-through caught in CoreSmith's
# RTL that its own no-backpressure harness passed.
DROP_DUT = """
module toy_top (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] s_axis_tdata,
    input  wire s_axis_tvalid,
    output wire s_axis_tready,
    input  wire s_axis_tuser,
    input  wire s_axis_tlast,
    output reg  [7:0] m_axis_tdata,
    output reg  m_axis_tvalid,
    input  wire m_axis_tready,
    output reg  m_axis_tlast,
    input  wire [7:0] cfg_offset
);
    assign s_axis_tready = 1'b1;
    always @(posedge clk) begin
        if (!rst_n) begin
            m_axis_tvalid <= 1'b0;
            m_axis_tlast <= 1'b0;
        end else begin
            m_axis_tvalid <= 1'b0;   // BUG: clear regardless of acceptance
            if (s_axis_tvalid && s_axis_tready) begin
                m_axis_tdata <= s_axis_tdata + cfg_offset;
                m_axis_tvalid <= 1'b1;
                m_axis_tlast <= s_axis_tlast;
            end
        end
    end
endmodule
"""


# A 32-bit WORD-stream DUT (echo + offset). The old byte-granular harness drove
# only 8 bits of the 32-bit tdata and captured only the low byte -- it could not
# grade a word stream at all (CoreSmith's run-through limitation). dv-30
# width-aware driving packs 4 payload bytes/beat and captures 4 bytes/beat.
WORD_DUT = """
module toy_top (
    input  wire clk,
    input  wire rst_n,
    input  wire [31:0] s_axis_tdata,
    input  wire s_axis_tvalid,
    output wire s_axis_tready,
    input  wire s_axis_tuser,
    input  wire s_axis_tlast,
    output reg  [31:0] m_axis_tdata,
    output reg  m_axis_tvalid,
    input  wire m_axis_tready,
    output reg  m_axis_tlast,
    input  wire [7:0] cfg_offset
);
    assign s_axis_tready = !m_axis_tvalid || m_axis_tready;
    always @(posedge clk) begin
        if (!rst_n) begin
            m_axis_tvalid <= 1'b0;
            m_axis_tlast <= 1'b0;
        end else begin
            if (m_axis_tvalid && m_axis_tready) m_axis_tvalid <= 1'b0;
            if (s_axis_tvalid && s_axis_tready) begin
                m_axis_tdata <= s_axis_tdata + cfg_offset;
                m_axis_tvalid <= 1'b1;
                m_axis_tlast <= s_axis_tlast;
            end
        end
    end
endmodule
"""

WORD_REFERENCE = '''\
def run(stim):
    off = stim.get("offset", 0)
    px = stim["pixels"]   # little-endian u32 words, 4 bytes each
    out = bytearray()
    for i in range(0, len(px), 4):
        w = px[i] | (px[i+1] << 8) | (px[i+2] << 16) | (px[i+3] << 24)
        r = (w + off) & 0xFFFFFFFF
        out += bytes([r & 0xff, (r >> 8) & 0xff, (r >> 16) & 0xff, (r >> 24) & 0xff])
    return bytes(out)
'''

WORD_ACCEPTANCE = '''\
import random
_r = random.Random(3)
def _words(nw):
    b = []
    for _ in range(nw):
        w = _r.randrange(0, 1 << 32)
        b += [w & 0xff, (w >> 8) & 0xff, (w >> 16) & 0xff, (w >> 24) & 0xff]
    return b
cases = [
    ("small", {"pixels": _words(4), "offset": 7}),
    ("mission", {"pixels": _words(80), "offset": 100}),
]
'''


# A framed DUT with NO egress tlast: the run ends on the quiet-window rule
# (sender done + output idle) rather than on an egress tlast.
NO_TLAST_DUT = """
module toy_top (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] s_axis_tdata,
    input  wire s_axis_tvalid,
    output wire s_axis_tready,
    input  wire s_axis_tuser,
    input  wire s_axis_tlast,
    output reg  [7:0] m_axis_tdata,
    output reg  m_axis_tvalid,
    input  wire m_axis_tready,
    input  wire [7:0] cfg_offset
);
    assign s_axis_tready = !m_axis_tvalid || m_axis_tready;
    always @(posedge clk) begin
        if (!rst_n) begin
            m_axis_tvalid <= 1'b0;
        end else begin
            if (m_axis_tvalid && m_axis_tready) m_axis_tvalid <= 1'b0;
            if (s_axis_tvalid && s_axis_tready) begin
                m_axis_tdata <= s_axis_tdata + cfg_offset;
                m_axis_tvalid <= 1'b1;
            end
        end
    end
endmodule
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name == "acceptance_stimulus.py":
        width = 32 if text == WORD_ACCEPTANCE else 8
        mapping = {"payload": "pixels", "input_width": width, "output_width": width,
                   "packing": "bytes", "byte_order": "little", "sidebands": {"cfg_offset": "offset"}}
        text += f"\nAXIS_MAPPING = {mapping!r}\n"
    path.write_text(text, encoding="utf-8")


def _project(tmp_path: Path, bug: str = "") -> tuple[Path, Path]:
    root = tmp_path
    top = root / "rtl" / "toy_top.v"
    _write(top, TOY_DUT.replace("{BUG}", bug))
    _write(root / "inputs" / "toy_golden.py", REFERENCE)
    _write(root / "inputs" / "acceptance_stimulus.py", ACCEPTANCE)
    return root, top


def _env(monkeypatch):
    for var in ("CORESMITH_REFERENCE_ENTRY", "CORESMITH_ACCEPTANCE_STIMULUS",
                "CORESMITH_FIDELITY_GATE", "CORESMITH_FIDELITY_METRIC",
                "CORESMITH_ACCEPTANCE_DV"):
        monkeypatch.delenv(var, raising=False)


needs_verilator = pytest.mark.skipif(
    not shutil.which("verilator"), reason="verilator not on PATH")


@needs_verilator
class TestAcceptanceDVEndToEnd:
    def test_correct_dut_passes(self, tmp_path, monkeypatch):
        _env(monkeypatch)
        root, top = _project(tmp_path)
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert not res["skipped"], res["reason"]
        assert res["passed"], res
        assert len(res["cases"]) == 2
        assert all(r["ok"] for r in res["cases"])
        assert (root / ".coresmith" / "acceptance_dv.json").exists()

    def test_wrong_dut_diverges_with_offset(self, tmp_path, monkeypatch):
        _env(monkeypatch)
        root, top = _project(tmp_path, bug="+ 8'd1")
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert not res["skipped"], res["reason"]
        assert not res["passed"]
        v = res["violations"][0]
        assert v["criterion"] == "acceptance_dv_divergence"
        assert v["first_divergence_offset"] == 0

    def test_no_artifact_honest_skip(self, tmp_path, monkeypatch):
        _env(monkeypatch)
        root, top = _project(tmp_path)
        (root / "inputs" / "acceptance_stimulus.py").unlink()
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert res["skipped"]
        assert "acceptance stimulus" in res["reason"]

    def test_ungradable_golden_is_a_skip_not_a_pass(self, tmp_path, monkeypatch):
        # The charter is HONEST SKIPS, NEVER A FALSE PASS: when the reference
        # raises on every case the rows are unjudged, so the gate must report
        # a skip instead of a vacuous PASS.
        _env(monkeypatch)
        root, top = _project(tmp_path)
        _write(root / "inputs" / "toy_golden.py",
               'def run(stim):\n    raise ValueError("boom")\n')
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert res["skipped"], res
        assert not res["passed"]
        assert "not gradable" in res["reason"]

    def test_numpy_golden_is_byte_compared(self, tmp_path, monkeypatch):
        # An ndarray golden used to compare against 0 bytes -> false divergence.
        _env(monkeypatch)
        root, top = _project(tmp_path)
        _write(root / "inputs" / "toy_golden.py",
               'import numpy as np\n\n\n'
               'def run(stim):\n'
               '    off = stim.get("offset", 0)\n'
               '    return np.asarray([(v + off) & 0xFF for v in stim["pixels"]],\n'
               '                      dtype=np.uint8)\n')
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert not res["skipped"], res["reason"]
        assert res["passed"], res

    def test_no_tlast_top_runs_to_completion(self, tmp_path, monkeypatch):
        # A top without an egress tlast completes on the idle window and
        # captures every beat (the old rule ended capture on a beat, not on
        # quiet, and could truncate the tail).
        _env(monkeypatch)
        root = tmp_path
        top = root / "rtl" / "toy_top.v"
        _write(top, NO_TLAST_DUT)
        _write(root / "inputs" / "toy_golden.py", REFERENCE)
        _write(root / "inputs" / "acceptance_stimulus.py", ACCEPTANCE)
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert not res["skipped"], res["reason"]
        assert res["passed"], res
        assert [r["rtl_bytes"] for r in res["cases"]] == [4, 200]

    def test_kill_switch(self, tmp_path, monkeypatch):
        _env(monkeypatch)
        monkeypatch.setenv("CORESMITH_ACCEPTANCE_DV", "0")
        root, top = _project(tmp_path)
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert res["skipped"]

    def test_backpressure_catches_dropped_beat(self, tmp_path, monkeypatch):
        # dv-hardening-27: the drop-on-transfer-edge DUT must FAIL when the
        # harness randomizes output backpressure (the default).
        _env(monkeypatch)
        root = tmp_path
        top = root / "rtl" / "toy_top.v"
        _write(top, DROP_DUT)
        _write(root / "inputs" / "toy_golden.py", REFERENCE)
        _write(root / "inputs" / "acceptance_stimulus.py", ACCEPTANCE)
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert not res["skipped"], res["reason"]
        assert not res["passed"], "backpressure must expose the dropped beat"

    def test_no_backpressure_misses_dropped_beat(self, tmp_path, monkeypatch):
        # The SAME buggy DUT PASSES with backpressure disabled -- documenting
        # exactly the blind spot that shipped the CoreSmith handshake bug.
        _env(monkeypatch)
        monkeypatch.setenv("CORESMITH_ACCEPTANCE_DV_BACKPRESSURE", "0")
        root = tmp_path
        top = root / "rtl" / "toy_top.v"
        _write(top, DROP_DUT)
        _write(root / "inputs" / "toy_golden.py", REFERENCE)
        _write(root / "inputs" / "acceptance_stimulus.py", ACCEPTANCE)
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert not res["skipped"], res["reason"]
        assert res["passed"], "no-backpressure harness misses the drop (blind spot)"

    def test_word_stream_driven_and_captured(self, tmp_path, monkeypatch):
        # dv-hardening-30: a 32-bit WORD stream (the run-through's matmul shape)
        # must be driven/captured at full width -- 4 payload bytes per beat --
        # and match the oracle byte-exact, WITH backpressure on.
        _env(monkeypatch)
        root = tmp_path
        top = root / "rtl" / "toy_top.v"
        _write(top, WORD_DUT)
        _write(root / "inputs" / "toy_golden.py", WORD_REFERENCE)
        _write(root / "inputs" / "acceptance_stimulus.py", WORD_ACCEPTANCE)
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert not res["skipped"], res["reason"]
        assert res["passed"], res

    def test_word_stream_wrong_offset_diverges(self, tmp_path, monkeypatch):
        # A word DUT with the wrong offset must FAIL -- proving the width-aware
        # capture actually compares all 4 bytes, not just the low byte.
        _env(monkeypatch)
        root = tmp_path
        top = root / "rtl" / "toy_top.v"
        _write(top, WORD_DUT.replace("s_axis_tdata + cfg_offset",
                                     "s_axis_tdata + cfg_offset + 32'h100"))
        _write(root / "inputs" / "toy_golden.py", WORD_REFERENCE)
        _write(root / "inputs" / "acceptance_stimulus.py", WORD_ACCEPTANCE)
        adopt(root, top, name="toy_top")
        res = run_acceptance_dv(str(root), str(top))
        assert not res["skipped"], res["reason"]
        assert not res["passed"], "wrong upper-byte offset must be caught"


class TestTopModuleScoping:
    """dv-hardening-23 (armD defect #9): helper modules in the chip-top file
    (rst_sync_2ff etc.) must not leak their ports into the top contract's
    sideband map -- the harness would drive nonexistent top pins."""

    def test_helper_module_ports_not_in_sidebands(self):
        from orchestrator.langgraph.acceptance_dv import (
            classify_contract,
            discover_ports,
        )

        rtl = """
module chip_top (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] s_axis_tdata,
    input  wire s_axis_tvalid,
    output wire s_axis_tready,
    input  wire s_axis_tlast,
    input  wire [5:0] cfg_qp,
    output wire [7:0] m_axis_tdata,
    output wire m_axis_tvalid,
    input  wire m_axis_tready,
    output wire m_axis_tlast
);
endmodule

module rst_sync_2ff (
    input  wire clk,
    input  wire rst_n_in,
    output wire rst_n_out
);
endmodule
"""
        import re
        m = re.search(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_]*)", rtl)
        end = rtl.find("endmodule", m.start())
        span = rtl[m.start():end]
        contract = classify_contract(discover_ports(span))
        assert contract is not None
        assert "rst_n_in" not in contract["sidebands"]
        assert set(contract["sidebands"]) == {"cfg_qp"}


class TestShapeDerivedSidebands:
    def test_undeclared_array_geometry_is_not_inferred(self):
        contract = {"sidebands": {"cfg_frame_width": 8, "cfg_frame_height": 8}}
        assert map_stimulus({"frames": [[1, 2], [3, 4]]}, contract) is None

    def test_explicit_sidebands_are_used_verbatim(self):
        contract = {"s_axis": {"data_width": 8}, "m_axis": {"data_width": 8},
                    "sidebands": {"cfg_frame_width": 8}}
        mapping = {"payload": "samples", "input_width": 8, "output_width": 8,
                   "packing": "bytes", "byte_order": "little", "sidebands": {"cfg_frame_width": "width"}}
        mapped = map_stimulus({"samples": [1, 2, 3, 4], "width": 99}, contract, mapping=mapping)
        assert mapped["sidebands"] == {"cfg_frame_width": 99}


class TestMultiInputStreamHonestSkip:
    """dv-hardening-25 (aes128 OOD): a chip_top with two input stream groups
    (s_axis + s_axis_key) can't be driven by the single-input-stream harness;
    classify_contract must return None (-> honest skip) rather than mapping the
    2nd stream to sidebands and false-failing (aes128 live: 3/3 rtl_bytes=0 on
    a byte-exact RTL)."""

    _AES_TOP = """
module aes_top (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] s_axis_tdata,
    input  wire s_axis_tvalid,
    output wire s_axis_tready,
    input  wire s_axis_tlast,
    input  wire [7:0] s_axis_key_tdata,
    input  wire s_axis_key_tvalid,
    output wire s_axis_key_tready,
    input  wire s_axis_key_tlast,
    output wire [7:0] m_axis_tdata,
    output wire m_axis_tvalid,
    input  wire m_axis_tready,
    output wire m_axis_tlast
);
endmodule
"""

    _SINGLE_TOP = """
module enc_top (
    input  wire clk,
    input  wire rst_n,
    input  wire [7:0] s_axis_tdata,
    input  wire s_axis_tvalid,
    output wire s_axis_tready,
    input  wire s_axis_tlast,
    input  wire [5:0] cfg_qp,
    output wire [7:0] m_axis_tdata,
    output wire m_axis_tvalid,
    input  wire m_axis_tready,
    output wire m_axis_tlast
);
endmodule
"""

    def test_two_input_streams_skips(self):
        from orchestrator.langgraph.acceptance_dv import (
            classify_contract,
            discover_ports,
        )
        assert classify_contract(discover_ports(self._AES_TOP)) is None

    def test_single_input_stream_still_classifies(self):
        from orchestrator.langgraph.acceptance_dv import (
            classify_contract,
            discover_ports,
        )
        c = classify_contract(discover_ports(self._SINGLE_TOP))
        assert c is not None
        assert c["s_axis"]["prefix"] == "s_axis"
        assert "cfg_qp" in c["sidebands"]
