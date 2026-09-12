# Copyright (c) Meta Platforms, Inc. and affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Engine-owned state store.

The canonical SQLite project state (``.coresmith/project.sqlite``, see
``project_db``): block registry, contracts, per-block lifecycle state, settings,
and the DV / PPA / coverage scoreboard tables; plus an oracle-integrity manifest
(``trust``). Every WRITE is best-effort (never fails a pipeline node); reads
are read-only and degrade to disk fallbacks in the CLI when the db is absent.
"""

from orchestrator.state_store.project_db import ProjectDB, open_project  # noqa: F401
from orchestrator.state_store.store import Scoreboard  # noqa: F401
