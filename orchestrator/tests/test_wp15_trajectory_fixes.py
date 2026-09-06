"""WP-15: fixes derived from the Arm F / E2 trajectory audits."""
from __future__ import annotations

import json

from orchestrator.langchain.agents.coresmith_llm import _parse_codex_json


def test_codex_error_event_becomes_llm_error_marker():
    out = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t1"}),
        json.dumps({"type": "turn.started"}),
        json.dumps({"type": "error", "message": "You've hit your usage limit. Visit ..."}),
    ])
    text, usage = _parse_codex_json(out)
    assert text.startswith("[ClaudeLLM error: codex CLI reported: You've hit your usage limit")
    assert usage["session_id"] == "t1"


def test_codex_turn_failed_event_becomes_llm_error_marker():
    out = json.dumps({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}})
    text, _ = _parse_codex_json(out)
    assert text.startswith("[ClaudeLLM error: codex CLI reported: You've hit your usage limit")


def test_codex_agent_message_wins_over_error_event():
    out = "\n".join([
        json.dumps({"type": "error", "message": "transient"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}),
    ])
    assert _parse_codex_json(out)[0] == "done"


def test_infra_markers_include_usage_limit():
    import inspect
    from orchestrator.langgraph import pipeline_graph as pg
    src = inspect.getsource(pg.diagnose_node)
    assert "usage limit" in src and "[ClaudeLLM error:" in src


def test_squeeze_is_gone():
    from orchestrator.langgraph import pipeline_graph as pg
    assert not hasattr(pg, "_maybe_squeeze_throughput")


def test_stage_project_inputs_symlinks_rom_images(tmp_path):
    from orchestrator.langgraph.integration_helpers import _stage_project_inputs
    (tmp_path / "inputs" / "rom_images").mkdir(parents=True)
    (tmp_path / "inputs" / "rom_images" / "q.memh").write_text("00\n")
    sim = tmp_path / "sim_build" / "integration"
    sim.mkdir(parents=True)
    _stage_project_inputs(sim, tmp_path)
    assert (sim / "inputs" / "rom_images" / "q.memh").read_text() == "00\n"
    _stage_project_inputs(sim, tmp_path)  # idempotent
    assert (sim / "inputs").is_symlink()


def test_stage_project_inputs_without_inputs_dir_is_noop(tmp_path):
    from orchestrator.langgraph.integration_helpers import _stage_project_inputs
    sim = tmp_path / "sim_build" / "integration"
    sim.mkdir(parents=True)
    _stage_project_inputs(sim, tmp_path)
    assert not (sim / "inputs").exists()


def test_acceptance_stimulus_falls_back_to_model_stimulus(tmp_path, monkeypatch):
    from orchestrator.architecture.reference_oracle import _acceptance_stimulus_path
    monkeypatch.delenv("CORESMITH_ACCEPTANCE_STIMULUS", raising=False)
    monkeypatch.delenv("CORESMITH_MODEL_STIMULUS", raising=False)
    assert _acceptance_stimulus_path(str(tmp_path)) == ""
    (tmp_path / "inputs").mkdir()
    ms = tmp_path / "inputs" / "model_stimulus.py"
    ms.write_text("stimulus = b'x'\n")
    assert _acceptance_stimulus_path(str(tmp_path)) == str(ms)
    acc = tmp_path / "inputs" / "acceptance_stimulus.py"
    acc.write_text("stimulus = b'y'\n")
    assert _acceptance_stimulus_path(str(tmp_path)) == str(acc)
