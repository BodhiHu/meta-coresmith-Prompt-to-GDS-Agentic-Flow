# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- **Breaking:** the architecture phase no longer runs the uArch exploration,
  Memory Map, Clock Tree, Register Spec or Complexity Review stages, and their
  artifacts are no longer produced. Project state is one SQLite database with the
  JSON files beside it as regenerated views.
- **Breaking:** acceptance is the task's own checker. A task ships
  `inputs/task_adapter.py` and its verdict outranks the engine's internal
  requirements; the built-in stream harness is the fallback and now requires an
  explicit `AXIS_MAPPING` instead of inferring packing and geometry.
- **Breaking:** the chip top and the chassis are declared in `inputs/task.yaml`
  rather than guessed from the RTL, and integration adopts one validated
  candidate manifest that every consumer reads.
- The oracle integrity baseline moved outside the project, by default under
  `~/.coresmith/trust/`; task adapters run inside a bubblewrap boundary.
- See [docs/migration-arm-e.md](docs/migration-arm-e.md) for migrating an
  existing project.

### Added
- Initial open-source release of coresmith
- LangGraph-based ASIC pipeline orchestration (architecture, RTL, verification, synthesis, backend)
- MCP server for interactive use with Claude Code
- Headless CI mode with auto-retry and auto-skip
- Sky130 PDK support via Volare
- OpenTelemetry tracing
