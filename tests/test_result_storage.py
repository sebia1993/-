import json
import os
from pathlib import Path

import pytest

import result_storage


def test_write_json_atomically_fsyncs_before_replace(tmp_path, monkeypatch):
    result_path = tmp_path / "results" / "session.json"
    events = []
    original_fsync = os.fsync
    original_replace = os.replace

    def recording_fsync(file_descriptor):
        events.append("fsync")
        return original_fsync(file_descriptor)

    def recording_replace(source, destination):
        events.append("replace")
        assert "fsync" in events
        return original_replace(source, destination)

    monkeypatch.setattr(result_storage.os, "fsync", recording_fsync)
    monkeypatch.setattr(result_storage.os, "replace", recording_replace)

    result_storage.write_json_atomically(result_path, {"status": "completed"})

    assert events.index("fsync") < events.index("replace")
    assert json.loads(result_path.read_text(encoding="utf-8")) == {"status": "completed"}
    assert list(result_path.parent.glob("*.tmp")) == []


def test_write_json_atomically_cleans_temporary_file_when_replace_fails(tmp_path, monkeypatch):
    result_path = tmp_path / "session.json"

    def fail_replace(_source: Path, _destination: Path):
        raise OSError("replace failed")

    monkeypatch.setattr(result_storage.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        result_storage.write_json_atomically(result_path, {"status": "failed"})

    assert not result_path.exists()
    assert list(tmp_path.iterdir()) == []
