"""WP-17/WP-49: the integration design name comes from the recorded candidate, never a file guess."""
from __future__ import annotations

import json

from orchestrator.harness.top_module import write_candidate_receipt
from orchestrator.langgraph.integration_helpers import (
    _existing_top_module,
    load_architecture_connections,
)

TOP = "video_enc_core_top"


def _top_file(tmp_path, name="chip.v", body=None):
    d = tmp_path / "rtl" / "integration"
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(body or ("module helper(); endmodule\nmodule " + TOP + "(); helper h(); endmodule\n"))
    return p


def test_recorded_candidate_names_the_design(tmp_path):
    p = _top_file(tmp_path)
    write_candidate_receipt(tmp_path, TOP, str(p), {})
    assert _existing_top_module(tmp_path / "rtl" / "integration") == TOP
    assert _existing_top_module(tmp_path / "rtl" / "integration", "other") == TOP


def test_no_receipt_means_no_name(tmp_path):
    _top_file(tmp_path)          # a file alone is not evidence of the top
    assert _existing_top_module(tmp_path / "rtl" / "integration") == ""
    assert _existing_top_module(tmp_path / "rtl" / "nothing") == ""


def test_load_architecture_connections_uses_the_receipt(tmp_path):
    cs = tmp_path / ".coresmith"
    cs.mkdir()
    (cs / "architecture_state.json").write_text(json.dumps({
        "block_diagram": {"connections": [{"from_block": "a", "to_block": "b"}]},
        "prd_spec": {"prd": {"title": "PRD - Video Enc Core"}},
    }))
    conns, name = load_architecture_connections(str(tmp_path))
    assert conns and name == "video_enc_core_top"
    p = _top_file(tmp_path, "other_name.v",
                  "module rom_arbiter(input clk); endmodule\n"
                  "/* c */ module " + TOP + "(input clk); rom_arbiter u(.clk(clk)); endmodule\n")
    conns, name = load_architecture_connections(str(tmp_path))
    assert name == "video_enc_core_top"       # no receipt: the PRD name, not the file
    write_candidate_receipt(tmp_path, TOP, str(p), {})
    conns, name = load_architecture_connections(str(tmp_path))
    assert name == TOP
