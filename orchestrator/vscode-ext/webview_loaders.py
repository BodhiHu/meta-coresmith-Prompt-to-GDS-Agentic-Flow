#!/usr/bin/env python3
"""Read-only loaders for the CoreSmith run-review web UI.

Everything in this module reads a run directory (``PROJECT_ROOT``) and returns
plain JSON-able dicts for ``serve.py``.  It never writes to the run directory:
SQLite is opened with ``file:...?mode=ro`` (falling back to ``immutable=1`` for
WAL databases copied without their -wal/-shm sidecars), and every other input
is read as text.

Data sources (see docs/WEBVIEW.md for the full map):

* ``.coresmith/pipeline_events.jsonl``  -- graph node enter/exit, llm_start /
  llm_end, chip_lead_decision, gate events, interrupts.
* ``.coresmith/llm_calls.jsonl``        -- one record per LLM call (prompts,
  response, usage, duration).
* ``.coresmith/codex_turns.jsonl``      -- the Codex CLI ``--json`` event stream
  (agent messages, command executions with output, file changes, token usage).
  Records carry ``pid`` + ``wall_start``; the ``thread.started.thread_id``
  equals ``llm_calls.usage.session_id`` which is how calls are joined to turns.
* ``.coresmith/chip_lead/decisions.jsonl`` -- in-graph chip-lead decisions
  (no timestamps; paired by index with ``chip_lead_decision`` events).
* ``.coresmith/project.sqlite``         -- canonical state (blocks, attempts,
  diagnoses, constraints, results, settings, dv_results, ppa_history,
  coverage_results).  The JSON views next to it are used when the DB is absent
  (older runs).
* ``.coresmith/step_logs/<block>/<step>_attemptN.log`` -- tool logs.
* ``.coresmith/blocks/<block>/*.json``  -- per-block gate reports.
* ``syn/output/<block>/<block>_report.txt`` -- Yosys ``stat`` report.
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------- #
# Small cached readers (keyed on path + mtime + size so polling stays cheap)
# --------------------------------------------------------------------------- #

_CACHE: dict[tuple, tuple[tuple, Any]] = {}


def _stat_sig(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _cached(kind: str, path: Path, loader):
    sig = _stat_sig(path)
    key = (kind, str(path))
    if sig is None:
        _CACHE.pop(key, None)
        return loader(None)
    hit = _CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    value = loader(path)
    _CACHE[key] = (sig, value)
    return value


def read_jsonl(path: Path) -> list[dict]:
    """Parse a JSONL file, skipping blank / torn lines.  Cached by mtime."""

    def _load(p):
        if p is None:
            return []
        out = []
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return out

    return _cached("jsonl", path, _load)


def read_json(path: Path, default=None):
    def _load(p):
        if p is None:
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    return _cached("json", path, _load)


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _rel(root: Path, p: Path) -> str | None:
    try:
        return str(p.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        return None


def safe_rel_path(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` under ``root`` and reject traversal outside it."""
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        return None
    p = (root / rel).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return None
    return p


# --------------------------------------------------------------------------- #
# Path rebasing: logs carry absolute paths of the ORIGINAL run directory.
# --------------------------------------------------------------------------- #

_ANCHORS = ("/.coresmith/", "/rtl/", "/tb/", "/arch/", "/syn/", "/inputs/",
            "/sim_build/", "/codex-call-")


def original_roots(root: Path) -> list[str]:
    """Absolute roots the run was recorded under (daemon.json, final_report)."""
    roots: list[str] = []
    for src in (
        read_json(root / ".coresmith" / "daemon.json", {}) or {},
        read_json(root / "final_report.json", {}) or {},
    ):
        pr = (src or {}).get("project_root")
        if pr and pr not in roots:
            roots.append(pr.rstrip("/"))
    return roots


def rebase_path(root: Path, path: str | None) -> str | None:
    """Map an absolute path from the logs to a path relative to ``root``.

    Returns the relative path when the file exists in the run directory,
    otherwise ``None``.
    """
    if not path:
        return None
    s = str(path)
    root_r = root.resolve()
    # Already relative?
    if not s.startswith("/"):
        cand = safe_rel_path(root, s)
        if cand and cand.exists():
            return s
    # Under the current root?
    try:
        p = Path(s).resolve()
        if p.exists() and p.is_relative_to(root_r):
            return str(p.relative_to(root_r))
    except OSError:
        pass
    # Under the original root?
    for orig in original_roots(root):
        if s.startswith(orig + "/"):
            rel = s[len(orig) + 1:]
            cand = safe_rel_path(root, rel)
            if cand and cand.exists():
                return rel
    # Anchor on a well-known top-level directory.
    for anchor in _ANCHORS:
        idx = s.find(anchor)
        if idx >= 0:
            rel = s[idx + 1:]
            cand = safe_rel_path(root, rel)
            if cand and cand.exists():
                return rel
            # codex-call workspaces mirror the run tree: strip the workspace.
            if anchor == "/codex-call-":
                rest = rel.split("/", 1)
                if len(rest) == 2:
                    cand = safe_rel_path(root, rest[1])
                    if cand and cand.exists():
                        return rest[1]
    return None


# --------------------------------------------------------------------------- #
# SQLite (read-only)
# --------------------------------------------------------------------------- #

def open_project_db(root: Path) -> sqlite3.Connection | None:
    db = root / ".coresmith" / "project.sqlite"
    if not db.exists():
        return None
    for uri in (f"file:{db}?mode=ro", f"file:{db}?immutable=1"):
        try:
            con = sqlite3.connect(uri, uri=True, timeout=1.0)
            con.row_factory = sqlite3.Row
            con.execute("select 1 from sqlite_master limit 1").fetchall()
            return con
        except sqlite3.Error:
            continue
    return None


def db_rows(con: sqlite3.Connection | None, sql: str, params: tuple = ()) -> list[dict]:
    if con is None:
        return []
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def _uj(text, default=None):
    if text is None:
        return default
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return default


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #

_SEQ_CELL_RE = re.compile(
    r"(dfxtp|dfrtp|dfstp|dfbbn|dfbbp|dfxbp|dfrbp|dfsbp|edfxtp|edfxbp|sdf|dlxtp|"
    r"dlrtp|dlxbp|dlrbp|dlclkp|\$_DFF|\$_SDFF|\$_DFFE|\$_ADFF|\$_DLATCH|\$_ALDFF|"
    r"\$dff|\$adff|\$sdff|\$dffe|\$dlatch)", re.I)


def parse_yosys_stat(text: str) -> dict:
    """Parse the LAST ``Printing statistics`` block of a Yosys log/report.

    Handles both the tabular Yosys >= 0.4x format::

           11495 1.34E+05 cells
             608 1.22E+04   sky130_fd_sc_hd__dfxtp_1

    and the older ``Number of cells: N`` / ``$_DFF_P_ 12`` layout.  Returns
    ``{cells, ff, wires, wire_bits, ports, port_bits, chip_area_um2,
    sequential_area_um2, sequential_pct, cell_types: [{name, count, area}],
    macros: [{name, count}], unknown_area_types: [...], module}``.
    """
    out: dict[str, Any] = {
        "cells": None, "ff": None, "wires": None, "wire_bits": None,
        "ports": None, "port_bits": None, "chip_area_um2": None,
        "sequential_area_um2": None, "sequential_pct": None,
        "cell_types": [], "macros": [], "unknown_area_types": [], "module": None,
    }
    if not text:
        return out
    idx = text.rfind("Printing statistics")
    section = text[idx:] if idx >= 0 else text
    lines = section.splitlines()
    cell_types: list[dict] = []
    in_cells = False
    for ln in lines:
        m = re.match(r"^=== (\S+) ===", ln.strip())
        if m:
            out["module"] = m.group(1)
            continue
        m = re.match(r"^\s*Chip area for (?:top )?module '\\?([^']+)':\s+([\d.]+)", ln)
        if m:
            out["chip_area_um2"] = _f(m.group(2))
            continue
        m = re.match(r"^\s*of which used for sequential elements:\s+([\d.]+)\s+\(([\d.]+)%\)", ln)
        if m:
            out["sequential_area_um2"] = _f(m.group(1))
            out["sequential_pct"] = _f(m.group(2))
            continue
        m = re.match(r"^\s*Area for cell type \\?(\S+) is unknown!", ln)
        if m:
            out["unknown_area_types"].append(m.group(1))
            continue
        # New tabular format
        m = re.match(r"^\s*(\d+)\s+(\S+)\s+(wires|wire bits|public wires|public wire bits|ports|port bits|cells)\s*$", ln)
        if m:
            key = m.group(3)
            val = _i(m.group(1))
            if key == "cells":
                out["cells"] = val
                in_cells = True
            elif key == "wires":
                out["wires"] = val
            elif key == "wire bits":
                out["wire_bits"] = val
            elif key == "ports":
                out["ports"] = val
            elif key == "port bits":
                out["port_bits"] = val
            continue
        m = re.match(r"^\s*(\d+)\s+(\S+)\s+(\$?\\?[\w$.\\-]+)\s*$", ln)
        if m and in_cells:
            area = _f(m.group(2)) if m.group(2) != "-" else None
            cell_types.append({"name": m.group(3).lstrip("\\"), "count": _i(m.group(1)), "area": area})
            continue
        # Old format
        m = re.match(r"^\s*Number of cells:\s+(\d+)", ln)
        if m:
            out["cells"] = _i(m.group(1))
            in_cells = True
            continue
        m = re.match(r"^\s*Number of wires:\s+(\d+)", ln)
        if m:
            out["wires"] = _i(m.group(1))
            continue
        m = re.match(r"^\s+(\$?\\?[\w$.\\-]+)\s+(\d+)\s*$", ln)
        if m and in_cells and not ln.strip().startswith("Number of"):
            cell_types.append({"name": m.group(1).lstrip("\\"), "count": _i(m.group(2)), "area": None})
            continue
    out["cell_types"] = cell_types
    ff = 0
    have_seq = False
    macros: list[dict] = []
    for ct in cell_types:
        name = ct["name"]
        if _SEQ_CELL_RE.search(name):
            ff += ct["count"] or 0
            have_seq = True
        elif not name.startswith("sky130_") and not name.startswith("$") and not name.startswith("gf180") and not name.startswith("nangate"):
            macros.append({"name": name, "count": ct["count"]})
    out["ff"] = ff if have_seq or cell_types else None
    out["macros"] = macros
    return out


_STA_PATH_KEYS = (
    ("Startpoint", "startpoint"), ("Endpoint", "endpoint"),
    ("Path Group", "path_group"), ("Path Type", "path_type"),
)


def parse_sta_report(text: str) -> dict:
    """Parse OpenSTA ``report_wns`` / ``report_tns`` / ``report_checks`` text.

    Returns ``{wns_ns, tns_ns, paths: [{startpoint, endpoint, path_group,
    path_type, arrival_ns, required_ns, slack_ns, violated}]}``.  Accepts both
    the legacy ``wns -3.42`` and OpenSTA 3.x ``wns max -3.42`` spellings plus
    the engine's ``CORESMITH_WNS <n>`` / ``worst slack max <n>`` prints.
    """
    out: dict[str, Any] = {"wns_ns": None, "tns_ns": None, "paths": []}
    if not text:
        return out
    for key, tag in (("wns_ns", "wns"), ("tns_ns", "tns")):
        m = re.search(rf"^\s*{tag}(?:\s+max)?\s+(-?[0-9.]+(?:[eE][+-]?\d+)?)\s*$", text, re.M | re.I)
        if m:
            out[key] = _f(m.group(1))
    if out["wns_ns"] is None:
        m = re.search(r"CORESMITH_WNS\s+(-?[0-9.]+(?:[eE][+-]?\d+)?)", text)
        if m is None:
            m = re.search(r"worst slack\s*(?:-?max)?\s*(-?[0-9.]+(?:[eE][+-]?\d+)?)", text, re.I)
        if m:
            out["wns_ns"] = _f(m.group(1))
    cur: dict | None = None
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("Startpoint:"):
            if cur:
                out["paths"].append(cur)
            cur = {"startpoint": s[len("Startpoint:"):].strip(), "endpoint": None,
                   "path_group": None, "path_type": None, "arrival_ns": None,
                   "required_ns": None, "slack_ns": None, "violated": None}
            continue
        if cur is None:
            continue
        for label, key in _STA_PATH_KEYS[1:]:
            if s.startswith(label + ":"):
                cur[key] = s[len(label) + 1:].strip()
                break
        else:
            # OpenSTA prints "<delay> <time> data arrival time": take the last
            # number before the label.
            m = re.search(r"(-?[0-9.]+)\s+data arrival time", s)
            if m:
                cur["arrival_ns"] = _f(m.group(1))
                continue
            m = re.search(r"(-?[0-9.]+)\s+data required time", s)
            if m:
                cur["required_ns"] = _f(m.group(1))
                continue
            m = re.search(r"(-?[0-9.]+)\s+slack \((MET|VIOLATED)\)", s)
            if m:
                cur["slack_ns"] = _f(m.group(1))
                cur["violated"] = m.group(2) == "VIOLATED"
                out["paths"].append(cur)
                cur = None
    if cur:
        out["paths"].append(cur)
    if out["wns_ns"] is None and out["paths"]:
        slacks = [p["slack_ns"] for p in out["paths"] if p.get("slack_ns") is not None]
        if slacks:
            out["wns_ns"] = min(slacks)
    return out


def parse_step_log_header(text: str) -> dict:
    """Header lines our step logs start with (``Command:``, ``Return code:``)."""
    meta: dict[str, Any] = {}
    for line in text.splitlines()[:10]:
        if line.startswith("=== ") and line.endswith(" LOG ==="):
            meta["kind"] = line[4:-8].strip().lower()
        elif line.startswith("Command:"):
            meta["command"] = line[len("Command:"):].strip()
        elif line.startswith("Return code:"):
            rc = line[len("Return code:"):].strip()
            meta["return_code"] = _i(rc) if rc.lstrip("-").isdigit() else None
            meta["return_code_raw"] = rc
        elif line.startswith("Block:"):
            meta["block"] = line[len("Block:"):].strip()
        elif line.startswith("Attempt:"):
            meta["attempt"] = _i(line[len("Attempt:"):].strip())
        elif line.startswith("Timestamp:"):
            meta["timestamp"] = line[len("Timestamp:"):].strip()
    return meta


_COCOTB_ROW_RE = re.compile(
    r"\*\*\s+(\S+)\s+(PASS|FAIL|SKIP|ERROR)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+\*\*")
_COCOTB_SUM_RE = re.compile(r"TESTS=(\d+)\s+PASS=(\d+)\s+FAIL=(\d+)\s+SKIP=(\d+)")


def parse_cocotb_results(text: str) -> dict:
    """Extract the cocotb regression table from a simulate log."""
    out: dict[str, Any] = {"tests_total": None, "tests_passed": None,
                           "tests_failed": None, "tests_skipped": None, "tests": []}
    if not text:
        return out
    for m in _COCOTB_ROW_RE.finditer(text):
        out["tests"].append({
            "name": m.group(1), "status": m.group(2),
            "sim_time_ns": _f(m.group(3)), "real_time_s": _f(m.group(4)),
        })
    m = None
    for m in _COCOTB_SUM_RE.finditer(text):
        pass
    if m:
        out["tests_total"] = _i(m.group(1))
        out["tests_passed"] = _i(m.group(2))
        out["tests_failed"] = _i(m.group(3))
        out["tests_skipped"] = _i(m.group(4))
    # First failing assertion, if any -- the thing the architect wants first.
    fm = re.search(r"^\s*(AssertionError:.*)$", text, re.M)
    if fm:
        out["first_assertion"] = fm.group(1).strip()[:600]
    return out


def parse_verilator_lint(text: str) -> dict:
    warnings = re.findall(r"^%Warning-(\w+):\s*(.*)$", text, re.M)
    errors = re.findall(r"^%Error(?:-(\w+))?:\s*(.*)$", text, re.M)
    return {
        "warning_count": len(warnings),
        "error_count": len(errors),
        "warning_kinds": sorted({w[0] for w in warnings}),
        "errors": [e[1][:300] for e in errors[:10]],
    }


def parse_synth_log_summary(text: str) -> dict:
    """Cheap tail-parse of a synthesize step log: area / cells / yosys errors."""
    tail = text[-40000:] if len(text) > 40000 else text
    stat = parse_yosys_stat(tail) if "Printing statistics" in tail else parse_yosys_stat("")
    errs = re.findall(r"^(?:ERROR:|.*\bERROR\b:?)\s*(.*)$", text, re.M)
    return {
        "cells": stat.get("cells"), "ff": stat.get("ff"),
        "chip_area_um2": stat.get("chip_area_um2"),
        "errors": [e.strip()[:300] for e in errs[:5]],
        "error_count": len(errs),
    }


# --------------------------------------------------------------------------- #
# Codex sessions
# --------------------------------------------------------------------------- #

def load_codex_sessions(root: Path) -> dict[str, dict]:
    """Group ``codex_turns.jsonl`` into sessions keyed by ``"<pid>:<wall_start>"``.

    Each session holds the final state of every item (``item.started`` /
    ``item.updated`` / ``item.completed`` collapse onto one entry by ``item.id``),
    the ``thread_id`` and the ``turn.completed`` usage.
    """
    path = root / ".coresmith" / "codex_turns.jsonl"
    if not path.exists():
        path = root / ".socmate" / "codex_turns.jsonl"

    def _load(p):
        sessions: dict[str, dict] = {}
        if p is None:
            return sessions
        try:
            fh = open(p, "r", encoding="utf-8", errors="replace")
        except OSError:
            return sessions
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pid = rec.get("pid")
                ws = rec.get("wall_start")
                key = f"{pid}:{ws}"
                sess = sessions.get(key)
                if sess is None:
                    sess = sessions[key] = {
                        "key": key, "pid": pid, "wall_start": ws,
                        "thread_id": None, "first_ts": rec.get("ts"),
                        "last_ts": rec.get("ts"), "items": [], "_by_id": {},
                        "usage": None, "turn_count": 0,
                    }
                ts = rec.get("ts")
                if ts is not None:
                    if sess["first_ts"] is None or ts < sess["first_ts"]:
                        sess["first_ts"] = ts
                    if sess["last_ts"] is None or ts > sess["last_ts"]:
                        sess["last_ts"] = ts
                ev = rec.get("event") or {}
                et = ev.get("type", "")
                if et == "thread.started":
                    sess["thread_id"] = ev.get("thread_id")
                elif et == "turn.started":
                    sess["turn_count"] += 1
                elif et == "turn.completed":
                    u = ev.get("usage")
                    if u:
                        sess["usage"] = u
                elif et in ("item.started", "item.updated", "item.completed"):
                    item = ev.get("item") or {}
                    iid = item.get("id") or f"anon-{len(sess['items'])}"
                    entry = sess["_by_id"].get(iid)
                    state = "completed" if et == "item.completed" else "in_progress"
                    if entry is None:
                        entry = {"id": iid, "ts": ts, "state": state, "item": item}
                        sess["_by_id"][iid] = entry
                        sess["items"].append(entry)
                    else:
                        entry["item"] = item
                        entry["state"] = state
                        entry["ts"] = ts
                elif et == "error":
                    sess["items"].append({"id": f"err-{len(sess['items'])}", "ts": ts,
                                          "state": "completed",
                                          "item": {"type": "error", "message": ev.get("message") or json.dumps(ev)}})
        for s in sessions.values():
            s.pop("_by_id", None)
        return sessions

    return _cached("codex", path, _load)


def _summarize_item(entry: dict, max_chars: int = 4000) -> dict:
    """Project a codex item to the card-friendly shape used by the UI."""
    item = entry.get("item") or {}
    kind = item.get("type") or "other"
    out: dict[str, Any] = {"id": entry.get("id"), "ts": entry.get("ts"),
                           "state": entry.get("state"), "kind": kind}
    if kind == "command_execution":
        output = item.get("aggregated_output") or ""
        out.update({
            "command": item.get("command") or "",
            "exit_code": item.get("exit_code"),
            "status": item.get("status"),
            "output_len": len(output),
        })
        if len(output) > max_chars:
            head = output[: max_chars // 2]
            tail = output[-max_chars // 2:]
            out["output"] = head + f"\n\n... [{len(output) - max_chars:,} chars elided; open the turn for the full output] ...\n\n" + tail
            out["truncated"] = True
        else:
            out["output"] = output
            out["truncated"] = False
    elif kind == "file_change":
        out["changes"] = [
            {"path": c.get("path"), "kind": c.get("kind")}
            for c in (item.get("changes") or [])
        ]
        out["status"] = item.get("status")
    elif kind == "agent_message":
        text = item.get("text") or ""
        out["text"] = text if len(text) <= max_chars * 4 else text[: max_chars * 4] + "\n... [truncated]"
    elif kind == "reasoning":
        text = item.get("text") or item.get("summary") or ""
        if isinstance(text, list):
            text = "\n".join(str(t.get("text", t)) if isinstance(t, dict) else str(t) for t in text)
        out["text"] = text[: max_chars * 4]
    elif kind == "todo_list":
        out["items"] = [{"text": t.get("text"), "completed": bool(t.get("completed"))}
                        for t in (item.get("items") or [])]
    elif kind == "web_search":
        out["query"] = item.get("query") or (item.get("action") or {}).get("query") or ""
    elif kind == "error":
        out["message"] = item.get("message")
    else:
        out["raw"] = json.dumps(item, default=str)[: max_chars]
    return out


# --------------------------------------------------------------------------- #
# Blocks metadata
# --------------------------------------------------------------------------- #

def load_blocks_meta(root: Path) -> dict[str, dict]:
    """Block identity + budgets from project.sqlite, falling back to the JSON views."""
    meta: dict[str, dict] = {}
    con = open_project_db(root)
    rows = db_rows(con, "select * from blocks order by ordinal")
    if rows:
        for r in rows:
            extra = _uj(r.get("extra_json"), {}) or {}
            meta[r["name"]] = {
                "name": r["name"], "ordinal": r.get("ordinal"),
                "tier": str(r.get("tier")) if r.get("tier") is not None else None,
                "subsystem": r.get("subsystem"), "description": r.get("description"),
                "python_source": r.get("python_source"), "rtl_target": r.get("rtl_target"),
                "testbench": r.get("testbench"),
                "estimated_gates": r.get("estimated_gates"),
                "flip_flop_budget": r.get("flip_flop_budget"),
                "area_budget_um2": r.get("area_budget_um2"),
                "in_queue": r.get("in_queue"),
                "spec_contract_version": r.get("spec_contract_version"),
                "interfaces": extra.get("interfaces"),
                "semantic_contracts": extra.get("semantic_contracts"),
                "source": "sqlite",
            }
    if con is not None:
        con.close()
    if not meta:
        bd = read_json(root / ".coresmith" / "block_diagram.json", {}) or {}
        blocks = bd.get("blocks") if isinstance(bd, dict) else bd
        for i, b in enumerate(blocks or []):
            if not isinstance(b, dict) or not b.get("name"):
                continue
            meta[b["name"]] = {
                "name": b["name"], "ordinal": i,
                "tier": str(b.get("tier")) if b.get("tier") is not None else None,
                "subsystem": b.get("subsystem"), "description": b.get("description"),
                "python_source": b.get("python_source"), "rtl_target": b.get("rtl_target"),
                "testbench": b.get("testbench"),
                "estimated_gates": b.get("estimated_gates"),
                "flip_flop_budget": b.get("flip_flop_budget"),
                "area_budget_um2": b.get("area_budget_um2"),
                "interfaces": b.get("interfaces"),
                "semantic_contracts": b.get("semantic_contracts"),
                "source": "block_diagram.json",
            }
    specs = read_json(root / ".coresmith" / "block_specs.json", []) or []
    for s in specs if isinstance(specs, list) else []:
        if not isinstance(s, dict) or not s.get("name"):
            continue
        m = meta.setdefault(s["name"], {"name": s["name"], "ordinal": len(meta), "source": "block_specs.json"})
        for k in ("tier", "rtl_target", "testbench", "description", "python_source"):
            if m.get(k) in (None, "") and s.get(k) not in (None, ""):
                m[k] = str(s[k]) if k == "tier" else s[k]
    return meta


# --------------------------------------------------------------------------- #
# Run index: events -> segments/rounds, llm calls -> segments, codex sessions
# --------------------------------------------------------------------------- #

TIER_NODES = {"Init Tier", "Advance Tier", "Integration Review", "Pipeline Complete",
              "Generate Uarch Specs",  # single-context uArch stage (run-level)
              "Integration Check", "Integration DV", "Validation DV", "Contract Audit",
              "Final Report", "Chip Lead", "Backend Complete", "Flat Top Synthesis"}
HITL_NODES = {"Review Uarch Spec", "Ask Human", "Escalate PRD", "Escalate Diagram",
              "Escalate Constraints", "Escalate Exhausted", "Final Review"}
SUB_EVENT_TYPES = {"lint_retry_bypass", "ppa_retry_bypass", "gate_result", "mem_price",
                   "gate_failed", "llm_timed out", "uarch_feasibility_blocked",
                   "uarch_feasibility_resume", "interrupt", "maxgeo_gate_pass",
                   "acceptance_dv", "delivered_netlist_check", "escalation_response"}
_BRACKET_RE = re.compile(r"\[([^\]]+)\]")


def _block_from_run_name(run_name: str, block_names: Iterable[str]) -> str | None:
    m = _BRACKET_RE.search(run_name or "")
    if not m:
        return None
    key = _norm(m.group(1))
    for b in block_names:
        if _norm(b) == key:
            return b
    return None


def _node_from_run_name(run_name: str) -> str:
    base = _BRACKET_RE.sub("", run_name or "").strip()
    base = re.sub(r"\s*-\s*Retry #\d+\s*$", "", base).strip()
    return {
        "Generate Verilog": "Generate RTL",
        "Analyze Failure": "Diagnose Failure",
    }.get(base, base)


class RunIndex:
    """Everything the review UI needs about *who did what, when* in a run."""

    def __init__(self, root: Path):
        self.root = Path(root)
        cs = self.root / ".coresmith"
        self.events: list[dict] = read_jsonl(cs / "pipeline_events.jsonl")
        self.llm_calls: list[dict] = read_jsonl(cs / "llm_calls.jsonl")
        self.codex: dict[str, dict] = load_codex_sessions(self.root)
        self.blocks_meta: dict[str, dict] = load_blocks_meta(self.root)
        self.segments: list[dict] = []
        self.rounds: dict[str, list[dict]] = {}
        self.calls: list[dict] = []
        self.decisions: list[dict] = []
        self.interrupts: list[dict] = []
        self.step_logs: dict[str, list[dict]] = {}
        self.pipeline_start: float | None = None
        self.pipeline_end: float | None = None
        self._build()

    # -- events -> segments ------------------------------------------------ #
    def _build(self) -> None:
        evs = self.events
        if evs:
            tss = [e.get("ts") for e in evs if isinstance(e.get("ts"), (int, float))]
            if tss:
                self.pipeline_start = min(tss)
                self.pipeline_end = max(tss)
        open_segs: dict[tuple[str, str], dict] = {}
        current_round: dict[str, int] = {}
        current_tier: str | None = None
        self._seg_counter = 0
        self._init_ts: dict[str, list[float]] = {}
        for e in evs:
            et = e.get("event", "")
            node = e.get("node")
            ts = e.get("ts")
            if ts is None:
                continue
            block = e.get("block") or ""
            if et == "graph_node_enter" and node:
                if node == "Init Tier":
                    current_tier = str(e.get("tier")) if e.get("tier") is not None else current_tier
                if node == "Init Block" and block:
                    current_round[block] = current_round.get(block, 0) + 1
                    self._init_ts.setdefault(block, []).append(ts)
                key = (block, node)
                if key in open_segs:
                    # LangGraph re-enters a node on resume; keep the original.
                    continue
                self._seg_counter += 1
                seg = {
                    "seg_id": self._seg_counter, "block": block or None, "node": node,
                    "enter_ts": ts, "exit_ts": None, "attempt": e.get("attempt"),
                    "round": current_round.get(block) if block else None,
                    "tier": str(e.get("tier")) if e.get("tier") is not None else current_tier,
                    "enter": {k: v for k, v in e.items() if k not in ("ts", "iso", "event", "node", "block")},
                    "exit": None, "status": "running", "calls": [], "sub_events": [],
                    "tool_runs": [],
                }
                open_segs[key] = seg
                self.segments.append(seg)
            elif et == "graph_node_exit" and node:
                key = (block, node)
                seg = open_segs.pop(key, None)
                if seg is None:
                    self._seg_counter += 1
                    seg = {
                        "seg_id": self._seg_counter, "block": block or None, "node": node,
                        "enter_ts": ts, "exit_ts": None, "attempt": e.get("attempt"),
                        "round": current_round.get(block) if block else None,
                        "tier": str(e.get("tier")) if e.get("tier") is not None else current_tier,
                        "enter": {}, "exit": None, "status": "running", "calls": [],
                        "sub_events": [], "tool_runs": [], "instant": True,
                    }
                    self.segments.append(seg)
                seg["exit_ts"] = ts
                if seg.get("attempt") is None and e.get("attempt") is not None:
                    seg["attempt"] = e.get("attempt")
                exit_fields = {k: v for k, v in e.items() if k not in ("ts", "iso", "event", "node", "block")}
                if isinstance(exit_fields.get("tool_stdout"), str) and len(exit_fields["tool_stdout"]) > 4000:
                    exit_fields["tool_stdout"] = exit_fields["tool_stdout"][-4000:]
                seg["exit"] = exit_fields
                seg["status"] = _exit_status(exit_fields)
                if node == "Advance Tier" and e.get("new_tier_index") is not None:
                    pass
            elif et in SUB_EVENT_TYPES:
                self._attach_sub_event(e, open_segs)
            elif et == "chip_lead_decision":
                self.decisions.append({
                    "ts": ts, "iso": e.get("iso"), "type": e.get("type"),
                    "action": e.get("action"), "decision_index": e.get("decision_index"),
                    "block": block or None, "node": node,
                })
        # Historical run: anything still open is treated as done unless recent.
        now = time.time()
        live = self.pipeline_end is not None and (now - self.pipeline_end) < 90
        for seg in self.segments:
            if seg["exit_ts"] is None and not live:
                seg["status"] = "waiting" if seg["node"] in HITL_NODES else "incomplete"
            elif seg["exit_ts"] is None:
                seg["status"] = "waiting" if seg["node"] in HITL_NODES else "running"
        self.is_live = live

        # Rounds per block (virtual segments get their round from the time of
        # the Init Block enter that precedes them).
        import bisect
        for seg in self.segments:
            b = seg.get("block")
            if not b:
                continue
            rnd = seg.get("round")
            if rnd is None:
                inits = self._init_ts.get(b) or []
                rnd = bisect.bisect_right(inits, seg["enter_ts"]) if inits else 0
                rnd = rnd or 1
            seg["round"] = rnd
            rl = self.rounds.setdefault(b, [])
            entry = next((r for r in rl if r["round"] == rnd), None)
            if entry is None:
                entry = {"round": rnd, "start_ts": seg["enter_ts"], "end_ts": seg["exit_ts"],
                         "segments": [], "outcome": None, "tier": seg.get("tier")}
                rl.append(entry)
            entry["segments"].append(seg["seg_id"])
            entry["start_ts"] = min(entry["start_ts"], seg["enter_ts"])
            end = seg["exit_ts"] or seg["enter_ts"]
            if entry["end_ts"] is None or end > entry["end_ts"]:
                entry["end_ts"] = end
            if seg["node"] == "Block Done":
                ok = (seg.get("exit") or {}).get("success")
                entry["outcome"] = "passed" if ok else "failed"
            elif seg["node"] == "Route Decision":
                entry["decision"] = (seg.get("exit") or {}).get("decision")

        self._attach_decision_reasoning()
        self._attach_llm_calls()
        self._link_decisions_to_calls()
        self._attach_step_logs()
        self._collect_interrupts()

    def _attach_sub_event(self, e: dict, open_segs: dict) -> None:
        block = e.get("block") or ""
        node = e.get("node")
        rec = {"ts": e.get("ts"), "iso": e.get("iso"), "event": e.get("event"),
               "node": node, "block": block or None,
               "fields": {k: v for k, v in e.items()
                          if k not in ("ts", "iso", "event", "node", "block")}}
        if isinstance(rec["fields"].get("partial_stdout"), str):
            rec["fields"]["partial_stdout"] = rec["fields"]["partial_stdout"][:2000]
        target = open_segs.get((block, node))
        if target is None:
            # Fall back to any open segment of the block (e.g. gate_result under
            # "Gate Sim" fires while Synthesize/Block Done is the open node).
            for (b, _n), s in reversed(list(open_segs.items())):
                if b == block:
                    target = s
                    break
        if target is None:
            # Instantaneous virtual segment so it still shows up in the trajectory.
            self._seg_counter += 1
            label = node if (node and node != "LLM") else _EVENT_LABELS.get(e.get("event"), e.get("event"))
            seg = {
                "seg_id": self._seg_counter, "block": block or None, "node": label,
                "enter_ts": e.get("ts"), "exit_ts": e.get("ts"), "attempt": e.get("attempt"),
                "round": None, "tier": None, "enter": {}, "exit": rec["fields"],
                "status": _event_status(e.get("event"), rec["fields"]), "calls": [],
                "sub_events": [rec], "tool_runs": [], "instant": True, "virtual": True,
                "event": e.get("event"),
            }
            self.segments.append(seg)
            return
        target["sub_events"].append(rec)

    def _attach_decision_reasoning(self) -> None:
        lines = read_jsonl(self.root / ".coresmith" / "chip_lead" / "decisions.jsonl")
        # decisions.jsonl has no timestamps: pair by decision_index (1-based)
        # with the chip_lead_decision events, which do.
        by_index = {d.get("decision_index"): d for d in self.decisions if d.get("decision_index") is not None}
        for i, rec in enumerate(lines, start=1):
            d = by_index.get(i)
            if d is None:
                d = {"ts": None, "iso": None, "type": rec.get("interrupt_type"),
                     "action": rec.get("action"), "decision_index": i, "block": None, "node": "Chip Lead"}
                self.decisions.append(d)
            d["reasoning"] = rec.get("reasoning")
            if rec.get("block_name"):
                d["block"] = rec.get("block_name")
            if not d.get("type"):
                d["type"] = rec.get("interrupt_type")
            if not d.get("action"):
                d["action"] = rec.get("action")
            extra = {k: v for k, v in rec.items()
                     if k not in ("interrupt_type", "block_name", "action", "reasoning")}
            if extra:
                d["extra"] = extra
        # Locate the block(s) each decision touched: explicit block_name, else
        # the block whose segment was open at the decision time, else blocks
        # named in the reasoning.
        names = list(self.blocks_meta.keys()) or sorted({s["block"] for s in self.segments if s.get("block")})
        for d in self.decisions:
            touched: list[str] = []
            if d.get("block"):
                touched.append(d["block"])
            ts = d.get("ts")
            if ts is not None and not touched:
                for seg in self.segments:
                    if seg.get("block") and seg["enter_ts"] <= ts <= (seg["exit_ts"] or ts) and seg["node"] in HITL_NODES:
                        touched.append(seg["block"])
            text = d.get("reasoning") or ""
            for b in names:
                if b in text and b not in touched:
                    touched.append(b)
            d["blocks"] = touched
        self.decisions.sort(key=lambda d: (d.get("ts") is None, d.get("ts") or 0, d.get("decision_index") or 0))

    def _link_decisions_to_calls(self) -> None:
        """Attach the chip-lead LLM call (if any) that produced each decision."""
        lead_calls = [c for c in self.calls if (c.get("run_name") or "").startswith("Chip Lead")]
        for d in self.decisions:
            ts = d.get("ts")
            if ts is None:
                continue
            best = None
            for c in lead_calls:
                if c.get("ts") is None:
                    continue
                dt = ts - c["ts"]
                if -2.0 <= dt <= 120.0 and (best is None or dt < (ts - best["ts"])):
                    best = c
            if best is not None:
                d["call_id"] = best["call_id"]
                d["duration_s"] = best.get("duration_s")
                d["model"] = best.get("model")

    def _attach_llm_calls(self) -> None:
        block_names = list(self.blocks_meta.keys()) or sorted({s["block"] for s in self.segments if s.get("block")})
        by_thread = {s["thread_id"]: s for s in self.codex.values() if s.get("thread_id")}
        sessions_by_start = sorted(self.codex.values(), key=lambda s: s.get("wall_start") or 0)
        block_segs = [s for s in self.segments if s.get("block") and not s.get("virtual")]
        tier_segs = [s for s in self.segments if not s.get("block") and not s.get("virtual")]
        used_sessions: set[str] = set()
        for idx, rec in enumerate(self.llm_calls, start=1):
            ts = rec.get("ts")
            dur = _f(rec.get("duration_s")) or 0.0
            start = (ts - dur) if ts is not None else None
            run_name = rec.get("run_name") or ""
            block_hint = _block_from_run_name(run_name, block_names)
            usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else {}
            session = by_thread.get(usage.get("session_id")) if usage else None
            if session is None and start is not None:
                best = None
                best_d = 6.0
                for s in sessions_by_start:
                    if s["key"] in used_sessions or s.get("wall_start") is None:
                        continue
                    d = abs(s["wall_start"] - start)
                    if d < best_d:
                        best_d = d
                        best = s
                session = best
            if session is not None:
                used_sessions.add(session["key"])
            seg = None
            if ts is not None:
                cands = []
                for s in block_segs:
                    if block_hint and s["block"] != block_hint:
                        continue
                    lo = s["enter_ts"] - 2.0
                    hi = (s["exit_ts"] + 2.0) if s["exit_ts"] is not None else float("inf")
                    if (start if start is not None else ts) >= lo and ts <= hi:
                        cands.append(s)
                if cands:
                    seg = min(cands, key=lambda s: ((s["exit_ts"] or ts) - s["enter_ts"]))
                elif not block_hint:
                    for s in tier_segs:
                        lo = s["enter_ts"] - 2.0
                        hi = (s["exit_ts"] + 2.0) if s["exit_ts"] is not None else float("inf")
                        if (start if start is not None else ts) >= lo and ts <= hi:
                            seg = s
                            break
            block = seg["block"] if seg else block_hint
            node = seg["node"] if seg else _node_from_run_name(run_name)
            rnd = seg.get("round") if seg else None
            if rnd is None and block and ts is not None:
                for r in self.rounds.get(block, []):
                    if r["start_ts"] - 2 <= ts <= (r["end_ts"] or ts) + 2:
                        rnd = r["round"]
                        break
            n_cmd = n_files = n_msg = 0
            files: list[str] = []
            if session:
                for entry in session["items"]:
                    k = (entry.get("item") or {}).get("type")
                    if k == "command_execution":
                        n_cmd += 1
                    elif k == "file_change":
                        n_files += 1
                        for c in (entry["item"].get("changes") or []):
                            if c.get("path"):
                                files.append(c["path"])
                    elif k == "agent_message":
                        n_msg += 1
            resp = rec.get("response") or ""
            call = {
                "call_id": idx, "ts": ts, "iso": rec.get("iso"), "start_ts": start,
                "duration_s": dur, "model": rec.get("model"), "provider": rec.get("provider"),
                "run_name": run_name, "graph": rec.get("graph"),
                "block": block, "node": node, "round": rnd,
                "attempt": seg.get("attempt") if seg else None,
                "seg_id": seg["seg_id"] if seg else None,
                "status": "timeout" if rec.get("timed_out") else ("error" if rec.get("error") else "ok"),
                "error": rec.get("error") or None, "timed_out": bool(rec.get("timed_out")),
                "usage": usage or None,
                "system_prompt_len": rec.get("system_prompt_len") or len(rec.get("system_prompt") or ""),
                "user_prompt_len": rec.get("user_prompt_len") or len(rec.get("user_prompt") or ""),
                "response_len": rec.get("response_len") or len(resp),
                "response_preview": resp[:400],
                "session_key": session["key"] if session else None,
                "session_id": (session or {}).get("thread_id") or (usage or {}).get("session_id"),
                "n_commands": n_cmd, "n_file_changes": n_files, "n_messages": n_msg,
                "files_changed": sorted(set(files)),
            }
            self.calls.append(call)
            if seg is not None:
                seg["calls"].append(idx)

    def _attach_step_logs(self) -> None:
        root = self.root / ".coresmith" / "step_logs"
        if not root.is_dir():
            return
        block_segs = [s for s in self.segments if s.get("block") and not s.get("virtual")]
        for bdir in sorted(root.iterdir()):
            if not bdir.is_dir():
                continue
            block = bdir.name
            logs: list[dict] = []
            for p in sorted(bdir.iterdir()):
                if not p.is_file():
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                stem = p.stem
                step, _, att = stem.rpartition("_attempt")
                if not step:
                    step, att = stem, ""
                rec = {
                    "block": block, "name": p.name, "rel_path": _rel(self.root, p),
                    "step": step, "attempt": _i(att) if att.isdigit() else None,
                    "mtime": st.st_mtime, "size": st.st_size, "seg_id": None,
                }
                # Attach to the segment whose window contains the mtime.
                mt = st.st_mtime
                cands = [s for s in block_segs if s["block"] == block
                         and s["enter_ts"] - 1.0 <= mt <= ((s["exit_ts"] or mt) + 45.0)]
                if cands:
                    def _score(s):
                        strict = 0 if (s["enter_ts"] - 1.0 <= mt <= (s["exit_ts"] or mt) + 1.0) and not s.get("instant") else 1
                        return (strict, (s["exit_ts"] or mt) - s["enter_ts"])
                    seg = min(cands, key=_score)
                    rec["seg_id"] = seg["seg_id"]
                    rec["round"] = seg.get("round")
                    seg["tool_runs"].append(rec["name"])
                logs.append(rec)
            self.step_logs[block] = logs

    def _collect_interrupts(self) -> None:
        out: list[dict] = []
        for seg in self.segments:
            if seg["node"] in HITL_NODES or seg["node"] == "PPA Gate Unmeasurable":
                ex = seg.get("exit") or {}
                out.append({
                    "ts": seg["enter_ts"], "end_ts": seg["exit_ts"], "kind": "node",
                    "node": seg["node"], "block": seg.get("block"), "round": seg.get("round"),
                    "attempt": seg.get("attempt"), "status": seg["status"],
                    "action": ex.get("action"), "detail": ex,
                })
            for se in seg.get("sub_events", []):
                if se["event"] == "interrupt" and se.get("node") == seg["node"]:
                    continue  # already represented by the node segment itself
                if se["event"] in ("interrupt", "uarch_feasibility_blocked", "uarch_feasibility_resume",
                                   "escalation_response", "gate_failed"):
                    out.append({
                        "ts": se["ts"], "end_ts": None, "kind": "event", "node": se.get("node"),
                        "block": se.get("block") or seg.get("block"), "round": seg.get("round"),
                        "event": se["event"], "action": se["fields"].get("action"),
                        "detail": se["fields"],
                    })
        out.sort(key=lambda r: r["ts"] or 0)
        self.interrupts = out

    # -- lookups ------------------------------------------------------------ #
    def seg(self, seg_id: int) -> dict | None:
        for s in self.segments:
            if s["seg_id"] == seg_id:
                return s
        return None

    def call(self, call_id: int) -> dict | None:
        if 1 <= call_id <= len(self.calls):
            return self.calls[call_id - 1]
        return None

    def block_names(self) -> list[str]:
        names = list(self.blocks_meta.keys())
        for s in self.segments:
            b = s.get("block")
            if b and b not in names:
                names.append(b)
        return names

    def block_status(self, block: str) -> dict:
        segs = [s for s in self.segments if s.get("block") == block]
        done = [s for s in segs if s["node"] == "Block Done"]
        status = "not_started"
        if segs:
            status = "in_progress"
        open_seg = next((s for s in segs if s["exit_ts"] is None), None)
        if done:
            last = done[-1]
            ok = (last.get("exit") or {}).get("success")
            status = "passed" if ok else "failed"
            later = [s for s in segs if s["enter_ts"] > last["enter_ts"]]
            if later:
                status = "in_progress"
        if open_seg is not None and self.is_live:
            status = "waiting" if open_seg["node"] in HITL_NODES else "running"
        elif open_seg is not None and status == "in_progress":
            status = "waiting" if open_seg["node"] in HITL_NODES else "incomplete"
        rounds = self.rounds.get(block, [])
        rtl_attempts = max([s.get("attempt") or 0 for s in segs if s["node"] == "Generate RTL"] + [0])
        return {
            "status": status, "rounds": len(rounds), "rtl_attempts": rtl_attempts,
            "first_ts": min([s["enter_ts"] for s in segs]) if segs else None,
            "last_ts": max([(s["exit_ts"] or s["enter_ts"]) for s in segs]) if segs else None,
            "open_node": open_seg["node"] if open_seg else None,
            "llm_calls": sum(1 for c in self.calls if c.get("block") == block),
            "llm_time_s": round(sum(c.get("duration_s") or 0 for c in self.calls if c.get("block") == block), 1),
        }


_EVENT_LABELS = {
    "llm_timed out": "LLM timeout", "gate_result": "Gate Sim", "mem_price": "Memory price",
    "gate_failed": "Gate failed", "lint_retry_bypass": "Lint retry bypass",
    "ppa_retry_bypass": "PPA retry bypass", "uarch_feasibility_blocked": "uArch feasibility blocked",
    "uarch_feasibility_resume": "uArch feasibility resume", "interrupt": "Interrupt",
}


def _event_status(event: str | None, fields: dict) -> str:
    st = fields.get("status")
    if st in ("not_run", "skipped"):
        return "skipped"
    if event in ("llm_timed out", "gate_failed", "uarch_feasibility_blocked"):
        return "failed"
    if event in ("interrupt",):
        return "waiting"
    return _exit_status(fields)


def _exit_status(fields: dict) -> str:
    if not fields:
        return "done"
    if fields.get("error"):
        return "failed"
    for flag in ("success", "passed", "clean", "all_pass", "lint_clean", "sim_passed", "ok"):
        v = fields.get(flag)
        if v is False:
            return "failed"
    if fields.get("status") == "error":
        return "failed"
    return "done"


_INDEX_CACHE: dict[str, tuple[tuple, RunIndex]] = {}


def get_index(root: Path) -> RunIndex:
    """Return a cached RunIndex, rebuilt when any of its source files change."""
    root = Path(root)
    cs = root / ".coresmith"
    sig = tuple(_stat_sig(cs / n) for n in (
        "pipeline_events.jsonl", "llm_calls.jsonl", "codex_turns.jsonl",
        "project.sqlite", "block_diagram.json", "block_specs.json"))
    sig = sig + (_stat_sig(cs / "chip_lead" / "decisions.jsonl"),)
    hit = _INDEX_CACHE.get(str(root))
    if hit and hit[0] == sig:
        return hit[1]
    idx = RunIndex(root)
    _INDEX_CACHE[str(root)] = (sig, idx)
    return idx


# --------------------------------------------------------------------------- #
# Per-block artefacts
# --------------------------------------------------------------------------- #

def _block_dir(root: Path, block: str) -> Path:
    return root / ".coresmith" / "blocks" / block


def _read_block_json(root: Path, block: str, name: str, default=None):
    return read_json(_block_dir(root, block) / name, default)


def block_synth_report(root: Path, block: str) -> dict:
    """Yosys stat parse of syn/output/<block>/<block>_report.txt (+ sdc / netlist)."""
    out_dir = root / "syn" / "output" / block
    rpt = out_dir / f"{block}_report.txt"
    result: dict[str, Any] = {"report_rel_path": None, "stat": None, "sdc": None,
                              "period_ns": None, "netlist_rel_path": None, "script_rel_path": None}
    if rpt.exists():
        result["report_rel_path"] = _rel(root, rpt)
        result["report_mtime"] = rpt.stat().st_mtime
        result["stat"] = _cached("yosys", rpt, lambda p: parse_yosys_stat(read_text(p)) if p else None)
    sdc = out_dir / f"{block}.sdc"
    if sdc.exists():
        txt = read_text(sdc)
        result["sdc"] = txt
        result["sdc_rel_path"] = _rel(root, sdc)
        m = re.search(r"create_clock\s+.*?-period\s+([\d.]+)", txt)
        if m:
            result["period_ns"] = _f(m.group(1))
    nl = out_dir / f"{block}_netlist.v"
    if nl.exists():
        result["netlist_rel_path"] = _rel(root, nl)
        result["netlist_size"] = nl.stat().st_size
    ys = out_dir / f"synth_{block}.ys"
    if ys.exists():
        result["script_rel_path"] = _rel(root, ys)
    return result


_STA_CANDIDATES = ("*sta*.rpt", "*sta*.txt", "*sta*.log", "timing*.rpt", "timing*.txt", "*_timing.log")


def block_sta_report(root: Path, block: str) -> dict:
    """Any persisted OpenSTA text for the block (none is written by the current
    engine; see docs/WEBVIEW.md 'known gaps')."""
    found: list[Path] = []
    for d in (root / "syn" / "output" / block, root / ".coresmith" / "step_logs" / block,
              _block_dir(root, block)):
        if not d.is_dir():
            continue
        for pat in _STA_CANDIDATES:
            found.extend(sorted(d.glob(pat)))
    seen = set()
    reports = []
    for p in found:
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        parsed = parse_sta_report(read_text(p))
        reports.append({"rel_path": _rel(root, p), "mtime": p.stat().st_mtime, **parsed})
    return {"reports": reports, "persisted": bool(reports)}


def block_files(root: Path, block: str, meta: dict | None = None) -> dict:
    """RTL / TB / spec files for the block, including attempt backups."""
    meta = meta or {}
    out: dict[str, Any] = {"rtl": None, "rtl_versions": [], "tb": [], "spec": None,
                           "spec_review": None, "spec_versions": []}
    rtl_rel = meta.get("rtl_target")
    rtl_path = safe_rel_path(root, rtl_rel) if rtl_rel else None
    if rtl_path is None or not rtl_path.exists():
        for p in (root / "rtl").rglob(f"{block}.v") if (root / "rtl").is_dir() else []:
            rtl_path = p
            rtl_rel = _rel(root, p)
            break
    if rtl_path is not None and rtl_path.exists():
        st = rtl_path.stat()
        out["rtl"] = {"rel_path": rtl_rel, "size": st.st_size, "mtime": st.st_mtime}
    bdir = _block_dir(root, block)
    versions: list[dict] = []
    if bdir.is_dir():
        for p in sorted(bdir.glob("rtl_backup_attempt*.v")):
            m = re.search(r"attempt(\d+)", p.name)
            n = _i(m.group(1)) if m else None
            st = p.stat()
            versions.append({
                "label": f"RTL before attempt {n}" if n else p.name,
                "attempt_before": n, "rel_path": _rel(root, p),
                "size": st.st_size, "mtime": st.st_mtime,
            })
    versions.sort(key=lambda v: v["mtime"])
    if out["rtl"]:
        versions.append({"label": "current RTL", "attempt_before": None, **out["rtl"]})
    out["rtl_versions"] = versions
    tb_dir = root / "tb"
    if tb_dir.is_dir():
        for p in sorted(tb_dir.rglob("*")):
            if p.is_file() and block in p.name and p.suffix in (".py", ".v", ".sv"):
                st = p.stat()
                out["tb"].append({"rel_path": _rel(root, p), "size": st.st_size, "mtime": st.st_mtime,
                                  "kind": "model" if p.name.endswith("_model.py") else "testbench"})
    spec = root / "arch" / "uarch_specs" / f"{block}.md"
    if spec.exists():
        st = spec.stat()
        out["spec"] = {"rel_path": _rel(root, spec), "size": st.st_size, "mtime": st.st_mtime}
    rev = root / "arch" / "uarch_specs_review" / f"{block}.md"
    if rev.exists():
        st = rev.stat()
        out["spec_review"] = {"rel_path": _rel(root, rev), "size": st.st_size, "mtime": st.st_mtime}
    # Spec drafts written inside codex-call-* workspaces (one per uArch call).
    for p in sorted(root.glob(f"codex-call-*/arch/uarch_specs/{block}.md")):
        st = p.stat()
        out["spec_versions"].append({"rel_path": _rel(root, p), "size": st.st_size, "mtime": st.st_mtime,
                                     "workspace": p.parts[len(root.parts)]})
    out["spec_versions"].sort(key=lambda v: v["mtime"])
    if out["spec"]:
        out["spec_versions"].append({"label": "current spec", **out["spec"]})
    return out


def block_db_results(root: Path, block: str) -> dict:
    con = open_project_db(root)
    out = {
        "attempts": [], "diagnoses": [], "constraints": [], "results": {},
        "dv_results": [], "ppa_history": [], "coverage_results": [], "db": con is not None,
    }
    if con is None:
        # JSON views
        out["attempts"] = _read_block_json(root, block, "attempt_history.json", []) or []
        diag = _read_block_json(root, block, "diagnosis.json", None)
        if diag:
            out["diagnoses"] = [{"attempt": diag.get("attempt"), "category": diag.get("category"),
                                 "confidence": diag.get("confidence"), "diagnosis": diag, "ts": None}]
        out["constraints"] = _read_block_json(root, block, "constraints.json", []) or []
        best = _read_block_json(root, block, "best_result.json", None)
        if best:
            out["results"]["best"] = best
        return out
    try:
        for r in db_rows(con, "select * from attempts where block=? order by id", (block,)):
            out["attempts"].append({"attempt": r.get("attempt"), "category": r.get("category"),
                                    "error": r.get("error"), "ts": r.get("ts"),
                                    **(_uj(r.get("extra_json"), {}) or {})})
        for r in db_rows(con, "select * from diagnoses where block=? order by id", (block,)):
            out["diagnoses"].append({"attempt": r.get("attempt"), "category": r.get("category"),
                                     "confidence": r.get("confidence"), "ts": r.get("ts"),
                                     "diagnosis": _uj(r.get("diagnosis_json"), {}) or {}})
        for r in db_rows(con, "select * from constraints where block=? order by id", (block,)):
            out["constraints"].append({"rule": r.get("rule"), "source": r.get("source"),
                                       "attempt": r.get("attempt"), "ts": r.get("ts"),
                                       **(_uj(r.get("extra_json"), {}) or {})})
        for r in db_rows(con, "select * from results where block=?", (block,)):
            out["results"][r["kind"]] = _uj(r.get("value_json"), {})
        for r in db_rows(con, "select * from dv_results where block=? order by id", (block,)):
            r["first_divergence"] = _uj(r.get("first_divergence"), None)
            r["log_rel_path"] = rebase_path(root, r.get("log_path"))
            out["dv_results"].append(r)
        for r in db_rows(con, "select * from ppa_history where block=? order by id", (block,)):
            r["reasons"] = _uj(r.get("reasons"), None)
            r["report_rel_path"] = rebase_path(root, r.get("report_path"))
            out["ppa_history"].append(r)
        for r in db_rows(con, "select * from coverage_results where block=? order by id", (block,)):
            r["uncovered"] = _uj(r.get("uncovered"), None)
            out["coverage_results"].append(r)
    finally:
        con.close()
    if not out["results"].get("best"):
        best = _read_block_json(root, block, "best_result.json", None)
        if best:
            out["results"]["best"] = best
    if not out["attempts"]:
        out["attempts"] = _read_block_json(root, block, "attempt_history.json", []) or []
    return out


def _latest_gate_ppa(ppa_rows: list[dict]) -> dict | None:
    gate = [r for r in ppa_rows if r.get("source") == "gate" and r.get("cells")]
    if not gate:
        gate = [r for r in ppa_rows if r.get("source") == "gate"]
    return gate[-1] if gate else None


def build_block_review(root: Path, block: str) -> dict:
    """The full design-review payload for one block."""
    idx = get_index(root)
    meta = idx.blocks_meta.get(block) or {"name": block}
    db = block_db_results(root, block)
    # Round number for each ppa_history row (by timestamp).
    for r in db["ppa_history"]:
        r["round"] = None
        for rr in idx.rounds.get(block, []):
            if rr["start_ts"] - 2 <= (r.get("ts") or 0) <= (rr["end_ts"] or float("inf")) + 2:
                r["round"] = rr["round"]
                break

    files = block_files(root, block, meta)
    synth = block_synth_report(root, block)
    sta = block_sta_report(root, block)
    status = idx.block_status(block)

    best = db["results"].get("best") or _read_block_json(root, block, "best_result.json", {}) or {}
    coverage = _read_block_json(root, block, "coverage.json", None) or best.get("coverage")
    throughput = _read_block_json(root, block, "throughput.json", None) or best.get("throughput")
    dv_summary = _read_block_json(root, block, "dv_summary.json", None)
    gate_sim = _read_block_json(root, block, "gate_sim_report.json", None)
    conformance = _read_block_json(root, block, "contract_conformance.json", None)
    mem_price = _read_block_json(root, block, "mem_price.json", None)
    ppa_report = _read_block_json(root, block, "ppa_report.json", None)
    provenance = _read_block_json(root, block, "provenance.json", None)
    failure_sig = _read_block_json(root, block, "_failure_sig.json", None)
    prev_err_path = _block_dir(root, block) / "previous_error.txt"
    previous_error = read_text(prev_err_path) if prev_err_path.exists() else ""

    # Simulation: latest simulate log parse (tests table)
    sim_logs = [entry for entry in idx.step_logs.get(block, []) if entry["step"] == "simulate"]
    sim_logs.sort(key=lambda entry: entry["mtime"])
    sim_parsed = None
    if sim_logs:
        p = root / sim_logs[-1]["rel_path"]
        sim_parsed = _cached("cocotb", p, lambda q: parse_cocotb_results(read_text(q)) if q else None)
        sim_parsed = dict(sim_parsed or {})
        sim_parsed["log_rel_path"] = sim_logs[-1]["rel_path"]
        sim_parsed["mtime"] = sim_logs[-1]["mtime"]
    lint_logs = [entry for entry in idx.step_logs.get(block, []) if entry["step"] == "lint"]
    lint_logs.sort(key=lambda entry: entry["mtime"])
    lint_parsed = None
    if lint_logs:
        p = root / lint_logs[-1]["rel_path"]
        lint_parsed = _cached("lint", p, lambda q: parse_verilator_lint(read_text(q)) if q else None)
        lint_parsed = dict(lint_parsed or {})
        lint_parsed["log_rel_path"] = lint_logs[-1]["rel_path"]

    # Timing: WNS per attempt from ppa_history + sdc period
    period = synth.get("period_ns")
    if period is None and ppa_report:
        for c in ppa_report.get("checks", []):
            if c.get("metric") == "wns_ns" and c.get("period_ns"):
                period = c["period_ns"]
    wns_rows = [{"attempt": r.get("attempt"), "ts": r.get("ts"), "wns_ns": r.get("wns_ns"), "round": r.get("round"),
                 "cells": r.get("cells"), "ff": r.get("ff"), "area_um2": r.get("area_um2"),
                 "ppa_ok": r.get("ppa_ok"), "reasons": r.get("reasons"), "probe": r.get("probe"),
                 "source": r.get("source")}
                for r in db["ppa_history"]]
    latest = _latest_gate_ppa(db["ppa_history"])
    wns = latest.get("wns_ns") if latest else None
    fmax = None
    if wns is not None and period:
        denom = period - wns
        fmax = round(1000.0 / denom, 2) if denom > 0 else None
    sta_paths = []
    tns = None
    for rep in sta.get("reports", []):
        sta_paths.extend(rep.get("paths") or [])
        if rep.get("tns_ns") is not None:
            tns = rep["tns_ns"]
        if wns is None and rep.get("wns_ns") is not None:
            wns = rep["wns_ns"]
    timing = {
        "period_ns": period, "wns_ns": wns, "tns_ns": tns, "fmax_mhz": fmax,
        "wns_check": next((c for c in (ppa_report or {}).get("checks", []) if c.get("metric") == "wns_ns"), None),
        "history": wns_rows, "paths": sta_paths, "sta_persisted": sta.get("persisted"),
        "sta_reports": [{"rel_path": r.get("rel_path")} for r in sta.get("reports", [])],
        "sdc_rel_path": synth.get("sdc_rel_path"),
        "note": None if sta.get("persisted") else (
            "The engine measures WNS with OpenSTA in a temporary directory and persists only "
            "the worst slack (ppa_history.wns_ns). TNS and the failing-path report "
            "(report_checks) are not written to the run directory."),
    }

    # Synthesis summary: budgets vs measured
    stat = synth.get("stat") or {}
    sram_area = (mem_price or {}).get("total_area_um2")
    checks = list((ppa_report or {}).get("checks", []))
    synth_summary = {
        "cells": (latest or {}).get("cells") if latest else stat.get("cells"),
        "ff": (latest or {}).get("ff") if latest else stat.get("ff"),
        "std_cell_area_um2": stat.get("chip_area_um2"),
        "area_um2": (latest or {}).get("area_um2") if latest else stat.get("chip_area_um2"),
        "sram_area_um2": sram_area,
        "sequential_pct": stat.get("sequential_pct"),
        "macros": stat.get("macros") or [],
        "unknown_area_types": stat.get("unknown_area_types") or [],
        "ppa_ok": (latest or {}).get("ppa_ok") if latest else (ppa_report or {}).get("ppa_ok"),
        "reasons": (latest or {}).get("reasons") if latest else (ppa_report or {}).get("reasons"),
        "budgets": {
            "estimated_gates": meta.get("estimated_gates"),
            "flip_flop_budget": meta.get("flip_flop_budget"),
            "area_budget_um2": meta.get("area_budget_um2"),
        },
        "checks": checks,
        "report_rel_path": synth.get("report_rel_path"),
        "netlist_rel_path": synth.get("netlist_rel_path"),
        "netlist_size": synth.get("netlist_size"),
        "script_rel_path": synth.get("script_rel_path"),
        "cell_types": (stat.get("cell_types") or [])[:400],
        "module": stat.get("module"),
        "history": [r for r in db["ppa_history"]],
        "synth_logs": [entry for entry in idx.step_logs.get(block, []) if entry["step"] == "synthesize"],
    }

    # Diagnoses as recorded in the event stream.  The sqlite diagnoses /
    # attempts tables are cleared when a block re-enters its loop (new round),
    # so earlier rounds only survive here (previews are truncated by the
    # engine).
    event_diagnoses = []
    for s in idx.segments:
        if s.get("block") != block:
            continue
        if s["node"] == "Diagnose Failure":
            ex = s.get("exit") or {}
            event_diagnoses.append({
                "ts": s["exit_ts"] or s["enter_ts"], "round": s.get("round"),
                "phase": (s.get("enter") or {}).get("phase"), "category": ex.get("category"),
                "confidence": ex.get("confidence"), "needs_human": ex.get("needs_human"),
                "suggested_fix": ex.get("suggested_fix"), "diagnosis_preview": ex.get("diagnosis_preview"),
                "failure_signature": ex.get("failure_signature"), "repeat_count": ex.get("repeat_count"),
                "call_ids": list(s.get("calls") or []),
            })
    route_decisions = [{"ts": s["exit_ts"], "round": s.get("round"), "attempt": s.get("attempt"),
                        "decision": (s.get("exit") or {}).get("decision")}
                       for s in idx.segments if s.get("block") == block and s["node"] == "Route Decision"]
    # Decisions / interrupts touching this block
    decisions = [d for d in idx.decisions if block in (d.get("blocks") or [])]
    interrupts = [i for i in idx.interrupts if i.get("block") == block]

    # Carried-forward defects mentioning the block
    cfd = read_json(root / ".coresmith" / "carried_forward_defects.json", []) or []
    cfd = [d for d in cfd if isinstance(d, dict) and (d.get("first_divergence_block") == block or block in json.dumps(d))]

    # Contracts touching the block
    contracts = []
    con = open_project_db(root)
    if con is not None:
        for r in db_rows(con, "select edge_id, producer_block, producer_port, consumer_block, consumer_port, handshake_protocol, data_width_bits, version from contracts where producer_block=? or consumer_block=? order by ordinal", (block, block)):
            contracts.append(r)
        con.close()
    else:
        ic = read_json(root / ".coresmith" / "interface_contracts.json", {}) or {}
        for e in (ic.get("edges") or ic.get("contracts") or []) if isinstance(ic, dict) else []:
            if isinstance(e, dict) and block in (e.get("producer_block"), e.get("consumer_block")):
                contracts.append({k: e.get(k) for k in ("edge_id", "producer_block", "producer_port",
                                                        "consumer_block", "consumer_port",
                                                        "handshake_protocol", "data_width_bits", "version")})

    gates = _block_gates(best, coverage, throughput, gate_sim, conformance, mem_price, synth_summary, timing, lint_parsed)
    return {
        "block": block, "meta": meta, "status": status, "files": files,
        "spec_summary": _spec_summary(root, block),
        "sim": {
            "best": best, "dv_summary": dv_summary, "coverage": coverage, "throughput": throughput,
            "parsed": sim_parsed, "dv_results": db["dv_results"], "coverage_results": db["coverage_results"],
            "sim_logs": sim_logs, "gate_sim": gate_sim,
        },
        "lint": lint_parsed,
        "synth": synth_summary, "timing": timing, "mem_price": mem_price,
        "issues": {
            "attempts": db["attempts"], "diagnoses": db["diagnoses"], "constraints": db["constraints"],
            "event_diagnoses": event_diagnoses, "route_decisions": route_decisions,
            "previous_error": previous_error, "previous_error_rel_path": _rel(root, prev_err_path) if prev_err_path.exists() else None,
            "conformance": conformance, "carried_forward_defects": cfd, "failure_signature": failure_sig,
            "provenance": provenance,
        },
        "decisions": decisions, "interrupts": interrupts, "contracts": contracts,
        "rounds": [{k: v for k, v in r.items() if k != "segments"} for r in idx.rounds.get(block, [])],
        "gates": gates,
    }


def _spec_summary(root: Path, block: str) -> dict | None:
    p = root / "arch" / "uarch_specs" / f"{block}.md"
    if not p.exists():
        return None
    content = read_text(p)
    summary = None
    m = re.search(r"```json\s*\n(.*?)```", content, re.S)
    if m:
        try:
            summary = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            summary = None
    overview = ""
    om = re.search(r"##\s*1\.\s*Block\s+Overview\s*\n(.*?)(?=\n##\s|\Z)", content, re.S)
    if om:
        overview = om.group(1).strip().split("\n\n")[0].strip()[:800]
    headings = re.findall(r"^(#{1,3})\s+(.+)$", content, re.M)
    return {"summary": summary, "overview": overview, "size": len(content),
            "headings": [{"level": len(h[0]), "text": h[1].strip()} for h in headings][:60]}


def _block_gates(best, coverage, throughput, gate_sim, conformance, mem_price, synth, timing, lint) -> list[dict]:
    """Plain pass/fail summary of every gate the block was judged by."""
    gates = []

    def add(name, passed, detail, measured=None, budget=None):
        gates.append({"gate": name, "passed": passed, "detail": detail,
                      "measured": measured, "budget": budget})

    if lint:
        add("lint", (lint.get("error_count") or 0) == 0,
            f"{lint.get('error_count', 0)} errors, {lint.get('warning_count', 0)} warnings")
    if best:
        tp, tt = best.get("tests_passed"), best.get("tests_total")
        add("simulation", best.get("sim_passed"),
            f"{tp}/{tt} tests passed" if tt is not None else ("passed" if best.get("sim_passed") else "failed"),
            measured=tp, budget=tt)
    if coverage and coverage.get("applicable") is not False:
        add("coverage", coverage.get("passed"),
            f"{coverage.get('pct')}% (floor {coverage.get('floor')}%)",
            measured=coverage.get("pct"), budget=coverage.get("floor"))
    if throughput:
        if throughput.get("applicable"):
            add("throughput", throughput.get("passed"),
                f"{throughput.get('measured_cyc_per_op')} cyc/op measured vs {throughput.get('declared_cyc_per_op')} declared (threshold {throughput.get('threshold_cyc_per_op')})",
                measured=throughput.get("measured_cyc_per_op"), budget=throughput.get("threshold_cyc_per_op"))
        else:
            add("throughput", None, throughput.get("reason") or "not applicable")
    if gate_sim:
        add("gate_sim", gate_sim.get("ok") if gate_sim.get("ran") else None,
            gate_sim.get("status") + (": " + gate_sim.get("reason") if gate_sim.get("reason") else ""))
    if conformance:
        add("contract_conformance", conformance.get("ok") if conformance.get("ran") else None,
            f"{conformance.get('checked_edges', 0)} edges checked, {len(conformance.get('deviations') or [])} deviations")
    if mem_price:
        add("mem_price", mem_price.get("ok"),
            f"{mem_price.get('total_area_um2')} um2 SRAM/ROM vs budget {mem_price.get('area_budget_um2')}",
            measured=mem_price.get("total_area_um2"), budget=mem_price.get("area_budget_um2"))
    for c in synth.get("checks") or []:
        add(f"ppa:{c.get('metric')}", c.get("passed"),
            f"actual {c.get('actual')} vs budget {c.get('budget')} (limit {c.get('limit')})",
            measured=c.get("actual"), budget=c.get("budget") if c.get("budget") is not None else c.get("limit"))
    if not any(g["gate"].startswith("ppa:") for g in gates) and synth.get("ppa_ok") is not None:
        add("ppa", bool(synth.get("ppa_ok")), "; ".join(synth.get("reasons") or []) or "within budget")
    if timing.get("wns_ns") is not None:
        add("timing", timing["wns_ns"] >= 0, f"WNS {timing['wns_ns']} ns at {timing.get('period_ns')} ns period",
            measured=timing["wns_ns"], budget=0)
    return gates


# --------------------------------------------------------------------------- #
# Block trajectory
# --------------------------------------------------------------------------- #

NODE_ORDER_HINT = ["Init Block", "Generate Uarch Spec", "Review Uarch Spec", "Generate RTL",
                   "Lint Fix", "Generate Testbench", "TB Fix", "Synthesize", "Gate Sim",
                   "Contract Conformance", "PPA Gate Unmeasurable", "Diagnose Failure",
                   "Route Decision", "Ask Human", "Block Done"]


def build_block_trajectory(root: Path, block: str) -> dict:
    idx = get_index(root)
    rounds_out = []
    for r in idx.rounds.get(block, []):
        segs = [idx.seg(sid) for sid in r["segments"]]
        segs = [s for s in segs if s]
        segs.sort(key=lambda s: (s["enter_ts"], s["seg_id"]))
        seg_out = []
        for s in segs:
            calls = [idx.call(cid) for cid in s["calls"]]
            calls = [c for c in calls if c]
            logs = [entry for entry in idx.step_logs.get(block, []) if entry.get("seg_id") == s["seg_id"]]
            steps: list[dict] = []
            for c in calls:
                steps.append({"type": "llm_call", "ts": c["start_ts"] or c["ts"], **c})
            for entry in logs:
                steps.append({"type": "tool_run", "ts": entry["mtime"], **entry})
            for se in s.get("sub_events", []):
                steps.append({"type": "event", **se})
            steps.sort(key=lambda x: x.get("ts") or 0)
            seg_out.append({
                "seg_id": s["seg_id"], "node": s["node"], "enter_ts": s["enter_ts"],
                "exit_ts": s["exit_ts"], "duration_s": (s["exit_ts"] - s["enter_ts"]) if s["exit_ts"] else None,
                "attempt": s.get("attempt"), "status": s["status"], "enter": s.get("enter"),
                "exit": s.get("exit"), "instant": s.get("instant", False),
                "steps": steps, "n_calls": len(calls), "n_tool_runs": len(logs),
                "hitl": s["node"] in HITL_NODES,
            })
        rounds_out.append({
            "round": r["round"], "start_ts": r["start_ts"], "end_ts": r["end_ts"],
            "duration_s": (r["end_ts"] - r["start_ts"]) if r["end_ts"] else None,
            "outcome": r.get("outcome"), "decision": r.get("decision"), "tier": r.get("tier"),
            "segments": seg_out,
            "n_calls": sum(x["n_calls"] for x in seg_out),
            "llm_time_s": round(sum(c.get("duration_s") or 0 for x in seg_out for c in x["steps"] if c["type"] == "llm_call"), 1),
        })
    # Calls attributed to the block but to no node segment (e.g. chip-lead
    # calls, lint-fix inner calls outside a node window).
    orphan_calls = [c for c in idx.calls if c.get("block") == block and c.get("seg_id") is None]
    orphan_logs = [entry for entry in idx.step_logs.get(block, []) if entry.get("seg_id") is None]
    return {
        "block": block, "meta": idx.blocks_meta.get(block) or {"name": block},
        "status": idx.block_status(block), "rounds": rounds_out,
        "unattributed": {"calls": orphan_calls, "tool_runs": orphan_logs},
        "decisions": [d for d in idx.decisions if block in (d.get("blocks") or [])],
        "is_live": idx.is_live,
    }


# --------------------------------------------------------------------------- #
# LLM call detail
# --------------------------------------------------------------------------- #

def build_call_detail(root: Path, call_id: int, max_chars: int = 4000) -> dict | None:
    idx = get_index(root)
    call = idx.call(call_id)
    if call is None:
        return None
    rec = idx.llm_calls[call_id - 1]
    session = idx.codex.get(call["session_key"]) if call.get("session_key") else None
    turns = []
    if session:
        for i, entry in enumerate(session["items"]):
            t = _summarize_item(entry, max_chars=max_chars)
            t["index"] = i
            if t["kind"] == "file_change":
                for c in t["changes"]:
                    c["rel_path"] = rebase_path(root, c.get("path"))
                    c["exists"] = c["rel_path"] is not None
            turns.append(t)
    seg = idx.seg(call["seg_id"]) if call.get("seg_id") else None
    return {
        **call,
        "system_prompt": rec.get("system_prompt") or "",
        "user_prompt": rec.get("user_prompt") or "",
        "response": rec.get("response") or "",
        "timeout_s": rec.get("timeout"),
        "call_index": rec.get("call_index"),
        "session": {
            "key": session["key"], "thread_id": session.get("thread_id"), "pid": session.get("pid"),
            "wall_start": session.get("wall_start"), "first_ts": session.get("first_ts"),
            "last_ts": session.get("last_ts"), "turn_count": session.get("turn_count"),
            "usage": session.get("usage"), "n_items": len(session["items"]),
        } if session else None,
        "turns": turns,
        "segment": {"seg_id": seg["seg_id"], "node": seg["node"], "block": seg.get("block"),
                    "round": seg.get("round"), "attempt": seg.get("attempt"),
                    "status": seg["status"], "exit": seg.get("exit")} if seg else None,
    }


def build_call_turn(root: Path, call_id: int, turn_index: int) -> dict | None:
    idx = get_index(root)
    call = idx.call(call_id)
    if call is None or not call.get("session_key"):
        return None
    session = idx.codex.get(call["session_key"])
    if session is None or not (0 <= turn_index < len(session["items"])):
        return None
    entry = session["items"][turn_index]
    t = _summarize_item(entry, max_chars=10**9)
    t["index"] = turn_index
    if t["kind"] == "file_change":
        for c in t["changes"]:
            c["rel_path"] = rebase_path(root, c.get("path"))
            c["exists"] = c["rel_path"] is not None
    return t


def list_calls(root: Path, block: str | None = None, node: str | None = None) -> list[dict]:
    idx = get_index(root)
    out = []
    for c in idx.calls:
        if block and c.get("block") != block:
            continue
        if node and c.get("node") != node:
            continue
        out.append(c)
    return out


# --------------------------------------------------------------------------- #
# Text slices / diffs
# --------------------------------------------------------------------------- #

def text_slice(root: Path, rel: str, offset: int = 0, limit: int = 400, tail: bool = False) -> dict | None:
    p = safe_rel_path(root, rel)
    if p is None or not p.is_file():
        return None
    st = p.stat()
    # Cache the split lines per file signature so paging is cheap.
    lines = _cached("lines", p, lambda q: read_text(q).splitlines() if q else [])
    total = len(lines)
    limit = max(1, min(int(limit), 5000))
    if tail:
        offset = max(0, total - limit)
    offset = max(0, min(int(offset), total))
    chunk = lines[offset: offset + limit]
    meta = parse_step_log_header("\n".join(lines[:10])) if rel.endswith(".log") else {}
    return {
        "rel_path": rel, "size": st.st_size, "mtime": st.st_mtime, "total_lines": total,
        "offset": offset, "count": len(chunk), "lines": chunk, "header": meta,
        "eof": offset + len(chunk) >= total,
    }


def text_search(root: Path, rel: str, query: str, max_hits: int = 200) -> dict | None:
    p = safe_rel_path(root, rel)
    if p is None or not p.is_file() or not query:
        return None
    lines = _cached("lines", p, lambda q: read_text(q).splitlines() if q else [])
    q = query.lower()
    hits = []
    for i, ln in enumerate(lines):
        if q in ln.lower():
            hits.append({"line": i, "text": ln[:300]})
            if len(hits) >= max_hits:
                break
    return {"rel_path": rel, "query": query, "hits": hits, "total_lines": len(lines),
            "truncated": len(hits) >= max_hits}


def unified_diff(root: Path, rel_a: str, rel_b: str, context: int = 3) -> dict | None:
    pa = safe_rel_path(root, rel_a)
    pb = safe_rel_path(root, rel_b)
    if pa is None or pb is None or not pa.is_file() or not pb.is_file():
        return None
    a = read_text(pa).splitlines()
    b = read_text(pb).splitlines()
    diff = list(difflib.unified_diff(a, b, fromfile=rel_a, tofile=rel_b, lineterm="", n=context))
    added = sum(1 for entry in diff if entry.startswith("+") and not entry.startswith("+++"))
    removed = sum(1 for entry in diff if entry.startswith("-") and not entry.startswith("---"))
    return {"a": rel_a, "b": rel_b, "lines": diff[:20000], "added": added, "removed": removed,
            "truncated": len(diff) > 20000, "identical": not diff}


# --------------------------------------------------------------------------- #
# Run overview
# --------------------------------------------------------------------------- #

_SECRET_RE = re.compile(r"(token|key|secret|password|passwd|credential)", re.I)


def load_env_file(root: Path) -> list[dict]:
    p = root / ".coresmith" / "env"
    if not p.is_file():
        return []
    out = []
    for line in read_text(p).splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        if _SECRET_RE.search(k):
            v = "<redacted>"
        out.append({"key": k.strip(), "value": v.strip()})
    return out


def run_settings(root: Path) -> dict:
    con = open_project_db(root)
    settings = {r["name"]: r["value"] for r in db_rows(con, "select name, value from settings")}
    if con is not None:
        con.close()
    engine = read_json(root / ".coresmith" / "engine_sha.json", {}) or {}
    daemon = read_json(root / ".coresmith" / "daemon.json", {}) or {}
    final = read_json(root / "final_report.json", {}) or {}
    return {
        "engine_sha": settings.get("engine_sha") or engine.get("sha") or final.get("engine_sha"),
        "engine_sha_first_seen": settings.get("engine_sha_first_seen") or engine.get("first_seen"),
        "engine_sha_changed": engine.get("changed"),
        "settings": settings,
        "daemon": daemon,
        "env": load_env_file(root),
        "project_root": str(root),
        "original_roots": original_roots(root),
        "has_sqlite": (root / ".coresmith" / "project.sqlite").exists(),
        "target_clock_mhz": final.get("target_clock_mhz"),
    }


def build_blocks_table(root: Path) -> list[dict]:
    idx = get_index(root)
    con = open_project_db(root)
    ppa_by_block: dict[str, list[dict]] = {}
    dv_by_block: dict[str, list[dict]] = {}
    cov_by_block: dict[str, list[dict]] = {}
    for r in db_rows(con, "select * from ppa_history order by id"):
        ppa_by_block.setdefault(r["block"], []).append(r)
    for r in db_rows(con, "select * from dv_results where source='gate' order by id"):
        dv_by_block.setdefault(r["block"], []).append(r)
    for r in db_rows(con, "select * from coverage_results order by id"):
        cov_by_block.setdefault(r["block"], []).append(r)
    if con is not None:
        con.close()
    rows = []
    for name in idx.block_names():
        meta = idx.blocks_meta.get(name) or {"name": name}
        st = idx.block_status(name)
        best = _read_block_json(root, name, "best_result.json", {}) or {}
        cov = _read_block_json(root, name, "coverage.json", None) or best.get("coverage") or {}
        thr = _read_block_json(root, name, "throughput.json", None) or best.get("throughput") or {}
        gate_sim = _read_block_json(root, name, "gate_sim_report.json", {}) or {}
        conf = _read_block_json(root, name, "contract_conformance.json", None)
        memp = _read_block_json(root, name, "mem_price.json", None)
        ppa_rows = ppa_by_block.get(name, [])
        latest = _latest_gate_ppa(ppa_rows)
        if latest is None:
            synth = block_synth_report(root, name)
            stat = synth.get("stat") or {}
            latest = {"cells": stat.get("cells"), "ff": stat.get("ff"),
                      "area_um2": stat.get("chip_area_um2"), "wns_ns": None,
                      "ppa_ok": None, "source": "yosys_report"} if stat.get("cells") else None
        dv_rows = dv_by_block.get(name, [])
        last_dv = dv_rows[-1] if dv_rows else None
        rows.append({
            "name": name, "tier": meta.get("tier"), "subsystem": meta.get("subsystem"),
            "description": meta.get("description"), "status": st["status"], "rounds": st["rounds"],
            "rtl_attempts": st["rtl_attempts"], "llm_calls": st["llm_calls"], "llm_time_s": st["llm_time_s"],
            "open_node": st.get("open_node"),
            "dv": {
                "passed": best.get("sim_passed") if best else (bool(last_dv.get("passed")) if last_dv else None),
                "tests_passed": best.get("tests_passed") if best else (last_dv or {}).get("tests_passed"),
                "tests_total": best.get("tests_total") if best else (last_dv or {}).get("tests_total"),
            },
            "coverage_pct": cov.get("pct"), "coverage_passed": cov.get("passed"),
            "throughput": {"applicable": thr.get("applicable"), "passed": thr.get("passed"),
                           "measured": thr.get("measured_cyc_per_op"), "declared": thr.get("declared_cyc_per_op")},
            "gate_sim": gate_sim.get("status"),
            "conformance_ok": conf.get("ok") if conf and conf.get("ran") else None,
            "mem_price_ok": memp.get("ok") if memp else None,
            "cells": (latest or {}).get("cells"), "ff": (latest or {}).get("ff"),
            "area_um2": (latest or {}).get("area_um2"), "wns_ns": (latest or {}).get("wns_ns"),
            "ppa_ok": (latest or {}).get("ppa_ok"), "ppa_reasons": _uj((latest or {}).get("reasons"), None) if isinstance((latest or {}).get("reasons"), str) else (latest or {}).get("reasons"),
            "budgets": {"estimated_gates": meta.get("estimated_gates"),
                        "flip_flop_budget": meta.get("flip_flop_budget"),
                        "area_budget_um2": meta.get("area_budget_um2")},
            "first_ts": st.get("first_ts"), "last_ts": st.get("last_ts"),
        })
    rows.sort(key=lambda r: (_i(r.get("tier")) or 99, r.get("first_ts") or 0, r["name"]))
    return rows


def build_integration(root: Path) -> dict:
    idx = get_index(root)
    tier_segs = [s for s in idx.segments if not s.get("block") and not s.get("virtual")]
    out: dict[str, Any] = {
        "integration_result": read_json(root / ".coresmith" / "integration_result.json", None),
        "chip_throughput": read_json(root / ".coresmith" / "chip_throughput.json", None),
        "final_report": None,
        "carried_forward_defects": read_json(root / ".coresmith" / "carried_forward_defects.json", []) or [],
        "logs": [], "segments": [], "contract_audit": None,
    }
    fr = read_json(root / "final_report.json", None)
    if fr:
        out["final_report"] = {k: fr.get(k) for k in ("schema", "generated_at", "design_name", "target_clock_mhz",
                                                       "engine_sha", "signoff", "retired_blocks")}
        out["final_report"]["md_rel_path"] = "final_report.md" if (root / "final_report.md").exists() else None
    for ir in out["integration_result"] or {}:
        pass
    ir = out["integration_result"]
    if isinstance(ir, dict):
        ir["lint_log_rel_path"] = rebase_path(root, ir.get("lint_log_path"))
        ir["top_rtl_rel_path"] = rebase_path(root, ir.get("top_rtl_path"))
    for sub in ("integration", "chip_top"):
        for entry in idx.step_logs.get(sub, []):
            out["logs"].append(entry)
    for s in tier_segs:
        out["segments"].append({
            "seg_id": s["seg_id"], "node": s["node"], "tier": s.get("tier"), "enter_ts": s["enter_ts"],
            "exit_ts": s["exit_ts"], "duration_s": (s["exit_ts"] - s["enter_ts"]) if s["exit_ts"] else None,
            "status": s["status"], "exit": s.get("exit"), "enter": s.get("enter"),
            "n_calls": len(s["calls"]), "calls": [idx.call(c) for c in s["calls"] if idx.call(c)],
            "sub_events": s.get("sub_events", []),
        })
    audit = root / ".coresmith" / "contract_audit"
    if audit.is_dir():
        out["contract_audit"] = [{"rel_path": _rel(root, p), "size": p.stat().st_size}
                                 for p in sorted(audit.iterdir()) if p.is_file()]
    return out


def build_overview(root: Path) -> dict:
    idx = get_index(root)
    rows = build_blocks_table(root)
    usage_tot = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}
    llm_time = 0.0
    by_model: dict[str, int] = {}
    timeouts = errors = 0
    for c in idx.calls:
        u = c.get("usage") or {}
        for k in usage_tot:
            usage_tot[k] += _i(u.get(k)) or 0
        llm_time += c.get("duration_s") or 0
        by_model[c.get("model") or "?"] = by_model.get(c.get("model") or "?", 0) + 1
        if c.get("timed_out"):
            timeouts += 1
        elif c.get("error"):
            errors += 1
    tiers: dict[str, list[str]] = {}
    for r in rows:
        tiers.setdefault(str(r.get("tier") or "?"), []).append(r["name"])
    status_counts: dict[str, int] = {}
    for r in rows:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
    settings = run_settings(root)
    final = read_json(root / "final_report.json", {}) or {}
    return {
        "project_root": str(root),
        "design_name": final.get("design_name") or (read_json(root / ".coresmith" / "integration_result.json", {}) or {}).get("design_name"),
        "pipeline_start": idx.pipeline_start, "pipeline_end": idx.pipeline_end,
        "wall_time_s": (idx.pipeline_end - idx.pipeline_start) if idx.pipeline_start and idx.pipeline_end else None,
        "is_live": idx.is_live,
        "totals": {
            "blocks": len(rows), "status_counts": status_counts,
            "llm_calls": len(idx.calls), "llm_time_s": round(llm_time, 1), "usage": usage_tot,
            "by_model": by_model, "timeouts": timeouts, "errors": errors,
            "codex_sessions": len(idx.codex),
            "codex_commands": sum(c.get("n_commands") or 0 for c in idx.calls),
            "codex_file_changes": sum(c.get("n_file_changes") or 0 for c in idx.calls),
            "events": len(idx.events), "decisions": len(idx.decisions), "interrupts": len(idx.interrupts),
            "rounds": sum(len(v) for v in idx.rounds.values()),
        },
        "tiers": [{"tier": t, "blocks": b} for t, b in sorted(tiers.items(), key=lambda kv: (_i(kv[0]) or 99, kv[0]))],
        "blocks": rows,
        "signoff": final.get("signoff"),
        "engine_sha": settings.get("engine_sha"),
        "model": next(iter(by_model), None),
        "provider": next((c.get("provider") for c in idx.calls if c.get("provider")), None),
    }


def build_decisions(root: Path) -> dict:
    idx = get_index(root)
    return {"decisions": idx.decisions, "interrupts": idx.interrupts,
            "pipeline_start": idx.pipeline_start}
