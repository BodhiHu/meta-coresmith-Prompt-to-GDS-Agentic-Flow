"""Transactional reviewed-spec adoption with fail-closed verification invalidation."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AdoptionResult:
    ok: bool
    adopted: list[str] = field(default_factory=list)
    reverify: list[str] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)
    error: str = ""


def adopt_reviewed_specs(project_db, edited_blocks, reviewed_specs) -> AdoptionResult:
    names = list(dict.fromkeys(edited_blocks or []))
    spec_dir = project_db.root / "arch/uarch_specs"
    installed, originals = [], {}
    try:
        spec_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".review-adoption-", dir=spec_dir) as td:
            staged, hashes, changed = {}, {}, set()
            for i, name in enumerate(names):
                if Path(name).name != name or name in (".", ".."):
                    raise ValueError(f"Invalid block name {name!r}")
                src = (reviewed_specs or {}).get(name)
                if not src or not Path(src).is_file():
                    raise FileNotFoundError(f"Reviewed spec missing for {name}")
                canonical = spec_dir / f"{name}.md"
                old = canonical.read_bytes() if canonical.exists() else None
                originals[name] = old
                staged[name] = Path(td) / f"new-{i}.md"
                shutil.copy2(src, staged[name])
                data = staged[name].read_bytes()
                hashes[name] = hashlib.sha256(data).hexdigest()
                if data != old:
                    changed.add(name)
            # Commit invalidations BEFORE replacing any file. A crash or partial
            # filesystem failure can never preserve a best result for new bytes.
            invalidated = project_db.invalidate_results_for_specs(hashes)
            try:
                for name in names:
                    if name in changed:
                        os.replace(staged[name], spec_dir / f"{name}.md")
                        installed.append(name)
            except Exception:
                # Roll back only completed replacements. The invalidations stay
                # committed, conservatively requiring verification after failure.
                for i, name in enumerate(reversed(installed)):
                    canonical = spec_dir / f"{name}.md"
                    old = originals[name]
                    if old is None:
                        canonical.unlink(missing_ok=True)
                    else:
                        backup = Path(td) / f"rollback-{i}.md"
                        backup.write_bytes(old)
                        os.replace(backup, canonical)
                raise
        return AdoptionResult(True, names, sorted(changed | set(invalidated)), hashes)
    except Exception as exc:
        return AdoptionResult(False, error=f"{type(exc).__name__}: {exc}")
