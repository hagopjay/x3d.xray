"""Fresh local build directories with reversible, same-filesystem promotion.

This is a sequential two-directory transaction, not an atomic pair or a lock.
Do not serve or mutate the destinations concurrently. Existing destinations
become named sibling backups; this helper never deletes their contents. Failed
staging directories remain available for diagnosis.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
import tempfile
from typing import Callable
import uuid

from .page import write_publish_map


def _targets(data_dir: str, out_dir: str) -> tuple[Path, Path]:
    paths = []
    for value in (data_dir, out_dir):
        raw = Path(value).expanduser().absolute()
        if raw.is_symlink():
            raise ValueError("staging: destination must not be a symlink")
        path = raw.resolve()
        if path.parent == path:
            raise ValueError("staging: filesystem roots cannot be build destinations")
        if path.exists() and not path.is_dir():
            raise ValueError("staging: destination must be a directory")
        if (path / ".git").exists():
            raise ValueError("staging: repository roots cannot be build destinations")
        paths.append(path)
    data, out = paths
    if data.is_relative_to(out) or out.is_relative_to(data):
        raise ValueError("staging: data and output destinations must be distinct and non-nested")
    return data, out


def _backup_name(target: Path) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    while True:
        candidate = target.with_name(f"{target.name}.backup-{stamp}-{uuid.uuid4().hex[:8]}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate


def _retained(stages: tuple[Path, Path]) -> None:
    print(f"staging: diagnostic data retained at {stages[0]}")
    print(f"staging: diagnostic output retained at {stages[1]}")


def run_staged(data_dir: str, out_dir: str, runner: Callable[[str, str], int]) -> int:
    """Run with fresh absolute paths, then promote successful results.

    Nonzero runner results leave existing destinations untouched. Runner
    exceptions retain stages and propagate. Promotion failures attempt rollback
    and return 1; any rollback failure is stated explicitly. Validation errors
    raise ValueError before stages are created.
    """
    targets = _targets(data_dir, out_dir)
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for target in targets:
            created.append(Path(tempfile.mkdtemp(prefix=f".{target.name}.stage-", dir=str(target.parent))))
    except OSError:
        for stage in created:
            print(f"staging: incomplete diagnostic directory retained at {stage}")
        raise
    stages = (created[0], created[1])
    try:
        rc = runner(str(stages[0]), str(stages[1]))
        if not isinstance(rc, int):
            raise TypeError("staging: runner must return an integer exit status")
    except BaseException:
        _retained(stages)
        raise
    if rc:
        _retained(stages)
        return rc

    backups: list[tuple[Path, Path]] = []
    promoted: list[tuple[Path, Path]] = []
    try:
        # Re-check direct destinations after the potentially lengthy build.
        # This is error detection, not protection against concurrent mutation.
        if _targets(data_dir, out_dir) != targets:
            raise ValueError("staging: destinations changed during the build")
        for target in targets:
            if target.exists():
                backup = _backup_name(target)
                target.rename(backup)
                backups.append((target, backup))
        for stage, target in zip(stages, targets):
            stage.rename(target)
            promoted.append((stage, target))
        # Publication URLs store absolute source locations, so a stage's map
        # cannot be reused verbatim after the directories have moved.
        write_publish_map(str(targets[1]))
    except Exception as error:
        failures = []
        for stage, target in reversed(promoted):
            try:
                target.rename(stage)
            except OSError:
                failures.append(f"new output remains at {target}")
        for target, backup in reversed(backups):
            try:
                if target.exists() or target.is_symlink():
                    raise OSError("destination is occupied")
                backup.rename(target)
            except OSError:
                failures.append(f"original destination remains backed up at {backup}")
        print(f"staging: promotion failed ({type(error).__name__}); rollback {'incomplete' if failures else 'completed'}")
        for failure in failures:
            print("staging: " + failure)
        _retained(stages)
        return 1
    for target, backup in backups:
        print(f"staging: previous {target.name} preserved at {backup}")
    print(f"staging: promoted fresh data to {targets[0]} and output to {targets[1]}")
    return 0
