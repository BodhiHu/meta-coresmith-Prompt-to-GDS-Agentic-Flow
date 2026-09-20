# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""WP-71: the Claude CLI never receives a system prompt that exceeds the kernel's
per-argument limit on argv; large prompts travel through --system-prompt-file."""
from __future__ import annotations

from unittest.mock import patch

from orchestrator.langchain.agents import coresmith_llm
from orchestrator.langchain.agents.coresmith_llm import (
    _CLAUDE_INLINE_PROMPT_LIMIT,
    ClaudeLLM,
    _claude_system_prompt_args,
)


def test_small_prompt_stays_inline(tmp_path):
    args = _claude_system_prompt_args("short system prompt", str(tmp_path))
    assert args == ["--system-prompt", "short system prompt"]
    assert not (tmp_path / ".coresmith" / "llm_prompts").exists()


def test_large_prompt_goes_through_a_file(tmp_path):
    prompt = "x" * (_CLAUDE_INLINE_PROMPT_LIMIT + 1)
    args = _claude_system_prompt_args(prompt, str(tmp_path))
    assert args[0] == "--system-prompt-file"
    path = tmp_path / ".coresmith" / "llm_prompts"
    assert (path / args[1].rsplit("/", 1)[1]).read_text() == prompt
    # content-addressed: the same prompt reuses the same file
    assert _claude_system_prompt_args(prompt, str(tmp_path)) == args
    # a different prompt gets a different file
    other = _claude_system_prompt_args(prompt + "y", str(tmp_path))
    assert other[1] != args[1]


def test_argv_never_carries_an_oversized_system_prompt(tmp_path, monkeypatch):
    """The uArch system prompt (~150 KB) used to raise ``[Errno 7] Argument
    list too long`` from Popen; the CLI argv must stay small."""
    monkeypatch.setenv("CORESMITH_LLM_PROVIDER", "claude")
    monkeypatch.setenv("CORESMITH_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(coresmith_llm._time_mod, "sleep", lambda *_a, **_k: None)
    model = ClaudeLLM(model="opus-4.8", timeout=10, claude_path="/bin/true")
    big = "S" * 200_000
    captured: dict = {}

    def fake_watchdog(cmd, user_prompt, project_root, resolved_model, t0):
        captured["cmd"] = list(cmd)
        return ('{"type":"result","result":"ok"}', "", 0, 0.1, False, False, {})

    with patch.object(model, "_run_cli_with_watchdog", side_effect=fake_watchdog):
        model._generate_via_claude_cli(big, "hello")
    cmd = captured["cmd"]
    assert "--system-prompt" not in cmd
    assert "--system-prompt-file" in cmd
    assert max(len(a.encode()) for a in cmd) < 4096
    prompt_file = cmd[cmd.index("--system-prompt-file") + 1]
    assert open(prompt_file, encoding="utf-8").read() == big
