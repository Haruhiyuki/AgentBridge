import json

import agentbridge.readiness_cli as readiness_module
from agentbridge.readiness_cli import (
    READINESS_EXIT_DEGRADED,
    READINESS_EXIT_NOT_READY,
    build_parser,
    main,
    readiness_action_text,
    readiness_exit_code,
)


def readiness_payload(
    status: str = "degraded",
    *,
    checks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    counts = {"pass": 1, "warn": 2, "fail": 0}
    total = 3
    if checks is not None:
        counts = {"pass": 0, "warn": 0, "fail": 0}
        for check in checks:
            check_status = str(check.get("status") or "fail")
            counts[check_status] = counts.get(check_status, 0) + 1
        total = len(checks)
    return {
        "schema_version": "agentbridge.readiness.v1",
        "status": status,
        "summary": {
            "total": total,
            "counts": counts,
        },
        "checks": checks or [],
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
            "actions",
            "--fail-on-warn",
        ]
    )

    assert args.api_url == "http://bridge.local"
    assert args.api_token_file == token_file
    assert args.device_id == "readiness-device"
    assert args.device_key == "device-secret"
    assert args.timeout_seconds == 2.5
    assert args.format == "actions"
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


def test_readiness_cli_actions_output_prioritizes_failures(monkeypatch, capsys):
    monkeypatch.setattr(
        readiness_module,
        "urllib_json_transport",
        lambda method, url, headers, payload, timeout_seconds: readiness_payload(
            "not_ready",
            checks=[
                {
                    "id": "terminal.event_outbox",
                    "category": "terminal",
                    "status": "warn",
                    "summary": "Terminal lifecycle event outbox is healthy.",
                    "next_step": "Flush pending lifecycle events.",
                },
                {
                    "id": "bot.onebot_v11_capability",
                    "category": "bot_gateway",
                    "status": "fail",
                    "summary": "OneBot V11 fallback capability contract is available.",
                    "next_step": "Configure OneBot transport.",
                },
                {
                    "id": "control_plane.health",
                    "category": "control_plane",
                    "status": "pass",
                    "summary": "Control Plane API is responding.",
                },
            ],
        ),
    )

    result = main(["--format", "actions", "--fail-on-fail"])

    assert result == READINESS_EXIT_NOT_READY
    assert capsys.readouterr().out.splitlines() == [
        "status=not_ready pass=1 warn=1 fail=1",
        (
            "fail bot_gateway/bot.onebot_v11_capability: "
            "OneBot V11 fallback capability contract is available."
        ),
        "  next: Configure OneBot transport.",
        "warn terminal/terminal.event_outbox: Terminal lifecycle event outbox is healthy.",
        "  next: Flush pending lifecycle events.",
    ]


def test_readiness_cli_actions_output_reports_all_passed():
    assert (
        readiness_action_text(
            readiness_payload(
                "ready",
                checks=[
                    {
                        "id": "control_plane.health",
                        "category": "control_plane",
                        "status": "pass",
                        "summary": "Control Plane API is responding.",
                    }
                ],
            )
        )
        == "status=ready pass=1 warn=0 fail=0\nall readiness checks passed"
    )


def test_readiness_cli_actions_output_includes_acceptance_bundle_summary():
    assert readiness_action_text(
        readiness_payload(
            "degraded",
            checks=[
                {
                    "id": "acceptance.evidence_bundle",
                    "category": "acceptance",
                    "status": "warn",
                    "summary": "MVP acceptance evidence bundle is portable.",
                    "next_step": "Complete all MVP acceptance sections.",
                    "evidence": {
                        "artifact_count": 1,
                        "summary": {
                            "ready": False,
                            "counts": {
                                "passed": 1,
                                "failed": 0,
                                "blocked": 0,
                                "not_run": 7,
                            },
                            "artifact_error_count": 0,
                            "checklist_incomplete_count": 24,
                            "checklist_error_count": 0,
                        },
                    },
                }
            ],
        )
    ).splitlines() == [
        "status=degraded pass=0 warn=1 fail=0",
        (
            "warn acceptance/acceptance.evidence_bundle: "
            "MVP acceptance evidence bundle is portable."
        ),
        (
            "  evidence: bundle_ready=false artifacts=1 passed=1 failed=0 "
            "blocked=0 not_run=7 artifact_errors=0 checklist_incomplete=24 "
            "checklist_errors=0"
        ),
        "  next: Complete all MVP acceptance sections.",
    ]


def test_readiness_cli_actions_output_includes_acceptance_manifest_and_section_counts():
    assert readiness_action_text(
        readiness_payload(
            "degraded",
            checks=[
                {
                    "id": "acceptance.evidence_manifest",
                    "category": "acceptance",
                    "status": "warn",
                    "summary": "MVP manual acceptance evidence manifest is available.",
                    "next_step": "Set signed-off evidence manifest.",
                    "evidence": {
                        "section_count": 1,
                        "summary": {
                            "ready": False,
                            "counts": {
                                "passed": 1,
                                "failed": 0,
                                "blocked": 0,
                                "not_run": 0,
                                "missing": 7,
                                "invalid": 0,
                            },
                            "artifact_error_count": 0,
                            "checklist_incomplete_count": 3,
                            "checklist_error_count": 0,
                        },
                    },
                },
                {
                    "id": "acceptance.native_session",
                    "category": "acceptance",
                    "status": "warn",
                    "summary": "Design-document section 34.1 is signed off.",
                    "next_step": "Mark every checklist item passed.",
                    "evidence": {
                        "section": "34.1",
                        "status": "passed",
                        "artifact_count": 1,
                        "artifact_error_count": 0,
                        "checklist_total": 3,
                        "checklist_passed_count": 1,
                        "checklist_error_count": 0,
                    },
                },
            ],
        )
    ).splitlines() == [
        "status=degraded pass=0 warn=2 fail=0",
        (
            "warn acceptance/acceptance.evidence_manifest: "
            "MVP manual acceptance evidence manifest is available."
        ),
        (
            "  evidence: manifest_ready=false sections=1 passed=1 failed=0 "
            "blocked=0 not_run=0 missing=7 invalid=0 artifact_errors=0 "
            "checklist_incomplete=3 checklist_errors=0"
        ),
        "  next: Set signed-off evidence manifest.",
        (
            "warn acceptance/acceptance.native_session: "
            "Design-document section 34.1 is signed off."
        ),
        (
            "  evidence: section=34.1 status=passed artifacts=1 artifact_errors=0 "
            "checklist=1/3 checklist_incomplete=2 checklist_errors=0"
        ),
        "  next: Mark every checklist item passed.",
    ]


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
