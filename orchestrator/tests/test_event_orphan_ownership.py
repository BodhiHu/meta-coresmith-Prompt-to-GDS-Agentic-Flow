"""Orphan recovery must not close nodes owned by a live daemon/process."""

from __future__ import annotations

import json
import os

from orchestrator.graph_lifecycle import GraphLifecycle
from orchestrator.langgraph.event_stream import write_graph_event


def _lifecycle(root) -> GraphLifecycle:
    return GraphLifecycle("test", str(root / "checkpoint.db"), "unused", "unused", str(root))


def _events(root):
    path = root / ".coresmith" / "pipeline_events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_foreign_cli_does_not_close_live_daemon_node(tmp_path, monkeypatch):
    write_graph_event(str(tmp_path), "Run PnR", "graph_node_enter", {})
    daemon = tmp_path / ".coresmith" / "daemon.json"
    daemon.write_text(json.dumps({"pid": 4242}))
    monkeypatch.setattr(os, "getpid", lambda: 5252)
    monkeypatch.setattr(GraphLifecycle, "_pid_is_alive", staticmethod(lambda pid: int(pid) == 4242))

    _lifecycle(tmp_path)._close_orphaned_events()

    assert [event["event"] for event in _events(tmp_path)] == ["graph_node_enter"]


def test_true_restart_closes_node_owned_by_dead_process(tmp_path, monkeypatch):
    event_path = tmp_path / ".coresmith" / "pipeline_events.jsonl"
    event_path.parent.mkdir()
    event_path.write_text(json.dumps({
        "event": "graph_node_enter", "node": "Run PnR", "pid": 1111,
    }) + "\n")
    (tmp_path / ".coresmith" / "daemon.json").write_text(
        json.dumps({"pid": 2222})
    )
    monkeypatch.setattr(os, "getpid", lambda: 2222)
    monkeypatch.setattr(GraphLifecycle, "_pid_is_alive", staticmethod(lambda pid: int(pid) == 2222))

    _lifecycle(tmp_path)._close_orphaned_events()

    events = _events(tmp_path)
    assert [event["event"] for event in events] == [
        "graph_node_enter", "graph_node_exit",
    ]
    assert events[-1]["server_restart"] is True


def test_same_live_process_node_is_not_an_orphan(tmp_path, monkeypatch):
    event_path = tmp_path / ".coresmith" / "pipeline_events.jsonl"
    event_path.parent.mkdir()
    event_path.write_text(json.dumps({
        "event": "graph_node_enter", "node": "Run PnR", "pid": 3333,
    }) + "\n")
    monkeypatch.setattr(os, "getpid", lambda: 3333)
    monkeypatch.setattr(GraphLifecycle, "_pid_is_alive", staticmethod(lambda pid: int(pid) == 3333))

    _lifecycle(tmp_path)._close_orphaned_events()

    assert [event["event"] for event in _events(tmp_path)] == ["graph_node_enter"]


def test_legacy_unowned_enter_still_recovers_without_live_daemon(tmp_path):
    event_path = tmp_path / ".coresmith" / "pipeline_events.jsonl"
    event_path.parent.mkdir()
    event_path.write_text(json.dumps({
        "event": "graph_node_enter", "node": "Generate RTL",
    }) + "\n")

    _lifecycle(tmp_path)._close_orphaned_events()

    assert [event["event"] for event in _events(tmp_path)] == [
        "graph_node_enter", "graph_node_exit",
    ]
