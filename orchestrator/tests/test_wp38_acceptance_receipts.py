"""WP-38: the stream acceptance stage never passes a partial receipt, never guesses
a case setting, and carries candidate identity."""
from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

from orchestrator.langgraph import acceptance_dv as ad

STREAM_TOP = """
module s_top(
    input  wire        clk,
    input  wire        rst_n,
    input  wire        cfg_valid,
    input  wire [7:0]  cfg_addr,
    input  wire [31:0] cfg_data,
    input  wire        start,
    input  wire        in_valid,
    output wire        in_ready,
    input  wire [31:0] in_data,
    input  wire        in_last,
    output wire        out_valid,
    input  wire        out_ready,
    output wire [31:0] out_data,
    output wire        out_last
);
endmodule
"""


def _contract():
    return ad.classify_contract(ad.discover_ports(STREAM_TOP))


def test_words_are_packed_little_endian_not_truncated():
    m = ad.map_stimulus({"data": [0x12345678, 0x89ABCDEF], "cfg": {0: 1}, "word_bytes": 4}, _contract())
    assert m["payload"] == [0x78, 0x56, 0x34, 0x12, 0xEF, 0xCD, 0xAB, 0x89]
    assert m["in_word_bytes"] == 4 and m["out_word_bytes"] == 4


def test_declared_schedule_and_bounds_propagate():
    m = ad.map_stimulus({"data": [1], "cfg": [(0, 1)], "word_bytes": 1, "out_word_bytes": 2,
                         "max_out_words": 7, "seed": 42, "gap_pct": 0, "bp_pct": 100}, _contract())
    assert (m["out_word_bytes"], m["max_out_words"], m["seed"], m["gap_pct"], m["bp_pct"]) == (2, 7, 42, 0, 100)


def test_template_reads_the_declared_fields_and_bounds_output():
    src = ad.generate_stream_harness(_contract(), "s_top")
    for tok in ("max_out_words", "in_wb", "out_wb", "gap_pct", "bp_pct",
                "out_words >= max_out_words", "i * in_wb + b"):
        assert tok in src
    assert "0xffu)" in src and "(uint8_t)(top->out_data & 0xffu)" not in src


def test_read_results_flags_truncation(tmp_path):
    p = tmp_path / "out.bin"
    p.write_bytes(struct.pack("<III", 0, 5, 4) + b"ab")          # says 4 bytes, has 2
    r = ad._read_results(p, 2)
    assert len(r) == 1 and r[0]["truncated"] is True


def test_call_accept_passes_case_when_wanted():
    seen = {}

    def with_case(expected, observed, case=None):
        seen["case"] = case
        return True

    assert ad._call_accept(with_case, b"a", b"a", "c1", {"psnr_floor": 33}) is True
    assert seen["case"] == {"name": "c1", "stimulus": {"psnr_floor": 33}}
    assert ad._call_accept(lambda e, o: e == o, b"a", b"a", "c2", {}) is True


def _project(tmp_path, cases_src: str):
    root = tmp_path / "proj"
    (root / "inputs").mkdir(parents=True)
    (root / "rtl").mkdir()
    top = root / "rtl" / "s_top.v"
    top.write_text(STREAM_TOP)
    (root / "inputs" / "acceptance_stimulus.py").write_text(cases_src)
    (root / "inputs" / "golden.py").write_text("def run(stim):\n    return bytes(stim['data'])\n")
    (root / "inputs" / "accept.py").write_text("def accept(e, o):\n    return bytes(e) == bytes(o)\n")
    return root, top


def _env(monkeypatch, root):
    monkeypatch.setenv("CORESMITH_SOURCE_ROOT", str(root / "inputs" / "golden.py"))
    monkeypatch.setenv("CORESMITH_REFERENCE_ENTRY", "run")
    monkeypatch.setenv("CORESMITH_FUNCTIONAL_ACCEPTANCE", str(root / "inputs" / "accept.py"))
    monkeypatch.delenv("CORESMITH_ACCEPTANCE_STIMULUS", raising=False)
    monkeypatch.setattr(ad.shutil, "which", lambda name: "/usr/bin/" + name)


def test_incomplete_receipt_is_not_a_pass(tmp_path, monkeypatch):
    """Review round 2: two cases requested, one record returned -> passed=True."""
    root, top = _project(tmp_path,
                         "cases = [('one', {'data': b'\\x01', 'cfg': {0: 1}, 'word_bytes': 1}),"
                         " ('two', {'data': b'\\x02', 'cfg': {0: 1}, 'word_bytes': 1})]\n")
    _env(monkeypatch, root)
    monkeypatch.setattr(ad, "_build", lambda *a, **k: "/bin/true")

    def fake_run(argv, **kw):
        Path(argv[2]).write_bytes(struct.pack("<III", 0, 1, 1) + b"\x01")   # ONE record
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(ad.subprocess, "run", fake_run)
    res = ad.run_acceptance_dv(str(root), str(top), [])
    assert res["passed"] is False and res["skipped"] is False
    assert res["oracle_incomplete"] is True and res["kind"] == "oracle_incomplete"
    assert "1 complete case record(s) for 2 requested" in res["reason"]
    assert res["violations"][0]["criterion"] == "acceptance_dv_oracle_incomplete"


def test_build_failure_parks_not_skips(tmp_path, monkeypatch):
    root, top = _project(tmp_path, "cases = [('one', {'data': b'\\x01', 'cfg': {0: 1}, 'word_bytes': 1})]\n")
    _env(monkeypatch, root)
    monkeypatch.setattr(ad, "_build", lambda *a, **k: None)
    res = ad.run_acceptance_dv(str(root), str(top), [])
    assert res["oracle_incomplete"] and res["kind"] == "infrastructure_error" and not res["skipped"]


def test_undeclared_case_setting_is_an_adapter_defect(tmp_path, monkeypatch):
    root, top = _project(tmp_path, "cases = [('one', {'data': b'\\x01', 'cfg': {0: 1}})]\n")
    _env(monkeypatch, root)
    res = ad.run_acceptance_dv(str(root), str(top), [])
    assert res["oracle_incomplete"] and res["kind"] == "adapter_defect" and "word_bytes" in res["reason"]


def test_too_many_or_duplicate_cases_never_truncate(tmp_path, monkeypatch):
    root, top = _project(tmp_path, "cases = [('a', {'data': b'\\x01', 'cfg': {0: 1}, 'word_bytes': 1}),"
                                   " ('a', {'data': b'\\x02', 'cfg': {0: 1}, 'word_bytes': 1})]\n")
    _env(monkeypatch, root)
    res = ad.run_acceptance_dv(str(root), str(top), [])
    assert res["kind"] == "adapter_defect" and "duplicate" in res["reason"]
    monkeypatch.setenv("CORESMITH_ACCEPTANCE_MAX_CASES", "1")
    res = ad.run_acceptance_dv(str(root), str(top), [])
    assert res["kind"] == "adapter_defect" and "exceed" in res["reason"]


def test_park_payload_has_no_fix_tb_and_types_oracle_problems():
    from orchestrator.langgraph import pipeline_graph as pg
    src = open(pg.__file__, encoding="utf-8").read()
    assert '"supported_actions": (["retry", "abort"] if _acc_oracle' in src
    acc = src[src.index('"phase": "acceptance_dv",'):src.index('"reference_files"', src.index('"phase": "acceptance_dv",'))]
    code = "\n".join(ln for ln in acc.splitlines() if not ln.strip().startswith("#"))
    assert "fix_tb" not in code.replace("fix_tb is not offered", "")
    assert "ACCEPTANCE_ORACLE_INCOMPLETE" in src
