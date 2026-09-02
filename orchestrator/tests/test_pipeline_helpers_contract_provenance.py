# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""C5(c): EAGER contract-slice invalidation at the gap-resolver amendment site.

The gap resolver amends ``interface_contracts.json`` for ONE block, but an
amendment lands on an EDGE -- so the partner block's contract slice moves too.
The old code only logged a note ("partner blocks' recorded passes MAY be
invalidated ... on their next entry") and the lazy check it deferred to sat
BEHIND the gate-scoped reuse shortcut, so it never ran. Measured live: 10/12
specs and 9/12 models entering the composition gate had been generated against
a superseded contract.

``write_amended_contract`` now diffs every block's ``block_contract_sha1``
across the write and drops a disk-first ``contract_stale`` marker on each
block that actually moved.
"""
from __future__ import annotations

import json
from pathlib import Path

from orchestrator.langchain.agents.gap_resolver import apply_contract_amendments
from orchestrator.langgraph import pipeline_helpers as ph


def _doc(x_width: int = 8, y_width: int = 16) -> dict:
    """Two INDEPENDENT edges: (prod_x -> x) and (prod_y -> y)."""
    return {
        "defaults": {"packing": "lsb_first"},
        "contracts": [
            {"edge_id": "prod_x__to__x", "producer_block": "prod_x",
             "consumer_block": "x", "payload": {"width": x_width}},
            {"edge_id": "prod_y__to__y", "producer_block": "prod_y",
             "consumer_block": "y", "payload": {"width": y_width}},
        ],
    }


def _freeze(root: Path, doc: dict) -> None:
    cs = root / ".coresmith"
    cs.mkdir(parents=True, exist_ok=True)
    (cs / "interface_contracts.json").write_text(json.dumps(doc, indent=2))


def _marker(root: Path, block: str) -> Path:
    return root / ".coresmith" / "blocks" / block / ph.CONTRACT_STALE_MARKER


class TestContractBlockNames:
    def test_lists_both_sides_of_every_edge_sorted_and_deduped(self):
        doc = _doc()
        doc["contracts"].append({"edge_id": "x__to__y",
                                 "producer_block": "x",
                                 "consumer_block": "y"})
        assert ph.contract_block_names(doc) == ["prod_x", "prod_y", "x", "y"]

    def test_tolerates_a_missing_or_malformed_document(self):
        assert ph.contract_block_names({}) == []
        assert ph.contract_block_names({"contracts": ["not-a-dict"]}) == []


class TestRecordedContractSha1:
    def test_absent_sidecar_is_empty(self, tmp_path):
        assert ph.recorded_contract_sha1(
            tmp_path, "x", "uarch_spec_contract_sha1") == ""

    def test_reads_and_strips(self, tmp_path):
        bd = tmp_path / ".coresmith" / "blocks" / "x"
        bd.mkdir(parents=True)
        (bd / "uarch_spec_contract_sha1").write_text("  abc123\n")
        assert ph.recorded_contract_sha1(
            tmp_path, "x", "uarch_spec_contract_sha1") == "abc123"


class TestWriteAmendedContract:
    def test_marks_only_the_blocks_whose_slice_moved(self, tmp_path):
        _freeze(tmp_path, _doc())
        amended = _doc(x_width=9)          # only the prod_x -> x edge changes
        changed = ph.write_amended_contract(tmp_path, amended)

        assert changed == ["prod_x", "x"]
        assert _marker(tmp_path, "x").exists()
        assert _marker(tmp_path, "prod_x").exists()
        assert not _marker(tmp_path, "y").exists()
        assert not _marker(tmp_path, "prod_y").exists()

    def test_marker_records_the_old_and_new_slice_hashes(self, tmp_path):
        _freeze(tmp_path, _doc())
        before = ph.block_contract_sha1(str(tmp_path), "x")
        ph.write_amended_contract(tmp_path, _doc(x_width=9))
        after = ph.block_contract_sha1(str(tmp_path), "x")

        assert before != after
        assert _marker(tmp_path, "x").read_text().strip() == \
            f"{before} -> {after}"

    def test_persists_the_amended_document(self, tmp_path):
        _freeze(tmp_path, _doc())
        ph.write_amended_contract(tmp_path, _doc(x_width=9))
        live = json.loads(
            (tmp_path / ".coresmith" / "interface_contracts.json").read_text())
        assert live["contracts"][0]["payload"]["width"] == 9

    def test_a_no_op_write_marks_nothing(self, tmp_path):
        _freeze(tmp_path, _doc())
        assert ph.write_amended_contract(tmp_path, _doc()) == []
        assert not _marker(tmp_path, "x").exists()
        assert not _marker(tmp_path, "y").exists()

    def test_end_to_end_through_a_real_resolver_amendment(self, tmp_path):
        """The shape the gap resolver actually produces: an amendment keyed on
        one edge_id must invalidate exactly that edge's two blocks."""
        _freeze(tmp_path, _doc())
        doc = json.loads(
            (tmp_path / ".coresmith" / "interface_contracts.json").read_text())
        doc, applied = apply_contract_amendments(doc, [{
            "edge_id": "prod_x__to__x",
            "add_representations": {
                "enums": [{"name": "mode", "values": {"raw": 0, "packed": 1}}],
            },
            "append_note": "mode=packed uses 9-bit payload",
        }])
        assert applied, "fixture amendment must apply to a real edge"

        changed = ph.write_amended_contract(tmp_path, doc)
        assert changed == ["prod_x", "x"]
        assert _marker(tmp_path, "x").exists()
        assert not _marker(tmp_path, "y").exists()
