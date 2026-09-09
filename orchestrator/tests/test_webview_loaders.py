"""Tests for the run-review loaders behind orchestrator/vscode-ext/serve.py.

Two layers:

* Synthetic fixtures (always run): parsers (Yosys stat, OpenSTA, cocotb,
  Verilator), the event -> rounds/segments index, LLM call <-> codex session
  correlation, parallel-block isolation, path safety, text paging / diff, and
  the HTTP routes.
* Staged real runs (skipped when the directory is absent): the two runs under
  ``/home/ubuntu/remote/webview-work/runs`` are walked end to end so every
  block's trajectory / review payload builds without error and the numbers the
  UI shows are present.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import time
from http.server import HTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

_EXT_DIR = Path(__file__).resolve().parents[1] / "vscode-ext"
_LOADERS_PATH = _EXT_DIR / "webview_loaders.py"
_SERVE_PATH = _EXT_DIR / "serve.py"
STAGED = Path(os.environ.get("CORESMITH_STAGED_RUNS", "/home/ubuntu/remote/webview-work/runs"))

if not _LOADERS_PATH.exists():
    pytest.skip("orchestrator/vscode-ext/webview_loaders.py absent", allow_module_level=True)


def _import_loaders():
    spec = importlib.util.spec_from_file_location("webview_loaders_under_test", str(_LOADERS_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def L():
    return _import_loaders()


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #

YOSYS_TABULAR = """
19. Printing statistics.

=== output_byte_fifo ===

        +----------Local Count, excluding submodules.
        |        +-Local Area, excluding submodules.
        |        |
      280        - wires
      420        - wire bits
       29        - public wires
      169        - public wire bits
       16        - ports
       87        - port bits
      326 2.68E+03 cells
        1        -   cs_sram_1rw1r
       14   70.067   sky130_fd_sc_hd__a21oi_1
       43  860.826   sky130_fd_sc_hd__dfxtp_1
        2    20.0    sky130_fd_sc_hd__edfxtp_1
       21  210.202   sky130_fd_sc_hd__mux2i_1
   Area for cell type \\cs_sram_1rw1r is unknown!
   Chip area for module '\\output_byte_fifo': 2678.819200
     of which used for sequential elements: 860.825600 (32.13%)

20. Executing Verilog backend.
"""

YOSYS_LEGACY = """
=== adder ===

   Number of wires:                 12
   Number of wire bits:             40
   Number of cells:                 30
     $_AND_                          5
     $_DFF_P_                        8
     $_XOR_                         17

   Chip area for module '\\adder': 123.4
"""

STA_TEXT = """
Startpoint: fifo_rptr_reg[0] (rising edge-triggered flip-flop clocked by clk)
Endpoint: dout_reg[3] (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max

  Delay    Time   Description
---------------------------------------------------------
   0.00    0.00   clock clk (rise edge)
   3.20    3.20   data arrival time
  20.00   19.60   data required time
---------------------------------------------------------
  16.40   slack (MET)


Startpoint: mul_a_reg (rising edge-triggered flip-flop clocked by clk)
Endpoint: acc_reg (rising edge-triggered flip-flop clocked by clk)
Path Group: clk
Path Type: max
  23.10   23.10   data arrival time
  19.60   19.60   data required time
  -3.50   slack (VIOLATED)

wns max -3.50
tns max -12.25
"""


def test_parse_yosys_stat_tabular(L):
    st = L.parse_yosys_stat(YOSYS_TABULAR)
    assert st["cells"] == 326
    assert st["wires"] == 280 and st["ports"] == 16
    assert st["chip_area_um2"] == pytest.approx(2678.8192)
    assert st["sequential_area_um2"] == pytest.approx(860.8256)
    assert st["sequential_pct"] == pytest.approx(32.13)
    assert st["ff"] == 45  # dfxtp + edfxtp
    assert st["macros"] == [{"name": "cs_sram_1rw1r", "count": 1}]
    assert st["unknown_area_types"] == ["cs_sram_1rw1r"]
    assert st["module"] == "output_byte_fifo"
    names = [c["name"] for c in st["cell_types"]]
    assert "sky130_fd_sc_hd__dfxtp_1" in names and "cs_sram_1rw1r" in names


def test_parse_yosys_stat_legacy_format(L):
    st = L.parse_yosys_stat(YOSYS_LEGACY)
    assert st["cells"] == 30 and st["wires"] == 12
    assert st["ff"] == 8
    assert st["chip_area_um2"] == pytest.approx(123.4)
    assert not st["macros"]


def test_parse_yosys_stat_uses_last_section(L):
    text = YOSYS_TABULAR.replace("326 2.68E+03 cells", "999 1.0E+03 cells") + YOSYS_TABULAR
    assert L.parse_yosys_stat(text)["cells"] == 326


def test_parse_sta_report_paths_and_totals(L):
    r = L.parse_sta_report(STA_TEXT)
    assert r["wns_ns"] == pytest.approx(-3.5)
    assert r["tns_ns"] == pytest.approx(-12.25)
    assert len(r["paths"]) == 2
    bad = [p for p in r["paths"] if p["violated"]][0]
    assert bad["startpoint"].startswith("mul_a_reg") and bad["endpoint"].startswith("acc_reg")
    assert bad["slack_ns"] == pytest.approx(-3.5)
    assert bad["arrival_ns"] == pytest.approx(23.10)
    good = [p for p in r["paths"] if not p["violated"]][0]
    assert good["slack_ns"] == pytest.approx(16.40) and good["path_group"] == "clk"


def test_parse_sta_report_legacy_and_engine_prints(L):
    assert L.parse_sta_report("wns -1.25\ntns -4.0\n")["wns_ns"] == pytest.approx(-1.25)
    assert L.parse_sta_report("CORESMITH_WNS 14.33\n")["wns_ns"] == pytest.approx(14.33)
    assert L.parse_sta_report("worst slack max 2.5\n")["wns_ns"] == pytest.approx(2.5)
    assert L.parse_sta_report("")["paths"] == []


def test_parse_cocotb_results(L):
    log = """
** test_x.test_reset            PASS         230.00           0.00     194063.55  **
** test_x.test_known_vector     FAIL        2670.00           0.01     453704.64  **
** TESTS=2 PASS=1 FAIL=1 SKIP=0                     2900.00           0.01     339336.78  **
    AssertionError: first neighbour request was C40, expected C1
"""
    r = L.parse_cocotb_results(log)
    assert (r["tests_total"], r["tests_passed"], r["tests_failed"]) == (2, 1, 1)
    assert [t["status"] for t in r["tests"]] == ["PASS", "FAIL"]
    assert r["first_assertion"].startswith("AssertionError")


def test_parse_verilator_lint_and_step_header(L):
    log = """=== LINT LOG ===
Timestamp: 2026-09-05T16:44:13
Block: cavlc
Attempt: 2
Command: verilator --lint-only x.v
Return code: 0

=== STDOUT ===
%Warning-DECLFILENAME: x.v:41:8: Filename mismatch
%Warning-WIDTH: x.v:50:1: Operator ASSIGN expects 8 bits
%Error: x.v:60:1: syntax error
"""
    lint = L.parse_verilator_lint(log)
    assert lint["warning_count"] == 2 and lint["error_count"] == 1
    assert lint["warning_kinds"] == ["DECLFILENAME", "WIDTH"]
    hdr = L.parse_step_log_header(log)
    assert hdr["kind"] == "lint" and hdr["attempt"] == 2 and hdr["return_code"] == 0
    assert hdr["command"].startswith("verilator")
    assert L.parse_step_log_header("Return code: N/A (exception)\n")["return_code"] is None


# --------------------------------------------------------------------------- #
# Synthetic run directory
# --------------------------------------------------------------------------- #

T0 = 1_700_000_000.0


def _ev(ts, event, node=None, block=None, **kw):
    e = {"ts": ts, "iso": "2026-01-01T00:00:00", "event": event}
    if node:
        e["node"] = node
    if block:
        e["block"] = block
    e.update(kw)
    return e


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _codex_session(pid, wall_start, thread_id, end_ts, items):
    """Emit a codex_turns.jsonl session the way the engine flushes it."""
    rows = [{"ts": end_ts, "wall_start": wall_start, "pid": pid, "event": {"type": "thread.started", "thread_id": thread_id}},
            {"ts": end_ts, "wall_start": wall_start, "pid": pid, "event": {"type": "turn.started"}}]
    for i, item in enumerate(items):
        item = dict(item, id=f"item_{i}")
        rows.append({"ts": end_ts, "wall_start": wall_start, "pid": pid, "event": {"type": "item.started", "item": dict(item, status="in_progress")}})
        rows.append({"ts": end_ts, "wall_start": wall_start, "pid": pid, "event": {"type": "item.completed", "item": dict(item, status="completed")}})
    rows.append({"ts": end_ts, "wall_start": wall_start, "pid": pid,
                 "event": {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 10}}})
    return rows


@pytest.fixture
def synthetic_run(tmp_path):
    """Two blocks in one tier running in parallel; block A goes through two
    rounds (integration review -> revise) with a failed synth + retry."""
    cs = tmp_path / ".coresmith"
    cs.mkdir()
    A, B = "alpha_engine", "beta_fifo"
    events = [
        _ev(T0, "graph_node_enter", "Init Tier", tier="1", block_count=2),
        _ev(T0 + 0.1, "graph_node_exit", "Init Tier", tier="1", status="ok"),
        # round 1, both blocks start together
        _ev(T0 + 1, "graph_node_enter", "Init Block", A),
        _ev(T0 + 1.1, "graph_node_exit", "Init Block", A, status="ok"),
        _ev(T0 + 1, "graph_node_enter", "Init Block", B),
        _ev(T0 + 1.1, "graph_node_exit", "Init Block", B, status="ok"),
        _ev(T0 + 2, "graph_node_enter", "Generate RTL", A, attempt=1),
        _ev(T0 + 2, "graph_node_enter", "Generate RTL", B, attempt=1),
        _ev(T0 + 2.5, "llm_start", "LLM", run_name="Generate Verilog [Alpha Engine]"),
        _ev(T0 + 2.5, "llm_start", "LLM", run_name="Generate Verilog [Beta Fifo]"),
        _ev(T0 + 100, "llm_end", "LLM", run_name="Generate Verilog [Alpha Engine]"),
        _ev(T0 + 100, "graph_node_exit", "Generate RTL", A, attempt=1, lint_clean=True, status="ok"),
        _ev(T0 + 120, "llm_end", "LLM", run_name="Generate Verilog [Beta Fifo]"),
        _ev(T0 + 120, "graph_node_exit", "Generate RTL", B, attempt=1, lint_clean=True, status="ok"),
        _ev(T0 + 121, "graph_node_enter", "Synthesize", A),
        _ev(T0 + 130, "graph_node_exit", "Synthesize", A, success=False, gate_count=0, status="error"),
        _ev(T0 + 130, "graph_node_enter", "Diagnose Failure", A, phase="synth"),
        _ev(T0 + 200, "graph_node_exit", "Diagnose Failure", A, category="LOGIC_ERROR", confidence=0.9,
            suggested_fix="register the mux", diagnosis_preview="path too long", status="ok"),
        _ev(T0 + 200, "graph_node_exit", "Route Decision", A, decision="retry_rtl", attempt=1, status="ok"),
        _ev(T0 + 201, "graph_node_enter", "Generate RTL", A, attempt=2),
        _ev(T0 + 201, "lint_retry_bypass", "Generate RTL", A, attempt=2, backup="/x/rtl_backup_attempt2.v"),
        _ev(T0 + 300, "graph_node_exit", "Generate RTL", A, attempt=2, lint_clean=True, status="ok"),
        _ev(T0 + 301, "graph_node_enter", "Synthesize", A),
        _ev(T0 + 310, "graph_node_exit", "Synthesize", A, success=True, gate_count=1234, chip_area_um2=99.5, status="ok"),
        _ev(T0 + 311, "gate_result", "Gate Sim", A, name="gate_level_sim", kind="gate_sim", status="not_run", passed=False, reason="scope=chip_top"),
        _ev(T0 + 312, "graph_node_exit", "Block Done", A, success=True, status="ok"),
        _ev(T0 + 122, "graph_node_enter", "Synthesize", B),
        _ev(T0 + 125, "graph_node_exit", "Synthesize", B, success=True, gate_count=50, status="ok"),
        _ev(T0 + 126, "graph_node_exit", "Block Done", B, success=True, status="ok"),
        # tier-level review -> revise -> round 2 for A only
        _ev(T0 + 320, "graph_node_enter", "Integration Review", tier="1"),
        _ev(T0 + 380, "chip_lead_decision", "Chip Lead", type="uarch_integration_review", action="revise", decision_index=1),
        _ev(T0 + 380, "graph_node_exit", "Integration Review", action="revise", issues_found=1, status="ok"),
        _ev(T0 + 381, "graph_node_enter", "Init Block", A),
        _ev(T0 + 381.1, "graph_node_exit", "Init Block", A, status="ok"),
        _ev(T0 + 382, "graph_node_enter", "Ask Human", A, attempt=1),
        _ev(T0 + 400, "graph_node_exit", "Ask Human", A, action="fix_rtl", status="ok"),
        _ev(T0 + 401, "graph_node_enter", "Generate RTL", A, attempt=1),
        _ev(T0 + 450, "graph_node_exit", "Generate RTL", A, attempt=1, lint_clean=True, status="ok"),
        _ev(T0 + 451, "graph_node_exit", "Block Done", A, success=True, status="ok"),
    ]
    _write_jsonl(cs / "pipeline_events.jsonl", events)
    calls = [
        {"ts": T0 + 100, "iso": "x", "model": "m", "provider": "codex_cli", "run_name": "Generate Verilog [Alpha Engine]",
         "call_index": 1, "graph": "pipeline", "duration_s": 97.5, "timeout": 900, "timed_out": False, "error": "",
         "system_prompt": "SYS A", "user_prompt": "USER A", "response": "RESP A",
         "usage": {"input_tokens": 10, "cached_input_tokens": 5, "output_tokens": 3, "reasoning_output_tokens": 1, "session_id": "thread-A1"}},
        {"ts": T0 + 120, "iso": "x", "model": "m", "provider": "codex_cli", "run_name": "Generate Verilog [Beta Fifo]",
         "call_index": 2, "graph": "pipeline", "duration_s": 117.5, "timeout": 900, "timed_out": False, "error": "",
         "system_prompt": "SYS B", "user_prompt": "USER B", "response": "RESP B",
         "usage": {"input_tokens": 20, "cached_input_tokens": 0, "output_tokens": 4, "reasoning_output_tokens": 0, "session_id": "thread-B1"}},
        {"ts": T0 + 200, "iso": "x", "model": "m", "provider": "codex_cli", "run_name": "Analyze Failure [Alpha Engine]",
         "call_index": 3, "graph": "pipeline", "duration_s": 69.0, "timeout": 900, "timed_out": True, "error": "",
         "system_prompt": "SYS D", "user_prompt": "USER D", "response": "", "usage": {}},
        {"ts": T0 + 300, "iso": "x", "model": "m", "provider": "codex_cli", "run_name": "Generate Verilog [Alpha Engine] - Retry #1",
         "call_index": 4, "graph": "pipeline", "duration_s": 98.0, "timeout": 900, "timed_out": False, "error": "",
         "system_prompt": "SYS A2", "user_prompt": "USER A2", "response": "RESP A2",
         "usage": {"input_tokens": 10, "output_tokens": 3, "session_id": "thread-A2"}},
        {"ts": T0 + 379, "iso": "x", "model": "m", "provider": "codex_cli", "run_name": "Chip Lead [uarch_integration_review]",
         "call_index": 5, "graph": "pipeline", "duration_s": 58.0, "timeout": 900, "timed_out": False, "error": "",
         "system_prompt": "SYS L", "user_prompt": "USER L", "response": "revise",
         "usage": {"input_tokens": 10, "output_tokens": 3, "session_id": "thread-L"}},
    ]
    _write_jsonl(cs / "llm_calls.jsonl", calls)
    big_output = "x" * 20000
    turns = []
    turns += _codex_session(11, T0 + 2.5, "thread-A1", T0 + 100, [
        {"type": "agent_message", "text": "I will write alpha"},
        {"type": "command_execution", "command": "/bin/bash -lc \"ls rtl\"", "aggregated_output": "alpha_engine.v\n", "exit_code": 0},
        {"type": "file_change", "changes": [{"path": "/orig/root/rtl/alpha_engine.v", "kind": "add"}]},
        {"type": "command_execution", "command": "/bin/bash -lc \"verilator --lint-only rtl/alpha_engine.v\"", "aggregated_output": big_output, "exit_code": 1},
    ])
    turns += _codex_session(12, T0 + 2.5, "thread-B1", T0 + 120, [
        {"type": "agent_message", "text": "I will write beta"},
        {"type": "file_change", "changes": [{"path": "/orig/root/rtl/beta_fifo.v", "kind": "add"}]},
    ])
    # The timed-out diagnose call has a session without a thread id match
    # (no usage): it must be matched by wall_start proximity.
    turns += _codex_session(13, T0 + 131, "thread-D-unlinked", T0 + 200, [
        {"type": "agent_message", "text": "diagnosing"},
    ])
    turns += _codex_session(14, T0 + 202, "thread-A2", T0 + 300, [
        {"type": "file_change", "changes": [{"path": "/orig/root/rtl/alpha_engine.v", "kind": "update"}]},
    ])
    turns += _codex_session(15, T0 + 321, "thread-L", T0 + 379, [
        {"type": "agent_message", "text": "revise"},
    ])
    _write_jsonl(cs / "codex_turns.jsonl", turns)
    _write_jsonl(cs / "chip_lead" / "decisions.jsonl", [
        {"interrupt_type": "uarch_integration_review", "block_name": "", "action": "revise",
         "reasoning": "alpha_engine names its ports wrong"},
    ])
    (cs / "daemon.json").write_text(json.dumps({"project_root": "/orig/root", "pid": 1, "port": 1}))
    # Files referenced by the transcript + a step log for the retry.
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "alpha_engine.v").write_text("module alpha_engine(input clk);\nendmodule\n")
    (tmp_path / "rtl" / "beta_fifo.v").write_text("module beta_fifo(input clk);\nendmodule\n")
    blocks_dir = cs / "blocks" / A
    blocks_dir.mkdir(parents=True)
    (blocks_dir / "rtl_backup_attempt2.v").write_text("module alpha_engine(input clk);\n// old\nendmodule\n")
    (blocks_dir / "best_result.json").write_text(json.dumps({"sim_passed": True, "attempt": 2, "tests_passed": 3, "tests_total": 3,
                                                             "coverage": {"applicable": True, "pct": 91.0, "floor": 70.0, "passed": True}}))
    logs = cs / "step_logs" / A
    logs.mkdir(parents=True)
    synth_log = logs / "synthesize_attempt2.log"
    synth_log.write_text("=== SYNTHESIZE LOG ===\nTimestamp: t\nBlock: alpha_engine\nAttempt: 2\nCommand: yosys -s x.ys\nReturn code: 0\n\n=== STDOUT ===\n" + YOSYS_TABULAR)
    os.utime(synth_log, (T0 + 309, T0 + 309))
    sim_log = logs / "simulate_attempt2.log"
    sim_log.write_text("=== SIMULATE LOG ===\nBlock: alpha_engine\nAttempt: 2\nCommand: make\nReturn code: 0\n\n** TESTS=3 PASS=3 FAIL=0 SKIP=0   1.0 1.0 1.0 **\n")
    os.utime(sim_log, (T0 + 299, T0 + 299))
    # Block metadata via the JSON views (no sqlite in this fixture).
    (cs / "block_diagram.json").write_text(json.dumps({"blocks": [
        {"name": A, "tier": 1, "subsystem": "core", "description": "alpha", "rtl_target": "rtl/alpha_engine.v",
         "estimated_gates": 1000, "flip_flop_budget": 100, "area_budget_um2": 5000.0},
        {"name": B, "tier": 1, "subsystem": "mem", "description": "beta", "rtl_target": "rtl/beta_fifo.v"},
    ], "connections": []}))
    syn = tmp_path / "syn" / "output" / A
    syn.mkdir(parents=True)
    (syn / f"{A}_report.txt").write_text(YOSYS_TABULAR)
    (syn / f"{A}.sdc").write_text("create_clock -name clk -period 20.0 [get_ports clk]\n")
    return tmp_path


def test_index_rounds_segments_and_attempts(L, synthetic_run):
    idx = L.get_index(synthetic_run)
    assert idx.block_names() == ["alpha_engine", "beta_fifo"]
    rounds = idx.rounds["alpha_engine"]
    assert [r["round"] for r in rounds] == [1, 2]
    assert rounds[0]["outcome"] == "passed" and rounds[0]["decision"] == "retry_rtl"
    nodes_r1 = [idx.seg(s)["node"] for s in rounds[0]["segments"]]
    assert nodes_r1[:3] == ["Init Block", "Generate RTL", "Synthesize"]
    assert "Gate Sim" in nodes_r1  # virtual segment from the gate_result event
    gate = [idx.seg(s) for s in rounds[0]["segments"] if idx.seg(s)["node"] == "Gate Sim"][0]
    assert gate["status"] == "skipped" and gate["round"] == 1
    rtl_attempts = [idx.seg(s)["attempt"] for s in rounds[0]["segments"] if idx.seg(s)["node"] == "Generate RTL"]
    assert rtl_attempts == [1, 2]
    st = idx.block_status("alpha_engine")
    assert st["status"] == "passed" and st["rounds"] == 2 and st["rtl_attempts"] == 2
    assert idx.block_status("beta_fifo")["rounds"] == 1
    # Exit-only nodes become instantaneous segments.
    route = [s for s in idx.segments if s["node"] == "Route Decision"][0]
    assert route["instant"] and route["exit"]["decision"] == "retry_rtl"


def test_parallel_blocks_are_not_interleaved(L, synthetic_run):
    idx = L.get_index(synthetic_run)
    by_name = {c["run_name"]: c for c in idx.calls}
    a = by_name["Generate Verilog [Alpha Engine]"]
    b = by_name["Generate Verilog [Beta Fifo]"]
    assert a["block"] == "alpha_engine" and a["node"] == "Generate RTL" and a["attempt"] == 1
    assert b["block"] == "beta_fifo" and b["node"] == "Generate RTL"
    # Both windows overlap in time; each call lands on its own block's segment.
    assert a["seg_id"] != b["seg_id"]
    assert idx.seg(a["seg_id"])["block"] == "alpha_engine"
    assert idx.seg(b["seg_id"])["block"] == "beta_fifo"
    retry = by_name["Generate Verilog [Alpha Engine] - Retry #1"]
    assert retry["attempt"] == 2 and retry["round"] == 1
    lead = by_name["Chip Lead [uarch_integration_review]"]
    assert lead["block"] is None and lead["node"] == "Integration Review"


def test_codex_sessions_joined_by_thread_id_and_by_time(L, synthetic_run):
    idx = L.get_index(synthetic_run)
    by_name = {c["run_name"]: c for c in idx.calls}
    a = by_name["Generate Verilog [Alpha Engine]"]
    assert a["session_id"] == "thread-A1" and a["n_commands"] == 2 and a["n_file_changes"] == 1
    assert a["files_changed"] == ["/orig/root/rtl/alpha_engine.v"]
    diag = by_name["Analyze Failure [Alpha Engine]"]
    assert diag["status"] == "timeout"
    assert diag["session_id"] == "thread-D-unlinked"  # matched by wall_start proximity
    detail = L.build_call_detail(synthetic_run, a["call_id"])
    assert detail["system_prompt"] == "SYS A" and detail["response"] == "RESP A"
    kinds = [t["kind"] for t in detail["turns"]]
    assert kinds == ["agent_message", "command_execution", "file_change", "command_execution"]
    # Absolute paths from the original run root are rebased onto the run dir.
    fc = detail["turns"][2]["changes"][0]
    assert fc["rel_path"] == "rtl/alpha_engine.v" and fc["exists"] is True
    big = detail["turns"][3]
    assert big["truncated"] and big["output_len"] == 20000 and len(big["output"]) < 20000
    full = L.build_call_turn(synthetic_run, a["call_id"], 3)
    assert full["output_len"] == 20000 and not full["truncated"] and len(full["output"]) == 20000
    assert L.build_call_turn(synthetic_run, a["call_id"], 99) is None
    assert L.build_call_detail(synthetic_run, 999) is None


def test_decisions_pair_with_events_and_calls(L, synthetic_run):
    idx = L.get_index(synthetic_run)
    assert len(idx.decisions) == 1
    d = idx.decisions[0]
    assert d["action"] == "revise" and d["reasoning"].startswith("alpha_engine")
    assert d["ts"] == pytest.approx(T0 + 380)
    assert d["blocks"] == ["alpha_engine"]  # named in the reasoning
    assert d["call_id"] == 5  # the chip-lead LLM call that produced it
    kinds = [(i["node"], i["action"]) for i in idx.interrupts]
    assert ("Ask Human", "fix_rtl") in kinds


def test_step_logs_attach_to_the_right_segment(L, synthetic_run):
    tr = L.build_block_trajectory(synthetic_run, "alpha_engine")
    r1 = tr["rounds"][0]
    synth2 = [s for s in r1["segments"] if s["node"] == "Synthesize"][1]
    assert [x["name"] for x in synth2["steps"] if x["type"] == "tool_run"] == ["synthesize_attempt2.log"]
    rtl2 = [s for s in r1["segments"] if s["node"] == "Generate RTL" and s["attempt"] == 2][0]
    types = [x["type"] for x in rtl2["steps"]]
    assert "event" in types and "llm_call" in types and "tool_run" in types
    assert tr["unattributed"]["calls"] == []


def test_block_review_numbers_and_gates(L, synthetic_run):
    rv = L.build_block_review(synthetic_run, "alpha_engine")
    assert rv["meta"]["tier"] == "1" and rv["meta"]["source"] == "block_diagram.json"
    assert rv["synth"]["cells"] == 326 and rv["synth"]["ff"] == 45
    assert rv["synth"]["macros"] == [{"name": "cs_sram_1rw1r", "count": 1}]
    assert rv["timing"]["period_ns"] == 20.0 and rv["timing"]["sta_persisted"] is False
    assert rv["timing"]["note"]  # explains the missing TNS / path report
    assert rv["files"]["rtl"]["rel_path"] == "rtl/alpha_engine.v"
    assert [v["label"] for v in rv["files"]["rtl_versions"]] == ["RTL before attempt 2", "current RTL"]
    gates = {g["gate"]: g["passed"] for g in rv["gates"]}
    assert gates["simulation"] is True and gates["coverage"] is True
    assert rv["sim"]["parsed"]["tests_passed"] == 3
    assert [d["category"] for d in rv["issues"]["event_diagnoses"]] == ["LOGIC_ERROR"]
    assert rv["issues"]["route_decisions"][0]["decision"] == "retry_rtl"
    assert rv["decisions"][0]["action"] == "revise"


def test_overview_and_blocks_table(L, synthetic_run):
    ov = L.build_overview(synthetic_run)
    assert ov["totals"]["blocks"] == 2 and ov["totals"]["llm_calls"] == 5
    assert ov["totals"]["timeouts"] == 1
    assert ov["totals"]["usage"]["input_tokens"] == 50
    assert ov["totals"]["codex_commands"] == 2 and ov["totals"]["codex_file_changes"] == 3
    rows = {r["name"]: r for r in ov["blocks"]}
    assert rows["alpha_engine"]["status"] == "passed" and rows["alpha_engine"]["rounds"] == 2
    assert rows["alpha_engine"]["cells"] == 326  # from the Yosys report when no sqlite
    assert rows["alpha_engine"]["dv"]["tests_total"] == 3
    assert rows["beta_fifo"]["cells"] is None
    integ = L.build_integration(synthetic_run)
    assert [s["node"] for s in integ["segments"]] == ["Init Tier", "Integration Review"]
    assert integ["segments"][1]["calls"][0]["call_id"] == 5


def test_index_cache_invalidates_on_change(L, synthetic_run):
    idx1 = L.get_index(synthetic_run)
    assert L.get_index(synthetic_run) is idx1
    p = synthetic_run / ".coresmith" / "llm_calls.jsonl"
    time.sleep(0.01)
    with open(p, "a") as fh:
        fh.write(json.dumps({"ts": T0 + 500, "model": "m", "provider": "p", "run_name": "Generate Testbench [Beta Fifo]",
                             "duration_s": 1, "system_prompt": "", "user_prompt": "", "response": "", "usage": {}}) + "\n")
    idx2 = L.get_index(synthetic_run)
    assert idx2 is not idx1 and len(idx2.calls) == 6


def test_sqlite_read_only_and_json_fallback(L, tmp_path):
    cs = tmp_path / ".coresmith"
    cs.mkdir()
    assert L.open_project_db(tmp_path) is None
    db = cs / "project.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        create table blocks(name text primary key, ordinal int, tier text, subsystem text, description text,
            python_source text, rtl_target text, testbench text, estimated_gates int, flip_flop_budget int,
            area_budget_um2 real, extra_json text, in_queue int, spec_contract_version int);
        insert into blocks values('x_blk', 0, '2', 'sub', 'desc', '', 'rtl/x.v', 'tb/x.py', 10, 5, 1.0, '{"interfaces": {"a": 1}}', 1, 1);
        create table settings(name text primary key, value text, updated_at real);
        insert into settings values('engine_sha', 'abc123', 0);
        create table ppa_history(id integer primary key, ts real, block text, attempt int, source text, probe text,
            cells int, ff int, mem_bits int, area_um2 real, wns_ns real, elaborated int, budget_ff int,
            budget_area_um2 real, ppa_ok int, reasons text, report_path text);
        insert into ppa_history(ts, block, attempt, source, probe, cells, ff, area_um2, wns_ns, ppa_ok, reasons)
            values(1, 'x_blk', 1, 'gate', 'synth', 10, 2, 3.5, -0.75, 0, '["too slow"]');
    """)
    con.commit()
    con.close()
    before = db.read_bytes()
    meta = L.load_blocks_meta(tmp_path)
    assert meta["x_blk"]["tier"] == "2" and meta["x_blk"]["interfaces"] == {"a": 1}
    assert L.run_settings(tmp_path)["engine_sha"] == "abc123"
    _write_jsonl(cs / "pipeline_events.jsonl", [_ev(T0, "graph_node_enter", "Init Block", "x_blk"),
                                                _ev(T0 + 1, "graph_node_exit", "Init Block", "x_blk", status="ok")])
    rows = L.build_blocks_table(tmp_path)
    assert rows[0]["wns_ns"] == -0.75 and rows[0]["ppa_ok"] == 0 and rows[0]["ppa_reasons"] == ["too slow"]
    rv = L.build_block_review(tmp_path, "x_blk")
    assert rv["timing"]["wns_ns"] == -0.75
    assert {g["gate"]: g["passed"] for g in rv["gates"]}["timing"] is False
    assert db.read_bytes() == before  # never written


def test_path_safety_and_rebase(L, tmp_path):
    (tmp_path / "rtl").mkdir()
    (tmp_path / "rtl" / "a.v").write_text("x")
    assert L.safe_rel_path(tmp_path, "rtl/a.v") == (tmp_path / "rtl" / "a.v").resolve()
    assert L.safe_rel_path(tmp_path, "../etc/passwd") is None
    assert L.safe_rel_path(tmp_path, "/etc/passwd") is None
    assert L.safe_rel_path(tmp_path, "rtl/../../x") is None
    (tmp_path / ".coresmith").mkdir()
    (tmp_path / ".coresmith" / "daemon.json").write_text(json.dumps({"project_root": "/orig/run"}))
    assert L.rebase_path(tmp_path, "/orig/run/rtl/a.v") == "rtl/a.v"
    assert L.rebase_path(tmp_path, "/somewhere/else/codex-call-abc/rtl/a.v") == "rtl/a.v"
    assert L.rebase_path(tmp_path, "/orig/run/rtl/missing.v") is None
    assert L.rebase_path(tmp_path, "rtl/a.v") == "rtl/a.v"


def test_text_slice_search_and_diff(L, tmp_path):
    p = tmp_path / "big.log"
    p.write_text("\n".join(f"line {i} {'ERROR' if i % 100 == 0 else ''}" for i in range(1000)))
    head = L.text_slice(tmp_path, "big.log", offset=0, limit=10)
    assert head["total_lines"] == 1000 and head["count"] == 10 and head["lines"][0] == "line 0 ERROR"
    tail = L.text_slice(tmp_path, "big.log", limit=5, tail=True)
    assert tail["offset"] == 995 and tail["eof"] and tail["lines"][-1] == "line 999 "
    mid = L.text_slice(tmp_path, "big.log", offset=990, limit=100)
    assert mid["count"] == 10 and mid["eof"]
    assert L.text_slice(tmp_path, "../big.log") is None
    hits = L.text_search(tmp_path, "big.log", "error")
    assert [h["line"] for h in hits["hits"]] == [0, 100, 200, 300, 400, 500, 600, 700, 800, 900]
    (tmp_path / "a.v").write_text("module a;\nwire x;\nendmodule\n")
    (tmp_path / "b.v").write_text("module a;\nwire y;\nreg z;\nendmodule\n")
    d = L.unified_diff(tmp_path, "a.v", "b.v")
    assert d["added"] == 2 and d["removed"] == 1 and not d["identical"]
    assert L.unified_diff(tmp_path, "a.v", "a.v")["identical"]


def test_env_secrets_redacted(L, tmp_path):
    cs = tmp_path / ".coresmith"
    cs.mkdir()
    (cs / "env").write_text("CORESMITH_MODEL=gpt\nOPENAI_API_KEY=sk-secret\n# comment\nGH_TOKEN=abc\n")
    env = {e["key"]: e["value"] for e in L.load_env_file(tmp_path)}
    assert env["CORESMITH_MODEL"] == "gpt"
    assert env["OPENAI_API_KEY"] == "<redacted>" and env["GH_TOKEN"] == "<redacted>"


# --------------------------------------------------------------------------- #
# HTTP routes through serve.py
# --------------------------------------------------------------------------- #

@pytest.fixture
def http_server(synthetic_run, monkeypatch):
    monkeypatch.setenv("CORESMITH_PROJECT_ROOT", str(synthetic_run))
    spec = importlib.util.spec_from_file_location("serve_review_under_test", str(_SERVE_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "PROJECT_ROOT", synthetic_run)
    srv = HTTPServer(("127.0.0.1", 0), mod.WebviewHandler)
    t = Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(base, path):
    try:
        with urlopen(Request(base + path), timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None


def test_http_review_endpoints(http_server):
    st, ov = _get(http_server, "/api/run/overview")
    assert st == 200 and ov["totals"]["blocks"] == 2
    st, tr = _get(http_server, "/api/block/alpha_engine/trajectory")
    assert st == 200 and len(tr["rounds"]) == 2
    st, rv = _get(http_server, "/api/block/alpha_engine/review")
    assert st == 200 and rv["synth"]["cells"] == 326
    st, call = _get(http_server, "/api/llm_call/1")
    assert st == 200 and call["run_name"] == "Generate Verilog [Alpha Engine]" and len(call["turns"]) == 4
    st, turn = _get(http_server, "/api/llm_call/1/turn/3")
    assert st == 200 and turn["output_len"] == 20000
    st, _ = _get(http_server, "/api/llm_call/77")
    assert st == 404
    st, txt = _get(http_server, "/api/text?path=rtl/alpha_engine.v&limit=1")
    assert st == 200 and txt["lines"] == ["module alpha_engine(input clk);"]
    st, _ = _get(http_server, "/api/text?path=../../etc/passwd")
    assert st == 404
    st, diff = _get(http_server, "/api/diff?a=.coresmith/blocks/alpha_engine/rtl_backup_attempt2.v&b=rtl/alpha_engine.v")
    assert st == 200 and diff["removed"] == 1
    st, dec = _get(http_server, "/api/run/decisions")
    assert st == 200 and dec["decisions"][0]["call_id"] == 5
    st, calls = _get(http_server, "/api/llm_calls?block=beta_fifo")
    assert st == 200 and [c["run_name"] for c in calls["calls"]] == ["Generate Verilog [Beta Fifo]"]
    # Legacy per-node trajectory is scoped to one block and its calls only.
    st, legacy = _get(http_server, "/api/node_trajectory/Generate%20RTL?block=beta_fifo")
    assert st == 200 and [a["block"] for a in legacy] == ["beta_fifo"]
    names = [s["run_name"] for a in legacy for s in a["steps"] if s["type"] == "llm_call"]
    assert names == ["Generate Verilog [Beta Fifo]"]
    assert legacy[0]["steps"][0]["call_id"] == 2
    assert [t["kind"] for t in legacy[0]["steps"][0]["codex_turns"]] == ["agent_message", "file_change"]


# --------------------------------------------------------------------------- #
# Staged real runs (skipped when not present)
# --------------------------------------------------------------------------- #

RUNS = [p for p in (STAGED / "h264-arm-e-snapshot", STAGED / "h264-arm-b") if p.exists()]


@pytest.mark.skipif(not RUNS, reason="staged runs not present")
@pytest.mark.parametrize("run", RUNS, ids=[r.name for r in RUNS])
def test_staged_run_builds_every_view(L, run):
    idx = L.get_index(run)
    assert len(idx.block_names()) == 12
    assert all(c["session_key"] for c in idx.calls), "every LLM call joins a codex session"
    ov = L.build_overview(run)
    assert ov["totals"]["llm_calls"] == len(idx.calls) and ov["totals"]["blocks"] == 12
    for name in idx.block_names():
        tr = L.build_block_trajectory(run, name)
        assert tr["rounds"], name
        for r in tr["rounds"]:
            nodes = [s["node"] for s in r["segments"]]
            assert nodes[0] == "Init Block", (name, nodes)
            for s in r["segments"]:
                assert all(c.get("block") in (name, None) for c in s["steps"] if c["type"] == "llm_call")
        rv = L.build_block_review(run, name)
        assert rv["files"]["rtl"], name
        assert rv["gates"], name
        json.dumps(rv)  # JSON-serialisable
    dec = L.build_decisions(run)
    assert dec["decisions"]
    L.build_integration(run)


@pytest.mark.skipif(not (STAGED / "h264-arm-e-snapshot").exists(), reason="staged run not present")
def test_staged_arm_e_specifics(L):
    run = STAGED / "h264-arm-e-snapshot"
    tr = L.build_block_trajectory(run, "cavlc_macroblock_encoder")
    assert len(tr["rounds"]) == 5
    assert [r["outcome"] for r in tr["rounds"]] == ["failed", "failed", "passed", "passed", "passed"]
    r2 = tr["rounds"][1]
    assert [s["attempt"] for s in r2["segments"] if s["node"] == "Generate RTL"] == [1, 2]
    rv = L.build_block_review(run, "cavlc_macroblock_encoder")
    assert rv["synth"]["cells"] == 11495 and rv["synth"]["ff"] == 2688
    assert rv["synth"]["macros"] == [{"name": "cs_sram_1rw1r", "count": 1}]
    assert rv["timing"]["wns_ns"] == pytest.approx(14.2058) and rv["timing"]["period_ns"] == 20.0
    assert rv["timing"]["fmax_mhz"] == pytest.approx(172.59, abs=0.01)
    assert rv["timing"]["tns_ns"] is None and rv["timing"]["sta_persisted"] is False
    assert rv["sim"]["parsed"]["tests_passed"] == 5
    assert len(rv["issues"]["event_diagnoses"]) == 5
    rows = {r["name"]: r for r in L.build_blocks_table(run)}
    assert rows["inverse_reconstruction_engine"]["status"] == "incomplete"
    assert rows["output_byte_fifo"]["wns_ns"] == pytest.approx(17.2408)
    idx = L.get_index(run)
    assert len(idx.decisions) == 9 and all(d.get("call_id") for d in idx.decisions)
    d = L.build_call_detail(run, idx.decisions[0]["call_id"])
    assert d["run_name"].startswith("Chip Lead")


@pytest.mark.skipif(not (STAGED / "h264-arm-b").exists(), reason="staged run not present")
def test_staged_arm_b_specifics(L):
    run = STAGED / "h264-arm-b"
    ov = L.build_overview(run)
    assert ov["signoff"]["status"] == "PASS"
    assert ov["totals"]["status_counts"] == {"passed": 12}
    integ = L.build_integration(run)
    nodes = [s["node"] for s in integ["segments"]]
    assert "Integration DV" in nodes and "Validation DV" in nodes
    assert integ["integration_result"]["lint_log_rel_path"] == ".coresmith/step_logs/chip_top/integration_lint_attempt1.log"
    rv = L.build_block_review(run, "forward_transform_engine")
    assert rv["synth"]["cells"] is None  # synthesis was skipped in that run
    assert [a["category"] for a in rv["issues"]["attempts"]] == ["LOGIC_ERROR"]
