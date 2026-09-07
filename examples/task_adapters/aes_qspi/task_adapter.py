# coresmith-python: /home/ubuntu/ppabench-venv/bin/python
# coresmith-timeout-s: 5400
"""aes_qspi task adapter: the PUBLISHED ppabench grader's functional part, verbatim.

hidden_tb.py (the FIPS-197 known-answer case plus 5 derived random cases of 2..4
blocks, byte-exact) + qspi_host.py + oracle.py under system Icarus, then the
task.yaml throughput gate (cycles per AES block, cap 42; golden 21).
"""
import sys

sys.path.insert(0, "/home/ubuntu/task-seeds/_common")
from ppabench_accel_adapter import grade as _grade  # noqa: E402

TASK = "aes_qspi"
TOP = "user_project_wrapper"
LABEL = "published ppabench grader: hidden_tb + throughput gate (functional part)"
CASES = ["fips197"] + [f"seed{i}" for i in range(1, 6)]


def grade(candidate, workdir):
    return _grade(TASK, candidate, workdir)
