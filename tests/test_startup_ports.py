from __future__ import annotations

import subprocess
from configparser import ConfigParser
from pathlib import Path

import pytest

import app as app_module
import startup_ports as ports_module
from startup_ports import (
    CURRENT_CONFIG_VERSION,
    FIREWALL_ALLOWED,
    FIREWALL_NOT_APPLICABLE,
    FIREWALL_NOT_FOUND,
    FIREWALL_UNKNOWN,
    PortChangeDeclined,
    PortResolution,
    StartupPortError,
    check_windows_firewall_port,
    config_requires_probe_enable_migration,
    find_available_port,
    migrate_config,
    persist_port_change,
    persist_probe_port_change,
    prompt_for_port_change,
    prompt_for_probe_port_change,
    resolve_probe_port,
    resolve_startup_port,
    rewrite_base_url_port,
)


def write_config(
    tmp_path: Path,
    *,
    base_url: str = "http://files.local:8000",
    config_version: int | None = CURRENT_CONFIG_VERSION,
    probe_enabled: bool = False,
) -> Path:
    path = tmp_path / "config.ini"
    version_line = [] if config_version is None else [f"CONFIG_VERSION={config_version}"]
    path.write_text(
        "\n".join(
            [
                "[app]",
                *version_line,
                "HOST=0.0.0.0",
                "PORT=8000",
                f"BASE_URL={base_url}",
                "STORAGE_ROOT=uploads",
                "DELETE_ALLOWED_IPS=127.0.0.1,::1",
                "RECENT_LIMIT=50",
                "CUSTOM_OPTION=preserved",
                "",
                "[network_probe]",
                f"ENABLED={'true' if probe_enabled else 'false'}",
                "PORT=5201",
                "",
                "[custom]",
                "VALUE=kept",
            ]
        ),
        encoding="utf-8",
    )
    return path


def read_config(path: Path) -> ConfigParser:
    parser = ConfigParser()
    parser.read(path, encoding="utf-8")
    return parser


def test_resolve_startup_port_keeps_available_configured_port():
    confirmations = []
    resolution = resolve_startup_port(
        "0.0.0.0",
        8000,
        availability_check=lambda host, port: port == 8000,
        existing_instance_check=lambda port: False,
        confirm_change=lambda old, new: confirmations.append((old, new)) or True,
    )

    assert resolution == PortResolution(8000, 8000)
    assert confirmations == []


def test_resolve_startup_port_detects_existing_instance_without_fallback():
    resolution = resolve_startup_port(
        "0.0.0.0",
        8000,
        availability_check=lambda host, port: False,
        existing_instance_check=lambda port: True,
        confirm_change=lambda old, new: pytest.fail("confirmation must not be requested"),
    )

    assert resolution.existing_instance
    assert not resolution.changed


def test_resolve_startup_port_selects_first_available_non_probe_port():
    checked = []

    def available(host, port):
        checked.append(port)
        return port == 8003

    resolution = resolve_startup_port(
        "0.0.0.0",
        8000,
        excluded_ports={8002},
        availability_check=available,
        existing_instance_check=lambda port: False,
        confirm_change=lambda old, new: (old, new) == (8000, 8003),
    )

    assert resolution == PortResolution(8000, 8003)
    assert checked == [8000, 8001, 8003]


def test_resolve_startup_port_decline_does_not_select_port():
    with pytest.raises(PortChangeDeclined):
        resolve_startup_port(
            "0.0.0.0",
            8000,
            availability_check=lambda host, port: port == 8001,
            existing_instance_check=lambda port: False,
            confirm_change=lambda old, new: False,
        )


def test_find_available_port_stops_after_99_candidates():
    checked = []
    result = find_available_port(
        "0.0.0.0",
        8000,
        availability_check=lambda host, port: checked.append(port) or False,
    )

    assert result is None
    assert checked == list(range(8001, 8100))


def test_resolve_startup_port_reports_exhausted_range():
    with pytest.raises(StartupPortError, match="사용할 수 있는 포트"):
        resolve_startup_port(
            "0.0.0.0",
            65535,
            availability_check=lambda host, port: False,
            existing_instance_check=lambda port: False,
        )


def test_prompt_for_port_change_accepts_enter_and_retries_invalid_input():
    answers = iter(["maybe", ""])
    messages = []

    assert prompt_for_port_change(
        8000,
        8001,
        input_func=lambda prompt: next(answers),
        output_func=messages.append,
    )
    assert messages == ["Y 또는 N을 입력하세요. Enter는 Y로 처리됩니다."]


def test_prompt_for_port_change_rejects_n_and_noninteractive_input():
    assert not prompt_for_port_change(8000, 8001, input_func=lambda prompt: "n")
    messages = []
    assert not prompt_for_port_change(8000, 8001, output_func=messages.append, interactive=False)
    assert "자동으로 변경하지 않습니다" in messages[0]


def test_resolve_probe_port_selects_available_port_and_excludes_web_port():
    checked = []

    resolution = resolve_probe_port(
        "0.0.0.0",
        5201,
        excluded_ports={5202},
        availability_check=lambda host, port: checked.append(port) or port == 5203,
        confirm_change=lambda old, new: (old, new) == (5201, 5203),
    )

    assert resolution == PortResolution(5201, 5203)
    assert checked == [5201, 5203]


def test_resolve_probe_port_treats_web_port_as_unavailable():
    resolution = resolve_probe_port(
        "0.0.0.0",
        5201,
        excluded_ports={5201},
        availability_check=lambda host, port: port in {5201, 5202},
        confirm_change=lambda old, new: True,
    )

    assert resolution == PortResolution(5201, 5202)


def test_resolve_probe_port_decline_keeps_web_server_decision_separate():
    with pytest.raises(PortChangeDeclined, match="TCP 측정 포트"):
        resolve_probe_port(
            "0.0.0.0",
            5201,
            availability_check=lambda host, port: port == 5202,
            confirm_change=lambda old, new: False,
        )


def test_prompt_for_probe_port_change_accepts_enter_and_handles_noninteractive():
    assert prompt_for_probe_port_change(5201, 5202, input_func=lambda prompt: "")
    messages = []
    assert not prompt_for_probe_port_change(
        5201,
        5202,
        output_func=messages.append,
        interactive=False,
    )
    assert "TCP 측정 포트" in messages[0]


@pytest.mark.parametrize(
    ("value", "old_port", "new_port", "expected", "has_warning"),
    [
        ("", 8000, 8001, "", False),
        ("http://files.local:8000", 8000, 8001, "http://files.local:8001", False),
        ("http://files.local:9000", 8000, 8001, "http://files.local:9000", True),
        ("http://files.local", 80, 8001, "http://files.local:8001", False),
        ("not-a-url", 8000, 8001, "not-a-url", True),
    ],
)
def test_rewrite_base_url_port(value, old_port, new_port, expected, has_warning):
    updated, warning = rewrite_base_url_port(value, old_port, new_port)

    assert updated == expected
    assert bool(warning) is has_warning


def test_persist_port_change_updates_port_base_url_and_preserves_options(tmp_path):
    path = write_config(tmp_path)

    result = persist_port_change(path, 8000, 8001)

    parser = read_config(path)
    assert parser.getint("app", "PORT") == 8001
    assert parser.get("app", "BASE_URL") == "http://files.local:8001"
    assert parser.get("app", "CUSTOM_OPTION") == "preserved"
    assert parser.getint("network_probe", "PORT") == 5201
    assert parser.get("custom", "VALUE") == "kept"
    assert result.base_url_changed
    assert not result.warning


def test_persist_port_change_creates_complete_missing_config(tmp_path):
    path = tmp_path / "config.ini"

    persist_port_change(path, 8000, 8001)

    parser = read_config(path)
    assert parser.getint("app", "PORT") == 8001
    assert parser.getint("app", "CONFIG_VERSION") == CURRENT_CONFIG_VERSION
    assert parser.get("app", "STORAGE_ROOT") == "uploads"
    assert parser.getboolean("network_probe", "ENABLED") is True
    assert parser.getint("network_probe", "PORT") == 5201


def test_legacy_config_migration_enables_probe_once(tmp_path):
    path = write_config(tmp_path, config_version=None, probe_enabled=False)

    assert config_requires_probe_enable_migration(path)
    result = migrate_config(path)

    parser = read_config(path)
    assert result.previous_version == 0
    assert result.current_version == CURRENT_CONFIG_VERSION
    assert result.probe_enabled_changed
    assert parser.getint("app", "CONFIG_VERSION") == CURRENT_CONFIG_VERSION
    assert parser.getboolean("network_probe", "ENABLED") is True
    assert parser.get("app", "CUSTOM_OPTION") == "preserved"
    assert parser.get("custom", "VALUE") == "kept"


def test_current_config_respects_user_probe_disable(tmp_path):
    path = write_config(tmp_path, probe_enabled=False)
    original = path.read_bytes()

    assert not config_requires_probe_enable_migration(path)
    result = migrate_config(path)

    assert not result.probe_enabled_changed
    assert read_config(path).getboolean("network_probe", "ENABLED") is False
    assert path.read_bytes() == original


def test_persist_probe_port_change_preserves_other_settings(tmp_path):
    path = write_config(tmp_path, probe_enabled=True)

    persist_probe_port_change(path, 5202)

    parser = read_config(path)
    assert parser.getint("network_probe", "PORT") == 5202
    assert parser.getboolean("network_probe", "ENABLED") is True
    assert parser.getint("app", "PORT") == 8000
    assert parser.get("custom", "VALUE") == "kept"


def test_persist_port_change_replace_failure_keeps_original_file(tmp_path, monkeypatch):
    path = write_config(tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr(ports_module.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("busy")))

    with pytest.raises(OSError, match="busy"):
        persist_port_change(path, 8000, 8001)

    assert path.read_bytes() == original
    assert list(tmp_path.glob(".config.ini.*.tmp")) == []


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(0, FIREWALL_ALLOWED), (1, FIREWALL_NOT_FOUND), (2, FIREWALL_UNKNOWN)],
)
def test_windows_firewall_status_uses_process_exit_code(returncode, expected):
    def fake_run(command, **kwargs):
        assert command[0] == "powershell.exe"
        assert "8001" in command[-1]
        return subprocess.CompletedProcess(command, returncode)

    assert (
        check_windows_firewall_port(8001, platform="win32", run_command=fake_run)
        == expected
    )


def test_windows_firewall_status_skips_non_windows():
    assert check_windows_firewall_port(8001, platform="darwin") == FIREWALL_NOT_APPLICABLE


class FakeWebServer:
    def __init__(self, events):
        self.events = events

    def serve_forever(self):
        self.events.append("serve")

    def server_close(self):
        self.events.append("close")


def test_main_binds_selected_port_before_persisting_config(tmp_path, monkeypatch):
    path = write_config(tmp_path, base_url="http://files.local:8000")
    events = []
    real_persist = ports_module.persist_port_change

    monkeypatch.setattr(app_module, "resolve_startup_port", lambda *args, **kwargs: PortResolution(8000, 8001))

    def fake_make_server(host, port, flask_app, threaded):
        assert read_config(path).getint("app", "PORT") == 8000
        assert port == 8001
        events.append("bind")
        return FakeWebServer(events)

    def persist(config_path, old_port, new_port):
        events.append("persist")
        return real_persist(config_path, old_port, new_port)

    monkeypatch.setattr(app_module, "make_server", fake_make_server)
    monkeypatch.setattr(app_module, "persist_port_change", persist)
    monkeypatch.setattr(app_module, "print_firewall_status", lambda port: events.append("firewall"))
    monkeypatch.setattr(app_module, "print_server_addresses", lambda config: events.append("addresses"))

    assert app_module.main(["--config", str(path)]) == 0
    assert read_config(path).getint("app", "PORT") == 8001
    assert events == ["bind", "persist", "firewall", "addresses", "serve", "close"]


def test_main_bind_failure_does_not_change_config(tmp_path, monkeypatch):
    path = write_config(tmp_path)
    original = path.read_bytes()
    monkeypatch.setattr(app_module, "resolve_startup_port", lambda *args, **kwargs: PortResolution(8000, 8001))
    monkeypatch.setattr(app_module, "make_server", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("taken")))

    assert app_module.main(["--config", str(path)]) == 2
    assert path.read_bytes() == original


def test_main_existing_instance_exits_without_binding(tmp_path, monkeypatch):
    path = write_config(tmp_path)
    monkeypatch.setattr(
        app_module,
        "resolve_startup_port",
        lambda *args, **kwargs: PortResolution(8000, 8000, existing_instance=True),
    )
    monkeypatch.setattr(app_module, "print_server_addresses", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_module, "make_server", lambda *args, **kwargs: pytest.fail("must not bind"))

    assert app_module.main(["--config", str(path)]) == 0


def test_main_starts_probe_on_approved_fallback_then_persists_port(tmp_path, monkeypatch):
    path = write_config(tmp_path, probe_enabled=True)
    events = []

    class FakeProbeService:
        def __init__(self, *, config, measurement_gate, normalize_ip):
            self.config = config
            self.start_error = ""

        def start(self):
            events.append(("probe-start", self.config.port))
            return True

        def stop(self):
            events.append("probe-stop")

    monkeypatch.setattr(
        app_module,
        "resolve_startup_port",
        lambda *args, **kwargs: PortResolution(8000, 8000),
    )
    monkeypatch.setattr(
        app_module,
        "resolve_probe_port",
        lambda *args, **kwargs: PortResolution(5201, 5202),
    )
    monkeypatch.setattr(app_module, "ProbeService", FakeProbeService)
    monkeypatch.setattr(app_module, "make_server", lambda *args, **kwargs: FakeWebServer(events))
    monkeypatch.setattr(app_module, "print_firewall_status", lambda port: events.append(("firewall", port)))
    monkeypatch.setattr(app_module, "print_server_addresses", lambda config: events.append("addresses"))

    assert app_module.main(["--config", str(path)]) == 0

    assert read_config(path).getint("network_probe", "PORT") == 5202
    assert events == [
        ("probe-start", 5202),
        ("firewall", 5202),
        "addresses",
        "serve",
        "probe-stop",
        "close",
    ]


def test_main_keeps_web_server_running_when_probe_port_change_is_declined(tmp_path, monkeypatch):
    path = write_config(tmp_path, probe_enabled=True)
    events = []

    class FakeProbeService:
        def __init__(self, *, config, measurement_gate, normalize_ip):
            self.config = config
            self.start_error = ""

        def start(self):
            pytest.fail("probe must not start after port change is declined")

        def stop(self):
            events.append("probe-stop")

    monkeypatch.setattr(
        app_module,
        "resolve_startup_port",
        lambda *args, **kwargs: PortResolution(8000, 8000),
    )
    monkeypatch.setattr(
        app_module,
        "resolve_probe_port",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            PortChangeDeclined("사용자가 TCP 측정 포트 변경을 취소했습니다.")
        ),
    )
    monkeypatch.setattr(app_module, "ProbeService", FakeProbeService)
    monkeypatch.setattr(app_module, "make_server", lambda *args, **kwargs: FakeWebServer(events))
    monkeypatch.setattr(app_module, "print_server_addresses", lambda config: events.append("addresses"))

    assert app_module.main(["--config", str(path)]) == 0

    assert read_config(path).getint("network_probe", "PORT") == 5201
    assert events == ["addresses", "serve", "probe-stop", "close"]


def test_main_keeps_migrated_probe_enabled_when_config_write_fails(tmp_path, monkeypatch):
    path = write_config(tmp_path, config_version=None, probe_enabled=False)
    events = []

    class FakeProbeService:
        def __init__(self, *, config, measurement_gate, normalize_ip):
            assert config.enabled is True
            self.config = config
            self.start_error = ""

        def start(self):
            events.append("probe-start")
            return True

        def stop(self):
            events.append("probe-stop")

    monkeypatch.setattr(app_module, "migrate_config", lambda path: (_ for _ in ()).throw(OSError("read-only")))
    monkeypatch.setattr(
        app_module,
        "resolve_startup_port",
        lambda *args, **kwargs: PortResolution(8000, 8000),
    )
    monkeypatch.setattr(
        app_module,
        "resolve_probe_port",
        lambda *args, **kwargs: PortResolution(5201, 5201),
    )
    monkeypatch.setattr(app_module, "ProbeService", FakeProbeService)
    monkeypatch.setattr(app_module, "make_server", lambda *args, **kwargs: FakeWebServer(events))
    monkeypatch.setattr(app_module, "print_firewall_status", lambda port: None)
    monkeypatch.setattr(app_module, "print_server_addresses", lambda config: None)

    assert app_module.main(["--config", str(path)]) == 0

    parser = read_config(path)
    assert not parser.has_option("app", "CONFIG_VERSION")
    assert parser.getboolean("network_probe", "ENABLED") is False
    assert events == ["probe-start", "serve", "probe-stop", "close"]


def test_main_does_not_persist_fallback_probe_port_when_bind_fails(tmp_path, monkeypatch):
    path = write_config(tmp_path, probe_enabled=True)
    events = []

    class FakeProbeService:
        def __init__(self, *, config, measurement_gate, normalize_ip):
            self.config = config
            self.start_error = "bind failed"

        def start(self):
            events.append("probe-start-failed")
            return False

        def stop(self):
            events.append("probe-stop")

    monkeypatch.setattr(
        app_module,
        "resolve_startup_port",
        lambda *args, **kwargs: PortResolution(8000, 8000),
    )
    monkeypatch.setattr(
        app_module,
        "resolve_probe_port",
        lambda *args, **kwargs: PortResolution(5201, 5202),
    )
    monkeypatch.setattr(app_module, "ProbeService", FakeProbeService)
    monkeypatch.setattr(app_module, "make_server", lambda *args, **kwargs: FakeWebServer(events))
    monkeypatch.setattr(app_module, "print_server_addresses", lambda config: None)

    assert app_module.main(["--config", str(path)]) == 0

    assert read_config(path).getint("network_probe", "PORT") == 5201
    assert events == ["probe-start-failed", "serve", "probe-stop", "close"]
