"""Validation reporting counts requirements, not traceability references."""

from __future__ import annotations

import json

from orchestrator.langgraph.final_report import _req_note
from orchestrator.langgraph.pipeline_graph import _load_ers_validation_context


def _write_ers(tmp_path, ers):
    state = tmp_path / ".coresmith"
    state.mkdir()
    (state / "ers_spec.json").write_text(json.dumps({"ers": ers}))


def test_repeated_covers_ids_do_not_inflate_requirement_count(tmp_path):
    _write_ers(tmp_path, {
        "functional_requirements": [
            "FR-ONE: first obligation",
            "FR-TWO: second obligation",
        ],
        "validation_dv_requirements": [
            {
                "id": "VAL-001",
                "requirement": "Validate the first obligation",
                "covers": ["FR-ONE", "FR-ONE", "FR-TWO"],
                "threshold": "zero mismatches",
            },
            {
                "id": "VAL-002",
                "requirement": "Validate it another way",
                "covers": ["FR-ONE", "FR-TWO"],
            },
        ],
    })

    context, count = _load_ers_validation_context(str(tmp_path))

    assert json.loads(context)["ers"]["validation_dv_requirements"][0][
        "covers"
    ] == ["FR-ONE", "FR-ONE", "FR-TWO"]
    assert count == 4


def test_distinct_uncoded_requirements_remain_distinct(tmp_path):
    _write_ers(tmp_path, {
        "per_block_requirements": [{
            "block_name": "core",
            "requirements": [
                "Reset all state synchronously.",
                "Retire each instruction exactly once.",
                "Reset all state synchronously.",
            ],
        }],
        "verification_requirements": [
            "Check reset release.",
            "Check retirement order.",
        ],
    })

    _, count = _load_ers_validation_context(str(tmp_path))

    assert count == 4


def test_hyphenated_prose_is_not_mistaken_for_a_coded_id(tmp_path):
    _write_ers(tmp_path, {
        "functional_requirements": [
            "Single-outstanding requests must hold until accepted.",
            "Single-outstanding responses must hold until accepted.",
        ],
    })

    _, count = _load_ers_validation_context(str(tmp_path))

    assert count == 2


def test_uncoded_signal_requirements_preserve_case(tmp_path):
    _write_ers(tmp_path, {
        "functional_requirements": [
            "Drive pin A after reset.",
            "Drive pin a after reset.",
        ],
    })

    _, count = _load_ers_validation_context(str(tmp_path))

    assert count == 2


def test_explicit_dict_ids_are_authoritative_and_case_normalized(tmp_path):
    _write_ers(tmp_path, {
        "validation_dv_requirements": [
            {"id": "val-001", "requirement": "first wording"},
            {"id": "VAL-001", "requirement": "second wording"},
        ],
    })

    _, count = _load_ers_validation_context(str(tmp_path))

    assert count == 1


def test_duplicate_coded_requirement_is_counted_once(tmp_path):
    _write_ers(tmp_path, {
        "functional_requirements": ["FR-ONE: first wording"],
        "verification_requirements": ["FR-ONE: repeated wording"],
    })

    _, count = _load_ers_validation_context(str(tmp_path))

    assert count == 1


def test_final_report_labels_count_as_context_not_coverage():
    note = _req_note({"requirement_count": 41})
    assert note == (
        "41 unique ERS requirement records in context; not a coverage verdict"
    )
