# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for the static composition auditor (composition_audit.py).

Each failure-class fixture is a miniature of a REAL defect observed in the
2026-07 video_codec worker A/B at the model-integration gate:

- ``undriven_net``: Sonnet's ``raster_pos_latched`` -- declared, consumed by
  two blocks, assigned by nothing.
- ``instantiation_signature``: Gemini's ``qp_data`` unexpected-kwarg (x4
  attempts burned).
- ``zero_width_signal``: Gemini's ``intbv value 0 >= maximum 00000000``.
- ``unlowerable_introspection``: Sonnet's derive_wr_last snooping the
  producer's internal FSM from inside chip_model.

``TestStarredArgExpansion`` covers the auditor's own false-positive class: a
valid ``inv_start = [...]; InverseEngine(*inv_start, *cav_start)`` was reported
as "missing required parameter(s)" for every port the star covered, a hard
pre-simulation stop that cost three hand-expansion repair rounds on a live run.

Pure AST for everything except ``TestGateIntegration`` /
``TestCrashTracebackLocalization``, which import and run the fixture through
the real gate (Amaranth elaboration, no EDA). No LLM, no simulation of RTL.
Compatible with ``-m "not live_llm and not requires_nix and not e2e"``.

Every fixture is Amaranth: the auditor only understands
``class <stem>(Elaboratable)`` block models with ``__init__``/``elaborate``,
derives ports from ``__init__`` parameters aliased onto ``self``, and derives
port DIRECTION from ``self.<port>.eq(...)`` targets inside ``elaborate``. The
fixtures are minimal by design -- the auditor never executes a block model, so
they only have to have the right SHAPE, not to be useful hardware.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.architecture.composition_audit import (
    audit_chip_model,
    audit_violations,
    block_signature_appendix,
    collect_block_port_info,
    composition_audit_enabled,
)

# ---------------------------------------------------------------------------
# Miniature block models
# ---------------------------------------------------------------------------

PROD_MODEL = '''\
from amaranth import Elaboratable, Module


class prod(Elaboratable):
    def __init__(self, clk, rst, din, din_vld, dout, dout_vld):
        self.clk, self.rst = clk, rst
        self.din, self.din_vld = din, din_vld
        self.dout, self.dout_vld = dout, dout_vld

    def elaborate(self, platform):
        m = Module()
        m.d.sync += self.dout_vld.eq(self.din_vld)
        with m.If(self.din_vld):
            m.d.sync += self.dout.eq(self.din + 1)
        return m
'''

CONS_MODEL = '''\
from amaranth import Elaboratable, Module


class cons(Elaboratable):
    def __init__(self, clk, rst, din, din_vld, pos, dout, dout_vld):
        self.clk, self.rst = clk, rst
        self.din, self.din_vld, self.pos = din, din_vld, pos
        self.dout, self.dout_vld = dout, dout_vld

    def elaborate(self, platform):
        m = Module()
        m.d.sync += self.dout_vld.eq(self.din_vld)
        with m.If(self.din_vld):
            m.d.sync += self.dout.eq(self.din + self.pos)
        return m
'''

# A block with a 3-signal srdy/drdy handshake: drives qp_drdy (grant) and its
# output pair; consumes qp/qp_srdy.
QP_MODEL = '''\
from amaranth import Elaboratable, Module


class qp_block(Elaboratable):
    def __init__(self, clk, rst, qp, qp_srdy, qp_drdy, dout, dout_vld):
        self.clk, self.rst = clk, rst
        self.qp, self.qp_srdy, self.qp_drdy = qp, qp_srdy, qp_drdy
        self.dout, self.dout_vld = dout, dout_vld

    def elaborate(self, platform):
        m = Module()
        m.d.comb += self.qp_drdy.eq(1)
        m.d.sync += self.dout_vld.eq(self.qp_srdy)
        with m.If(self.qp_srdy):
            m.d.sync += self.dout.eq(self.qp)
        return m
'''

# A block that drives its output through a LOCAL helper submodule: the driven-
# param analysis must still see self.dout as an output (and self.din as an
# input) when the value routes through the helper.
NESTED_MODEL = '''\
from amaranth import Elaboratable, Module, Signal


class _inner(Elaboratable):
    def __init__(self):
        self.src = Signal(8)
        self.dst = Signal(8)

    def elaborate(self, platform):
        m = Module()
        m.d.sync += self.dst.eq(self.src)
        return m


class nested(Elaboratable):
    def __init__(self, clk, rst, din, dout):
        self.clk, self.rst, self.din, self.dout = clk, rst, din, dout

    def elaborate(self, platform):
        m = Module()
        inner = _inner()
        m.submodules.inner = inner
        m.d.comb += inner.src.eq(self.din)
        m.d.comb += self.dout.eq(inner.dst)
        return m
'''


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _models_dir(tmp_path: Path, **models: str) -> Path:
    d = tmp_path / "arch" / "block_models"
    for stem, text in models.items():
        _write(d / f"{stem}.py", text)
    return d


def _chip(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "arch" / "block_models" / "_chip_model.py"
    _write(p, body)
    return p


def _checks(violations: list[dict]) -> list[str]:
    return [v.get("audit_check", "") for v in violations]


def _observed(violations: list[dict], check: str) -> str:
    return " ".join(
        v["observed"] for v in violations if v.get("audit_check") == check
    )


# ---------------------------------------------------------------------------
# Port-info extraction
# ---------------------------------------------------------------------------


class TestBlockPortInfo:
    def test_signature_and_driven(self, tmp_path):
        d = _models_dir(tmp_path, prod=PROD_MODEL, qp_block=QP_MODEL)
        infos = collect_block_port_info(d)
        assert infos["prod"].params == [
            "clk", "rst", "din", "din_vld", "dout", "dout_vld",
        ]
        assert infos["prod"].driven == {"dout", "dout_vld"}
        assert infos["qp_block"].driven == {"qp_drdy", "dout", "dout_vld"}

    def test_driven_through_local_helper(self, tmp_path):
        d = _models_dir(tmp_path, nested=NESTED_MODEL)
        infos = collect_block_port_info(d)
        assert "dout" in infos["nested"].driven
        assert "din" not in infos["nested"].driven

    def test_signature_appendix(self, tmp_path):
        d = _models_dir(tmp_path, prod=PROD_MODEL, qp_block=QP_MODEL)
        text = block_signature_appendix(d)
        assert "prod(clk, rst, din, din_vld, dout, dout_vld)" in text
        assert "qp_block(clk, rst, qp, qp_srdy, qp_drdy, dout, dout_vld)" in text


# ---------------------------------------------------------------------------
# Clean composition -> no violations
# ---------------------------------------------------------------------------

CLEAN_CHIP = '''\
from amaranth import Elaboratable, Module, Signal
from prod import prod
from cons import cons


class chip_model(Elaboratable):
    def __init__(self, clk, rst, x, x_vld, pos_in, y, y_vld):
        self.clk, self.rst = clk, rst
        self.x, self.x_vld, self.pos_in = x, x_vld, pos_in
        self.y, self.y_vld = y, y_vld

    def elaborate(self, platform):
        m = Module()
        mid = Signal(8)
        mid_vld = Signal()
        pos = Signal(8)

        m.submodules.i0 = prod(
            self.clk, self.rst, self.x, self.x_vld, mid, mid_vld)
        m.submodules.i1 = cons(
            self.clk, self.rst, mid, mid_vld, pos, self.y, self.y_vld)
        m.d.comb += pos.eq(self.pos_in)
        return m


def simulate(stimulus):
    return [], 0
'''


class TestCleanChip:
    def test_no_violations(self, tmp_path):
        _models_dir(tmp_path, prod=PROD_MODEL, cons=CONS_MODEL)
        chip = _chip(tmp_path, CLEAN_CHIP)
        res = audit_chip_model(chip, chip.parent)
        assert res.violations == []


# ---------------------------------------------------------------------------
# undriven_net (the raster_pos_latched class)
# ---------------------------------------------------------------------------

UNDRIVEN_CHIP = '''\
from amaranth import Elaboratable, Module, Signal
from prod import prod
from cons import cons


class chip_model(Elaboratable):
    def __init__(self, clk, rst, x, x_vld, y, y_vld):
        self.clk, self.rst = clk, rst
        self.x, self.x_vld = x, x_vld
        self.y, self.y_vld = y, y_vld

    def elaborate(self, platform):
        m = Module()
        mid = Signal(8)
        mid_vld = Signal()
        # consumed by BOTH blocks below, driven by NOTHING (raster_pos_latched):
        pos_latched = Signal(8)

        m.submodules.i0 = prod(
            self.clk, self.rst, self.x, self.x_vld, mid, mid_vld)
        m.submodules.i1 = cons(
            self.clk, self.rst, mid, mid_vld, pos_latched, self.y, self.y_vld)
        m.submodules.i2 = cons(
            self.clk, self.rst, mid, mid_vld, pos_latched, self.y, self.y_vld)
        return m


def simulate(stimulus):
    return [], 0
'''

_I2_LINE = '''\
        m.submodules.i2 = cons(
            self.clk, self.rst, mid, mid_vld, pos_latched, self.y, self.y_vld)
'''


class TestUndrivenNet:
    def test_multi_consumer_undriven_flagged(self, tmp_path):
        _models_dir(tmp_path, prod=PROD_MODEL, cons=CONS_MODEL)
        chip = _chip(tmp_path, UNDRIVEN_CHIP)
        res = audit_chip_model(chip, chip.parent)
        checks = _checks(res.violations)
        assert "undriven_net" in checks
        v = res.violations[checks.index("undriven_net")]
        assert "pos_latched" in v["observed"]
        assert v["gap_class"] == "contract"

    def test_single_consumer_zero_init_is_warning_only(self, tmp_path):
        single = UNDRIVEN_CHIP.replace(_I2_LINE, "")
        _models_dir(tmp_path, prod=PROD_MODEL, cons=CONS_MODEL)
        chip = _chip(tmp_path, single)
        res = audit_chip_model(chip, chip.parent)
        assert "undriven_net" not in _checks(res.violations)
        assert any("pos_latched" in w for w in res.warnings)

    def test_nonzero_init_tieoff_allowed(self, tmp_path):
        tied = UNDRIVEN_CHIP.replace(
            "pos_latched = Signal(8)",
            "pos_latched = Signal(8, init=1)",
        )
        _models_dir(tmp_path, prod=PROD_MODEL, cons=CONS_MODEL)
        chip = _chip(tmp_path, tied)
        res = audit_chip_model(chip, chip.parent)
        assert "undriven_net" not in _checks(res.violations)


# ---------------------------------------------------------------------------
# instantiation_signature (the qp_data kwarg class)
# ---------------------------------------------------------------------------

KWARG_CHIP = '''\
from amaranth import Elaboratable, Module, Signal
from qp_block import qp_block


class chip_model(Elaboratable):
    def __init__(self, clk, rst, y, y_vld):
        self.clk, self.rst = clk, rst
        self.y, self.y_vld = y, y_vld

    def elaborate(self, platform):
        m = Module()
        qp = Signal(6)
        qp_srdy = Signal()
        qp_drdy = Signal()
        m.submodules.i0 = qp_block(
            clk=self.clk, rst=self.rst, qp_data=qp, qp_srdy=qp_srdy,
            qp_drdy=qp_drdy, dout=self.y, dout_vld=self.y_vld)
        return m


def simulate(stimulus):
    return [], 0
'''

_KWARG_CALL = '''\
        m.submodules.i0 = qp_block(
            clk=self.clk, rst=self.rst, qp_data=qp, qp_srdy=qp_srdy,
            qp_drdy=qp_drdy, dout=self.y, dout_vld=self.y_vld)
'''


class TestInstantiationSignature:
    def test_unexpected_kwarg_flagged_with_signature(self, tmp_path):
        _models_dir(tmp_path, qp_block=QP_MODEL)
        chip = _chip(tmp_path, KWARG_CHIP)
        res = audit_chip_model(chip, chip.parent)
        checks = _checks(res.violations)
        assert "instantiation_signature" in checks
        sig_viols = [
            v for v in res.violations
            if v["audit_check"] == "instantiation_signature"
        ]
        joined = " ".join(v["observed"] + v["suggested_fix"] for v in sig_viols)
        assert "qp_data" in joined
        # The EXACT expected signature is in the feedback (the fix Gemini
        # needed handed to it after 4 blind attempts).
        assert "qp_block(clk, rst, qp, qp_srdy, qp_drdy, dout, dout_vld)" in joined
        # The missing required param (qp) is reported too.
        assert any("missing required" in v["observed"] for v in sig_viols)

    def test_too_many_positionals(self, tmp_path):
        chip_text = KWARG_CHIP.replace(
            _KWARG_CALL,
            "        m.submodules.i0 = qp_block(\n"
            "            self.clk, self.rst, qp, qp_srdy, qp_drdy, self.y,\n"
            "            self.y_vld, qp)\n",
        )
        _models_dir(tmp_path, qp_block=QP_MODEL)
        chip = _chip(tmp_path, chip_text)
        res = audit_chip_model(chip, chip.parent)
        assert "instantiation_signature" in _checks(res.violations)
        assert "8 positional args but constructor takes 7" in _observed(
            res.violations, "instantiation_signature"
        )


# ---------------------------------------------------------------------------
# Call-site *args / **kwargs (the hand-expansion repair-loop class)
# ---------------------------------------------------------------------------

def _star_chip(call: str, prologue: str = "", init_extra: str = "") -> str:
    """A qp_block instantiation wrapped in a minimal Amaranth chip model."""
    return (
        "from amaranth import Elaboratable, Module, Signal\n"
        "from qp_block import qp_block\n"
        "\n"
        "\n"
        "class chip_model(Elaboratable):\n"
        "    def __init__(self, clk, rst, y, y_vld):\n"
        "        self.clk, self.rst = clk, rst\n"
        "        self.y, self.y_vld = y, y_vld\n"
        + init_extra
        + "\n"
        "    def elaborate(self, platform):\n"
        "        m = Module()\n"
        "        qp = Signal(6)\n"
        "        qp_srdy = Signal()\n"
        "        qp_drdy = Signal()\n"
        + prologue
        + f"        m.submodules.i0 = {call}\n"
        "        return m\n"
        "\n"
        "\n"
        "def simulate(stimulus):\n"
        "    return [], 0\n"
    )


_UNRESOLVED_NOTE = "unresolvable *args/**kwargs"


class TestStarredArgExpansion:
    """``Engine(*ports)`` is valid Python and valid Amaranth.

    Reporting its covered ports as missing is a hard pre-simulation stop on
    correct code: the live run burned three rounds hand-expanding 51/50/37/41
    -positional calls because regeneration kept re-creating the idiom.
    """

    def _audit(self, tmp_path, chip_text):
        _models_dir(tmp_path, qp_block=QP_MODEL)
        chip = _chip(tmp_path, chip_text)
        return audit_chip_model(chip, chip.parent)

    def test_star_names_bound_to_list_literals_cover_required(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(*head, *tail)",
            prologue=(
                "        head = [self.clk, self.rst, qp, qp_srdy]\n"
                "        tail = [qp_drdy, self.y, self.y_vld]\n"
            ),
        ))
        assert res.violations == []
        assert not [w for w in res.warnings if _UNRESOLVED_NOTE in w]

    def test_star_tuple_literal_and_trailing_plain_arg(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(*head, qp_drdy, self.y, self.y_vld)",
            prologue="        head = (self.clk, self.rst, qp, qp_srdy)\n",
        ))
        assert res.violations == []

    def test_last_binding_before_the_call_wins(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(*ports)",
            prologue=(
                "        ports = [self.clk, self.rst]\n"
                "        ports = [self.clk, self.rst, qp, qp_srdy, qp_drdy,\n"
                "                 self.y, self.y_vld]\n"
            ),
        ))
        assert res.violations == []

    def test_short_list_names_exactly_the_uncovered_params(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(*head)",
            prologue="        head = [self.clk, self.rst, qp, qp_srdy]\n",
        ))
        assert "instantiation_signature" in _checks(res.violations)
        observed = _observed(res.violations, "instantiation_signature")
        assert "missing required parameter(s): qp_drdy, dout, dout_vld" in observed
        # the params the star DID cover are not named
        for covered in ("clk,", "rst,", "qp,"):
            assert covered not in observed.split("missing required")[1]

    def test_inline_starred_list_literal(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(*[self.clk, self.rst, qp, qp_srdy, qp_drdy, self.y,\n"
            "                                   self.y_vld])"
        ))
        assert res.violations == []

    def test_inline_starred_list_literal_too_long(self, tmp_path):
        # Expansion makes the arity KNOWN, so a genuine surplus is still a
        # violation -- the star is not a blanket amnesty.
        res = self._audit(tmp_path, _star_chip(
            "qp_block(*[self.clk, self.rst, qp, qp_srdy, qp_drdy, self.y,\n"
            "                                   self.y_vld, qp])"
        ))
        assert "8 positional args but constructor takes 7" in _observed(
            res.violations, "instantiation_signature"
        )

    def test_unresolvable_attribute_star_is_a_note_not_a_violation(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(*self.bundle, qp_drdy, self.y, self.y_vld)",
            init_extra="        self.bundle = (clk, rst, y, y_vld)\n",
        ))
        assert res.violations == []
        notes = [w for w in res.warnings if _UNRESOLVED_NOTE in w]
        assert len(notes) == 1
        assert "positional coverage not statically verified" in notes[0]
        assert "qp_block" in notes[0]

    def test_unresolvable_call_result_star_is_a_note(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(*ports)",
            prologue="        ports = self._collect_ports(qp, qp_srdy)\n",
        ))
        assert res.violations == []
        assert len([w for w in res.warnings if _UNRESOLVED_NOTE in w]) == 1

    def test_reassigned_non_literally_is_unresolvable(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(*ports)",
            prologue=(
                "        ports = [self.clk, self.rst, qp, qp_srdy, qp_drdy,\n"
                "                 self.y, self.y_vld]\n"
                "        ports = self._reorder(ports)\n"
            ),
        ))
        assert res.violations == []
        assert len([w for w in res.warnings if _UNRESOLVED_NOTE in w]) == 1

    def test_nested_star_inside_the_list_is_unresolvable(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(*ports)",
            prologue=(
                "        head = [qp, qp_srdy]\n"
                "        ports = [self.clk, self.rst, *head, qp_drdy,\n"
                "                 self.y, self.y_vld]\n"
            ),
        ))
        assert res.violations == []
        assert len([w for w in res.warnings if _UNRESOLVED_NOTE in w]) == 1

    def test_double_star_dict_literal_binds_by_name(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(**wiring)",
            prologue=(
                '        wiring = {"clk": self.clk, "rst": self.rst,\n'
                '                  "qp": qp, "qp_srdy": qp_srdy,\n'
                '                  "qp_drdy": qp_drdy, "dout": self.y,\n'
                '                  "dout_vld": self.y_vld}\n'
            ),
        ))
        assert res.violations == []
        assert not [w for w in res.warnings if _UNRESOLVED_NOTE in w]

    def test_double_star_dict_literal_keeps_the_unexpected_kwarg_check(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(**wiring)",
            prologue=(
                '        wiring = {"clk": self.clk, "rst": self.rst,\n'
                '                  "qp_data": qp, "qp_srdy": qp_srdy,\n'
                '                  "qp_drdy": qp_drdy, "dout": self.y,\n'
                '                  "dout_vld": self.y_vld}\n'
            ),
        ))
        observed = _observed(res.violations, "instantiation_signature")
        assert "unexpected keyword argument 'qp_data'" in observed
        assert "missing required parameter(s): qp" in observed

    def test_unresolvable_double_star_is_a_note(self, tmp_path):
        res = self._audit(tmp_path, _star_chip(
            "qp_block(self.clk, self.rst, **self.wiring)",
            init_extra="        self.wiring = {}\n",
        ))
        assert res.violations == []
        assert len([w for w in res.warnings if _UNRESOLVED_NOTE in w]) == 1

    def test_expanded_positionals_feed_the_net_analysis(self, tmp_path):
        # Expansion is not just violation suppression: the ports the star
        # covers must become real endpoints, so a net wired ONLY through a
        # star list is still checked for a driver.
        _models_dir(tmp_path, prod=PROD_MODEL, cons=CONS_MODEL)
        chip = _chip(tmp_path, UNDRIVEN_CHIP.replace(
            "        m.submodules.i1 = cons(\n"
            "            self.clk, self.rst, mid, mid_vld, pos_latched,"
            " self.y, self.y_vld)\n",
            "        wires = [self.clk, self.rst, mid, mid_vld, pos_latched,\n"
            "                 self.y, self.y_vld]\n"
            "        m.submodules.i1 = cons(*wires)\n",
        ))
        res = audit_chip_model(chip, chip.parent)
        assert "instantiation_signature" not in _checks(res.violations)
        assert "pos_latched" in _observed(res.violations, "undriven_net")


# ---------------------------------------------------------------------------
# zero_width_signal
# ---------------------------------------------------------------------------

ZERO_WIDTH_CHIP = '''\
from amaranth import Elaboratable, Module, Signal
from prod import prod

W_BAD = 4 - 4


class chip_model(Elaboratable):
    def __init__(self, clk, rst, x, x_vld, y, y_vld):
        self.clk, self.rst = clk, rst
        self.x, self.x_vld = x, x_vld
        self.y, self.y_vld = y, y_vld

    def elaborate(self, platform):
        m = Module()
        mid = Signal(W_BAD)
        mid_vld = Signal()
        m.submodules.i0 = prod(
            self.clk, self.rst, self.x, self.x_vld, mid, mid_vld)
        return m


def simulate(stimulus):
    return [], 0
'''


class TestZeroWidth:
    def test_zero_width_flagged(self, tmp_path):
        _models_dir(tmp_path, prod=PROD_MODEL)
        chip = _chip(tmp_path, ZERO_WIDTH_CHIP)
        res = audit_chip_model(chip, chip.parent)
        assert "zero_width_signal" in _checks(res.violations)

    def test_unresolvable_width_not_flagged(self, tmp_path):
        text = ZERO_WIDTH_CHIP.replace(
            "mid = Signal(W_BAD)",
            "mid = Signal(some_runtime_width)",
        )
        _models_dir(tmp_path, prod=PROD_MODEL)
        chip = _chip(tmp_path, text)
        res = audit_chip_model(chip, chip.parent)
        assert "zero_width_signal" not in _checks(res.violations)


# ---------------------------------------------------------------------------
# unlowerable_introspection (private simulator/hierarchy snooping)
# ---------------------------------------------------------------------------

SYMDICT_CHIP = '''\
from amaranth import Elaboratable, Module, Signal
from prod import prod


class chip_model(Elaboratable):
    def __init__(self, clk, rst, x, x_vld, y, y_vld):
        self.clk, self.rst = clk, rst
        self.x, self.x_vld = x, x_vld
        self.y, self.y_vld = y, y_vld

    def elaborate(self, platform):
        m = Module()
        mid = Signal(8)
        mid_vld = Signal()
        i0 = prod(self.clk, self.rst, self.x, self.x_vld, mid, mid_vld)
        m.submodules.i0 = i0

        # snooping the producer's PRIVATE hierarchy instead of a real port
        state_sig = i0._fragment.state
        m.d.comb += self.y_vld.eq(state_sig)
        return m


def simulate(stimulus):
    return [], 0
'''

SYMDICT_IN_SIMULATE_ONLY = '''\
from amaranth import Elaboratable, Module
from prod import prod


class chip_model(Elaboratable):
    def __init__(self, clk, rst, x, x_vld, y, y_vld):
        self.clk, self.rst = clk, rst
        self.x, self.x_vld = x, x_vld
        self.y, self.y_vld = y, y_vld

    def elaborate(self, platform):
        m = Module()
        m.submodules.i0 = prod(
            self.clk, self.rst, self.x, self.x_vld, self.y, self.y_vld)
        return m


def simulate(stimulus):
    holder = {}
    # post-sim observation snoop -- LEGITIMATE (rule 8)
    recon = holder.get("dut") and holder["dut"]._fragment.recon
    return recon, 0
'''


class TestSymdict:
    def test_symdict_in_chip_model_flagged(self, tmp_path):
        _models_dir(tmp_path, prod=PROD_MODEL)
        chip = _chip(tmp_path, SYMDICT_CHIP)
        res = audit_chip_model(chip, chip.parent)
        assert "unlowerable_introspection" in _checks(res.violations)

    def test_symdict_in_simulate_allowed(self, tmp_path):
        _models_dir(tmp_path, prod=PROD_MODEL)
        chip = _chip(tmp_path, SYMDICT_IN_SIMULATE_ONLY)
        res = audit_chip_model(chip, chip.parent)
        assert "unlowerable_introspection" not in _checks(res.violations)


# ---------------------------------------------------------------------------
# multi_driven_net
# ---------------------------------------------------------------------------

MULTI_DRIVEN_CHIP = CLEAN_CHIP.replace(
    "        m.d.comb += pos.eq(self.pos_in)\n",
    "        m.d.comb += pos.eq(self.pos_in)\n"
    "        m.d.comb += mid.eq(0)   # SECOND driver: prod already drives mid\n",
)


class TestMultiDriven:
    def test_double_driver_flagged(self, tmp_path):
        _models_dir(tmp_path, prod=PROD_MODEL, cons=CONS_MODEL)
        chip = _chip(tmp_path, MULTI_DRIVEN_CHIP)
        res = audit_chip_model(chip, chip.parent)
        checks = _checks(res.violations)
        assert "multi_driven_net" in checks
        v = res.violations[checks.index("multi_driven_net")]
        assert "mid" in v["observed"]


# ---------------------------------------------------------------------------
# Robustness + env gate
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_env_kill_switch(self, tmp_path, monkeypatch):
        _models_dir(tmp_path, prod=PROD_MODEL, cons=CONS_MODEL)
        chip = _chip(tmp_path, UNDRIVEN_CHIP)
        monkeypatch.setenv("CORESMITH_COMPOSITION_AUDIT", "0")
        assert not composition_audit_enabled()
        assert audit_violations(chip, chip.parent) == []
        monkeypatch.setenv("CORESMITH_COMPOSITION_AUDIT", "1")
        assert composition_audit_enabled()
        assert audit_violations(chip, chip.parent) != []

    def test_unparseable_chip_model_skips(self, tmp_path):
        _models_dir(tmp_path, prod=PROD_MODEL)
        chip = _chip(tmp_path, "def broken(:\n")
        res = audit_chip_model(chip, chip.parent)
        assert res.violations == []

    def test_unparseable_block_model_skipped(self, tmp_path):
        d = _models_dir(tmp_path, prod=PROD_MODEL)
        _write(d / "broken.py", "def broken(:\n")
        chip = _chip(tmp_path, CLEAN_CHIP.replace("from cons import cons\n", "")
                     .replace("        m.submodules.i1 = cons(\n"
                              "            self.clk, self.rst, mid, mid_vld,"
                              " pos, self.y, self.y_vld)\n", ""))
        res = audit_chip_model(chip, chip.parent)
        assert all(v["audit_check"] != "instantiation_signature"
                   for v in res.violations)

    def test_missing_chip_model_function_noop(self, tmp_path):
        _models_dir(tmp_path, prod=PROD_MODEL)
        chip = _chip(tmp_path, "def simulate(stimulus):\n    return [], 0\n")
        res = audit_chip_model(chip, chip.parent)
        assert res.violations == []

    def test_violations_json_safe(self, tmp_path):
        import json

        _models_dir(tmp_path, prod=PROD_MODEL, cons=CONS_MODEL)
        chip = _chip(tmp_path, UNDRIVEN_CHIP)
        res = audit_chip_model(chip, chip.parent)
        json.dumps(res.violations)  # must not raise


# ---------------------------------------------------------------------------
# Gate integration: the audit runs BEFORE simulation inside the real gate
# ---------------------------------------------------------------------------

REFERENCE_IMPL = '''\
def run(stim):
    return [v + 1 for v in stim]
'''

# Statically clean, but simulate() constructs a block model with a bad kwarg at
# RUNTIME -- escapes the static audit, raises TypeError in the sim. Exercises
# the R2 signature-feedback enrichment on the simulate()-raised path.
RUNTIME_KWARG_CHIP = '''\
from amaranth import Elaboratable, Module, Signal
from prod import prod


class chip_model(Elaboratable):
    def __init__(self, clk, rst, x, x_vld, y, y_vld):
        self.clk, self.rst = clk, rst
        self.x, self.x_vld = x, x_vld
        self.y, self.y_vld = y, y_vld

    def elaborate(self, platform):
        m = Module()
        m.submodules.i0 = prod(
            self.clk, self.rst, self.x, self.x_vld, self.y, self.y_vld)
        return m


def simulate(stimulus):
    clk = Signal()
    prod(clk=clk, rst=None, data_in=1, din_vld=None, dout=None, dout_vld=None)
    return [], 0
'''


class TestGateIntegration:
    def _project(self, tmp_path, chip_text):
        _models_dir(tmp_path, prod=PROD_MODEL, cons=CONS_MODEL)
        chip = _chip(tmp_path, chip_text)
        _write(tmp_path / "inputs" / "toy_golden.py", REFERENCE_IMPL)
        return tmp_path, chip

    def _gate_env(self, monkeypatch):
        monkeypatch.setenv("CORESMITH_BLOCK_GOLDENS", "1")
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        monkeypatch.delenv("CORESMITH_MODEL_STIMULUS", raising=False)
        monkeypatch.delenv("CORESMITH_FUNCTIONAL_ACCEPTANCE", raising=False)
        monkeypatch.delenv("CORESMITH_SIM_PYTHON", raising=False)

    def test_gate_returns_audit_violations_before_sim(self, tmp_path, monkeypatch):
        from orchestrator.architecture import model_integration

        self._gate_env(monkeypatch)
        monkeypatch.setenv("CORESMITH_COMPOSITION_AUDIT", "1")
        root, _ = self._project(tmp_path, UNDRIVEN_CHIP)
        violations = model_integration.run_model_integration_gate(str(root))
        assert violations, "expected the static audit to flag the undriven net"
        assert violations[0].get("criterion") == "composition_audit"
        assert violations[0].get("audit_check") == "undriven_net"

    def test_gate_kill_switch_falls_through_to_sim(self, tmp_path, monkeypatch):
        from orchestrator.architecture import model_integration

        self._gate_env(monkeypatch)
        monkeypatch.setenv("CORESMITH_COMPOSITION_AUDIT", "0")
        root, _ = self._project(tmp_path, UNDRIVEN_CHIP)
        violations = model_integration.run_model_integration_gate(str(root))
        # With the audit disabled the gate must behave exactly as before:
        # whatever it reports comes from the SIMULATION path, not the audit.
        assert all(
            v.get("criterion") != "composition_audit" for v in violations
        )

    def test_signature_feedback_on_runtime_typeerror(self, tmp_path, monkeypatch):
        from orchestrator.architecture import model_integration

        self._gate_env(monkeypatch)
        monkeypatch.setenv("CORESMITH_COMPOSITION_AUDIT", "1")
        root, _ = self._project(tmp_path, RUNTIME_KWARG_CHIP)
        violations = model_integration.run_model_integration_gate(str(root))
        assert violations
        v = violations[0]
        assert "unexpected keyword argument" in str(v.get("observed", ""))
        # R2: the EXACT factory signature is attached to the feedback.
        assert "prod(clk, rst, din, din_vld, dout, dout_vld)" in str(
            v.get("suggested_fix", "")
        )


# A block model whose logic CRASHES with a location-free exception (the armC
# live wall: bare "IndexError: list index out of range" -> unlocalized ->
# broadcast re-spec of every block).
CRASHING_MODEL = '''\
from amaranth import Elaboratable, Module

_LUT = [1, 2, 3]


class crasher(Elaboratable):
    def __init__(self, clk, rst, din, din_vld, dout, dout_vld):
        self.clk, self.rst = clk, rst
        self.din, self.din_vld = din, din_vld
        self.dout, self.dout_vld = dout, dout_vld

    def elaborate(self, platform):
        m = Module()
        m.d.sync += self.dout_vld.eq(self.din_vld)
        m.d.sync += self.dout.eq(_LUT[13])   # IndexError on elaboration
        return m
'''

CRASHING_CHIP = '''\
from amaranth import Elaboratable, Module, Signal
from amaranth.sim import Simulator
from crasher import crasher


class chip_model(Elaboratable):
    def __init__(self, clk, rst, x, x_vld, y, y_vld):
        self.clk, self.rst = clk, rst
        self.x, self.x_vld = x, x_vld
        self.y, self.y_vld = y, y_vld

    def elaborate(self, platform):
        m = Module()
        m.submodules.i0 = crasher(
            self.clk, self.rst, self.x, self.x_vld, self.y, self.y_vld)
        return m


def simulate(stimulus):
    dut = chip_model(Signal(), Signal(), Signal(8), Signal(),
                     Signal(8), Signal())
    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.run()
    return [], 1
'''


class TestCrashTracebackLocalization:
    """A sim crash must carry its traceback and localize to the crashing
    BLOCK (targeted revise), not report a bare message (broadcast re-fan)."""

    def _project(self, tmp_path):
        _models_dir(tmp_path, crasher=CRASHING_MODEL)
        _chip(tmp_path, CRASHING_CHIP)
        _write(tmp_path / "inputs" / "toy_golden.py", REFERENCE_IMPL)
        return tmp_path

    def _gate_env(self, monkeypatch):
        monkeypatch.setenv("CORESMITH_BLOCK_GOLDENS", "1")
        monkeypatch.setenv("CORESMITH_COMPOSITION_AUDIT", "1")
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        monkeypatch.delenv("CORESMITH_MODEL_STIMULUS", raising=False)
        monkeypatch.delenv("CORESMITH_FUNCTIONAL_ACCEPTANCE", raising=False)

    def _assert_localized(self, violations):
        assert violations
        v = violations[0]
        assert "IndexError" in str(v.get("observed", ""))
        # crash localized to the block, with file:line in the feedback
        assert v.get("first_divergence_block") == "crasher"
        assert v.get("affected_blocks") == ["crasher"]
        fix = str(v.get("suggested_fix", ""))
        assert "crasher.py" in fix
        assert "Crash traceback (tail)" in fix

    def test_thread_mode_localizes_crash(self, tmp_path, monkeypatch):
        from orchestrator.architecture import model_integration

        self._gate_env(monkeypatch)
        monkeypatch.delenv("CORESMITH_SIM_PYTHON", raising=False)
        root = self._project(tmp_path)
        self._assert_localized(
            model_integration.run_model_integration_gate(str(root))
        )

    def test_subprocess_mode_localizes_crash(self, tmp_path, monkeypatch):
        import sys

        from orchestrator.architecture import model_integration

        self._gate_env(monkeypatch)
        monkeypatch.setenv("CORESMITH_SIM_PYTHON", sys.executable)
        root = self._project(tmp_path)
        self._assert_localized(
            model_integration.run_model_integration_gate(str(root))
        )

    def test_context_gap_flag_when_block_dv_passed(self, tmp_path, monkeypatch):
        """When the crashing block PASSED its own DV, the violation must say
        so (stimulus-parity gap) -- redirects the fix to block + TB hole."""
        import json

        from orchestrator.architecture import model_integration

        self._gate_env(monkeypatch)
        monkeypatch.delenv("CORESMITH_SIM_PYTHON", raising=False)
        root = self._project(tmp_path)
        br = root / ".coresmith" / "blocks" / "crasher"
        br.mkdir(parents=True)
        (br / "best_result.json").write_text(json.dumps({"sim_passed": True}))
        violations = model_integration.run_model_integration_gate(str(root))
        assert violations
        fix = str(violations[0].get("suggested_fix", ""))
        assert "PASSED its own block DV" in fix
        assert "stimulus-parity" in fix

    def test_no_context_gap_flag_without_dv_pass(self, tmp_path, monkeypatch):
        from orchestrator.architecture import model_integration

        self._gate_env(monkeypatch)
        monkeypatch.delenv("CORESMITH_SIM_PYTHON", raising=False)
        root = self._project(tmp_path)
        violations = model_integration.run_model_integration_gate(str(root))
        assert violations
        assert "PASSED its own block DV" not in str(
            violations[0].get("suggested_fix", "")
        )

    def test_chip_model_crash_is_contract(self, tmp_path, monkeypatch):
        """A crash in the composition WIRING (not a block) is gap_class
        contract and must not blame a block."""
        from orchestrator.architecture import model_integration

        self._gate_env(monkeypatch)
        monkeypatch.delenv("CORESMITH_SIM_PYTHON", raising=False)
        _models_dir(tmp_path, prod=PROD_MODEL)
        chip_text = (
            "from amaranth import Elaboratable, Module\n"
            "from prod import prod\n"
            "\n"
            "\n"
            "class chip_model(Elaboratable):\n"
            "    def __init__(self, clk, rst, x, x_vld, y, y_vld):\n"
            "        self.clk, self.rst = clk, rst\n"
            "        self.x, self.x_vld = x, x_vld\n"
            "        self.y, self.y_vld = y, y_vld\n"
            "\n"
            "    def elaborate(self, platform):\n"
            "        m = Module()\n"
            "        m.submodules.i0 = prod(\n"
            "            self.clk, self.rst, self.x, self.x_vld, self.y,"
            " self.y_vld)\n"
            "        return m\n"
            "\n"
            "\n"
            "def simulate(stimulus):\n"
            "    lut = [1]\n"
            "    return [lut[9]], 1\n"   # IndexError in _chip_model.py itself
        )
        _chip(tmp_path, chip_text)
        _write(tmp_path / "inputs" / "toy_golden.py", REFERENCE_IMPL)
        violations = model_integration.run_model_integration_gate(str(tmp_path))
        assert violations
        v = violations[0]
        assert v.get("gap_class") == "contract"
        assert "affected_blocks" not in v
        assert "_chip_model.py" in str(v.get("suggested_fix", ""))


class TestSignatureFeedbackHelper:
    def test_non_signature_error_no_appendix(self, tmp_path):
        from orchestrator.architecture.model_integration import _signature_feedback

        d = _models_dir(tmp_path, prod=PROD_MODEL)
        assert _signature_feedback(d, "ZeroDivisionError: division by zero") == ""

    def test_signature_error_gets_appendix(self, tmp_path):
        from orchestrator.architecture.model_integration import _signature_feedback

        d = _models_dir(tmp_path, prod=PROD_MODEL)
        out = _signature_feedback(
            d, "prod() got an unexpected keyword argument 'data_in'"
        )
        assert "prod(clk, rst, din, din_vld, dout, dout_vld)" in out
