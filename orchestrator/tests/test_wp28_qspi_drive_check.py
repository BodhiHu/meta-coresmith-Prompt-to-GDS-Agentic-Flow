"""WP-28: the QSPI master BFM flags a read data nibble whose io lanes are released."""
from __future__ import annotations

from types import SimpleNamespace

from orchestrator.langgraph.bfm_lib.qspi_contract import QSPIContract
from orchestrator.langgraph.bfm_lib.qspi_master_bfm import QSPIMasterBFM


def _bfm():
    c = QSPIContract()
    dut = SimpleNamespace(io_in=SimpleNamespace(value=0), io_out=SimpleNamespace(value=0),
                          io_oeb=SimpleNamespace(value=0))
    setattr(dut, c.clk_name, SimpleNamespace())
    return QSPIMasterBFM(dut, c), c


def test_driven_lanes_are_clean():
    bfm, c = _bfm()
    assert bfm._note_drive(0) is True
    assert bfm._note_drive((0xF << (c.io0_bit + 4)) | 0b11) is True   # other pins released, lanes driven
    assert bfm.drive_violations == []


def test_released_lane_is_a_violation():
    bfm, c = _bfm()
    assert bfm._note_drive(0xF << c.io0_bit) is False
    assert len(bfm.drive_violations) == 1 and "released" in bfm.drive_violations[0]


def test_x_on_oeb_is_a_violation():
    bfm, _ = _bfm()
    assert bfm._note_drive("xxxx") is False
