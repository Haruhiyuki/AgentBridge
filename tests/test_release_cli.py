from pathlib import Path

from agentbridge.release_cli import (
    RELEASE_EXIT_FAIL,
    RELEASE_EXIT_WARN,
    build_parser,
    main,
    release_action_text,
    release_preflight_exit_code,
    release_preflight_report,
    release_version_check,
)

RELEASE_ENV_NAMES = (
    "AGENTBRIDGE_DATABASE_URL",
    "AGENTBRIDGE_API_TOKEN",
    "AGENTBRIDGE_API_TOKEN_FILE",
    "AGENTBRIDGE_ADMIN_TOKEN",
    "AGENTBRIDGE_ADMIN_TOKEN_FILE",
    "AGENTBRIDGE_WS_TOKEN",
    "AGENTBRIDGE_WS_TOKEN_FILE",
    "AGENTBRIDGE_DEVICE_KEYS",
    "AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS",
    "AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS_FILE",
    "AGENTBRIDGE_TERMINAL_BACKEND",
    "AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET",
    "AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN",
    "AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE",
    "AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE",
    "AGENTBRIDGE_ACCEPTANCE_BUNDLE_FILE",
    "AGENTBRIDGE_ACCEPTANCE_ARTIFACT_ROOT",
)


def configured_rc_env(tmp_path) -> dict[str, str]:
    evidence_file = tmp_path / "acceptance-evidence.json"
    bundle_file = tmp_path / "acceptance-bundle.zip"
    artifact_root = tmp_path / "acceptance-artifacts"
    token_file = tmp_path / "pty-host.token"
    evidence_file.write_text("{}", encoding="utf-8")
    bundle_file.write_bytes(b"PK")
    artifact_root.mkdir()
    token_file.write_text("secret", encoding="utf-8")
    return {
        "AGENTBRIDGE_DATABASE_URL": "sqlite:///agentbridge.db",
        "AGENTBRIDGE_API_TOKEN": "api-secret",
        "AGENTBRIDGE_ADMIN_TOKEN": "admin-secret",
        "AGENTBRIDGE_WS_TOKEN": "ws-secret",
        "AGENTBRIDGE_TERMINAL_BACKEND": "pty_host",
        "AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET": str(tmp_path / "pty-host.sock"),
        "AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE": str(token_file),
        "AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE": str(evidence_file),
        "AGENTBRIDGE_ACCEPTANCE_BUNDLE_FILE": str(bundle_file),
        "AGENTBRIDGE_ACCEPTANCE_ARTIFACT_ROOT": str(artifact_root),
    }


def test_release_preflight_report_passes_for_configured_rc(tmp_path):
    report = release_preflight_report(
        Path.cwd(),
        profile="rc",
        env=configured_rc_env(tmp_path),
    )
    checks = {check["id"]: check for check in report["checks"]}

    assert report["status"] == "ready"
    assert report["summary"]["counts"]["fail"] == 0
    assert checks["package.version"]["status"] == "pass"
    assert checks["package.console_scripts"]["status"] == "pass"
    assert checks["release.required_files"]["status"] == "pass"
    assert checks["config.terminal_backend"]["status"] == "pass"
    assert checks["acceptance.release_evidence"]["status"] == "pass"


def test_release_preflight_rc_fails_missing_product_configuration():
    report = release_preflight_report(Path.cwd(), profile="rc", env={})
    checks = {check["id"]: check for check in report["checks"]}

    assert report["status"] == "not_ready"
    assert release_preflight_exit_code(report, fail_on_warn=False) == RELEASE_EXIT_FAIL
    assert checks["config.database"]["status"] == "fail"
    assert checks["config.http_auth"]["status"] == "fail"
    assert checks["config.admin_auth"]["status"] == "fail"
    assert checks["config.websocket_auth"]["status"] == "fail"
    assert checks["config.terminal_backend"]["status"] == "fail"
    assert checks["acceptance.release_evidence"]["status"] == "fail"


def test_release_preflight_local_warns_missing_product_configuration():
    report = release_preflight_report(Path.cwd(), profile="local", env={})

    assert report["status"] == "degraded"
    assert release_preflight_exit_code(report, fail_on_warn=False) == 0
    assert release_preflight_exit_code(report, fail_on_warn=True) == RELEASE_EXIT_WARN
    assert "warn configuration/config.database" in release_action_text(report)


def test_release_version_check_fails_for_mismatched_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "9.9.9"\n',
        encoding="utf-8",
    )

    check = release_version_check(tmp_path)

    assert check["status"] == "fail"
    assert check["evidence"]["pyproject_version"] == "9.9.9"


def test_release_cli_parser_accepts_profile_output_and_exit_policy(tmp_path):
    args = build_parser().parse_args(
        [
            "--project-root",
            str(tmp_path),
            "--profile",
            "local",
            "--format",
            "summary",
            "--fail-on-warn",
        ]
    )

    assert args.project_root == tmp_path
    assert args.profile == "local"
    assert args.format == "summary"
    assert args.fail_on_warn is True


def test_release_cli_summary_output_for_configured_rc(monkeypatch, tmp_path, capsys):
    for name in RELEASE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in configured_rc_env(tmp_path).items():
        monkeypatch.setenv(name, value)

    result = main(["--project-root", str(Path.cwd()), "--profile", "rc", "--format", "summary"])

    assert result == 0
    assert capsys.readouterr().out.strip() == "profile=rc status=ready pass=9 warn=0 fail=0"
