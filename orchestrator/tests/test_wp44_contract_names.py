"""WP-44: undeclarable contract names are structural violations and gate failures, never drops."""
from __future__ import annotations

import json

from orchestrator.langgraph.contract_conformance import (
    check_block,
    illegal_contract_names,
    illegal_edge_end_names,
)


def _edge(eid, pport, cport, fields=(), proto="valid_only", pb="a", cb="b"):
    return {"edge_id": eid, "producer_block": pb, "producer_port": pport,
            "consumer_block": cb, "consumer_port": cport, "handshake_protocol": proto,
            "fields": [{"name": f} for f in fields], "sideband_signals": []}


def test_dotted_channel_is_flagged_with_both_ends():
    bad = illegal_contract_names([_edge("e1", "start.valid/start.payload", "s_start", ["payload"])])
    assert len(bad) == 1 and bad[0]["end"] == "producer" and bad[0]["derived"] == "start.valid"
    assert "not a legal Verilog identifier" in bad[0]["message"] and "revise the CONTRACT" in bad[0]["message"]


def test_dotted_signal_is_flagged():
    bad = illegal_contract_names([_edge("e2", "m_ctl", "s_ctl", ["status.done"])])
    assert [(b["end"], b["signal"], b["derived"]) for b in bad] == [
        ("producer", "status.done", "m_ctl_status.done"), ("consumer", "status.done", "s_ctl_status.done")]


def test_slash_aliases_and_plain_names_are_legal():
    assert illegal_contract_names([_edge("e3", "m_qspi_write_srdy/m_qspi_write_data",
                                         "s_qspi_write_drdy/s_qspi_write_data", ["data"], proto="srdy_drdy")]) == []
    assert illegal_edge_end_names(_edge("e4", "in", "in", ["in_last", "data"]), "in") == []


def test_block_fails_conformance_with_the_reason(tmp_path):
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / ".coresmith" / "interface_contracts.json").write_text(json.dumps(
        {"contracts": [_edge("e5", "irq.done_level", "s_irq", ["level"], pb="regmap", cb="wrap")]}))
    rtl = tmp_path / "regmap.v"
    rtl.write_text("module regmap(input wire clk, output wire irq_level);\nendmodule\n")
    r = check_block(tmp_path, "regmap", rtl)
    assert not r.ok and r.ambiguous and "irq.done_level" in r.ambiguous[0][1]
    assert "revise the CONTRACT" in r.as_feedback()


def test_interface_definition_validator_emits_illegal_identifier():
    from orchestrator.architecture.specialists.interface_definition import _validate_contracts
    result = {"contracts": [_edge("e6", "qspi_pads.csn_in/sck_in", "s_pads", ["csn_in", "sck_in"])]}
    out, notes = _validate_contracts(result, [])
    types = [v["type"] for v in out["contract_violations"]]
    assert "illegal_identifier" in types
    v = [v for v in out["contract_violations"] if v["type"] == "illegal_identifier"][0]
    assert v["category"] == "structural" and v["severity"] == "error" and "qspi_pads.csn" in v["violation"]
