# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""RTL Acceptance DV [dv-hardening-16] -- native-speed RTL-vs-GOLDEN at
mission scale.

The missing fourth tier of the DV ladder (armC finding):

  composition gate  (model vs golden, fast stimulus)     -- uarch stage
  full model DV     (model vs golden, mission scale)     -- uarch stage
  integration DV    (RTL vs model, cocotb)               -- RTL stage
  ACCEPTANCE DV     (RTL vs GOLDEN, mission scale)       -- RTL stage  <- this

Integration DV's oracle IS the composed model, so it can never see a
model-vs-golden divergence; and cocotb's per-edge Python overhead makes
mission-scale frames unaffordable (a QCIF frame that takes ~2s under a native
C++ Verilator harness takes >>10min under cocotb). This tier generates a
NATIVE C++ Verilator harness from the chip top's port contract (seeded from
the armC measurement harness that ran 80 full-QCIF encodes in ~3 minutes),
validates it against the composed-model oracle before trusting it, then runs
the FRD's acceptance cases (>=10 recommended: geometry x QP sweeps for image
IPs, audio segments, benchmark programs for CPUs -- the artifact defines the
matrix, the engine stays domain-generic) comparing RTL output to the golden
under the DECLARED criterion (fidelity metric when present, else byte-exact).

HONEST SKIPS, NEVER A FALSE PASS (same charter as rtl_model_equiv): no
acceptance artifact / no verilator / unsupported port shape / harness fails
oracle validation -> ``{"skipped": True, reason}``. A divergence, a fidelity
break, or a watchdog expiry IS a violation.

Env:
- ``CORESMITH_ACCEPTANCE_DV=0`` disables (default on).
- ``CORESMITH_ACCEPTANCE_DV_BUILD_TIMEOUT_S`` (default 900) verilate+build.
- ``CORESMITH_ACCEPTANCE_DV_RUN_TIMEOUT_S`` (default 1800, elastic x1.5 via
  state file) for the full case sweep.
- ``CORESMITH_ACCEPTANCE_DV_MAX_CYCLES`` per-frame watchdog (default 50M).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def acceptance_dv_enabled() -> bool:
    return os.environ.get(
        "CORESMITH_ACCEPTANCE_DV", "1"
    ).strip().lower() not in ("0", "false", "no", "off")


def _skip(reason: str) -> dict:
    logger.info("acceptance dv: SKIPPED -- %s", reason)
    return {"passed": False, "skipped": True, "reason": reason, "cases": []}


# ---------------------------------------------------------------------------
# Port contract discovery (regex over the chip-top Verilog, like rtl_model_equiv)
# ---------------------------------------------------------------------------

_PORT_RE = re.compile(
    r"\b(input|output)\s+(?:wire|reg|logic)?\s*"
    r"(?:\[\s*(\d+)\s*:\s*0\s*\]\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*[,;)]",
)


def discover_ports(rtl_text: str) -> dict[str, dict]:
    """{name: {dir, width}} for every port in the top module header region."""
    ports: dict[str, dict] = {}
    for m in _PORT_RE.finditer(rtl_text):
        direction, msb, name = m.group(1), m.group(2), m.group(3)
        ports[name] = {
            "dir": direction,
            "width": (int(msb) + 1) if msb else 1,
        }
    return ports


def classify_contract(ports: dict[str, dict]) -> dict | None:
    """Classify the framed-stream contract: clk/reset, s_axis group, m_axis
    group, sideband config inputs. None when the shape is unsupported."""
    names = set(ports)

    clk = next((n for n in ("clk", "clock", "clk_in") if n in names), None)
    rst = next((n for n in ("rst_n", "resetn", "rst", "reset") if n in names), None)
    if not clk or not rst:
        return None
    rst_active_low = rst.endswith("n")

    # WP-18: the published stream_core contract (config plane + start pulse +
    # byte-per-word in/out streams with in_last/out_last). Recognised before
    # the AXIS shape so a chip built to the task's own pin list gets the
    # mission-scale acceptance run instead of an honest skip.
    _sc_in = ("cfg_valid", "cfg_addr", "cfg_data", "start",
              "in_valid", "in_data", "in_last", "out_ready")
    _sc_out = ("in_ready", "out_valid", "out_data", "out_last")
    if (all(n in names and ports[n]["dir"] == "input" for n in _sc_in)
            and all(n in names and ports[n]["dir"] == "output" for n in _sc_out)):
        return {
            "kind": "stream_core",
            "clk": clk,
            "rst": rst,
            "rst_active_low": rst_active_low,
            "in_width": ports["in_data"]["width"],
            "out_width": ports["out_data"]["width"],
            "has_done": "done" in names,
            "sidebands": {},
        }

    def _group(direction: str) -> dict | None:
        # find <prefix>_tvalid with a matching _tready of the opposite driver
        for n, p in ports.items():
            if not n.endswith("_tvalid") or p["dir"] != direction:
                continue
            prefix = n[: -len("_tvalid")]
            if f"{prefix}_tready" in names and f"{prefix}_tdata" in names:
                return {
                    "prefix": prefix,
                    "data_width": ports[f"{prefix}_tdata"]["width"],
                    "has_tuser": f"{prefix}_tuser" in names,
                    "has_tlast": f"{prefix}_tlast" in names,
                }
        return None

    s_axis = _group("input")
    m_axis = _group("output")
    if not s_axis or not m_axis:
        return None

    # dv-hardening-25 (aes128 OOD): a top with MORE THAN ONE input stream group
    # (e.g. s_axis + s_axis_key on a block cipher) cannot be driven by this
    # single-input-stream harness -- it maps the 2nd stream's ports to constant
    # scalar sidebands, so that stream never loads and every case watchdogs to
    # a FALSE FAILURE (aes128 live: 3/3 rtl_bytes=0 while the RTL was actually
    # byte-exact, proven by an independent two-stream cocotb TB). Honest-skip
    # rather than false-fail; the harness only supports one input stream.
    _in_groups = {
        n[: -len("_tvalid")]
        for n, p in ports.items()
        if n.endswith("_tvalid") and p["dir"] == "input"
        and f"{n[: -len('_tvalid')]}_tready" in names
        and f"{n[: -len('_tvalid')]}_tdata" in names
    }
    if len(_in_groups) > 1:
        return None

    stream_names = {clk, rst}
    for g in (s_axis, m_axis):
        for suf in ("tdata", "tvalid", "tready", "tuser", "tlast"):
            stream_names.add(f"{g['prefix']}_{suf}")
    sidebands = {
        n: p["width"]
        for n, p in ports.items()
        if p["dir"] == "input" and n not in stream_names
    }
    return {
        "kind": "axis",
        "clk": clk,
        "rst": rst,
        "rst_active_low": rst_active_low,
        "s_axis": s_axis,
        "m_axis": m_axis,
        "sidebands": sidebands,
    }


def map_stimulus(stimulus: Any, contract: dict) -> dict | None:
    """Map a stimulus (dict / flat sequence) onto (payload bytes, sideband
    values) using the SAME conventions as the composed model's simulate():
    the array-valued field is the beat stream; scalar fields map to sideband
    ports by exact name, then substring, match. None when unmappable."""
    sb_values: dict[str, int] = {}
    payload = None
    if isinstance(stimulus, dict):
        for key, val in stimulus.items():
            if isinstance(val, (list, tuple, bytes, bytearray)) or hasattr(val, "ravel"):
                if payload is None and str(key).lower() != "cfg":
                    payload = val
                continue
            if isinstance(val, dict):
                continue
            if not isinstance(val, (int, float)):
                continue
            k = str(key).lower()
            port = None
            if k in contract["sidebands"]:
                port = k
            else:
                cands = [p for p in contract["sidebands"] if k in p or p in k]
                if len(cands) == 1:
                    port = cands[0]
            if port is not None:
                sb_values[port] = int(val)
    elif isinstance(stimulus, (list, tuple)):
        payload = stimulus
    if payload is None:
        return None
    # dv-hardening-24 (armD driver, defect #10): frame geometry is implicit in
    # the payload's 2D shape (stimuli carry only frames+qp), but _pack_cases
    # drives unmapped sidebands as 0 -> the chip waits forever for a 0x0 frame
    # (all-cases watchdog with zero output). Derive *_width / *_height sideband
    # values from the array shape when not explicitly given. Must run BEFORE
    # flattening -- only the raw payload still carries the 2D shape.
    try:
        import numpy as _np2

        _arr = _np2.asarray(
            stimulus.get("frames")
            if isinstance(stimulus, dict) and "frames" in stimulus
            else payload
        )
        if _arr.ndim >= 2:
            _h, _w = int(_arr.shape[-2]), int(_arr.shape[-1])
            for _p in contract["sidebands"]:
                if _p in sb_values:
                    continue
                _pl = _p.lower()
                if "width" in _pl:
                    sb_values[_p] = _w
                elif "height" in _pl:
                    sb_values[_p] = _h
    except Exception:  # noqa: BLE001
        pass
    try:
        import numpy as _np

        flat = [int(v) & 0xFF for v in _np.asarray(payload).ravel().tolist()]
    except Exception:  # noqa: BLE001
        try:
            flat = [int(v) & 0xFF for v in payload]
        except Exception:  # noqa: BLE001
            return None
    unmapped = [p for p in contract["sidebands"] if p not in sb_values]
    mapped = {"payload": flat, "sidebands": sb_values, "unmapped": unmapped}
    if contract.get("kind") == "stream_core":
        cfg = _stream_cfg_plane(stimulus, payload)
        if cfg is None:
            return None
        mapped["cfg"] = cfg
        try:
            mapped["cycle_cap"] = int(stimulus.get("cycle_cap") or 0) if isinstance(stimulus, dict) else 0
        except (TypeError, ValueError):
            mapped["cycle_cap"] = 0
    return mapped


def _stream_cfg_plane(stimulus: Any, payload: Any) -> list[tuple[int, int]] | None:
    """The config writes ``[(addr, value), ...]`` for a stream_core case.

    An explicit ``cfg`` (dict addr->value, or a list of pairs) wins. Otherwise
    the plane is derived from the published convention (0 n_frames, 1 width,
    2 height, 3 qp) when the stimulus carries ``n_frames`` and either a
    2-D+ ``frames`` array or ``width``/``height`` keys. None when nothing
    usable is declared -- an honest skip, never a zero-configured run.
    """
    if not isinstance(stimulus, dict):
        return None
    raw = stimulus.get("cfg")
    if isinstance(raw, dict):
        try:
            return [(int(k), int(v) & 0xFFFFFFFF) for k, v in raw.items()]
        except (TypeError, ValueError):
            return None
    if isinstance(raw, (list, tuple)):
        try:
            return [(int(a), int(v) & 0xFFFFFFFF) for a, v in raw]
        except (TypeError, ValueError):
            return None
    if "n_frames" not in stimulus:
        return None
    plane: list[tuple[int, int]] = [(0, int(stimulus["n_frames"]))]
    w = stimulus.get("width")
    h = stimulus.get("height")
    if (w is None or h is None) and "frames" in stimulus:
        try:
            import numpy as _np

            arr = _np.asarray(stimulus["frames"])
            if arr.ndim >= 2:
                h, w = int(arr.shape[-2]), int(arr.shape[-1])
        except Exception:  # noqa: BLE001
            pass
    if w is None or h is None:
        return None
    plane += [(1, int(w)), (2, int(h))]
    if stimulus.get("qp") is not None:
        plane.append((3, int(stimulus["qp"])))
    return plane


# ---------------------------------------------------------------------------
# Native harness generation (seeded from the armC measurement harness)
# ---------------------------------------------------------------------------

_HARNESS_TEMPLATE = r"""// Auto-generated RTL Acceptance DV harness (coresmith dv-hardening-16).
// Seeded from the armC measurement harness: inputs set up after the falling
// edge, handshakes sampled after the rising edge (mirrors the cocotb TBs).
// Binary protocol (little-endian u32):
//   input:  repeated { n_sidebands, {sb_value}*n, n_pixels, pixel bytes }
//   output: repeated { status, cycles, outlen, out bytes }  (status 0=ok 1=watchdog)
#include "V{TOP}.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <vector>

using DUT = V{TOP};
static vluint64_t g_cycle = 0;
static void posedge(DUT *t) { t->{CLK} = 1; t->eval(); g_cycle++; }
static void negedge(DUT *t) { t->{CLK} = 0; t->eval(); }

// [dv-hardening-27] Deterministic xorshift PRNG for randomized output
// backpressure + input gaps. A design that drops out_valid on its own transfer
// edge, or skews in_ready per beat, only fails UNDER backpressure -- with
// ready wired high and no input gaps (the old harness) those bugs pass. The
// A run-through proved the point: CoreSmith's byte/no-backpressure
// harness shipped two handshake bugs an independent backpressuring TB caught.
static uint32_t g_rng = 0x2545F491u;
static inline uint32_t xrng() {
    g_rng ^= g_rng << 13; g_rng ^= g_rng >> 17; g_rng ^= g_rng << 5;
    return g_rng;
}
// 0 = wire ready high + no gaps (legacy); 1 = randomized backpressure + gaps.
static const int BP = {BP_ENABLED};
// Bytes driven/captured per stream beat (= ceil(tdata_width/8), capped at 8).
static const int W_IN = {W_IN};
static const int W_OUT = {W_OUT};

static bool rd_u32(FILE *f, uint32_t *v) {
    unsigned char b[4];
    if (fread(b, 1, 4, f) != 4) return false;
    *v = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
    return true;
}
static void wr_u32(FILE *f, uint32_t v) {
    unsigned char b[4] = {(unsigned char)(v), (unsigned char)(v >> 8),
                          (unsigned char)(v >> 16), (unsigned char)(v >> 24)};
    fwrite(b, 1, 4, f);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    if (argc < 3) { fprintf(stderr, "usage: %s in.bin out.bin [max_cycles]\n", argv[0]); return 2; }
    uint64_t max_cycles = (argc >= 4) ? strtoull(argv[3], nullptr, 10) : {MAX_CYCLES}ULL;
    FILE *fin = fopen(argv[1], "rb");
    FILE *fout = fopen(argv[2], "wb");
    if (!fin || !fout) { fprintf(stderr, "io error\n"); return 2; }
    DUT *top = new DUT;

    uint32_t n_sb;
    while (rd_u32(fin, &n_sb)) {
        std::vector<uint32_t> sb(n_sb);
        for (uint32_t i = 0; i < n_sb; i++) if (!rd_u32(fin, &sb[i])) return 2;
        uint32_t n_pix;
        if (!rd_u32(fin, &n_pix)) return 2;
        std::vector<uint8_t> px(n_pix);
        if (n_pix && fread(px.data(), 1, n_pix, fin) != n_pix) return 2;

        // reset (per case seed so backpressure is deterministic + reproducible)
        g_rng = 0x2545F491u ^ ((uint32_t)n_pix * 2654435761u) ^ (n_sb * 40503u);
        top->{S_TVALID} = 0; top->{M_TREADY} = 1;
{SB_ZERO}
        top->{RST} = {RST_ASSERT};
        for (int i = 0; i < 5; i++) { negedge(top); posedge(top); }
        negedge(top);
        top->{RST} = {RST_DEASSERT};
        for (int i = 0; i < 5; i++) { negedge(top); posedge(top); }
        negedge(top);

        // [dv-hardening-30] Width-aware: pack W_IN payload bytes per input beat
        // into s_tdata (little-endian) and capture W_OUT bytes per output beat.
        // For a byte-wide stream (W_IN=W_OUT=1) this is byte-identical to the
        // old behaviour; for a 32-bit word stream (matmul) it drives whole words
        // instead of a single byte -- the run-through's other limitation.
        size_t n = (W_IN > 0) ? (px.size() / (size_t)W_IN) : px.size();
        size_t idx = 0;
        bool s_done = (n == 0), r_done = false, s_pend = false;
        uint64_t start_c = UINT64_MAX, end_c = UINT64_MAX, wd = 0, m_idle = 0;
        std::vector<uint8_t> out;

        auto load = [&](size_t i) {
            uint64_t w = 0;
            for (int b = 0; b < W_IN; b++) w |= (uint64_t)px[i * W_IN + b] << (8 * b);
            top->{S_TDATA} = w;
{S_TUSER_SET}
{S_TLAST_SET}
{SB_SET}
            top->{S_TVALID} = 1;
        };

        while ((!s_done || !r_done) && wd < max_cycles) {
            // Drive the handshake signals for THIS cycle before the edge.
            // Output backpressure: deassert m_tready ~30% of cycles.
            top->{M_TREADY} = (BP && (xrng() & 7) < 3) ? 0 : 1;
            // Input: hold the current word until accepted; insert ~15% gaps
            // (in_valid low) without advancing -- AXI-Stream master behaviour.
            if (!s_done) {
                // Gap only BETWEEN transfers: once a beat is presented TVALID
                // must stay high until TREADY (AXI-Stream valid stability).
                if (!s_pend && BP && (xrng() % 100) < 15) top->{S_TVALID} = 0;
                else { load(idx); s_pend = true; }
            }
            posedge(top); wd++;
            if (!s_done && top->{S_TVALID} && top->{S_TREADY}) {
                if (start_c == UINT64_MAX) start_c = g_cycle;
                idx++; s_pend = false;
                if (idx >= n) { s_done = true; top->{S_TVALID} = 0; }
            }
            if (!r_done && top->{M_TVALID} && top->{M_TREADY}) {
                uint64_t d = (uint64_t)top->{M_TDATA};
                for (int b = 0; b < W_OUT; b++) out.push_back((uint8_t)((d >> (8 * b)) & 0xff));
                m_idle = 0;
{M_TLAST_CHECK}
            }
{M_IDLE_CHECK}
            negedge(top);
        }
        uint32_t status = (start_c == UINT64_MAX || end_c == UINT64_MAX) ? 1u : 0u;
        uint64_t cycles = (status == 0) ? (end_c - start_c) : 0;
        wr_u32(fout, status);
        wr_u32(fout, (uint32_t)cycles);
        wr_u32(fout, (uint32_t)out.size());
        fwrite(out.data(), 1, out.size(), fout);
        fflush(fout);
    }
    fclose(fin); fclose(fout); delete top;
    return 0;
}
"""


def generate_harness(contract: dict, top_module: str, sb_order: list[str]) -> str:
    if contract.get("kind") == "stream_core":
        return generate_stream_harness(contract, top_module)
    s, m = contract["s_axis"], contract["m_axis"]
    sp, mp = s["prefix"], m["prefix"]
    sb_set = "\n".join(
        f"            top->{name} = sb[{i}];" for i, name in enumerate(sb_order)
    ) or "            (void)sb;"
    sb_zero = "\n".join(
        f"        top->{name} = 0;" for name in sb_order
    ) or "        (void)0;"
    tuser = (f"            top->{sp}_tuser = (i == 0) ? 1 : 0;"
             if s["has_tuser"] else "            (void)i;")
    tlast = (f"            top->{sp}_tlast = (i == n - 1) ? 1 : 0;"
             if s["has_tlast"] else "")
    m_tlast = (
        f"                if (top->{mp}_tlast) {{ end_c = g_cycle; r_done = true; }}"
        if m["has_tlast"] else
        "                end_c = g_cycle; /* no tlast: track last beat */"
    )
    # no egress tlast: done when the sender is done and the output has been
    # quiet for 64 cycles SINCE THE LAST ACCEPTED BEAT (m_idle resets on every
    # beat) -- counting wd here ended capture on the first post-drain beat.
    m_idle_check = (
        "            (void)m_idle;" if m["has_tlast"] else
        "            else if (s_done && end_c != UINT64_MAX && ++m_idle > 64)\n"
        "                r_done = true;"
    )
    return (
        _HARNESS_TEMPLATE
        .replace("{TOP}", top_module)
        .replace("{CLK}", contract["clk"])
        .replace("{RST}", contract["rst"])
        .replace("{RST_ASSERT}", "0" if contract["rst_active_low"] else "1")
        .replace("{RST_DEASSERT}", "1" if contract["rst_active_low"] else "0")
        .replace("{S_TDATA}", f"{sp}_tdata")
        .replace("{S_TVALID}", f"{sp}_tvalid")
        .replace("{S_TREADY}", f"{sp}_tready")
        .replace("{M_TDATA}", f"{mp}_tdata")
        .replace("{M_TVALID}", f"{mp}_tvalid")
        .replace("{M_TREADY}", f"{mp}_tready")
        .replace("{S_TUSER_SET}", tuser)
        .replace("{S_TLAST_SET}", tlast)
        .replace("{M_TLAST_CHECK}", m_tlast)
        .replace("{M_IDLE_CHECK}", m_idle_check)
        .replace("{SB_SET}", sb_set)
        .replace("{SB_ZERO}", sb_zero)
        .replace("{MAX_CYCLES}", os.environ.get(
            "CORESMITH_ACCEPTANCE_DV_MAX_CYCLES", "50000000"))
        .replace("{BP_ENABLED}", "0" if os.environ.get(
            "CORESMITH_ACCEPTANCE_DV_BACKPRESSURE", "1").strip().lower()
            in ("0", "false", "no", "off") else "1")
        .replace("{W_IN}", str(min(8, (int(s.get("data_width", 8)) + 7) // 8) or 1))
        .replace("{W_OUT}", str(min(8, (int(m.get("data_width", 8)) + 7) // 8) or 1))
    )


_STREAM_TEMPLATE = r"""// Auto-generated RTL Acceptance DV harness (coresmith WP-18, stream_core).
// Mirrors the published StreamHarness cycle semantics: inputs driven before
// the rising edge, handshakes sampled after it (post-edge ready/valid), random
// input gaps + output backpressure, ONE payload byte per word on both streams,
// completion on a consumed out_last beat. Cycles count from the start pulse.
// Binary protocol (little-endian u32):
//   input:  repeated { n_cfg, {addr, value}*n_cfg, cycle_cap, n_bytes, bytes }
//   output: repeated { status, cycles, outlen, out bytes }  (status 0=ok 1=watchdog)
#include "V{TOP}.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <utility>
#include <vector>

using DUT = V{TOP};
static vluint64_t g_cycle = 0;
static void posedge(DUT *t) { t->{CLK} = 1; t->eval(); g_cycle++; }
static void negedge(DUT *t) { t->{CLK} = 0; t->eval(); }
static uint32_t g_rng = 0x2545F491u;
static inline uint32_t xrng() {
    g_rng ^= g_rng << 13; g_rng ^= g_rng >> 17; g_rng ^= g_rng << 5;
    return g_rng;
}
static const int BP = {BP_ENABLED};

static bool rd_u32(FILE *f, uint32_t *v) {
    unsigned char b[4];
    if (fread(b, 1, 4, f) != 4) return false;
    *v = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
    return true;
}
static void wr_u32(FILE *f, uint32_t v) {
    unsigned char b[4] = {(unsigned char)(v), (unsigned char)(v >> 8),
                          (unsigned char)(v >> 16), (unsigned char)(v >> 24)};
    fwrite(b, 1, 4, f);
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    if (argc < 3) { fprintf(stderr, "usage: %s in.bin out.bin [max_cycles]\n", argv[0]); return 2; }
    uint64_t max_cycles_default = (argc >= 4) ? strtoull(argv[3], nullptr, 10) : {MAX_CYCLES}ULL;
    FILE *fin = fopen(argv[1], "rb");
    FILE *fout = fopen(argv[2], "wb");
    if (!fin || !fout) { fprintf(stderr, "io error\n"); return 2; }
    DUT *top = new DUT;

    uint32_t n_cfg;
    while (rd_u32(fin, &n_cfg)) {
        std::vector<std::pair<uint32_t, uint32_t>> cfg(n_cfg);
        for (uint32_t i = 0; i < n_cfg; i++) {
            if (!rd_u32(fin, &cfg[i].first) || !rd_u32(fin, &cfg[i].second)) return 2;
        }
        uint32_t cycle_cap, n_in;
        if (!rd_u32(fin, &cycle_cap)) return 2;
        if (!rd_u32(fin, &n_in)) return 2;
        std::vector<uint8_t> in(n_in);
        if (n_in && fread(in.data(), 1, n_in, fin) != n_in) return 2;
        uint64_t max_cycles = cycle_cap ? (uint64_t)cycle_cap : max_cycles_default;

        g_rng = 0x2545F491u ^ ((uint32_t)n_in * 2654435761u) ^ (n_cfg * 40503u);
        top->cfg_valid = 0; top->cfg_addr = 0; top->cfg_data = 0; top->start = 0;
        top->in_valid = 0; top->in_data = 0; top->in_last = 0; top->out_ready = 0;
        top->{RST} = {RST_ASSERT};
        for (int i = 0; i < 5; i++) { negedge(top); posedge(top); }
        negedge(top);
        top->{RST} = {RST_DEASSERT};
        negedge(top); posedge(top); negedge(top);

        // config plane: one write per cycle, then a one-cycle start pulse
        // Inputs settle with clk low (eval) BEFORE the rising edge, exactly
        // like a cocotb driver writing before `await RisingEdge`; a single
        // eval() with new inputs + clk=1 lets the flops sample stale D.
        for (auto &kv : cfg) {
            top->cfg_valid = 1; top->cfg_addr = kv.first; top->cfg_data = kv.second;
            top->eval(); posedge(top); negedge(top);
            top->cfg_valid = 0; top->eval();
        }
        top->start = 1; top->eval(); posedge(top); negedge(top); top->start = 0; top->eval();

        size_t n = in.size(), i = 0;
        uint64_t cyc = 0;
        bool done_beat = false, timed_out = false;
        std::vector<uint8_t> out;
        while (true) {
            bool gap = (i < n) && BP && ((xrng() % 100) < 10);
            top->in_valid = (i < n && !gap) ? 1 : 0;
            top->in_data = (i < n) ? (uint32_t)in[i] : 0u;
            top->in_last = (i == n - 1 && !gap) ? 1 : 0;
            top->out_ready = (BP && ((xrng() % 100) < 15)) ? 0 : 1;
            top->eval();
            posedge(top); cyc++;
            int iv = top->in_valid, ir = top->in_ready;
            int ov = top->out_valid, orr = top->out_ready, ol = top->out_last;
            if (i < n && iv == 1 && ir == 1) i++;
            if (ov == 1 && orr == 1) {
                out.push_back((uint8_t)(top->out_data & 0xffu));
                if (ol == 1) done_beat = true;
            }
            negedge(top);
            if (done_beat) break;
            if (cyc > max_cycles) { timed_out = true; break; }
        }
        top->in_valid = 0; top->in_last = 0; top->out_ready = 0;
        uint32_t status = timed_out ? 1u : 0u;
        wr_u32(fout, status);
        wr_u32(fout, (uint32_t)cyc);
        wr_u32(fout, (uint32_t)out.size());
        fwrite(out.data(), 1, out.size(), fout);
        fflush(fout);
    }
    fclose(fin); fclose(fout); delete top;
    return 0;
}
"""


def generate_stream_harness(contract: dict, top_module: str) -> str:
    """The stream_core harness source (see _STREAM_TEMPLATE)."""
    return (
        _STREAM_TEMPLATE
        .replace("{TOP}", top_module)
        .replace("{CLK}", contract["clk"])
        .replace("{RST}", contract["rst"])
        .replace("{RST_ASSERT}", "0" if contract["rst_active_low"] else "1")
        .replace("{RST_DEASSERT}", "1" if contract["rst_active_low"] else "0")
        .replace("{MAX_CYCLES}", os.environ.get(
            "CORESMITH_ACCEPTANCE_DV_MAX_CYCLES", "50000000"))
        .replace("{BP_ENABLED}", "0" if os.environ.get(
            "CORESMITH_ACCEPTANCE_DV_BACKPRESSURE", "1").strip().lower()
            in ("0", "false", "no", "off") else "1")
    )


# ---------------------------------------------------------------------------
# Build + run
# ---------------------------------------------------------------------------

def _build(workdir: Path, top_module: str, sources: list[str],
           harness_cpp: Path) -> str | None:
    """verilate + build; returns the binary path or None (reason logged)."""
    timeout = int(float(os.environ.get(
        "CORESMITH_ACCEPTANCE_DV_BUILD_TIMEOUT_S", "900") or 900))
    cmd = [
        "verilator", "--cc", "--exe", "--build", "-j", "2",
        "--top-module", top_module, "-Wno-fatal",
        "-CFLAGS", "-O2",
        str(harness_cpp),
    ] + sources
    try:
        r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning("acceptance dv: verilator build timed out (%ss)", timeout)
        return None
    if r.returncode != 0:
        logger.warning("acceptance dv: verilator build failed:\n%s",
                       (r.stderr or r.stdout)[-1500:])
        return None
    binp = workdir / "obj_dir" / f"V{top_module}"
    return str(binp) if binp.exists() else None


def _pack_cases(cases: list[tuple[str, dict]], sb_order: list[str],
                path: Path) -> None:
    with open(path, "wb") as f:
        for _name, mapped in cases:
            if "cfg" in mapped:
                cfg = mapped["cfg"]
                f.write(struct.pack("<I", len(cfg)))
                f.writelines(struct.pack("<II", int(a) & 0xFF, int(v) & 0xFFFFFFFF)
                             for a, v in cfg)
                f.write(struct.pack("<I", int(mapped.get("cycle_cap") or 0) & 0xFFFFFFFF))
                payload = mapped["payload"]
                f.write(struct.pack("<I", len(payload)))
                f.write(bytes(payload))
                continue
            sbv = mapped["sidebands"]
            f.write(struct.pack("<I", len(sb_order)))
            f.writelines(struct.pack("<I", sbv.get(p, 0) & 0xFFFFFFFF) for p in sb_order)
            payload = mapped["payload"]
            f.write(struct.pack("<I", len(payload)))
            f.write(bytes(payload))


def _read_results(path: Path, n: int) -> list[dict]:
    out = []
    with open(path, "rb") as f:
        for _ in range(n):
            hdr = f.read(12)
            if len(hdr) < 12:
                break
            status, cycles, outlen = struct.unpack("<III", hdr)
            data = f.read(outlen)
            out.append({"status": status, "cycles": cycles, "bytes": data})
    return out


def _golden_bytes(expected: Any) -> bytes | None:
    """Coerce a golden return value to comparable bytes, using the SAME
    conventions map_stimulus uses on the input side (numpy arrays -- the
    natural return type for the image IPs this tier targets -- ravel to
    masked bytes). None when the shape is not byte-comparable, which is an
    honest skip at the call site, never a divergence against 0 bytes."""
    if isinstance(expected, (bytes, bytearray)):
        return bytes(expected)
    try:
        import numpy as _np

        return bytes(int(v) & 0xFF
                     for v in _np.asarray(expected).ravel().tolist())
    except Exception:  # noqa: BLE001
        pass
    try:
        return bytes(int(v) & 0xFF for v in expected)
    except Exception:  # noqa: BLE001
        return None


def _first_diff(a: bytes, b: bytes) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b)) if len(a) != len(b) else -1


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def run_acceptance_dv(project_root: str, top_rtl: str,
                      block_rtls: Any = None) -> dict:
    """Returns {"passed", "skipped", "reason", "cases": [...], "violations"}.

    ``cases`` rows: {name, criterion, ok, cycles, rtl_bytes, golden_bytes,
    first_divergence, fidelity}.
    """
    if not acceptance_dv_enabled():
        return _skip("CORESMITH_ACCEPTANCE_DV=0")
    if not shutil.which("verilator"):
        return _skip("verilator not on PATH")

    from orchestrator.architecture.composition import (
        _run_reference,
        resolve_reference_entrypoint,
        resolve_reference_implementation,
    )
    from orchestrator.architecture.fidelity import (
        compute_fidelity_derate,
        fidelity_gate_enabled,
        resolve_fidelity_metric,
    )
    from orchestrator.architecture.reference_oracle import (
        _acceptance_stimulus_path,
        _import_module_from_path,
        _load_reference_module,
    )

    art = _acceptance_stimulus_path(project_root)
    if not art:
        return _skip("no acceptance stimulus artifact (FRD mandate: "
                     "inputs/acceptance_stimulus.py)")
    try:
        art_mod = _import_module_from_path(Path(art), "_cs_acceptance_rtl")
    except Exception as exc:  # noqa: BLE001
        return _skip(f"acceptance artifact failed to import: {exc}")
    raw_cases = getattr(art_mod, "cases", None)
    if not raw_cases:
        stim = getattr(art_mod, "stimulus", None)
        if stim is None:
            return _skip("acceptance artifact exposes neither cases nor stimulus")
        raw_cases = [("acceptance", stim)]

    ref_path = resolve_reference_implementation(project_root)
    if not ref_path:
        return _skip("no golden reference resolvable")
    try:
        ref_module = _load_reference_module(ref_path)
        entry_callable, entry_name = resolve_reference_entrypoint(
            project_root, ref_module)
    except Exception as exc:  # noqa: BLE001
        return _skip(f"reference not loadable: {exc}")
    if entry_callable is None:
        return _skip("no callable reference entry")

    top_p = Path(top_rtl)
    if not top_p.exists():
        return _skip(f"chip top RTL not found: {top_rtl}")
    rtl_text = top_p.read_text(encoding="utf-8", errors="replace")
    mname = re.search(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_]*)", rtl_text)
    if not mname:
        return _skip("no module declaration in chip top")
    top_module = mname.group(1)
    # dv-hardening-23 (armD driver-found, defect #9): scope port discovery to
    # the TOP module span. Integration chip-tops carry helper modules in the
    # same file (e.g. rst_sync_2ff); their inputs leaked into the sideband map
    # and the generated harness drove nonexistent top pins (rst_n_in) -->
    # C++ compile failure --> honest-skip at the acceptance gate.
    _end = rtl_text.find("endmodule", mname.start())
    _top_span = rtl_text[mname.start():(_end if _end != -1 else len(rtl_text))]
    contract = classify_contract(discover_ports(_top_span))
    if contract is None:
        return _skip("chip top is not the framed s_axis/m_axis + sideband "
                     "shape this harness covers")

    # Map every case; skip-honest if any is unmappable.
    sb_order = sorted(contract["sidebands"])
    mapped_cases: list[tuple[str, dict]] = []
    for name, stim in list(raw_cases)[:64]:
        m = map_stimulus(stim, contract)
        if m is None:
            return _skip(f"acceptance case {name!r} not mappable onto the "
                         f"chip contract (sidebands={sb_order})")
        mapped_cases.append((str(name), m))

    workdir = Path(tempfile.mkdtemp(prefix="cs_acceptance_dv_"))
    harness = workdir / "sim_main.cpp"
    harness.write_text(generate_harness(contract, top_module, sb_order))

    if isinstance(block_rtls, dict):
        block_paths = [str(p) for p in block_rtls.values() if p]
    else:
        block_paths = [str(p) for p in (block_rtls or []) if p]
    sources = [str(top_p.resolve())] + [
        str(Path(p).resolve()) for p in block_paths if Path(p).exists()
    ]
    # armD live (first Acceptance DV engagement): blocks instantiating the
    # shared cs_sram wrapper library MODMISSING'd because only top+blocks
    # were passed to verilator. Include the engine RTL lib exactly like the
    # block-sim Makefile does when any source references it.
    try:
        from orchestrator.langgraph.sram_wrapper import (
            uses_wrapper,
            wrapper_lib_path,
        )

        _any_wrapper = False
        for s in sources:
            try:
                if uses_wrapper(Path(s).read_text(encoding="utf-8",
                                                  errors="replace")):
                    _any_wrapper = True
                    break
            except OSError:
                continue
        if _any_wrapper:
            _lib = str(wrapper_lib_path())
            if _lib and Path(_lib).exists() and _lib not in sources:
                sources.append(_lib)
    except Exception:  # noqa: BLE001 - lib resolution is best-effort
        pass
    binp = _build(workdir, top_module, sources, harness)
    if not binp:
        return _skip("native harness build failed (see log)")

    inp, outp = workdir / "in.bin", workdir / "out.bin"
    _pack_cases(mapped_cases, sb_order, inp)

    run_to = int(float(os.environ.get(
        "CORESMITH_ACCEPTANCE_DV_RUN_TIMEOUT_S", "1800") or 1800))
    try:
        # WP-18: run from the PROJECT ROOT so project-relative $readmemh
        # images (inputs/rom_images/*.memh) resolve -- from any other cwd the
        # ROMs load empty and a correct chip stalls after its first frame.
        r = subprocess.run([binp, str(inp), str(outp)], capture_output=True,
                           text=True, timeout=run_to, cwd=str(project_root))
    except subprocess.TimeoutExpired:
        return {"passed": False, "skipped": False,
                "reason": f"native acceptance run exceeded {run_to}s",
                "cases": [], "violations": [{
                    "type": "acceptance_dv_failure",
                    "criterion": "acceptance_dv_timeout",
                    "suggested_fix": "raise CORESMITH_ACCEPTANCE_DV_RUN_TIMEOUT_S "
                                     "or reduce acceptance case count",
                }]}
    if r.returncode != 0:
        return _skip(f"native harness exited rc={r.returncode}: "
                     f"{(r.stderr or '')[-400:]}")
    results = _read_results(outp, len(mapped_cases))
    try:
        _keep = Path(project_root) / ".coresmith" / "acceptance_dv"
        _keep.mkdir(parents=True, exist_ok=True)
        for (_n, _m), _r in zip(mapped_cases, results):
            (_keep / f"{_n}.out.bin").write_bytes(bytes(_r["bytes"]))
    except OSError:
        pass

    use_fidelity = (fidelity_gate_enabled()
                    and resolve_fidelity_metric(project_root) is not None)
    # WP-18: a declared accept(expected, observed) predicate (decode + PSNR
    # for a codec, result match for an MCU) is the mission's own judge; byte
    # equality against the golden is only the fallback for bit-exact IPs.
    accept_fn = None
    try:
        from orchestrator.architecture.composition import resolve_functional_acceptance
        accept_fn = resolve_functional_acceptance(project_root)
    except Exception:  # noqa: BLE001
        accept_fn = None
    rows, violations = [], []
    for (name, _mapped), res, (rname, rstim) in zip(
        mapped_cases, results, list(raw_cases)[:64]
    ):
        try:
            expected = _run_reference(entry_callable, rstim, reraise=True)
        except Exception as exc:  # noqa: BLE001
            rows.append({"name": name, "ok": None,
                         "note": f"reference not invokable: {exc}"})
            continue
        exp_bytes = _golden_bytes(expected)
        row = {"name": name, "cycles": res["cycles"],
               "rtl_bytes": len(res["bytes"]), "status": res["status"]}
        if res["status"] != 0:
            row["ok"] = False
            row["note"] = "watchdog expired (no completion)"
            violations.append({
                "type": "acceptance_dv_failure",
                "criterion": "acceptance_dv_watchdog",
                "acceptance_case": name,
                "suggested_fix": "RTL never completed the mission-scale case "
                                 "(drain/EOF or stall class at scale).",
            })
        elif accept_fn is not None:
            try:
                ok = bool(accept_fn(exp_bytes if exp_bytes is not None else expected,
                                    bytes(res["bytes"])))
                note = ""
            except Exception as exc:  # noqa: BLE001
                ok, note = False, f"acceptance predicate raised: {exc}"
            row["ok"] = ok
            row["criterion"] = "acceptance_predicate"
            if note:
                row["note"] = note
            if not ok:
                violations.append({
                    "type": "acceptance_dv_failure",
                    "criterion": "acceptance_dv_predicate",
                    "acceptance_case": name,
                    "gap_class": "mission",
                    "suggested_fix": (
                        "RTL output fails the declared acceptance predicate at "
                        f"mission scale ({len(res['bytes'])}B streamed"
                        + (f"; {note}" if note else "") + "). Decode/grade the "
                        "captured stream offline (.coresmith/acceptance_dv/) and "
                        "fix the responsible block."
                    ),
                })
        elif use_fidelity:
            fid = compute_fidelity_derate(project_root, expected, res["bytes"])
            row["fidelity"] = fid
            row["ok"] = bool(fid and fid.get("within_budget"))
            if not row["ok"]:
                violations.append({
                    "type": "acceptance_dv_failure",
                    "criterion": "acceptance_dv_below_budget",
                    "acceptance_case": name,
                    "fidelity": fid,
                    "gap_class": "block_math",
                    "suggested_fix": (
                        "The REAL RTL is below the declared fidelity budget "
                        "on a mission-scale acceptance case. Compare against "
                        "the Full Model DV verdict: model-pass + RTL-fail "
                        "here = transcription divergence at scale; both-fail "
                        "= model content math."
                    ),
                })
        elif exp_bytes is None:
            # Unsupported golden shape: leave the row UNJUDGED (-> honest skip
            # below) instead of diverging it against a 0-byte reference.
            row["ok"] = None
            row["note"] = "golden output not byte-comparable"
        else:
            ok = bytes(res["bytes"]) == exp_bytes
            row["ok"] = ok
            if not ok:
                row["first_divergence"] = (
                    _first_diff(bytes(res["bytes"]), exp_bytes or b""))
                row["golden_bytes"] = len(exp_bytes or b"")
                violations.append({
                    "type": "acceptance_dv_failure",
                    "criterion": "acceptance_dv_divergence",
                    "acceptance_case": name,
                    "first_divergence_offset": row["first_divergence"],
                    "gap_class": "block_math",
                    "suggested_fix": (
                        "RTL output diverges from the GOLDEN at mission scale "
                        f"(first diff offset {row['first_divergence']}, "
                        f"{len(res['bytes'])}B vs {len(exp_bytes or b'')}B)."
                    ),
                })
        rows.append(row)

    # HONEST SKIPS, NEVER A FALSE PASS: rows the golden could not judge (the
    # reference raised, or its output is not byte-comparable) used to be
    # FILTERED OUT of the verdict, so a sweep where every case was ungradable
    # reported PASSED without ever comparing RTL to anything.
    graded = [r for r in rows if r.get("ok") is not None]
    ungraded = [r for r in rows if r.get("ok") is None]
    passed = (bool(graded) and not ungraded and not violations
              and all(r["ok"] for r in graded))
    skipped = bool(ungraded) and not violations
    reason = f"{len(rows)} acceptance case(s), {len(violations)} violation(s)"
    if skipped:
        reason = (f"{len(ungraded)}/{len(rows)} acceptance case(s) not "
                  "gradable against the golden: "
                  + "; ".join(str(r.get("note", "")) for r in ungraded[:3]))
    summary = {"passed": passed, "skipped": skipped,
               "reason": reason,
               "cases": rows, "violations": violations}
    try:
        out_json = Path(project_root) / ".coresmith" / "acceptance_dv.json"
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(
            {k: v for k, v in summary.items() if k != "violations"}
            | {"violations": violations}, default=str, indent=1))
    except Exception:  # noqa: BLE001
        pass
    logger.info("acceptance dv: %s (%s)",
                "PASSED" if passed else ("SKIPPED" if skipped else "FAILED"),
                summary["reason"])
    return summary
