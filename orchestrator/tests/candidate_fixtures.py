"""Tool-free adoption fixtures for tests of consumers and graph routing."""
from unittest.mock import patch

from orchestrator.harness.top_module import write_candidate_receipt


def adopt(root, top, blocks=None, name="chip_top"):
    expected = set((blocks or {}).keys())
    with patch("orchestrator.harness.hierarchy.elaborate_hierarchy", return_value=expected):
        receipt = write_candidate_receipt(root, name, str(top), blocks or {})
    from orchestrator.state_store.trust import capture_run_baseline
    capture_run_baseline(root)
    return receipt
