# Task adapters

A task adapter is the task's own pass/fail checker, plugged into the harness. The engine
assembles a candidate, gives it an identity, runs the adapter in the adapter's own
interpreter with a timeout, and refuses an incomplete receipt. What "correct" means is
the adapter's business, so nothing about a particular benchmark, bus or chassis lives in
the engine.

The contract is documented at the top of
[`orchestrator/harness/task_adapter.py`](../../orchestrator/harness/task_adapter.py).
In short, a task ships `inputs/task_adapter.py` with:

| | |
|---|---|
| `# coresmith-python:` | interpreter to run it with, default the engine's |
| `# coresmith-timeout-s:` | wall-clock budget, default 3600 |
| `CASES` | every case name the receipt must report, declared up front |
| `TOP` | the top module the adapter expects, checked against the declared top |
| `LABEL` | what the verdict means, shown verbatim in the log and the receipt |
| `grade(candidate, workdir)` | runs the checker and returns the receipt |

`candidate` carries `top`, `sources`, `dependencies`, `defines`, `parameters`,
`candidate_sha`, `project_root` and `inputs_dir`. `workdir` is the only writable
directory the adapter gets.

`grade` returns `{"cases": {name: {"ok": bool, ...}}}` and may add `budgets`, whose
entries also carry `ok`. A case that is missing, a budget that is not a boolean, or an
exception inside `grade` is treated as an incomplete oracle and parks the run: an
adapter that cannot judge never silently passes a chip.

## Example

[`example_stream/task_adapter.py`](example_stream/task_adapter.py) is a complete,
runnable adapter with no host-specific paths. It compiles the candidate with Icarus
Verilog, drives one vector per declared case, compares against a reference computed in
Python, and reports a cycle budget. Copy it to `inputs/task_adapter.py` and replace the
reference and the driver with the task's own.

Real adapters usually do less work than this one, because they call a published grader
instead of reimplementing the check. That is the intended shape: the closer the adapter
is to the grader the task is scored by, the less the harness can flatter itself.
