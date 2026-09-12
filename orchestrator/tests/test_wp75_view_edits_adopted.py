# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""WP-75: an on-disk edit of a registry view (block diagram, block specs,
interface contracts) is imported into the project database before the views
are regenerated, so a chip-lead or operator fix is never silently reverted."""
from __future__ import annotations

import json
import os

from orchestrator.state_store.project_db import open_project


def _edge(consumer_port: str) -> dict:
    return {
        "edge_id": "regmap_buffers__irq_out__to__user_project_wrapper__irq_in",
        "producer_block": "regmap_buffers", "producer_port": "irq_out",
        "consumer_block": "user_project_wrapper", "consumer_port": consumer_port,
        "handshake_protocol": "valid_only", "data_width_bits": 1,
    }


def test_edited_contract_view_becomes_canonical(tmp_path):
    db = open_project(tmp_path)
    db.import_contracts({"contracts": [_edge("irq_in (irq)")]})
    view = tmp_path / ".coresmith" / "interface_contracts.json"
    assert json.loads(view.read_text())["contracts"][0]["consumer_port"] == "irq_in (irq)"

    # the chip lead fixes the illegal name on disk (the view is 0444: replace it)
    fixed = json.loads(view.read_text())
    fixed["contracts"][0]["consumer_port"] = "irq_in"
    os.chmod(view, 0o644)
    view.write_text(json.dumps(fixed, indent=2))

    # any later database write re-exports the views: the edit must survive
    db.set_setting("unrelated", "1")
    db.export_views()
    assert json.loads(view.read_text())["contracts"][0]["consumer_port"] == "irq_in"
    assert db.contracts()["contracts"][0]["consumer_port"] == "irq_in"
    assert db.get_setting("view_adopted:interface_contracts.json")


def test_unparsable_edit_is_regenerated_from_the_database(tmp_path):
    db = open_project(tmp_path)
    db.import_contracts({"contracts": [_edge("irq_in")]})
    view = tmp_path / ".coresmith" / "interface_contracts.json"
    os.chmod(view, 0o644)
    view.write_text("{not json")
    db.export_views()
    assert json.loads(view.read_text())["contracts"][0]["consumer_port"] == "irq_in"


def test_untouched_views_round_trip(tmp_path):
    db = open_project(tmp_path)
    db.import_block_diagram({"blocks": [{"name": "leaf", "description": "d"}], "connections": []})
    db.import_contracts({"contracts": [_edge("irq_in")]})
    before = (tmp_path / ".coresmith" / "block_diagram.json").read_text()
    db.export_views()
    assert (tmp_path / ".coresmith" / "block_diagram.json").read_text() == before
    assert db.get_setting("view_adopted:block_diagram.json") is None


def test_edited_block_diagram_view_is_adopted(tmp_path):
    db = open_project(tmp_path)
    db.import_block_diagram({"blocks": [{"name": "leaf", "description": "d"}], "connections": []})
    view = tmp_path / ".coresmith" / "block_diagram.json"
    doc = json.loads(view.read_text())
    doc["blocks"][0]["description"] = "edited on disk"
    os.chmod(view, 0o644)
    view.write_text(json.dumps(doc))
    db.export_views()
    assert db.block_diagram()["blocks"][0]["description"] == "edited on disk"
    assert json.loads(view.read_text())["blocks"][0]["description"] == "edited on disk"
