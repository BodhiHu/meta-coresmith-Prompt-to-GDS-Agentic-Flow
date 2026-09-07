# Task adapters used for the ppabench sweep (task-owned, NOT engine code)

Each `<task>/task_adapter.py` is what a task ships in `inputs/task_adapter.py`
(see `orchestrator/harness/task_adapter.py` for the contract). The accel tasks run the
published `grade_accel.run_functional` (hidden_tb + qspi_host + oracle) and the task.yaml
throughput gate verbatim through `_common/ppabench_accel_adapter.py`; h264 runs the published
StreamHarness + grade_h264 on a declared 4-case subset; mcu3 wraps the author smoke testbench
and says so in its LABEL. Paths are those of the e6 experiment box.
