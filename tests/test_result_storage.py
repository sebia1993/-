import json
import os
from pathlib import Path

import pytest

import result_storage


def test_write_json_atomically_fsyncs_before_replace(tmp_path, monkeypatch):
    result_path = tmp_path / "results" / "session.json"
    events = []
    original_fsync = os.fsync
    original_replace = result_storage.durable_replace

    def recording_fsync(file_descriptor):
        events.append("fsync")
        return original_fsync(file_descriptor)

    def recording_replace(source, destination):
        events.append("replace")
        assert "fsync" in events
        return original_replace(source, destination)

    monkeypatch.setattr(result_storage.os, "fsync", recording_fsync)
    monkeypatch.setattr(result_storage, "durable_replace", recording_replace)

    result_storage.write_json_atomically(result_path, {"status": "completed"})

    assert events.index("fsync") < events.index("replace")
    assert json.loads(result_path.read_text(encoding="utf-8")) == {"status": "completed"}
    assert list(result_path.parent.glob("*.tmp")) == []


def test_write_json_atomically_cleans_temporary_file_when_replace_fails(tmp_path, monkeypatch):
    result_path = tmp_path / "session.json"

    def fail_replace(_source: Path, _destination: Path):
        raise OSError("replace failed")

    monkeypatch.setattr(result_storage, "durable_replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        result_storage.write_json_atomically(result_path, {"status": "failed"})

    assert not result_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_prune_old_json_results_keeps_newest_files_only(tmp_path):
    paths = []
    for index in range(5):
        path = tmp_path / f"session-{index}.json"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, ns=(index + 1, index + 1))
        paths.append(path)
    (tmp_path / "README_RESULTS_KO.txt").write_text("keep", encoding="utf-8")

    removed = result_storage.prune_old_json_results(tmp_path, max_files=3)

    assert removed == 2
    assert sorted(path.name for path in tmp_path.glob("*.json")) == [
        "session-2.json",
        "session-3.json",
        "session-4.json",
    ]
    assert (tmp_path / "README_RESULTS_KO.txt").exists()
