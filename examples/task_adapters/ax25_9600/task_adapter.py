# coresmith-python: /home/ubuntu/ppabench-venv/bin/python
# coresmith-timeout-s: 5400
"""ax25_9600 task adapter: the PUBLISHED ppabench grader's functional part, verbatim.

hidden_tb.py (5 random packets, each in bit mode and baseband mode, CAP_TIMEOUT
400,000 wb_clk cycles per case) + qspi_host.py + oracle.py under system Icarus,
then the task.yaml throughput gate (worst-case cycles per transmitted bit,
BIT-mode cases, cap 4.21). Dire Wolf, synthesis, STA and power belong to the
final grade, not to acceptance.
"""
import sys

sys.path.insert(0, "/home/ubuntu/task-seeds/_common")
from ppabench_accel_adapter import grade as _grade  # noqa: E402

TASK = "ax25_9600"
TOP = "user_project_wrapper"
LABEL = "published ppabench grader: hidden_tb + throughput gate (functional part)"
CASES = [f"seed{i}_{mode}" for i in range(1, 6) for mode in ("bit", "baseband")]


def grade(candidate, workdir):
    return _grade(TASK, candidate, workdir)
