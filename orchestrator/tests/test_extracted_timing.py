# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Deterministic RCX+STA extraction and fail-closed report tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.langgraph.extracted_timing import _unannotated_driver_evidence
from orchestrator.pdk.base import CheckResult, ToolResult


def _write_clean_reports(out: Path) -> None:
    (out / "setup.rpt").write_text(
        "Startpoint: rst_n\nEndpoint: u0/D\nPath Group: clk\n"
        "          22.21   slack (MET)\n"
    )
    (out / "hold.rpt").write_text(
        "Startpoint: u0/Q\nEndpoint: u1/D\nPath Group: clk\n"
        "           0.46   slack (MET)\n"
    )
    (out / "sta.rpt").write_text("wns max 0.00\ntns max 0.00\n")
    (out / "constraint_checks.rpt").write_text("")
    (out / "parasitic_annotation.rpt").write_text(
        "Found 10 unannotated drivers.\n"
        " clkload0/Y\n clkload1/Y\n clkload2/X\n clkload3/X\n"
        " clkload4/X\n clkload5/X\n clkload6/Y\n clkload7/X\n"
        " clkload8/X\n clkload9/Y\n"
        "Found 0 partially unannotated drivers.\n"
    )
    (out / "clocks.rpt").write_text("Clock clk period 40.00\n")
    (out / "power.rpt").write_text(
        "Total 5.81e-04 1.77e-04 1.34e-08 7.58e-04 100.0%\n"
    )
    (out / "area.rpt").write_text(
        "Design area 21010 um^2 51% utilization.\n"
    )


class _FakeStaTool:
    def __init__(self, producer=_write_clean_reports):
        self.producer = producer
        self.request = None

    def render_rcx_tcl(self, block_name, routed_def, sdc):
        return f'''set out_dir "$script_dir"
set def_file "{routed_def}"
set sdc_file "{sdc}"
extract_parasitics -ext_model_file deployment.rules
write_spef "$out_dir/{block_name}.spef"
exit
'''

    def run(self, request):
        self.request = request
        request.out_dir.mkdir(parents=True, exist_ok=True)
        self.producer(request.out_dir)
        (request.out_dir / f"{request.design}.spef").write_text(
            f'*SPEF "ieee 1481-1999"\n*DESIGN "{request.design}"\n'
            '*D_NET clk 0.1\n*CONN\n*END\n'
        )
        return ToolResult.from_checks(
            tool_ok=True,
            checks=[CheckResult("sta", "pass")],
            verb="run_sta",
            design=request.design,
        )


class _Deployment:
    name = "test-pdk"

    def __init__(self, tool):
        self._tool = tool

    def tool(self, verb):
        return self._tool if verb == "run_sta" else None


def _inputs(tmp_path):
    routed = tmp_path / "chip_top_routed.def"
    sdc = tmp_path / "chip_top.sdc"
    components = "".join(
        f"- clkload{i} fake_cell + PLACED ( {i} {i} ) N ;\n"
        for i in range(10)
    )
    routed.write_text(
        "DESIGN chip_top ;\nCOMPONENTS 10 ;\n" + components
        + "END COMPONENTS\nNETS 0 ;\nEND NETS\nEND DESIGN\n"
    )
    sdc.write_text("create_clock -period 40 [get_ports clk]\n")
    netlist = tmp_path / "chip_top_pnr.v"
    netlist.write_text("module chip_top(input clk); endmodule\n")
    return routed, sdc, netlist


def _run(tmp_path, tool=None):
    from orchestrator.langgraph.extracted_timing import run_extracted_timing

    routed, sdc, netlist = _inputs(tmp_path)
    return run_extracted_timing(
        block_name="chip_top",
        routed_def_path=str(routed),
        sdc_path=str(sdc),
        netlist_path=str(netlist),
        output_dir=tmp_path / "timing",
        timeout_s=123,
        deployment=_Deployment(tool or _FakeStaTool()),
    )


def test_real_tool_interface_passes_only_complete_measured_reports(tmp_path):
    tool = _FakeStaTool()
    result = _run(tmp_path, tool)

    assert result["met"] is True
    assert result["sign_off"] == "PASS"
    assert result["extraction_complete"] is True
    assert result["setup_slack_ns"] == pytest.approx(22.21)
    assert result["hold_slack_ns"] == pytest.approx(0.46)
    assert result["total_power_mw"] == pytest.approx(0.758)
    assert result["design_area_um2"] == pytest.approx(21010.0)
    assert tool.request.timeout_s == 123
    assert set(result["provenance"]["sha256"]) >= {
        "routed_def", "sdc", "netlist", "spef", "script", "report_setup",
    }
    script = Path(result["script_path"]).read_text()
    assert "extract_parasitics -ext_model_file deployment.rules" in script
    assert 'read_spef "$out_dir/chip_top.spef"' in script
    assert "report_checks -path_delay max" in script
    assert "check_setup -verbose" in script


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            lambda out: (out / "setup.rpt").write_text(
                "Startpoint: a\nEndpoint: b\n -0.01 slack (VIOLATED)\n"
            ),
            "setup slack is negative",
        ),
        (
            lambda out: (out / "hold.rpt").write_text(
                "Startpoint: a\nEndpoint: b\n nan slack (MET)\n"
            ),
            "hold slack is absent or non-finite",
        ),
        (
            lambda out: (out / "constraint_checks.rpt").write_text(
                "Warning: 4 unconstrained endpoints.\n"
            ),
            "constraint coverage is incomplete",
        ),
        (
            lambda out: (out / "parasitic_annotation.rpt").unlink(),
            "missing report(s): parasitic_annotation.rpt",
        ),
    ],
)
def test_report_defects_fail_closed(tmp_path, mutation, reason):
    def producer(out):
        _write_clean_reports(out)
        mutation(out)

    result = _run(tmp_path, _FakeStaTool(producer))

    assert result["met"] is False
    assert result["sign_off"] == "FAIL"
    assert any(reason in item for item in result["failure_reasons"])


def test_missing_spef_fails_even_when_reports_claim_pass(tmp_path):
    class NoSpef(_FakeStaTool):
        def run(self, request):
            self.request = request
            request.out_dir.mkdir(parents=True, exist_ok=True)
            self.producer(request.out_dir)
            return ToolResult.from_checks(
                tool_ok=True,
                checks=[CheckResult("sta", "pass")],
                verb="run_sta",
                design=request.design,
            )

    result = _run(tmp_path, NoSpef())
    assert result["met"] is False
    assert result["extraction_complete"] is False
    assert "extracted SPEF is missing or invalid" in result["error"]


def test_wholly_unannotated_functional_driver_fails(tmp_path):
    class ConnectedDriver(_FakeStaTool):
        def run(self, request):
            components = "".join(
                f"- clkload{i} fake_cell + PLACED ( {i} {i} ) N ;\n"
                for i in range(10)
            )
            request.inputs["routed_def"].write_text(
                "DESIGN chip_top ;\nCOMPONENTS 10 ;\n" + components
                + "END COMPONENTS\nNETS 1 ;\n"
                "- functional_net ( clkload0 Y ) ( u0 A ) ;\n"
                "END NETS\nEND DESIGN\n"
            )
            return super().run(request)

    result = _run(tmp_path, ConnectedDriver())
    assert result["met"] is False
    assert result["functional_unannotated_drivers"] == ["clkload0/Y"]
    assert "wholly unannotated" in result["error"]


def test_unresolved_unannotated_driver_is_not_assumed_disconnected(tmp_path):
    def producer(out):
        _write_clean_reports(out)
        (out / "parasitic_annotation.rpt").write_text(
            "Found 1 unannotated driver.\n ghost/Y\n"
            "Found 0 partially unannotated drivers.\n"
        )

    result = _run(tmp_path, _FakeStaTool(producer))
    assert result["met"] is False
    assert result["functional_unannotated_drivers"] == ["ghost/Y"]


def test_malformed_def_cannot_prove_unannotated_drivers_benign(tmp_path):
    class MalformedDef(_FakeStaTool):
        def run(self, request):
            request.inputs["routed_def"].write_text(
                "DESIGN chip_top ;\nCOMPONENTS 10 ;\n- clkload0 fake_cell ;\n"
                "NETS 0 ;\nEND DESIGN\n"
            )
            return super().run(request)

    result = _run(tmp_path, MalformedDef())
    assert result["met"] is False
    assert result["functional_unannotated_drivers"]


def test_specialnet_connection_is_functional_not_unconnected(tmp_path):
    class SpecialnetDriver(_FakeStaTool):
        def run(self, request):
            components = "".join(
                f"- clkload{i} fake_cell + PLACED ( {i} {i} ) N ;\n"
                for i in range(10)
            )
            request.inputs["routed_def"].write_text(
                "DESIGN chip_top ;\nCOMPONENTS 10 ;\n" + components
                + "END COMPONENTS\nNETS 0 ;\nEND NETS\n"
                "SPECIALNETS 1 ;\n- clock_spine ( clkload0 Y ) ;\n"
                "END SPECIALNETS\nEND DESIGN\n"
            )
            return super().run(request)

    result = _run(tmp_path, SpecialnetDriver())
    assert result["met"] is False
    assert "clkload0/Y" in result["functional_unannotated_drivers"]


def _hier_driver_def(*, connection: str = "", component: str = "u_core/_42_"):
    net_count = 1 if connection else 0
    net = f"- functional_net {connection} ;\n" if connection else ""
    return (
        "DESIGN chip_top ;\n"
        f"COMPONENTS 1 ;\n- {component} tie_cell + PLACED ( 0 0 ) N ;\n"
        "END COMPONENTS\n"
        f"NETS {net_count} ;\n{net}END NETS\n"
        "END DESIGN\n"
    )


def test_unused_exact_hierarchical_driver_is_proven_unconnected(tmp_path):
    routed = tmp_path / "chip_top.def"
    routed.write_text(_hier_driver_def())
    report = (
        "Found 1 unannotated driver.\n u_core/_42_/HI\n"
        "Found 0 partially unannotated drivers.\n"
    )

    declared, unconnected, unknown = _unannotated_driver_evidence(report, routed)

    assert declared == 1
    assert unconnected == ["u_core/_42_/HI"]
    assert unknown == []


def test_connected_exact_hierarchical_driver_still_blocks(tmp_path):
    routed = tmp_path / "chip_top.def"
    routed.write_text(_hier_driver_def(connection="( u_core/_42_ HI ) ( sink A )"))
    report = (
        "Found 1 unannotated driver.\n u_core/_42_/HI\n"
        "Found 0 partially unannotated drivers.\n"
    )

    declared, unconnected, unknown = _unannotated_driver_evidence(report, routed)

    assert declared == 1
    assert unconnected == []
    assert unknown == ["u_core/_42_/HI"]


def test_escaped_hierarchical_driver_remains_unknown(tmp_path):
    routed = tmp_path / "chip_top.def"
    routed.write_text(_hier_driver_def(component=r"\u_core/_42_"))
    report = (
        "Found 1 unannotated driver.\n \\u_core/_42_/HI\n"
        "Found 0 partially unannotated drivers.\n"
    )

    declared, unconnected, unknown = _unannotated_driver_evidence(report, routed)

    assert declared == 1
    assert unconnected == []
    assert unknown == [r"\u_core/_42_/HI"]


def test_duplicate_hierarchical_component_name_remains_unknown(tmp_path):
    routed = tmp_path / "chip_top.def"
    routed.write_text(
        "DESIGN chip_top ;\nCOMPONENTS 2 ;\n"
        "- u_core/_42_ tie_cell + PLACED ( 0 0 ) N ;\n"
        "- u_core/_42_ tie_cell + PLACED ( 10 0 ) N ;\n"
        "END COMPONENTS\nNETS 0 ;\nEND NETS\nEND DESIGN\n"
    )
    report = (
        "Found 1 unannotated driver.\n u_core/_42_/HI\n"
        "Found 0 partially unannotated drivers.\n"
    )

    declared, unconnected, unknown = _unannotated_driver_evidence(report, routed)

    assert declared == 1
    assert unconnected == []
    assert unknown == ["u_core/_42_/HI"]


def test_hierarchical_driver_declared_count_mismatch_stays_fail_closed(tmp_path):
    routed = tmp_path / "chip_top.def"
    routed.write_text(_hier_driver_def())
    report = (
        "Found 2 unannotated drivers.\n u_core/_42_/HI\n"
        "Found 0 partially unannotated drivers.\n"
    )

    declared, unconnected, unknown = _unannotated_driver_evidence(report, routed)

    assert declared == 2
    assert unconnected == []
    assert unknown == ["<unreconciled report: declared 2, listed 1>"]


def test_worst_slack_across_all_reported_groups_is_authoritative(tmp_path):
    def producer(out):
        _write_clean_reports(out)
        (out / "hold.rpt").write_text(
            "Path Group: fast_clock\n -0.20 slack (VIOLATED)\n"
            "Path Group: slow_clock\n  0.46 slack (MET)\n"
        )

    result = _run(tmp_path, _FakeStaTool(producer))
    assert result["hold_slack_ns"] == pytest.approx(-0.20)
    assert result["met"] is False
    assert any("hold slack is negative" in r for r in result["failure_reasons"])


def test_power_and_area_are_advisory_measurements(tmp_path):
    def producer(out):
        _write_clean_reports(out)
        (out / "power.rpt").unlink()
        (out / "area.rpt").unlink()

    result = _run(tmp_path, _FakeStaTool(producer))
    assert result["met"] is True
    assert result["total_power_mw"] is None
    assert result["design_area_um2"] is None


def test_unsupported_deployment_reports_actionable_capability(tmp_path):
    result = _run(tmp_path, tool=object())
    assert result["met"] is False
    assert "render_rcx_tcl capability" in result["error"]
