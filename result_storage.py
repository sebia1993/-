from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from runtime_stability import durable_replace


RESULT_JSON_MAX_FILES = 1_000


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary_path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        durable_replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def prune_old_json_results(
    results_root: Path,
    *,
    max_files: int = RESULT_JSON_MAX_FILES,
) -> int:
    keep = max(int(max_files), 1)
    candidates = []
    for path in results_root.glob("*.json"):
        try:
            if path.is_file():
                candidates.append((path.stat().st_mtime_ns, path.name, path))
        except FileNotFoundError:
            continue
    candidates.sort(reverse=True)
    removed = 0
    for _, _, path in candidates[keep:]:
        try:
            path.unlink()
            removed += 1
        except (FileNotFoundError, OSError):
            continue
    if removed:
        _fsync_directory(results_root)
    return removed


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
