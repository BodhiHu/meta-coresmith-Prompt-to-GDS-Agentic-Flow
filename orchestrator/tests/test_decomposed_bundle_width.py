import pytest

from orchestrator.langgraph.integration_helpers import (
    VerilogModule,
    VerilogPort,
    check_integration_compatibility,
)


def bundle(width=12, drop=False):
    source = [VerilogPort("fetch_request_req_valid", "output"),
              VerilogPort("fetch_request_req_pc", "output", 12),
              VerilogPort("fetch_request_ready_req_ready", "input")]
    sink = [VerilogPort("fetch_request_req_valid", "input"),
            VerilogPort("fetch_request_ready_req_ready", "output")]
    if not drop:
        sink.append(VerilogPort("fetch_request_req_pc", "input", width))
    return {"core": VerilogModule("core", source), "spi": VerilogModule("spi", sink)}


CONN = {"from_block": "core", "to_block": "spi", "from_port": "fetch_request",
        "to_port": "fetch_request", "interface": "fetch_request", "data_width": 13}


def test_complete_bundle_matches_architecture():
    assert check_integration_compatibility([CONN], bundle()) == []


@pytest.mark.parametrize("modules,kind", [
    (bundle(width=11), "width_mismatch"), (bundle(drop=True), "missing_bundle_field")])
def test_real_field_defects_remain_errors(modules, kind):
    issues = check_integration_compatibility([CONN], modules)
    assert any(m.issue_type == kind and m.severity == "error" for m in issues)


def test_wrong_architecture_total_still_warns():
    issues = check_integration_compatibility([{**CONN, "data_width": 14}], bundle())
    assert len(issues) == 1
    assert issues[0].details == {"actual_width": 13, "expected_width": 14}


def test_exact_field_names_do_not_accumulate_other_fields():
    edge = {**CONN, "from_port": "fetch_request_req_valid",
            "to_port": "fetch_request_req_valid", "data_width": 1}
    assert check_integration_compatibility([edge], bundle()) == []
