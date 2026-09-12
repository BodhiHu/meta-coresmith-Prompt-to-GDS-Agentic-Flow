"""Hierarchy evidence must come from elaboration, including inactive generates."""
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.langchain.agents.integration_lead import assert_blocks_instantiated


@pytest.mark.skipif(not shutil.which("yosys"), reason="requires yosys")
@pytest.mark.parametrize("body", [
    "generate if (0) begin:g leaf u(); end endgenerate",
    "genvar i; generate for(i=0;i<0;i=i+1) begin:g leaf u(); end endgenerate",
])
def test_inactive_generate_is_not_instantiated(body):
    result = assert_blocks_instantiated(
        f"module chip_top(); {body} endmodule", {"leaf"},
        sources=["module leaf(); endmodule"], top_module="chip_top")
    assert result and "leaf" in result


def test_missing_elaborator_is_infrastructure_failure():
    with patch("shutil.which", return_value=None):
        result = assert_blocks_instantiated(
            "module chip_top(); leaf u(); endmodule", {"leaf"},
            sources=["module leaf(); endmodule"], top_module="chip_top")
    assert result and result.kind == "infrastructure_error"


@pytest.mark.parametrize("cells,ok", [({}, False), ({"u": {"type": "leaf"}}, True)])
def test_elaborator_output_is_the_decision(tmp_path, cells, ok):
    # Captured JSON shape from hierarchy -check/write_json; the source text
    # deliberately contains a leaf in both cases.
    design = {"modules": {"chip_top": {"cells": cells}, "leaf": {"cells": {}}}}

    def run(argv, **kwargs):
        script = Path(argv[-1]).read_text()
        output = script.split("write_json ", 1)[1].strip().strip('"')
        Path(output).write_text(json.dumps(design))
        from subprocess import CompletedProcess
        return CompletedProcess(argv, 0, "", "")

    with patch("shutil.which", return_value="/fake/yosys"), patch("subprocess.run", side_effect=run):
        result = assert_blocks_instantiated(
            "module chip_top(); leaf u(); endmodule", {"leaf"},
            sources=["module leaf(); endmodule"], top_module="chip_top")
    assert (result is None) is ok


def test_top_must_be_explicit():
    result = assert_blocks_instantiated(
        "module chip_top(); endmodule module orphan(); leaf u(); endmodule", {"leaf"},
        sources=["module leaf(); endmodule"])
    assert result and "top" in result.lower()
