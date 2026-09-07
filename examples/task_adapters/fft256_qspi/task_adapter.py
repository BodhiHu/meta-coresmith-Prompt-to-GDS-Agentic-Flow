# coresmith-python: /home/ubuntu/ppabench-venv/bin/python
# coresmith-timeout-s: 5400
"""fft256_qspi task adapter: the PUBLISHED ppabench grader's functional part, verbatim.

hidden_tb.py (5 derived seeds, tolerance 24 LSB / SNR 45 dB) + qspi_host.py +
oracle.py under system Icarus, then the task.yaml throughput gate (cycles per
256-point transform, cap 7168; golden 3584).
"""
import sys

sys.path.insert(0, "/home/ubuntu/task-seeds/_common")
from ppabench_accel_adapter import grade as _grade  # noqa: E402

TASK = "fft256_qspi"
TOP = "user_project_wrapper"
LABEL = "published ppabench grader: hidden_tb + throughput gate (functional part)"
CASES = [f"seed{i}" for i in range(1, 6)]


def grade(candidate, workdir):
    return _grade(TASK, candidate, workdir)
