import json

import agentbridge.readiness_cli as readiness_module
from agentbridge.readiness_cli import (
    READINESS_EXIT_DEGRADED,
    READINESS_EXIT_NOT_READY,
    build_parser,
    main,
    readiness_exit_code,
)


def readiness_payload(status: str = "degraded") -> dict[str, object]:
    return {
        "schema_version": "agentbridge.readiness.v1",
        "status": status,
        "summary": {
            "total": 3,
            "counts": {"pass": 1, "warn": 2, "fail": 0},
        },
        "checks": [],
    }


def test_readiness_cli_parser_accepts_auth_and_exit_policy(tmp_path):
    token_file = tmp_path / "api-token"

    args = build_parser().parse_args(
        [
            "--api-url",
            "http://bridge.local",
            "--api-token-file",
            str(token_file),
            "--device-id",
            "readiness-device",
            "--device-key",
            "device-secret",
            "--timeout-seconds",
            "2.5",
            "--format",
            "summary",
            "--fail-on-warn",
        ]
    )

    assert args.api_url == "http://bridge.local"
    assert args.api_token_file == token_file
    assert args.device_id == "readiness-device"
    assert args.device_key == "device-secret"
    assert args.timeout_seconds == 2.5
    assert args.format == "summary"
    assert args.fail_on_warn is True


def test_readiness_cli_fetches_json_with_auth_headers(monkeypatch, capsys):
    calls = []

    def transport(method, url, headers, payload, timeout_seconds):
        calls.append((method, url, headers, payload, timeout_seconds))
        return readiness_payload("ready")

    monkeypatch.setattr(readiness_module, "urllib_json_transport", transport)

    result = main(
        [
            "--api-url",
            "http://bridge.local",
            "--api-token",
            "api-secret",
            "--device-id",
            "readiness-device",
            "--device-key",
            "device-secret",
        ]
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    assert calls == [
        (
            "GET",
            "http://bridge.local/api/v1/readiness",
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer api-secret",
                "X-AgentBridge-Device-ID": "readiness-device",
                "X-AgentBridge-Device-Key": "device-secret",
            },
            None,
            10.0,
        )
    ]


def test_readiness_cli_summary_output_and_fail_on_warn(monkeypatch, capsys):
    monkeypatch.setattr(
        readiness_module,
        "urllib_json_transport",
        lambda method, url, headers, payload, timeout_seconds: readiness_payload(
            "degraded"
        ),
    )

    result = main(["--format", "summary", "--fail-on-warn"])

    assert result == READINESS_EXIT_DEGRADED
    assert capsys.readouterr().out.strip() == "status=degraded pass=1 warn=2 fail=0"


def test_readiness_cli_fail_on_fail_only_for_not_ready():
    assert (
        readiness_exit_code(
            readiness_payload("degraded"),
            fail_on_warn=False,
            fail_on_fail=True,
        )
        == 0
    )
    assert (
        readiness_exit_code(
            readiness_payload("not_ready"),
            fail_on_warn=False,
            fail_on_fail=True,
        )
        == READINESS_EXIT_NOT_READY
    )
