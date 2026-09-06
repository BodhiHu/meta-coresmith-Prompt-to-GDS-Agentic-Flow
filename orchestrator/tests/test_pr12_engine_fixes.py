# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""PR#12 engine follow-ups surfaced by the evaluation harness sweep (findings #3/#5/#7/
#9/#12). Pure-function unit coverage; no LLM / EDA toolchain."""
from __future__ import annotations

import json
import re


# --- #12: cocotb @test(stage=N) discovery -----------------------------------
class TestCocotbStageDiscovery:
    PAT = r"@cocotb\.test\s*\("

    def test_counts_plain_and_staged_and_kwargs(self):
        tb = (
            "@cocotb.test()\n"
            "async def a(dut): pass\n"
            "@cocotb.test(stage=1)\n"
            "async def b(dut): pass\n"
            "@cocotb.test(skip=False, timeout_time=10)\n"
            "async def c(dut): pass\n"
        )
        assert len(re.findall(self.PAT, tb)) == 3

    def test_old_regex_missed_staged(self):
        # regression witness: the pre-fix literal regex found only 1 of 3
        assert len(re.findall(r"@cocotb\.test\(\)",
                              "@cocotb.test()\n@cocotb.test(stage=1)\n"
                              "@cocotb.test(stage=2)\n")) == 1


# --- #3: storage-lint env thresholds + reviewed-flop exception --------------
