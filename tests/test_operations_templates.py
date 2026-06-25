from __future__ import annotations

import plistlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = REPO_ROOT / "docs" / "operations" / "templates"


def test_pty_host_systemd_template_has_restart_and_secret_file():
    unit = (TEMPLATES / "agentbridge-pty-host.systemd.user.service").read_text(
        encoding="utf-8"
    )

    assert "ExecStart=__AGENTBRIDGE_PTY_HOST_BIN__" in unit
    assert "EnvironmentFile=%h/.config/agentbridge/pty-host.env" in unit
    assert "Restart=on-failure" in unit
    assert "RuntimeDirectory=agentbridge" in unit
    assert "UMask=0077" in unit
    assert "AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN" not in unit


def test_pty_host_env_template_contains_required_host_settings():
    env_template = (TEMPLATES / "agentbridge-pty-host.env.example").read_text(
        encoding="utf-8"
    )

    assert "AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET=" in env_template
    assert "AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN=__GENERATE_STRONG_TOKEN__" in env_template
    assert "AGENTBRIDGE_TERMINAL_PTY_HOST_STATE_PATH=" in env_template
    assert "AGENTBRIDGE_TERMINAL_PTY_OUTPUT_LIMIT_CHARS=1000000" in env_template


def test_pty_host_launchd_template_is_valid_plist_with_keepalive():
    plist_path = TEMPLATES / "com.agentbridge.pty-host.launchd.plist"
    payload = plistlib.loads(plist_path.read_bytes())

    assert payload["Label"] == "com.agentbridge.pty-host"
    assert payload["ProgramArguments"] == ["__AGENTBRIDGE_PTY_HOST_BIN__"]
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert payload["EnvironmentVariables"]["AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN"] == (
        "__GENERATE_STRONG_TOKEN__"
    )
