import csv
import io
from pathlib import Path
from zipfile import ZipFile

import pytest

from app import (
    build_download_url,
    create_app,
    is_delete_allowed,
    is_loopback_url,
    load_config,
    read_upload_log,
    resolve_storage_path,
    run_smoke_check,
)
from tools.verify_release_zip import REQUIRED_FILES, verify_zip


def write_config(tmp_path: Path, *, base_url: str = "http://files.local:8000") -> Path:
    config_path = tmp_path / "config.ini"
    config_path.write_text(
        "\n".join(
            [
                "[app]",
                "HOST=0.0.0.0",
                "PORT=8000",
                f"BASE_URL={base_url}",
                "STORAGE_ROOT=uploads",
                "DELETE_ALLOWED_IPS=127.0.0.1,::1,10.10.10.5",
                "RECENT_LIMIT=50",
            ]
        ),
        encoding="utf-8",
    )
    return config_path


@pytest.fixture()
def app_client(tmp_path):
    config_path = write_config(tmp_path)
    app = create_app(config_path)
    app.config.update(TESTING=True)
    return app.test_client(), load_config(config_path), tmp_path


def post_file(client, filename="장애로그.txt", content=b"hello", **fields):
    data = {
        "file": (io.BytesIO(content), filename),
        "storage_subdir": fields.pop("storage_subdir", ""),
        "memo": fields.pop("memo", ""),
    }
    data.update(fields)
    return client.post("/upload", data=data, content_type="multipart/form-data")


def test_base_url_download_link(tmp_path):
    config = load_config(write_config(tmp_path, base_url="http://10.10.10.25:8000"))
    assert build_download_url("abc123", config) == "http://10.10.10.25:8000/download/abc123"


def test_auto_ip_download_link(tmp_path):
    config = load_config(write_config(tmp_path, base_url=""))
    assert build_download_url("abc123", config, ip_address="10.10.10.25") == (
        "http://10.10.10.25:8000/download/abc123"
    )


def test_loopback_url_warning_detection():
    assert is_loopback_url("http://localhost:8000/download/a")
    assert is_loopback_url("http://127.0.0.1:8000/download/a")
    assert not is_loopback_url("http://10.10.10.25:8000/download/a")


def test_storage_path_rejects_outside_root(tmp_path):
    config = load_config(write_config(tmp_path))
    with pytest.raises(ValueError):
        resolve_storage_path("../outside", config)
    with pytest.raises(ValueError):
        resolve_storage_path("C:\\temp", config)


def test_upload_saves_file_and_csv(app_client):
    client, config, _ = app_client
    response = post_file(client, memo="장애 로그", storage_subdir="case-001")

    assert response.status_code == 200
    rows = read_upload_log(config)
    assert len(rows) == 1
    row = rows[0]
    assert row["original_filename"] == "장애로그.txt"
    assert row["storage_subdir"] == "case-001"
    assert row["memo"] == "장애 로그"
    assert Path(row["storage_path"]).read_bytes() == b"hello"


def test_memo_is_optional(app_client):
    client, config, _ = app_client
    response = post_file(client, filename="memo-optional.txt")

    assert response.status_code == 200
    assert read_upload_log(config)[0]["memo"] == ""


def test_duplicate_requires_confirmation_then_adds_id(app_client):
    client, config, _ = app_client
    assert post_file(client, filename="same.txt", content=b"one").status_code == 200

    conflict = post_file(client, filename="same.txt", content=b"two")
    assert conflict.status_code == 409
    assert "이미 존재".encode("utf-8") in conflict.data
    assert len(read_upload_log(config)) == 1

    confirmed = post_file(
        client,
        filename="same.txt",
        content=b"two",
        confirm_duplicate="1",
    )
    assert confirmed.status_code == 200
    rows = read_upload_log(config)
    assert len(rows) == 2
    assert rows[0]["stored_filename"].endswith("_same.txt")
    assert rows[0]["stored_filename"] != "same.txt"


def test_download_by_id(app_client):
    client, config, _ = app_client
    post_file(client, filename="download.txt", content=b"download me")
    upload_id = read_upload_log(config)[0]["upload_id"]

    response = client.get(f"/download/{upload_id}")

    assert response.status_code == 200
    assert response.data == b"download me"


def test_delete_requires_allowed_ip(app_client):
    client, config, _ = app_client
    post_file(client, filename="delete.txt", content=b"delete me")
    row = read_upload_log(config)[0]

    denied = client.post(
        f"/delete/{row['upload_id']}",
        environ_overrides={"REMOTE_ADDR": "10.10.10.6"},
    )
    assert denied.status_code == 403
    assert Path(row["storage_path"]).exists()
    assert len(read_upload_log(config)) == 1

    allowed = client.post(
        f"/delete/{row['upload_id']}",
        environ_overrides={"REMOTE_ADDR": "10.10.10.5"},
    )
    assert allowed.status_code == 302
    assert not Path(row["storage_path"]).exists()
    assert read_upload_log(config) == []


def test_delete_button_only_for_allowed_ip(app_client):
    client, config, _ = app_client
    post_file(client, filename="button.txt")

    allowed = client.get("/", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    denied = client.get("/", environ_overrides={"REMOTE_ADDR": "10.10.10.6"})

    assert f"/delete/{read_upload_log(config)[0]['upload_id']}".encode() in allowed.data
    assert b"/delete/" not in denied.data


def test_delete_allowed_ip_normalization(tmp_path):
    config = load_config(write_config(tmp_path))
    assert is_delete_allowed("::ffff:127.0.0.1", config)
    assert not is_delete_allowed("10.10.10.6", config)


def test_csv_header_is_utf8_sig(app_client):
    _, config, _ = app_client
    with config.log_path.open("r", encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle))
    assert header[0] == "upload_id"


def test_smoke_check_returns_success(tmp_path):
    config_path = write_config(tmp_path)
    assert run_smoke_check(config_path) == 0


def test_release_zip_verifier_accepts_expected_structure(tmp_path):
    zip_path = tmp_path / "internal-upload_v0.1.0_windows.zip"
    csv_header = (
        "upload_id,uploaded_at,original_filename,stored_filename,storage_subdir,"
        "storage_path,memo,download_url\n"
    )
    with ZipFile(zip_path, "w") as archive:
        for name in sorted(REQUIRED_FILES - {"README_START_HERE_KO.txt", "data/upload_log.csv"}):
            archive.writestr(name, "sample")
        archive.writestr("README_START_HERE_KO.txt", "사내 업로드 v0.1.0 Windows 실행 ZIP")
        archive.writestr("data/upload_log.csv", csv_header)

    assert verify_zip(str(zip_path), "v0.1.0") == []


def test_release_zip_verifier_rejects_dev_artifacts(tmp_path):
    zip_path = tmp_path / "bad.zip"
    with ZipFile(zip_path, "w") as archive:
        for name in REQUIRED_FILES:
            archive.writestr(name, "v0.1.0")
        archive.writestr(".venv/Lib/site-packages/example.txt", "bad")

    errors = verify_zip(str(zip_path), "v0.1.0")
    assert any(".venv" in error for error in errors)
