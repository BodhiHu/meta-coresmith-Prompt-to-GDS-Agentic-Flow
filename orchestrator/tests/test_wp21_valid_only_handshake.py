"""WP-21/WP-34: valid_only channels carry their strobe; the strobe is REQUIRED and
undeclared flow-control ports are deviations (review round 2 reversed WP-21b)."""
from __future__ import annotations

import json

from orchestrator.langgraph.contract_conformance import check_block, signal_specs


def _project(tmp_path, edges):
    (tmp_path / ".coresmith").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".coresmith" / "interface_contracts.json").write_text(json.dumps({"contracts": edges}))
    return tmp_path


def _edge(producer, consumer, chan, fields=(), proto="valid_only"):
    return {"edge_id": f"{producer}__{chan}__to__{consumer}__{chan}",
            "producer_block": producer, "producer_port": chan,
            "consumer_block": consumer, "consumer_port": chan,
            "handshake_protocol": proto,
            "fields": [{"name": f} for f in fields], "sideband_signals": []}


def _rtl(tmp_path, name, ports):
    f = tmp_path / f"{name}.v"
    f.write_text(f"module {name} (\n  " + ",\n  ".join(f"input wire {p}" for p in ports) + "\n);\nendmodule\n")
    return f


def test_valid_only_edge_declares_its_strobe():
    names = [s["name"] for s in signal_specs(_edge("a", "b", "start_event", ["mode", "packet_length"]))]
    assert names == ["mode", "packet_length", "valid"]


def test_valid_only_edge_does_not_duplicate_an_explicit_valid():
    names = [s["name"] for s in signal_specs(_edge("a", "b", "x", ["valid", "mode"]))]
    assert names.count("valid") == 1


def test_ax25_shape_conforms(tmp_path):
    root = _project(tmp_path, [_edge("regmap", "modem_controller", "start_event", ["mode", "packet_length"]),
                               _edge("modem_controller", "hdlc", "hdlc_start", ["packet_length"])])
    r = check_block(root, "modem_controller", _rtl(tmp_path, "modem_controller", [
        "start_event_valid", "start_event_mode", "start_event_packet_length",
        "hdlc_start_valid", "hdlc_start_packet_length"]))
    assert r.undeclared == [] and r.missing == [], (r.undeclared, r.missing)


def test_flow_control_extra_is_undeclared(tmp_path):
    root = _project(tmp_path, [_edge("a", "b", "req_ch", ["addr"], proto="req_resp")])
    r = check_block(root, "b", _rtl(tmp_path, "b", ["req_ch_addr", "req_ch_req", "req_ch_ack", "req_ch_bogus"]))
    assert r.undeclared == ["req_ch_ack", "req_ch_bogus", "req_ch_req"] and not r.ok


def test_missing_synthesized_valid_only_strobe_is_missing(tmp_path):
    root = _project(tmp_path, [_edge("ctl", "shaper", "soft_reset", ["pulse"])])
    r = check_block(root, "shaper", _rtl(tmp_path, "shaper", ["soft_reset_pulse"]))
    assert ("soft_reset", "soft_reset_valid") in r.missing and not r.ok


def test_wrong_handshake_does_not_pass(tmp_path):
    """Review round 2 counterexample: no required go_valid, a stray go_ready -> WP-21b said ok."""
    root = _project(tmp_path, [_edge("ctl", "shaper", "go", ["mode"])])
    r = check_block(root, "shaper", _rtl(tmp_path, "shaper", ["go_mode", "go_ready"]))
    assert not r.ok
    assert ("go", "go_valid") in r.missing and "go_ready" in r.undeclared


def test_contract_enumerated_valid_is_still_required(tmp_path):
    root = _project(tmp_path, [_edge("ctl", "shaper", "go", ["valid", "mode"])])
    r = check_block(root, "shaper", _rtl(tmp_path, "shaper", ["go_mode"]))
    assert ("go", "go_valid") in r.missing
