import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("done", [False, True])
async def test_whole_chip_progress_does_not_count_frontend_leaves(monkeypatch, done):
    import orchestrator.mcp_server as mcp

    snapshot = SimpleNamespace(values={
        "block_queue": [{"name": "core"}, {"name": "spi"}],
        "integration_top_path": "rtl/chip_top.v", "current_block_index": 0,
        "completed_blocks": [{"name": "chip_top", "success": True}] if done else [],
        "backend_done": done,
    }, tasks=[], next=[], config={})
    backend = SimpleNamespace(ensure_graph=AsyncMock(), thread_id="test", status="done",
                              error_message="", task=None,
                              graph=SimpleNamespace(aget_state=AsyncMock(return_value=snapshot)))
    monkeypatch.setattr(mcp, "_backend", backend)
    monkeypatch.setattr(mcp, "_get_diagnostics", lambda **kw: {})
    result = json.loads(await mcp.get_backend_state())
    assert result["total_blocks"] == 1
    assert result["remaining_count"] == (0 if done else 1)
