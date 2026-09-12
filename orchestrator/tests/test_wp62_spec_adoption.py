"""Reviewed specs and successful verification results must commit coherently."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from orchestrator.langgraph import pipeline_graph as pg


def setup(tmp_path, monkeypatch, mode):
    canonical = tmp_path / "arch/uarch_specs/leaf.md"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("OLD SPEC")
    source = tmp_path / "reviewed.md"
    source.write_text("NEW SPEC")
    db = pg._db(str(tmp_path))
    db.set_result("leaf", "best", {"sim_passed": True, "rtl_sha1": "old"})
    if mode == "missing":
        source.unlink()
    if mode == "copy_failure":
        monkeypatch.setattr("shutil.copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("copy failed")))
    review = AsyncMock(return_value={"summary": "edit requirement", "issues_found": 1,
        "issues_fixed": 1, "edited_blocks": ["leaf"], "reviewed_specs": {"leaf": str(source)}})
    monkeypatch.setattr("orchestrator.langchain.agents.integration_review_agent.IntegrationReviewAgent",
                        lambda *a, **k: SimpleNamespace(review=review))
    monkeypatch.setattr(pg, "_resolve_interrupt", AsyncMock(return_value={"action": "approve"}))
    monkeypatch.setenv("CORESMITH_STRICT_INTEGRATION_REVIEW", "0")
    state = {"project_root": str(tmp_path), "block_queue": [{"name": "leaf", "tier": 1}],
             "tier_list": [1], "current_tier_index": 0,
             "completed_blocks": [{"name": "leaf", "success": True}]}
    return state, canonical, source, db, review


@pytest.mark.parametrize("mode", ["success", "copy_failure", "missing"])
def test_review_node_adoption_table(tmp_path, monkeypatch, mode):
    state, canonical, source, db, review = setup(tmp_path, monkeypatch, mode)
    out = asyncio.run(pg.integration_review_node(state))
    if mode == "success":
        assert canonical.read_text() == "NEW SPEC"
        assert out["integration_review_action"] == "approve"
        assert not out["integration_review_failed"]
        assert db.result("leaf", "best") is None
        assert out["revise_blocks"] == {"leaf": True}
        assert pg.route_after_integration_review(out) == "init_tier"
    else:
        assert canonical.read_text() == "OLD SPEC"
        assert out["integration_review_action"] != "approve"
        assert out["integration_review_failed"]
        assert pg.route_after_integration_review(out) != "advance_tier"
        assert db.result("leaf", "best")["sim_passed"]


def test_explicit_revise_missing_source_cannot_reuse(tmp_path, monkeypatch):
    state, canonical, source, db, review = setup(tmp_path, monkeypatch, "missing")
    plan = pg._plan_targeted_revise(str(tmp_path), {}, ["leaf"], ["leaf"],
                                   {"leaf": str(source)}, [], "revise", 1)
    assert plan == {"leaf": False}


def test_failed_flag_overrules_an_approve_action():
    assert pg.route_after_integration_review({"integration_review_action": "approve",
                                              "integration_review_failed": True}) != "advance_tier"


def test_approved_spec_reverification_does_not_run_reviewer_again(tmp_path, monkeypatch):
    state, canonical, source, db, review = setup(tmp_path, monkeypatch, "success")
    first = asyncio.run(pg.integration_review_node(state))
    db.set_result("leaf", "best", {"sim_passed": True, "rtl_sha1": "new"})
    review.side_effect = AssertionError("already approved: must reverify without another edit cycle")
    second = asyncio.run(pg.integration_review_node({**state, **first}))
    assert pg.route_after_integration_review(second) == "advance_tier"
    assert not second["integration_review_failed"]
    assert review.call_count == 1


def test_multi_file_adoption_stages_every_copy_before_replacement(tmp_path, monkeypatch):
    import shutil
    specs = tmp_path / "arch/uarch_specs"
    specs.mkdir(parents=True)
    reviewed = {}
    for name in ["a", "b"]:
        (specs / f"{name}.md").write_text("old " + name)
        src = tmp_path / f"{name}.md"
        src.write_text("new " + name)
        reviewed[name] = str(src)
    copy = shutil.copy2
    def fail_second(src, dst, **kw):
        if str(src) == reviewed["b"]:
            raise OSError("second copy failed")
        return copy(src, dst, **kw)
    monkeypatch.setattr(shutil, "copy2", fail_second)
    pg._adopt_reviewed_specs(str(tmp_path), ["a", "b"], reviewed)
    assert (specs / "a.md").read_text() == "old a"
    assert (specs / "b.md").read_text() == "old b"
