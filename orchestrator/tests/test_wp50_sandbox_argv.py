"""WP-50: the Codex worker sandbox is real unless the operator opts out."""
from __future__ import annotations

from orchestrator.langchain.agents.coresmith_llm import ClaudeLLM


def test_workspace_write_adds_project_root_and_no_bypass():
    cmd = ClaudeLLM._build_codex_cmd("/x/codex", "m", "/proj/codex-call-1", "workspace-write",
                                     None, project_root="/proj")
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
    assert cmd[cmd.index("--add-dir") + 1] == "/proj"


def test_danger_full_access_is_the_explicit_opt_out():
    cmd = ClaudeLLM._build_codex_cmd("/x/codex", "m", "/wd", "danger-full-access",
                                     None, project_root="/proj")
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--add-dir" not in cmd


def test_project_root_from_env(monkeypatch):
    monkeypatch.setenv("CORESMITH_PROJECT_ROOT", "/runs/x")
    cmd = ClaudeLLM._build_codex_cmd("/x/codex", "m", "/runs/x/codex-call-2", "workspace-write", None)
    assert cmd[cmd.index("--add-dir") + 1] == "/runs/x"
    monkeypatch.delenv("CORESMITH_PROJECT_ROOT", raising=False)
    cmd = ClaudeLLM._build_codex_cmd("/x/codex", "m", "/wd", "workspace-write", None)
    assert "--add-dir" not in cmd


def test_resume_without_the_boundary_becomes_a_fresh_call(monkeypatch):
    monkeypatch.setenv("CORESMITH_CODEX_RESUME", "1")
    cmd = ClaudeLLM._build_codex_cmd("/x/codex", "m", "/wd", "workspace-write", "sess",
                                     supported_flags=frozenset({"--json", "-C", "-m"}), project_root="/proj")
    assert "resume" not in cmd and cmd[cmd.index("--add-dir") + 1] == "/proj"
