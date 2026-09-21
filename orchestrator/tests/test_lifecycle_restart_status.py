# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Persisted pending nodes remain resumable across process startup."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import TypedDict

import pytest
from langgraph.graph import END, START, StateGraph

from orchestrator.graph_lifecycle import (
    GraphLifecycle,
    _checkpoint_recovery_status,
)


def test_interrupt_has_priority_over_pending_next_node():
    state = SimpleNamespace(
        values={"phase": "drc"},
        next=("drc",),
        tasks=(SimpleNamespace(interrupts=(object(),)),),
    )

    assert _checkpoint_recovery_status(state) == "interrupted"


def test_pending_next_node_recovers_paused_not_done():
    state = SimpleNamespace(
        values={"phase": "pnr"}, next=("drc",), tasks=(),
    )

    assert _checkpoint_recovery_status(state) == "paused"


def test_only_terminal_checkpoint_recovers_done():
    terminal = SimpleNamespace(values={"phase": "complete"}, next=(), tasks=())
    empty = SimpleNamespace(values={}, next=(), tasks=())

    assert _checkpoint_recovery_status(terminal) == "done"
    assert _checkpoint_recovery_status(empty) is None


class _State(TypedDict):
    count: int


@pytest.mark.asyncio
async def test_real_langgraph_pending_checkpoint_recovers_paused(
    tmp_path, monkeypatch,
):
    """A process restart after node A must retain scheduled node B."""

    module_name = "_test_pending_restart_graph"
    module = ModuleType(module_name)

    def build_graph(checkpointer=None):
        graph = StateGraph(_State)
        graph.add_node("first", lambda state: {"count": state["count"] + 1})
        graph.add_node("second", lambda state: {"count": state["count"] + 1})
        graph.add_edge(START, "first")
        graph.add_edge("first", "second")
        graph.add_edge("second", END)
        return graph.compile(
            checkpointer=checkpointer, interrupt_after=["first"]
        )

    module.build_graph = build_graph
    monkeypatch.setitem(sys.modules, module_name, module)
    db = str(tmp_path / "checkpoint.db")
    config = {"configurable": {"thread_id": "pending-test"}}

    original = GraphLifecycle(
        "pending-test", db, module_name, "build_graph", str(tmp_path)
    )
    await original.ensure_graph()
    await original.graph.ainvoke({"count": 0}, config)
    before = await original.graph.aget_state(config)
    assert before.next == ("second",)
    await original.cleanup()

    restarted = GraphLifecycle(
        "pending-test", db, module_name, "build_graph", str(tmp_path)
    )
    try:
        await restarted.ensure_graph()
        after = await restarted.graph.aget_state(config)
        assert after.next == ("second",)
        assert restarted.status == "paused"
    finally:
        await restarted.cleanup()
