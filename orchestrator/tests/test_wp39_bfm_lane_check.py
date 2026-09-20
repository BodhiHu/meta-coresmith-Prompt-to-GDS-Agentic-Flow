"""WP-39: the QSPI BFM drive check inspects the four lane bits only."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from orchestrator.langgraph.bfm_lib import codegen
from orchestrator.langgraph.bfm_lib.qspi_master_bfm import QSPIMasterBFM


def _bfm(io0_bit=2, strict=False):
    b = QSPIMasterBFM.__new__(QSPIMasterBFM)
    b.c = SimpleNamespace(io0_bit=io0_bit)
    b.drive_violations = []
    b.strict_drive = strict
    b._oeb_missing_noted = False
    return b


def _vec(width=38, **bits):
    """A binary string (MSB first) with the named bit indices set to the given chars."""
    chars = ["0"] * width
    for k, v in bits.items():
        chars[width - 1 - int(k[1:])] = v
    return "".join(chars)


def test_unrelated_x_does_not_trip_the_lanes():
    """Review round 2 counterexample: bit 37 = X, lanes [5:2] = 0000 -> clean."""
    b = _bfm()
    assert b._note_drive(_vec(b37="x")) is True and b.drive_violations == []


def test_released_lane_is_a_violation():
    b = _bfm()
    assert b._note_drive(_vec(b3="1")) is False
    assert "io3..io0 = 0010" in b.drive_violations[0]


def test_unresolved_lane_is_a_violation():
    b = _bfm()
    assert b._note_drive(_vec(b4="z")) is False
    assert "unresolved" in b.drive_violations[0]


def test_int_values_still_work():
    b = _bfm()
    assert b._note_drive(0) is True
    assert b._note_drive((0xF << 6) | 0b11) is True      # other pins released, lanes driven
    assert b._note_drive(0xF << 2) is False


def test_simulator_value_with_binstr():
    b = _bfm()
    assert b._note_drive(SimpleNamespace(binstr=_vec(b37="x", b20="1"))) is True
    assert b._note_drive(SimpleNamespace(binstr=_vec(b2="x"))) is False


def test_strict_mode_raises_on_first_violation():
    b = _bfm(strict=True)
    with pytest.raises(AssertionError):
        b._note_drive(_vec(b5="1"))
    assert len(b.drive_violations) == 1


def test_generated_testbenches_use_strict_drive():
    src = codegen.__file__ and open(codegen.__file__, encoding="utf-8").read()
    assert src.count("QSPIMasterBFM(dut, c, strict_drive=True)") == 3
    assert "QSPIMasterBFM(dut, c)" not in src
