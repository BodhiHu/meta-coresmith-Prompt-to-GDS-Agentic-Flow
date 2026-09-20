# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""WP-76: RTL edited after candidate adoption (a chip-lead fix_rtl at
integration/validation DV) is re-integrated and re-adopted, not simulated
against a stale manifest and not parked as a functional failure."""
from __future__ import annotations

from langgraph.graph import END

from orchestrator.langgraph import pipeline_graph as pg


def test_stale_candidate_predicate():
    assert pg._stale_candidate({"passed": False, "returncode": -1, "kind": "candidate_mismatch",
                                "log": "Candidate manifest is stale or inconsistent"})
    assert not pg._stale_candidate({"passed": True})
    assert not pg._stale_candidate({"passed": False, "kind": "candidate_mismatch",
                                    "log": "Candidate changed before adapter invocation"})
    assert not pg._stale_candidate({"passed": False, "log": "tests failed"})


def test_integration_dv_routes_to_reintegration():
    stale = {"integration_dv_result": pg._reintegrate_result(
        "integration_dv", {"log": "Candidate manifest is stale or inconsistent"})}
    assert pg.route_after_integration_dv(stale) == "integration_check"
    assert pg.route_after_integration_dv({"integration_dv_result": {"passed": True}}) == "validation_dv"
    assert pg.route_after_integration_dv({"integration_dv_result": {"pending_decision": True}}) == "integration_dv_decision"
    assert pg.route_after_integration_dv({"integration_dv_result": {"action_taken": "fix_tb"}}) == "integration_dv"
    assert pg.route_after_integration_dv({"integration_dv_result": {"action_taken": "fix_rtl"}}) == "integration_check"
    assert pg.route_after_integration_dv({"integration_dv_result": {"passed": False}}) == END


def test_decisions_with_fix_rtl_go_through_the_integration_check():
    assert pg.route_after_integration_dv_decision({"integration_dv_result": {"action_taken": "fix_rtl"}}) == "integration_check"
    assert pg.route_after_integration_dv_decision({"integration_dv_result": {"action_taken": "retry"}}) == "integration_dv"
    assert pg.route_after_integration_dv_decision({"integration_dv_result": {"action_taken": "revise"}}) == "init_tier"
    assert pg.route_after_validation_dv_decision({"validation_dv_result": {"action_taken": "fix_rtl"}}) == "integration_check"
    assert pg.route_after_validation_dv_decision({"validation_dv_result": {"action_taken": "fix_tb"}}) == "validation_dv"


def test_validation_dv_routes_stale_candidates_to_reintegration():
    stale = {"validation_dv_result": pg._reintegrate_result(
        "validation_dv", {"log": "Candidate manifest is stale or inconsistent"})}
    assert pg.route_after_validation_dv(stale) == "integration_check"
    assert pg.route_after_validation_dv({"validation_dv_result": {"pending_decision": True}}) == "validation_dv_decision"
    assert pg.route_after_validation_dv({"validation_dv_result": {"action_taken": "retry"}}) == "validation_dv"


def test_graph_compiles_with_the_new_edges():
    """LangGraph validates conditional-edge targets at compile time, so a
    compiled graph proves every router target above is a real node."""
    graph = pg.build_pipeline_graph()
    nodes = set(graph.get_graph().nodes.keys())
    assert {"integration_check", "integration_dv", "validation_dv",
            "integration_dv_decision", "validation_dv_decision"} <= nodes
    for router, key in ((pg.route_after_integration_dv, "integration_dv_result"),
                        (pg.route_after_integration_dv_decision, "integration_dv_result"),
                        (pg.route_after_validation_dv, "validation_dv_result"),
                        (pg.route_after_validation_dv_decision, "validation_dv_result")):
        target = router({key: {"passed": False, "action_taken": "fix_rtl"}})
        assert target == "integration_check" and target in nodes
