# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Unit tests for the RETIRED v1 deterministic block-golden composition logic.

No LLM, no EDA. Compatible with `-m "not live_llm and not requires_nix and not e2e"`.

The v1 ``compose_and_run`` harness + ``BlockGolden.step()`` contract + FRD FUNC
vectors are superseded by the v2 MyHDL block-model + model-integration gate
(see test_model_integration.py). The v1 helpers are kept in composition.py for
forensic value, and these tests pin their behaviour; the end-to-end v1 gate is
now reached via the retained ``_run_composition_gate_v1`` (the public
``run_composition_gate`` delegates to the v2 model-integration gate).

Still-relevant shared helpers exercised here: resolve_reference_implementation,
resolve_reference_entrypoint, _run_reference signature mapping, and the
``block_goldens_enabled`` env flag.
"""

from __future__ import annotations

import textwrap
import types as _types
from pathlib import Path

import pytest

from orchestrator.architecture import composition

# ---------------------------------------------------------------------------
# Toy block goldens (written to disk, loaded via importlib like real ones)
# ---------------------------------------------------------------------------

BLOCK_A_DOUBLER = '''\
PORTS = {"inputs": ["in"], "outputs": ["out"]}


class BlockGolden:
    def __init__(self):
        pass

    def reset(self):
        pass

    def step(self, inputs):
        if "in" not in inputs:
            return {}
        return {"out": inputs["in"] * 2}
'''

BLOCK_B_ADD1 = '''\
PORTS = {"inputs": ["in"], "outputs": ["out"]}


class BlockGolden:
    def __init__(self):
        pass

    def reset(self):
        pass

    def step(self, inputs):
        if "in" not in inputs:
            return {}
        return {"out": inputs["in"] + 1}
'''

# Deliberately wrong: adds 99 instead of 1.
BLOCK_B_WRONG = '''\
PORTS = {"inputs": ["in"], "outputs": ["out"]}


class BlockGolden:
    def __init__(self):
        pass

    def reset(self):
        pass

    def step(self, inputs):
        if "in" not in inputs:
            return {}
        return {"out": inputs["in"] + 99}
'''

# Accumulator with a feedback edge from its own previous output.
BLOCK_ACC = '''\
PORTS = {"inputs": ["in", "acc_fb"], "outputs": ["out"]}


class BlockGolden:
    def __init__(self):
        self.last = 0

    def reset(self):
        self.last = 0

    def step(self, inputs):
        prev = inputs.get("acc_fb", 0)
        val = inputs.get("in", 0)
        total = prev + val
        return {"out": total}
'''


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# Toy chip = blockB(blockA(x)) = (x*2) + 1
TOY_BLOCK_DIAGRAM = {
    "blocks": [
        {"name": "blockA", "interfaces": {"in": {"direction": "input"},
                                          "out": {"direction": "output"}}},
        {"name": "blockB", "interfaces": {"in": {"direction": "input"},
                                          "out": {"direction": "output"}}},
    ],
    "connections": [
        # chip ingress -> blockA.in
        {"from": "chip_in", "to": "blockA", "from_port": "chip_in", "to_port": "in"},
        # blockA.out -> blockB.in
        {"from": "blockA", "to": "blockB", "from_port": "out", "to_port": "in"},
        # blockB.out -> chip egress
        {"from": "blockB", "to": "chip_out", "from_port": "out", "to_port": "chip_out"},
    ],
}


def toy_reference(x: int) -> int:
    """The reference implementation: (x*2) + 1."""
    return x * 2 + 1


# ---------------------------------------------------------------------------
# compose_and_run -- forward DAG
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Feedback edge (one-transaction delay accumulator)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# parse_func_vectors
# ---------------------------------------------------------------------------

class TestParseFuncVectors:
    def test_small_snippet(self):
        frd = textwrap.dedent(
            """\
            # FRD

            ## Functional Vectors

            - **ID**: FUNC-001
            - **Block / I-O ports**: blockA / in -> out
            - **Stimulus**: 5
            - **Expected output**: 11
            - **Priority**: must_have

            - **ID**: FUNC-002
            - **Block / I-O ports**: blockB / in -> out
            - **Stimulus**: 3
            - **Expected output**: 7
            - **Priority**: should_have

            ## Timing Requirements
            - **ID**: TIME-001
            """
        )
        vecs = composition.parse_func_vectors(frd)
        ids = {v["id"] for v in vecs}
        assert ids == {"FUNC-001", "FUNC-002"}
        by_id = {v["id"]: v for v in vecs}
        assert by_id["FUNC-001"]["block"].startswith("blockA")
        assert by_id["FUNC-001"]["stimulus"] == 5
        assert by_id["FUNC-001"]["expected_output"] == 11
        assert by_id["FUNC-001"]["priority"] == "must_have"
        # TIME-001 must NOT be parsed as a FUNC vector.
        assert "TIME-001" not in str(ids)

    def test_real_frd_field_layout(self):
        # Mirrors the field labels from orchestrator/langchain/prompts/frd_spec.md
        frd = textwrap.dedent(
            """\
            ## Functional Vectors

            ### FUNC-010
            - **ID**: FUNC-010
            - **Block / I-O ports**: dct_engine / pixel_in -> coeff_out
            - **Stimulus**: [8, 8, 8, 8, 8, 8, 8, 8]
            - **Expected output**: [64, 0, 0, 0, 0, 0, 0, 0]
            - **Priority**: must_have
            """
        )
        vecs = composition.parse_func_vectors(frd)
        assert len(vecs) == 1
        v = vecs[0]
        assert v["id"] == "FUNC-010"
        assert v["stimulus"] == [8, 8, 8, 8, 8, 8, 8, 8]
        assert v["expected_output"] == [64, 0, 0, 0, 0, 0, 0, 0]

    def test_empty_returns_empty(self):
        assert composition.parse_func_vectors("") == []
        assert composition.parse_func_vectors("# no vectors here") == []


# ---------------------------------------------------------------------------
# run_composition_gate -- end to end on a toy project tree
# ---------------------------------------------------------------------------

def _toy_project(tmp_path: Path, block_b_text: str, *, with_reference=True):
    """Build a minimal project tree the gate can consume."""
    import json

    root = tmp_path
    (root / ".coresmith").mkdir(parents=True, exist_ok=True)
    (root / ".coresmith" / "block_diagram.json").write_text(
        json.dumps(TOY_BLOCK_DIAGRAM), encoding="utf-8"
    )

    gd = root / "arch" / "block_goldens"
    _write(gd / "blockA.py", BLOCK_A_DOUBLER)
    _write(gd / "blockB.py", block_b_text)

    frd = textwrap.dedent(
        """\
        ## Functional Vectors

        - **ID**: FUNC-001
        - **Block / I-O ports**: blockB / chip_in -> chip_out
        - **Stimulus**: {"chip_in": [5]}
        - **Expected output**: 11
        - **Priority**: must_have
        """
    )
    _write(root / "arch" / "frd_spec.md", frd)

    if with_reference:
        ref = textwrap.dedent(
            """\
            def run(stim):
                x = stim["chip_in"][0]
                return x * 2 + 1
            """
        )
        _write(root / "inputs" / "toy_golden.py", ref)
    return root


# ---------------------------------------------------------------------------
# resolve_reference_implementation
# ---------------------------------------------------------------------------

class TestResolveReference:
    def test_finds_inputs_golden(self, tmp_path):
        ref = tmp_path / "inputs" / "mydesign_golden.py"
        _write(ref, "def run(x): return x\n")
        found = composition.resolve_reference_implementation(str(tmp_path))
        assert found is not None
        assert Path(found).name == "mydesign_golden.py"

    def test_none_when_absent(self, tmp_path):
        assert composition.resolve_reference_implementation(str(tmp_path)) is None

    def test_env_source_root_wins_over_prd_golden(self, tmp_path, monkeypatch):
        """An explicit CORESMITH_SOURCE_ROOT override must beat auto-discovery
        (e.g. a *_golden.py named in the PRD) so a bitstream-only reference
        WRAPPER can be selected even when the PRD cites the raw golden."""
        # PRD names a golden that also exists on disk (would win at priority 1).
        prd_golden = tmp_path / "examples" / "d" / "thing_golden.py"
        _write(prd_golden, "def encode_image(x): return (b'', [], None)\n")
        _write(tmp_path / "arch" / "prd_spec.md",
               "reference: examples/d/thing_golden.py\n")
        # Operator points at a bitstream-only wrapper instead.
        wrapper = tmp_path / "ref_wrapper.py"
        _write(wrapper, "def encode(x, qp=36): return b''\n")
        monkeypatch.setenv("CORESMITH_SOURCE_ROOT", str(wrapper))
        found = composition.resolve_reference_implementation(str(tmp_path))
        assert Path(found).name == "ref_wrapper.py", found

    def test_prd_golden_used_when_no_env_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CORESMITH_SOURCE_ROOT", raising=False)
        golden = tmp_path / "examples" / "d" / "thing_golden.py"
        _write(golden, "def run(x): return x\n")
        found = composition.resolve_reference_implementation(str(tmp_path))
        assert Path(found).name == "thing_golden.py", found

    def test_generator_reference_distinct_from_gate(self, tmp_path, monkeypatch):
        """Generators get the FULL golden via CORESMITH_GENERATOR_SOURCE even when
        the gate's CORESMITH_SOURCE_ROOT points at a bytes-only wrapper."""
        full = tmp_path / "design_golden.py"
        _write(full, "def encode_image_v2(p, qp=36): return (b'', [], None)\n")
        wrapper = tmp_path / "wrap.py"
        _write(wrapper, "def encode(p, qp=36): return b''\n")
        monkeypatch.setenv("CORESMITH_SOURCE_ROOT", str(wrapper))      # gate oracle
        monkeypatch.setenv("CORESMITH_GENERATOR_SOURCE", str(full))     # generators
        assert Path(composition.resolve_reference_implementation(str(tmp_path))).name == "wrap.py"
        assert Path(composition.resolve_generator_reference(str(tmp_path))).name == "design_golden.py"

    def test_generator_reference_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CORESMITH_GENERATOR_SOURCE", raising=False)
        monkeypatch.delenv("CORESMITH_SOURCE_ROOT", raising=False)
        golden = tmp_path / "inputs" / "x_golden.py"
        _write(golden, "def run(x): return x\n")
        # No generator override -> same as resolve_reference_implementation.
        assert (composition.resolve_generator_reference(str(tmp_path))
                == composition.resolve_reference_implementation(str(tmp_path)))


# ---------------------------------------------------------------------------
# block_goldens_enabled flag helper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# resolve_reference_entrypoint
# ---------------------------------------------------------------------------


def _make_ref_module(src: str, name: str = "_test_ref_mod"):
    """Build a module object from source for entrypoint tests."""
    mod = _types.ModuleType(name)
    mod.__name__ = name
    exec(compile(src, name, "exec"), mod.__dict__)
    return mod


class TestResolveReferenceEntrypoint:
    def test_env_bare_func(self, tmp_path, monkeypatch):
        mod = _make_ref_module("def my_entry(x):\n    return x + 1\n")
        monkeypatch.setenv("CORESMITH_REFERENCE_ENTRY", "my_entry")
        fn, name = composition.resolve_reference_entrypoint(str(tmp_path), mod)
        assert callable(fn)
        assert name == "my_entry"
        assert fn(4) == 5

    def test_env_module_colon_func(self, tmp_path, monkeypatch):
        # module:func form resolving via attr fallback on the ref module.
        mod = _make_ref_module("def top(x):\n    return x * 3\n")
        # "doesnotexist:top" -> import fails, falls back to getattr(mod, top)
        monkeypatch.setenv("CORESMITH_REFERENCE_ENTRY", "nosuchmod:top")
        fn, name = composition.resolve_reference_entrypoint(str(tmp_path), mod)
        assert callable(fn)
        assert fn(2) == 6

    def test_declared_in_prd(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        mod = _make_ref_module(
            "def helper(x):\n    return x\n"
            "def the_oracle(x):\n    return x - 7\n"
        )
        (tmp_path / "arch").mkdir(parents=True, exist_ok=True)
        (tmp_path / "arch" / "prd_spec.md").write_text(
            "Notes\nreference_entry_point: the_oracle\nmore\n",
            encoding="utf-8",
        )
        fn, name = composition.resolve_reference_entrypoint(str(tmp_path), mod)
        assert name == "the_oracle"
        assert fn(10) == 3

    def test_discovery_preferred_name(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        mod = _make_ref_module(
            "def aux(x):\n    return 0\n"
            "def encode(x):\n    return x + 100\n"
        )
        fn, name = composition.resolve_reference_entrypoint(str(tmp_path), mod)
        assert name == "encode"
        assert fn(1) == 101

    def test_discovery_single_function(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        mod = _make_ref_module("def whatever(x):\n    return x * x\n")
        fn, name = composition.resolve_reference_entrypoint(str(tmp_path), mod)
        assert name == "whatever"
        assert fn(5) == 25

    def test_discovery_ambiguous_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        # Two non-conventional public functions, no clear winner -> None.
        mod = _make_ref_module("def alpha(x):\n    return 1\ndef beta(x):\n    return 2\n")
        fn, name = composition.resolve_reference_entrypoint(str(tmp_path), mod)
        assert fn is None


# ---------------------------------------------------------------------------
# _run_reference signature mapping
# ---------------------------------------------------------------------------

class TestRunReferenceSignatureMapping:
    def test_by_name_mapping(self):
        def ref(a, b):
            return a * 10 + b

        out = composition._run_reference(ref, {"a": 3, "b": 4})
        assert out == 34

    def test_positional_when_names_dont_match(self):
        def ref(x, y):
            return [x, y]

        # keys do not match params -> positional in insertion order.
        out = composition._run_reference(ref, {"port_one": 1, "port_two": 2})
        assert out == [1, 2]

    def test_single_param_takes_whole_dict(self):
        def ref(stim):
            return stim["chip_in"][0] * 2 + 1

        out = composition._run_reference(ref, {"chip_in": [5]})
        assert out == 11

    def test_failure_returns_none(self):
        def ref(a, b, c):
            return a + b + c

        # too few args available -> exception -> None
        out = composition._run_reference(ref, {"only_one": 1})
        assert out is None

    def test_failure_reraises_when_requested(self):
        def ref(x):
            return x.shape  # crashes on a list -> no oracle

        # Default: swallow -> None (legacy/v1 path).
        assert composition._run_reference(ref, [1, 2, 3]) is None
        # reraise=True (the gate): a crash must surface, not silently pass.
        with pytest.raises(composition.ReferenceInvocationError):
            composition._run_reference(ref, [1, 2, 3], reraise=True)

    def test_tuple_normalized_to_list(self):
        def ref(x):
            return (x, x + 1)

        assert composition._run_reference(ref, 5) == [5, 6]


# ---------------------------------------------------------------------------
# parse_func_vectors -- structured stimulus/expected
# ---------------------------------------------------------------------------

class TestParseFuncVectorsStructured:
    def test_fenced_json_stimulus_and_expected(self):
        frd = textwrap.dedent(
            """\
            ## Functional Vectors

            ### FUNC-001
            - **ID**: FUNC-001
            - **Block / I-O ports**: top / chip_in -> chip_out
            - **Stimulus**: some prose describing the input
            - **Expected output**: prose value
            - **Machine-readable vector**:

            ```json
            {"stimulus": {"chip_in": [1, 2, 3]}, "expected": {"chip_out": [3, 5, 7]}}
            ```
            - **Priority**: must_have
            """
        )
        vecs = composition.parse_func_vectors(frd)
        assert len(vecs) == 1
        v = vecs[0]
        assert v["stimulus_struct"] == {"chip_in": [1, 2, 3]}
        assert v["expected_struct"] == {"chip_out": [3, 5, 7]}
        # prose fields preserved for back-compat
        assert v["stimulus"]

    def test_fenced_json_stimulus_only(self):
        frd = textwrap.dedent(
            """\
            ## Functional Vectors

            ### FUNC-002
            - **ID**: FUNC-002
            ```json
            {"stimulus": {"in": [9]}}
            ```
            """
        )
        vecs = composition.parse_func_vectors(frd)
        v = vecs[0]
        assert v["stimulus_struct"] == {"in": [9]}
        assert v["expected_struct"] is None

    def test_inline_structured_stimulus(self):
        frd = textwrap.dedent(
            """\
            ## Functional Vectors

            ### FUNC-003
            - **ID**: FUNC-003
            - **Stimulus (structured)**: {"pixels": [8, 8]}
            """
        )
        vecs = composition.parse_func_vectors(frd)
        v = vecs[0]
        assert v["stimulus_struct"] == {"pixels": [8, 8]}

    def test_no_structured_block_leaves_none(self):
        frd = textwrap.dedent(
            """\
            ## Functional Vectors

            - **ID**: FUNC-004
            - **Stimulus**: 5
            - **Expected output**: 11
            """
        )
        vecs = composition.parse_func_vectors(frd)
        v = vecs[0]
        assert v["stimulus_struct"] is None
        assert v["expected_struct"] is None
        assert v["stimulus"] == 5  # prose path unchanged


# ---------------------------------------------------------------------------
# Reference-as-oracle differential gate (end to end, no LLM/EDA)
# ---------------------------------------------------------------------------

def _ref_oracle_project(tmp_path: Path, block_b_text: str):
    """Toy project where the REFERENCE (not prose) is the oracle.

    chip = blockB(blockA(x)); reference ref(x) = x*2+1. FRD vector carries a
    structured stimulus and NO expected (reference computes it).
    """
    import json as _json

    root = tmp_path
    (root / ".coresmith").mkdir(parents=True, exist_ok=True)
    (root / ".coresmith" / "block_diagram.json").write_text(
        _json.dumps(TOY_BLOCK_DIAGRAM), encoding="utf-8"
    )
    gd = root / "arch" / "block_goldens"
    _write(gd / "blockA.py", BLOCK_A_DOUBLER)
    _write(gd / "blockB.py", block_b_text)

    frd = textwrap.dedent(
        """\
        ## Functional Vectors

        ### FUNC-001
        - **ID**: FUNC-001
        - **Block / I-O ports**: blockB / chip_in -> chip_out
        - **Stimulus**: prose
        - **Machine-readable vector**:
        ```json
        {"stimulus": {"chip_in": [1, 2, 3]}}
        ```
        - **Priority**: must_have
        """
    )
    _write(root / "arch" / "frd_spec.md", frd)

    # Reference implementation: x*2+1, single-param entry "run".
    ref = textwrap.dedent(
        """\
        def run(stim):
            xs = stim["chip_in"]
            return [x * 2 + 1 for x in xs]
        """
    )
    _write(root / "inputs" / "toy_golden.py", ref)
    return root


class TestFloatPolicyHelpers:
    """Fixed-point-default / float-epsilon gate policy (user spec): bias to
    fixed-point bit-exact; when the reference OUTPUT is float-valued, allow an
    epsilon tolerance."""


# ---------------------------------------------------------------------------
# Reference-entry ABI preflight + resolution provenance
# ---------------------------------------------------------------------------

class TestEntryAcceptsStimulus:
    """The oracle is always invoked as ``entry(stimulus)``."""

    def test_positional_forms_accept(self):
        def one(x):
            return x

        def defaulted(x=None):
            return x

        def varargs(*args):
            return args

        assert composition._entry_accepts_stimulus(one) is True
        assert composition._entry_accepts_stimulus(defaulted) is True
        assert composition._entry_accepts_stimulus(varargs) is True

    def test_zero_positional_forms_reject(self):
        def main():
            return 0

        def kwonly(*, stimulus=None):
            return stimulus

        def kwargs_only(**kw):
            return kw

        assert composition._entry_accepts_stimulus(main) is False
        assert composition._entry_accepts_stimulus(kwonly) is False
        assert composition._entry_accepts_stimulus(kwargs_only) is False
        assert composition._entry_accepts_stimulus(None) is False

    def test_uninspectable_callable_is_accepted(self):
        # "unknown" is not proof of impossibility -- a real invocation failure
        # is still reported as reference_uninvokable.
        assert composition._entry_accepts_stimulus(len) is True


class TestEntryAbiPreflight:
    """THE BUG: a zero-argument ``main()`` became the whole-chip oracle.

    A live run auto-selected ``generate_normative_rom_images.py:main`` -- a
    ROM utility -- as the golden reference; every vector died on "main() takes
    0 positional arguments but 1 was given" and the category error consumed 11
    chip-lead decisions.
    """

    def test_zero_arg_main_is_never_discovered(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        mod = _make_ref_module(
            "def main():\n    return 0\n"
            "def build_rom(path):\n    return path\n"
        )
        fn, name = composition.resolve_reference_entrypoint(str(tmp_path), mod)
        assert name != "main"
        assert fn is not None and fn is mod.build_rom

    def test_module_with_only_zero_arg_main_resolves_to_nothing(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        mod = _make_ref_module("def main():\n    return 0\n")
        fn, name, source = composition.resolve_reference_entrypoint(
            str(tmp_path), mod, with_provenance=True
        )
        assert fn is None
        assert source == composition.ENTRY_SOURCE_NONE

    def test_main_taking_a_stimulus_needs_explicit_config(
        self, tmp_path, monkeypatch
    ):
        """``main(stimulus)`` is invokable, but still never GUESSED at.

        Even as the module's SOLE public function: ``main`` is a script
        convention, so its presence says nothing about whether it models the
        chip. An operator asserting it explicitly is a different matter.
        """
        src = "def main(stimulus):\n    return stimulus\n"
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        mod = _make_ref_module(src)
        fn, _name = composition.resolve_reference_entrypoint(str(tmp_path), mod)
        assert fn is None

        # Declared in the PRD -> resolves (explicit, and ABI-clean).
        (tmp_path / "arch").mkdir(parents=True, exist_ok=True)
        (tmp_path / "arch" / "prd_spec.md").write_text(
            "reference_entry_point: main\n", encoding="utf-8"
        )
        fn2, name2, source2 = composition.resolve_reference_entrypoint(
            str(tmp_path), mod, with_provenance=True
        )
        assert fn2 is mod.main
        assert (name2, source2) == ("main", composition.ENTRY_SOURCE_DECLARED)

        # Env var -> resolves too, and outranks the declaration.
        monkeypatch.setenv("CORESMITH_REFERENCE_ENTRY", "main")
        fn3, name3, source3 = composition.resolve_reference_entrypoint(
            str(tmp_path), mod, with_provenance=True
        )
        assert fn3 is mod.main
        assert (name3, source3) == ("main", composition.ENTRY_SOURCE_ENV)

    def test_explicit_env_entry_with_no_positional_fails_fast(
        self, tmp_path, monkeypatch
    ):
        mod = _make_ref_module(
            "def main():\n    return 0\n"
            "def encode(x):\n    return x\n"
        )
        monkeypatch.setenv("CORESMITH_REFERENCE_ENTRY", "main")
        with pytest.raises(composition.ReferenceEntryPointError) as exc:
            composition.resolve_reference_entrypoint(str(tmp_path), mod)
        msg = str(exc.value)
        # Actionable: names the callable, its signature, and the way out --
        # and does NOT silently fall back to the `encode` heuristic.
        assert "main()" in msg
        assert "CORESMITH_REFERENCE_ENTRY" in msg
        assert "no positional argument" in msg.lower()

    def test_explicit_declared_entry_with_no_positional_fails_fast(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        mod = _make_ref_module("def main():\n    return 0\n")
        (tmp_path / "arch").mkdir(parents=True, exist_ok=True)
        (tmp_path / "arch" / "prd_spec.md").write_text(
            "reference_entry_point: main\n", encoding="utf-8"
        )
        with pytest.raises(composition.ReferenceEntryPointError) as exc:
            composition.resolve_reference_entrypoint(str(tmp_path), mod)
        assert "prd_spec.md" in str(exc.value)


class TestEntryResolutionProvenance:
    """``with_provenance=True`` reports WHICH tier chose the oracle."""

    def test_env_declared_and_discovered_tiers(self, tmp_path, monkeypatch):
        mod = _make_ref_module(
            "def encode(x):\n    return x\n"
            "def the_oracle(x):\n    return x\n"
        )
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        _fn, name, source = composition.resolve_reference_entrypoint(
            str(tmp_path), mod, with_provenance=True
        )
        assert (name, source) == ("encode", composition.ENTRY_SOURCE_DISCOVERED)

        (tmp_path / "arch").mkdir(parents=True, exist_ok=True)
        (tmp_path / "arch" / "prd_spec.md").write_text(
            "reference_entry_point: the_oracle\n", encoding="utf-8"
        )
        _fn, name, source = composition.resolve_reference_entrypoint(
            str(tmp_path), mod, with_provenance=True
        )
        assert (name, source) == ("the_oracle", composition.ENTRY_SOURCE_DECLARED)

        monkeypatch.setenv("CORESMITH_REFERENCE_ENTRY", "encode")
        _fn, name, source = composition.resolve_reference_entrypoint(
            str(tmp_path), mod, with_provenance=True
        )
        assert (name, source) == ("encode", composition.ENTRY_SOURCE_ENV)

    def test_default_call_still_returns_a_pair(self, tmp_path, monkeypatch):
        """Back-compat: every existing caller unpacks exactly two values."""
        monkeypatch.delenv("CORESMITH_REFERENCE_ENTRY", raising=False)
        mod = _make_ref_module("def encode(x):\n    return x\n")
        assert len(composition.resolve_reference_entrypoint(str(tmp_path), mod)) == 2
