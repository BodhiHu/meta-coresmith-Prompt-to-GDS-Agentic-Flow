"""WP-26: an illegal contract name is skipped by the Caravel assembler, not a hazard."""
from __future__ import annotations

from types import SimpleNamespace

from orchestrator.langgraph.integration_helpers import _resolve_by_contract


def _port(name, width=1):
    return SimpleNamespace(name=name, width=width)


def _modules():
    return {"aes_controller": SimpleNamespace(name="aes_controller", ports=[_port("status_done_done"), _port("clk")]),
            "user_project_wrapper": SimpleNamespace(name="user_project_wrapper", ports=[_port("irq_level_irq"), _port("io_in", 38)])}


def _port_exact(modules):
    def f(block, name):
        for p in modules[block].ports:
            if p.name == name:
                return p
        return None
    return f


def test_dotted_producer_port_is_skipped_not_a_hazard():
    edge = {"edge_id": "aes_controller__status_done__to__user_project_wrapper__irq_level",
            "producer_block": "aes_controller", "producer_port": "status.done",
            "consumer_block": "user_project_wrapper", "consumer_port": "irq_level",
            "handshake_protocol": "valid_only", "fields": [{"name": "irq"}], "sideband_signals": []}
    mods = _modules()
    res = _resolve_by_contract(edge, "aes_controller", "user_project_wrapper", _port_exact(mods), mods)
    assert res is not None
    paired, hazards = res
    assert hazards == [] and paired == []


def test_legal_edge_still_reports_missing_ports():
    edge = {"edge_id": "e", "producer_block": "aes_controller", "producer_port": "status_done",
            "consumer_block": "user_project_wrapper", "consumer_port": "irq_level",
            "handshake_protocol": "valid_only", "fields": [{"name": "irq"}], "sideband_signals": []}
    mods = _modules()
    paired, hazards = _resolve_by_contract(edge, "aes_controller", "user_project_wrapper", _port_exact(mods), mods)
    assert hazards and "implements no port" in hazards[0]
