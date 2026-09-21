# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Contract payload fields drive deterministic integration width checks."""

from __future__ import annotations

from orchestrator.langgraph.integration_helpers import (
    VerilogModule,
    VerilogPort,
    check_integration_compatibility,
    merge_contract_compatibility_connections,
)


def _modules(response_width: int = 16, *, response_payload: bool = True):
    core_ports = [
        VerilogPort("clk", "input"),
        VerilogPort("rst_n", "input"),
        VerilogPort("m_req_srdy", "output", 1),
        VerilogPort("m_req_drdy", "input", 1),
        VerilogPort("m_req_req_pc", "output", 12),
        VerilogPort("s_rsp_srdy", "input", 1),
        VerilogPort("s_rsp_drdy", "output", 1),
    ]
    if response_payload:
        core_ports.append(
            VerilogPort("s_rsp_rsp_instr", "input", response_width)
        )
    return {
        "mcu_core": VerilogModule("mcu_core", core_ports),
        "spi_rom": VerilogModule("spi_rom", [
            VerilogPort("clk", "input"),
            VerilogPort("rst_n", "input"),
            VerilogPort("s_req_srdy", "input", 1),
            VerilogPort("s_req_drdy", "output", 1),
            VerilogPort("s_req_req_pc", "input", 12),
            VerilogPort("m_rsp_srdy", "output", 1),
            VerilogPort("m_rsp_drdy", "input", 1),
            VerilogPort("m_rsp_rsp_instr", "output", 16),
        ]),
    }


def _contracts():
    return [
        {
            "edge_id": "mcu_core__fetch_req__to__spi_rom__fetch_req",
            "producer_block": "mcu_core",
            "consumer_block": "spi_rom",
            "producer_port": (
                "m_req_srdy/m_req_data (rtl: req_valid/req_pc)"
            ),
            "consumer_port": (
                "s_req_drdy/s_req_data (rtl: req_ready/req_pc)"
            ),
            "data_width_bits": 12,
            "fields": [{"name": "req_pc", "width": 12, "msb": 11, "lsb": 0}],
            "handshake_protocol": "srdy_drdy",
        },
        {
            "edge_id": "spi_rom__fetch_rsp__to__mcu_core__fetch_rsp",
            "producer_block": "spi_rom",
            "consumer_block": "mcu_core",
            "producer_port": (
                "m_rsp_srdy/m_rsp_data (rtl: rsp_valid/rsp_instr)"
            ),
            "consumer_port": (
                "s_rsp_drdy/s_rsp_data (rtl: rsp_ready/rsp_instr)"
            ),
            "data_width_bits": 16,
            "fields": [
                {"name": "rsp_instr", "width": 16, "msb": 15, "lsb": 0}
            ],
            "handshake_protocol": "srdy_drdy",
        },
    ]


def test_mcu_spi_contract_selects_payload_not_handshake_ports():
    assert check_integration_compatibility(_contracts(), _modules()) == []


def test_real_payload_width_mismatch_remains_error():
    issues = check_integration_compatibility(
        _contracts(), _modules(response_width=15)
    )
    errors = [issue for issue in issues if issue.severity == "error"]
    assert len(errors) == 1
    assert errors[0].issue_type == "width_mismatch"
    assert "m_rsp_rsp_instr is 16-bit" in errors[0].description
    assert "s_rsp_rsp_instr is 15-bit" in errors[0].description


def test_missing_payload_does_not_fall_back_to_srdy_control():
    issues = check_integration_compatibility(
        _contracts(), _modules(response_payload=False)
    )
    errors = [issue for issue in issues if issue.severity == "error"]
    assert len(errors) == 1
    assert errors[0].issue_type == "missing_port"
    assert "s_rsp_rsp_instr" in errors[0].description
    assert all("s_rsp_srdy is 1-bit" not in issue.description for issue in issues)


def test_contract_projection_keeps_unprojected_legacy_edges():
    modules = _modules()
    modules.update({
        "legacy_src": VerilogModule("legacy_src", [
            VerilogPort("out_data", "output", 8),
        ]),
        "legacy_dst": VerilogModule("legacy_dst", [
            VerilogPort("in_data", "input", 4),
        ]),
    })
    legacy_edge = {
        "from_block": "legacy_src",
        "to_block": "legacy_dst",
        "from_port": "out_data",
        "to_port": "in_data",
        "interface": "legacy_data",
        "data_width": 8,
    }

    issues = check_integration_compatibility(
        [*_contracts(), legacy_edge], modules
    )

    errors = [issue for issue in issues if issue.severity == "error"]
    assert len(errors) == 1
    assert "legacy_src.out_data is 8-bit" in errors[0].description
    assert "legacy_dst.in_data is 4-bit" in errors[0].description


def test_contract_with_explicit_packed_ports_checks_packed_bus():
    packed_edge = {
        "edge_id": "packed_src__to__packed_dst",
        "producer_block": "packed_src",
        "consumer_block": "packed_dst",
        "producer_port": "packed_payload",
        "consumer_port": "packed_payload",
        "data_width_bits": 16,
        "fields": [
            {"name": "opcode", "width": 4},
            {"name": "operand", "width": 12},
        ],
    }
    modules = {
        "packed_src": VerilogModule("packed_src", [
            VerilogPort("packed_payload", "output", 16),
        ]),
        "packed_dst": VerilogModule("packed_dst", [
            VerilogPort("packed_payload", "input", 8),
        ]),
    }

    issues = check_integration_compatibility([packed_edge], modules)

    errors = [issue for issue in issues if issue.severity == "error"]
    assert len(errors) == 1
    assert "packed_src.packed_payload is 16-bit" in errors[0].description
    assert "packed_dst.packed_payload is 8-bit" in errors[0].description


def test_fieldless_contract_edge_falls_back_instead_of_disappearing():
    edge = {
        "edge_id": "legacy_contract",
        "producer_block": "source",
        "consumer_block": "sink",
        "producer_port": "payload",
        "consumer_port": "payload",
        "data_width_bits": 8,
        "fields": [],
    }
    modules = {
        "source": VerilogModule("source", [
            VerilogPort("payload", "output", 8),
        ]),
        "sink": VerilogModule("sink", [
            VerilogPort("payload", "input", 4),
        ]),
    }

    issues = check_integration_compatibility([edge], modules)

    assert len(issues) == 1
    assert issues[0].issue_type == "width_mismatch"
    assert issues[0].severity == "error"


def test_directional_contract_field_uses_legacy_edge_check():
    edge = {
        "edge_id": "bidirectional_status",
        "producer_block": "left",
        "consumer_block": "right",
        "producer_port": "status_bus",
        "consumer_port": "status_bus",
        "data_width_bits": 8,
        "fields": [{
            "name": "status",
            "width": 8,
            "direction": "consumer_to_producer",
        }],
    }
    modules = {
        "left": VerilogModule("left", [
            VerilogPort("status_bus", "inout", 8),
        ]),
        "right": VerilogModule("right", [
            VerilogPort("status_bus", "inout", 4),
        ]),
    }

    issues = check_integration_compatibility([edge], modules)

    assert len(issues) == 1
    assert issues[0].issue_type == "width_mismatch"
    assert "status_bus" in issues[0].description
    assert "status_bus_status" not in issues[0].description


def test_partial_contract_keeps_unmatched_architecture_channel():
    architecture = [
        {
            "from_block": "mcu_core", "to_block": "spi_rom",
            "interface": "fetch_req", "data_width": 12,
        },
        {
            "from_block": "spi_rom", "to_block": "mcu_core",
            "interface": "fetch_rsp", "data_width": 16,
        },
        {
            "from_block": "spi_rom", "to_block": "mcu_core",
            "from_port": "fault_out", "to_port": "fault_in",
            "interface": "fault_status", "data_width": 1,
        },
    ]

    merged = merge_contract_compatibility_connections(
        architecture, _contracts()
    )

    assert len(merged) == 3
    assert merged[:2] == _contracts()
    assert merged[2]["interface"] == "fault_status"
