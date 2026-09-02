# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""The model-integration gate must refuse an oracle it cannot call.

Live failure: the composition gate auto-selected
``generate_normative_rom_images.py:main`` -- a ZERO-ARGUMENT ROM utility -- as
the whole-chip golden reference. ``_default_stimulus`` happily derived a
stimulus for it, every vector then died on "main() takes 0 positional arguments
but 1 was given", and the category error consumed 11 chip-lead decisions.

Two pins: (1) no stimulus is ever fabricated for a callable with no positional
parameter, and (2) an explicitly configured entry that fails the ABI preflight
is a HARD violation with an actionable message -- never a skip, never a pass.
"""

from __future__ import annotations

import pytest

from orchestrator.architecture import model_integration as mi
from orchestrator.architecture.composition import ReferenceEntryPointError


@pytest.fixture()
def goldens_on(monkeypatch):
    monkeypatch.setenv("CORESMITH_BLOCK_GOLDENS", "1")
    monkeypatch.delenv("CORESMITH_MODEL_STIMULUS", raising=False)


def _mk_models(root):
    """A minimal arch/block_models tree with a pass-through chip model."""
    d = root / "arch" / "block_models"
    d.mkdir(parents=True)
    (d / "blk.py").write_text("def blk(x):\n    return x\n", encoding="utf-8")
    (d / "_chip_model.py").write_text(
        "def simulate(stimulus):\n    return stimulus\n", encoding="utf-8"
    )
    return d


class TestDefaultStimulus:
    def test_zero_arg_entry_raises_instead_of_fabricating(self):
        def main():
            return 0

        with pytest.raises(ReferenceEntryPointError) as exc:
            mi._default_stimulus(main)
        msg = str(exc.value)
        assert "main()" in msg
        assert "no positional argument" in msg.lower()
        assert "CORESMITH_REFERENCE_ENTRY" in msg

    def test_keyword_only_entry_also_raises(self):
        def run(*, stimulus=None):
            return stimulus

        with pytest.raises(ReferenceEntryPointError):
            mi._default_stimulus(run)

    def test_single_positional_entry_still_gets_the_stream(self):
        def encode(data):
            return data

        assert mi._default_stimulus(encode) == [1, 2, 3, 4]

    def test_varargs_entry_still_gets_the_bare_stream(self):
        def encode(*args):
            return args

        assert mi._default_stimulus(encode) == [1, 2, 3, 4]

    def test_multi_param_entry_binds_by_first_name(self):
        def encode(data, width):
            return data

        assert mi._default_stimulus(encode) == {"data": [1, 2, 3, 4]}

    def test_none_entry_is_still_none(self):
        assert mi._default_stimulus(None) is None


class TestGateRefusesUncallableOracle:
    def test_explicit_zero_arg_entry_is_a_violation_not_a_pass(
        self, tmp_path, goldens_on, monkeypatch
    ):
        """CORESMITH_REFERENCE_ENTRY naming a zero-arg callable fails fast."""
        _mk_models(tmp_path)
        inputs = tmp_path / "inputs"
        inputs.mkdir()
        (inputs / "rom_golden.py").write_text(
            "def main():\n"
            "    return 0\n"
            "def build_rom(path):\n"
            "    return [1, 2, 3, 4]\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("CORESMITH_REFERENCE_ENTRY", "main")

        info: dict = {}
        viols = mi.run_model_integration_gate(str(tmp_path), result_info=info)

        assert len(viols) == 1
        v = viols[0]
        assert v["type"] == "model_integration_failure"
        assert v["criterion"] == "reference_entry_abi_mismatch"
        assert v["gap_class"] == "contract"
        assert "main()" in v["observed"]
        assert "NOT a pass" in v["suggested_fix"]
        # SKIP would read as "nothing checked"; this is a checked FAILURE.
        assert info["skipped"] is False

    def test_zero_arg_main_is_never_auto_selected(
        self, tmp_path, goldens_on, monkeypatch
    ):
        """Without explicit config the ROM utility is not promoted to oracle.

        The gate has no entry it can call, so it SKIPs (honestly) rather than
        selecting ``main`` and failing every vector on the call itself.
        """
        _mk_models(tmp_path)
        inputs = tmp_path / "inputs"
        inputs.mkdir()
        (inputs / "rom_golden.py").write_text(
            "def main():\n"
            "    return 0\n"
            "def emit_rom():\n"
            "    return 1\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)

        info: dict = {}
        viols = mi.run_model_integration_gate(str(tmp_path), result_info=info)

        assert viols == []
        assert info["skipped"] is True
        assert "reference entry" in info["reason"]
