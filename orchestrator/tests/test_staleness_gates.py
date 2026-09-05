# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""C6 + C7 gates.

C6: a generated block model that declares an interface gap (structured
INFEASIBLE-INTERFACE-GAP marker, or the observed ERROR_INTERFACE_GAP stub
idiom) is a model/spec feasibility conflict -- detect it so the uarch review
re-opens the feasibility interrupt instead of letting a stub-consistent
model+TB+RTL triple pass per-block DV.

C7: a block whose uarch spec was generated against an OLDER interface
contract than the live one is STALE -- the integration preflight refuses to
assemble (truncation adapters would destroy the amended semantics).
"""

import json



def _write_contracts(root, contracts):
    (root / ".coresmith").mkdir(exist_ok=True)
    (root / ".coresmith" / "interface_contracts.json").write_text(
        json.dumps({"contracts": contracts}))


def _edge(producer, consumer, width):
    return {"producer_block": producer, "consumer_block": consumer,
            "signal": f"{producer}_to_{consumer}", "width": width}


def _stamp(root, block, sha):
    d = root / ".coresmith" / "blocks" / block
    d.mkdir(parents=True, exist_ok=True)
    (d / "uarch_spec_contract_sha1").write_text(sha)


_VALID_MODEL_TMPL = """\
{header}from myhdl import block, always_seq

@block
def {name}(clk, rst):
    @always_seq(clk.posedge, reset=rst)
    def logic():
        pass
    return logic
"""


class TestApplyContractAmendments:
    """C10: resolver amendments merge into interface_contracts.json --
    pure-function semantics (match by edge_id or block pair, replace-by-name,
    append-note-once, skip-no-match)."""

    def _doc(self):
        return {"contracts": [
            {"edge_id": "rtm__lookup__to__rre", "producer_block": "rtm",
             "consumer_block": "rre", "notes": "existing"},
            {"edge_id": "fdc__job__to__rre", "producer_block": "fdc",
             "consumer_block": "rre"},
        ]}

    def test_match_by_edge_id_and_merge_enum(self):
        from orchestrator.langchain.agents.gap_resolver import (
            apply_contract_amendments,
        )
        doc, applied = apply_contract_amendments(self._doc(), [{
            "edge_id": "rtm__lookup__to__rre",
            "add_representations": {"enums": [
                {"name": "region_selector", "width": 2,
                 "values": {"huffman": "2'b00", "quant_range": "2'b10"}},
            ]},
            "append_note": "selectors frozen from rtm spec",
        }])
        assert len(applied) == 1 and "enums[region_selector]" in applied[0]
        c = doc["contracts"][0]
        assert c["representations"]["enums"][0]["values"]["quant_range"] == "2'b10"
        assert "[gap-resolution] selectors frozen" in c["notes"]
        assert c["notes"].startswith("existing")

    def test_replace_by_name_updates_not_duplicates(self):
        from orchestrator.langchain.agents.gap_resolver import (
            apply_contract_amendments,
        )
        doc = self._doc()
        amend = {"edge_id": "rtm__lookup__to__rre",
                 "add_representations": {"enums": [
                     {"name": "region_selector", "values": {"huffman": "2'b00"}}]}}
        doc, _ = apply_contract_amendments(doc, [amend])
        amend2 = {"edge_id": "rtm__lookup__to__rre",
                  "add_representations": {"enums": [
                      {"name": "region_selector",
                       "values": {"huffman": "2'b00", "base_matrix": "2'b11"}}]}}
        doc, _ = apply_contract_amendments(doc, [amend2])
        enums = doc["contracts"][0]["representations"]["enums"]
        assert len(enums) == 1
        assert enums[0]["values"]["base_matrix"] == "2'b11"

    def test_match_by_block_pair(self):
        from orchestrator.langchain.agents.gap_resolver import (
            apply_contract_amendments,
        )
        doc, applied = apply_contract_amendments(self._doc(), [{
            "producer_block": "fdc", "consumer_block": "rre",
            "add_representations": {"state_semantics": [
                {"name": "previous_frame_valid",
                 "rule": "0 until first KEYFRAME completes"}]},
        }])
        assert len(applied) == 1 and "fdc__job__to__rre" in applied[0]

    def test_no_match_is_skipped_never_guessed(self):
        from orchestrator.langchain.agents.gap_resolver import (
            apply_contract_amendments,
        )
        doc, applied = apply_contract_amendments(self._doc(), [{
            "edge_id": "does__not__exist",
            "add_representations": {"enums": [{"name": "x", "values": {}}]},
        }])
        assert applied == []
        assert "representations" not in doc["contracts"][0]

    def test_note_appended_once(self):
        from orchestrator.langchain.agents.gap_resolver import (
            apply_contract_amendments,
        )
        doc = self._doc()
        amend = {"edge_id": "fdc__job__to__rre", "append_note": "same note"}
        doc, a1 = apply_contract_amendments(doc, [amend])
        doc, a2 = apply_contract_amendments(doc, [amend])
        assert len(a1) == 1 and a2 == []
        assert doc["contracts"][1]["notes"].count("same note") == 1


class TestFreshArtifactRecovery:
    """C12/C13: codex-call-* recovery must never resurrect a PREVIOUS call's
    artifact -- only artifacts written at/after the call start are eligible."""

    def test_rtl_recovery_filters_stale_artifacts(self, tmp_path):
        import os
        import time

        from orchestrator.langchain.agents.rtl_generator import (
            _recover_codex_artifact,
        )

        art = tmp_path / "codex-call-old" / "rtl" / "b.v"
        art.parent.mkdir(parents=True)
        art.write_text("module b; endmodule\n")
        old = time.time() - 3600
        os.utime(art, (old, old))
        # call started NOW -> the hour-old artifact is not this call's output
        assert _recover_codex_artifact(
            str(tmp_path), "rtl/b.v", min_mtime=time.time() - 60) == ""
        # unfiltered (legacy) still finds it
        assert "module b" in _recover_codex_artifact(str(tmp_path), "rtl/b.v")

    def test_spec_recovery_filters_stale_artifacts(self, tmp_path):
        import os
        import time
        from pathlib import Path

        from orchestrator.langgraph.pipeline_helpers import (
            _recover_codex_call_artifact,
        )

        art = tmp_path / "codex-call-old" / "arch" / "uarch_specs" / "b.md"
        art.parent.mkdir(parents=True)
        art.write_text("# stale spec\n" * 100)
        old = time.time() - 3600
        os.utime(art, (old, old))
        rel = Path("arch/uarch_specs/b.md")
        assert _recover_codex_call_artifact(
            tmp_path, rel, min_mtime=time.time() - 60) == ""
        assert "stale spec" in _recover_codex_call_artifact(tmp_path, rel)


class TestContractPortGate:
    """C15: deterministic RTL-ports-vs-frozen-contract check -- a stale-width
    RTL passed its own TB 6/6 (34-bit port vs a 41-bit contract) and only died
    at integration; this gate is TB-independent and fails pre-sim."""

    def _project(self, tmp_path, rtl_text, width=41):
        (tmp_path / ".coresmith").mkdir()
        (tmp_path / ".coresmith" / "interface_contracts.json").write_text(
            json.dumps({"contracts": [
                {"edge_id": "fdc__job__to__rre", "producer_block": "fdc",
                 "consumer_block": "rre",
                 "producer_port": "m_axis_frame_job",
                 "consumer_port": "s_axis_frame_job",
                 "handshake_protocol": "axi_stream",
                 "data_width_bits": width},
                {"edge_id": "rre__lookup__to__rtm", "producer_block": "rre",
                 "consumer_block": "rtm",
                 "producer_port": "m_table_lookup_req_data",
                 "consumer_port": "s_lookup_req_data",
                 "handshake_protocol": "srdy_drdy",
                 "data_width_bits": 16},
            ]}))
        rtl = tmp_path / "rre.v"
        rtl.write_text(rtl_text)
        return rtl

    _GOOD_RTL = """\
module rre (
    input  wire clk,
    input  wire [40:0] s_axis_frame_job_tdata,
    input  wire s_axis_frame_job_tvalid,
    output wire [15:0] m_table_lookup_req_data,
    output wire m_table_lookup_req_srdy
);
endmodule
"""

    def test_stale_width_caught(self, tmp_path):
        from orchestrator.langgraph.pipeline_helpers import (
            check_rtl_contract_ports,
        )
        rtl = self._project(tmp_path, self._GOOD_RTL.replace("[40:0]", "[33:0]"))
        errors = check_rtl_contract_ports(tmp_path, "rre", str(rtl))
        assert len(errors) == 1
        assert "34 bits" in errors[0] and "requires 41 bits" in errors[0]

    def test_conformant_rtl_clean(self, tmp_path):
        from orchestrator.langgraph.pipeline_helpers import (
            check_rtl_contract_ports,
        )
        rtl = self._project(tmp_path, self._GOOD_RTL)
        assert check_rtl_contract_ports(tmp_path, "rre", str(rtl)) == []

    def test_missing_port_is_not_hard_fail(self, tmp_path):
        # C22: a contract port not found by NAME is advisory, not a hard error
        # (RTL wrapper/bundle naming legitimately diverges from the contract
        # endpoint name; the TB/integration confirm a truly-absent port). Only
        # a genuine WIDTH mismatch on a FOUND port hard-fails.
        from orchestrator.langgraph.pipeline_helpers import (
            check_rtl_contract_ports,
        )
        stripped = self._GOOD_RTL.replace(
            "    output wire [15:0] m_table_lookup_req_data,\n", "")
        rtl = self._project(tmp_path, stripped)
        assert check_rtl_contract_ports(tmp_path, "rre", str(rtl)) == []

    def test_no_contract_edges_is_clean(self, tmp_path):
        from orchestrator.langgraph.pipeline_helpers import (
            check_rtl_contract_ports,
        )
        (tmp_path / ".coresmith").mkdir()
        rtl = tmp_path / "solo.v"
        rtl.write_text("module solo (input wire clk); endmodule\n")
        assert check_rtl_contract_ports(tmp_path, "solo", str(rtl)) == []

    def test_bare_native_bundle_port_not_suffixed(self, tmp_path):
        # C22: srdy_drdy contracts can name a BARE native bundle port
        # (qspi_rx_internal, control_events) with no _data suffix, and the RTL
        # uses it VERBATIM. The gate must not append _data (qspi_rx_internal_
        # data) and false-fail a lint-clean block. Reproduces the aes finding.
        from orchestrator.langgraph.pipeline_helpers import (
            check_rtl_contract_ports,
        )
        (tmp_path / ".coresmith").mkdir()
        (tmp_path / ".coresmith" / "interface_contracts.json").write_text(
            json.dumps({"contracts": [
                {"edge_id": "wrap__rx__to__ctl", "producer_block": "wrap",
                 "consumer_block": "ctl",
                 "producer_port": "qspi_rx_internal",
                 "consumer_port": "async_gpio_rx",
                 "handshake_protocol": "srdy_drdy", "data_width_bits": 8},
            ]}))
        rtl = tmp_path / "wrap.v"
        rtl.write_text(
            "module wrap (\n"
            "    input wire clk,\n"
            "    output wire [7:0] qspi_rx_internal,\n"
            "    output wire qspi_rx_internal_valid\n"
            ");\nendmodule\n")
        assert check_rtl_contract_ports(tmp_path, "wrap", str(rtl)) == []
        # a bare port present but WRONG WIDTH still hard-fails (the real check)
        rtl.write_text(
            "module wrap (\n    input wire clk,\n"
            "    output wire [3:0] qspi_rx_internal\n);\nendmodule\n")
        errs = check_rtl_contract_ports(tmp_path, "wrap", str(rtl))
        assert len(errs) == 1 and "4 bits" in errs[0] and "8 bits" in errs[0]
        # a genuinely-absent port is advisory-only (not a hard error)
        rtl.write_text("module wrap (input wire clk); endmodule\n")
        assert check_rtl_contract_ports(tmp_path, "wrap", str(rtl)) == []

    def test_srdy_data_port_not_double_suffixed(self, tmp_path):
        # C19: srdy_drdy contracts name the DATA port directly (already ending
        # in _data, e.g. m_byte_request_data), NOT the _srdy handshake name.
        # The gate must NOT append a second _data (m_..._data_data) and false-
        # fail the block. Reproduces the matmul sweep finding where the original
        # C15 code broke EVERY srdy_drdy block.
        from orchestrator.langgraph.pipeline_helpers import (
            check_rtl_contract_ports,
        )
        (tmp_path / ".coresmith").mkdir()
        (tmp_path / ".coresmith" / "interface_contracts.json").write_text(
            json.dumps({"contracts": [
                {"edge_id": "rom__req__to__mac", "producer_block": "rom",
                 "consumer_block": "mac",
                 "producer_port": "m_byte_request_data",
                 "consumer_port": "s_byte_request_data",
                 "handshake_protocol": "srdy_drdy", "data_width_bits": 21},
            ]}))
        rtl = tmp_path / "rom.v"
        rtl.write_text(
            "module rom (\n"
            "    input wire clk,\n"
            "    output wire [20:0] m_byte_request_data,\n"
            "    output wire m_byte_request_srdy\n"
            ");\nendmodule\n")
        # producer side of rom: the data port IS m_byte_request_data (21 bits)
        errors = check_rtl_contract_ports(tmp_path, "rom", str(rtl))
        assert errors == [], errors


