"""Immutable, per-module snapshots of selected engine library source.

The published manifest is the source authority for every consumer. Engine
upgrades affect future adoptions, not already adopted source snapshots.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from orchestrator.harness.readmem_assets import Assets, bind_assets, modules, tokens


def library_sources(project_root, sources: list[str], library: str) -> list[str]:
    from orchestrator.harness.top_module import CandidateError
    root, path = Path(project_root).resolve(), Path(library).resolve()
    text = path.read_text()
    ts = tokens(path, text)
    # Splitting a compilation unit with macros/includes/global declarations can
    # change its meaning. Such a library needs standalone source files first.
    mods = modules(ts)
    covered = {t for mod in mods for t in mod.body}
    if any(t not in covered for t in ts):
        raise CandidateError(f'Engine library cannot be split into standalone modules: {path}')
    by_name = {mod.name: mod for mod in mods}
    if len(by_name) != len(mods):
        raise CandidateError(f'Ambiguous engine library module declarations: {path}')
    loader = Assets(root)
    candidate_tokens = [t for source in sources for t in loader.load(Path(source))]

    def references(body):
        # Conservative textual closure; Yosys removes inactive generate branches
        # before publication. Never use this closure as hierarchy evidence.
        return {t.text for i, t in enumerate(body) if t.text in by_name
                and not (i and body[i - 1].text == 'module')}

    pending = list(references(candidate_tokens))
    selected = set()
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        pending.extend(references(by_name[name].body) - selected)
    directory = root / '.coresmith/candidate-library'
    result = []
    for name in sorted(selected):
        mod = by_name[name]
        source = text[mod.body[0].start:mod.body[-1].end] + '\n'
        # Preserve resolution against the original engine directory before
        # moving a module into its immutable project-local snapshot.
        source = bind_assets([path], root, texts={path: source}).rewritten(path)
        digest = hashlib.sha256(source.encode()).hexdigest()
        dst = directory / f'{name}-{digest}.v'
        directory.mkdir(parents=True, exist_ok=True)
        try:
            with dst.open('x') as stream:
                stream.write(source)
        except FileExistsError:
            if dst.read_text() != source:
                raise CandidateError(f'Candidate library snapshot changed: {dst}')
        result.append(str(dst))
    return result


def retain_elaborated(sources: list[str], project_root, cells: set[str]) -> list[str]:
    directory = Path(project_root).resolve() / '.coresmith/candidate-library'
    return [p for p in sources if Path(p).parent != directory
            or Path(p).name.split('-', 1)[0] in cells]
