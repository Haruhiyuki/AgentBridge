from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentbridge.agent_adapter_client import (
    AgentAdapterClientError,
    secret_value,
    urllib_json_transport,
)

READINESS_EXIT_DEGRADED = 2
READINESS_EXIT_NOT_READY = 3
ReadinessOutputFormat = Literal["json", "summary", "actions"]


@dataclass(frozen=True)
class ReadinessClientConfig:
    base_url: str
    api_token: str | None = None
    device_id: str | None = None
    device_key: str | None = None
    timeout_seconds: float = 10.0


def readiness_headers(config: ReadinessClientConfig) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if config.api_token:
        headers["Authorization"] = f"Bearer {config.api_token}"
    if config.device_id and config.device_key:
        headers["X-AgentBridge-Device-ID"] = config.device_id
        headers["X-AgentBridge-Device-Key"] = config.device_key
    return headers


def fetch_readiness(config: ReadinessClientConfig) -> dict[str, object]:
    return urllib_json_transport(
        "GET",
        config.base_url.rstrip("/") + "/api/v1/readiness",
        readiness_headers(config),
        None,
        config.timeout_seconds,
    )


def readiness_exit_code(
    payload: dict[str, object],
    *,
    fail_on_warn: bool,
    fail_on_fail: bool,
) -> int:
    status = str(payload.get("status") or "")
    if status == "not_ready":
        return READINESS_EXIT_NOT_READY if fail_on_fail or fail_on_warn else 0
    if status == "degraded":
        return READINESS_EXIT_DEGRADED if fail_on_warn else 0
    return 0


def readiness_summary_text(payload: dict[str, object]) -> str:
    summary = payload.get("summary")
    counts = summary.get("counts") if isinstance(summary, dict) else {}
    if not isinstance(counts, dict):
        counts = {}
    return " ".join(
        [
            f"status={payload.get('status')}",
            f"pass={counts.get('pass', 0)}",
            f"warn={counts.get('warn', 0)}",
            f"fail={counts.get('fail', 0)}",
        ]
    )


def readiness_action_text(payload: dict[str, object]) -> str:
    checks = payload.get("checks")
    check_items = (
        [check for check in checks if isinstance(check, dict)]
        if isinstance(checks, list)
        else []
    )
    problem_checks = [
        (index, check)
        for index, check in enumerate(check_items)
        if str(check.get("status") or "fail") != "pass"
    ]
    status_order = {"fail": 0, "warn": 1}
    ordered_checks = sorted(
        problem_checks,
        key=lambda item: (status_order.get(str(item[1].get("status")), 2), item[0]),
    )

    lines = [readiness_summary_text(payload)]
    if not ordered_checks:
        lines.append("all readiness checks passed")
        return "\n".join(lines)

    for _, check in ordered_checks:
        status = str(check.get("status") or "unknown")
        category = str(check.get("category") or "unknown")
        check_id = str(check.get("id") or "unknown")
        summary = str(check.get("summary") or "")
        lines.append(f"{status} {category}/{check_id}: {summary}")
        evidence_text = readiness_action_evidence_text(check)
        if evidence_text:
            lines.append(f"  evidence: {evidence_text}")
        next_step = check.get("next_step")
        if next_step:
            lines.append(f"  next: {next_step}")
    return "\n".join(lines)


def readiness_action_evidence_text(check: dict[str, object]) -> str | None:
    check_id = str(check.get("id") or "")
    evidence = check.get("evidence")
    if not isinstance(evidence, dict):
        return None
    if check_id == "acceptance.evidence_manifest":
        return readiness_acceptance_manifest_evidence_text(evidence)
    if check_id == "acceptance.evidence_bundle":
        return readiness_acceptance_bundle_evidence_text(evidence)
    if check_id.startswith("acceptance."):
        return readiness_acceptance_section_evidence_text(evidence)
    return None


def readiness_acceptance_manifest_evidence_text(
    evidence: dict[str, object],
) -> str | None:
    summary = evidence.get("summary")
    if not isinstance(summary, dict):
        return None
    counts = summary.get("counts")
    counts_payload = counts if isinstance(counts, dict) else {}
    return " ".join(
        [
            f"manifest_ready={str(bool(summary.get('ready'))).lower()}",
            f"sections={evidence.get('section_count', 0)}",
            f"passed={counts_payload.get('passed', 0)}",
            f"failed={counts_payload.get('failed', 0)}",
            f"blocked={counts_payload.get('blocked', 0)}",
            f"not_run={counts_payload.get('not_run', 0)}",
            f"missing={counts_payload.get('missing', 0)}",
            f"invalid={counts_payload.get('invalid', 0)}",
            f"artifact_errors={summary.get('artifact_error_count', 0)}",
            f"checklist_incomplete={summary.get('checklist_incomplete_count', 0)}",
            f"checklist_errors={summary.get('checklist_error_count', 0)}",
        ]
    )


def readiness_acceptance_bundle_evidence_text(
    evidence: dict[str, object],
) -> str | None:
    summary = evidence.get("summary")
    if not isinstance(summary, dict):
        return None
    counts = summary.get("counts")
    counts_payload = counts if isinstance(counts, dict) else {}
    return " ".join(
        [
            f"bundle_ready={str(bool(summary.get('ready'))).lower()}",
            f"artifacts={evidence.get('artifact_count', 0)}",
            f"passed={counts_payload.get('passed', 0)}",
            f"failed={counts_payload.get('failed', 0)}",
            f"blocked={counts_payload.get('blocked', 0)}",
            f"not_run={counts_payload.get('not_run', 0)}",
            f"artifact_errors={summary.get('artifact_error_count', 0)}",
            f"checklist_incomplete={summary.get('checklist_incomplete_count', 0)}",
            f"checklist_errors={summary.get('checklist_error_count', 0)}",
        ]
    )


def readiness_acceptance_section_evidence_text(
    evidence: dict[str, object],
) -> str:
    checklist_total = readiness_int(evidence.get("checklist_total"))
    checklist_passed = readiness_int(evidence.get("checklist_passed_count"))
    checklist_incomplete = max(checklist_total - checklist_passed, 0)
    return " ".join(
        [
            f"section={evidence.get('section', 'unknown')}",
            f"status={evidence.get('status', 'unknown')}",
            f"artifacts={evidence.get('artifact_count', 0)}",
            f"artifact_errors={evidence.get('artifact_error_count', 0)}",
            f"checklist={checklist_passed}/{checklist_total}",
            f"checklist_incomplete={checklist_incomplete}",
            f"checklist_errors={evidence.get('checklist_error_count', 0)}",
        ]
    )


def readiness_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch AgentBridge product readiness status."
    )
    parser.add_argument("--api-url", help="AgentBridge API base URL")
    parser.add_argument("--api-token", help="API bearer token")
    parser.add_argument("--api-token-file", type=Path, help="File containing API token")
    parser.add_argument("--device-id", help="Managed/static device ID")
    parser.add_argument("--device-key", help="Managed/static device key")
    parser.add_argument("--device-key-file", type=Path, help="File containing device key")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument(
        "--format",
        choices=["json", "summary", "actions"],
        default="json",
        help="Output format",
    )
    exit_policy = parser.add_mutually_exclusive_group()
    exit_policy.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Exit non-zero for degraded or not_ready readiness status",
    )
    exit_policy.add_argument(
        "--fail-on-fail",
        action="store_true",
        help="Exit non-zero only for not_ready readiness status",
    )
    return parser


def build_config_from_args(args: argparse.Namespace) -> ReadinessClientConfig:
    api_token = secret_value(
        args.api_token,
        args.api_token_file,
        "AGENTBRIDGE_API_TOKEN",
        "AGENTBRIDGE_API_TOKEN_FILE",
    )
    device_key = secret_value(
        args.device_key,
        args.device_key_file,
        "AGENTBRIDGE_DEVICE_KEY",
        "AGENTBRIDGE_DEVICE_KEY_FILE",
    )
    return ReadinessClientConfig(
        base_url=args.api_url
        or os.environ.get("AGENTBRIDGE_API_URL")
        or "http://127.0.0.1:8000",
        api_token=api_token,
        device_id=args.device_id or os.environ.get("AGENTBRIDGE_DEVICE_ID"),
        device_key=device_key,
        timeout_seconds=args.timeout_seconds,
    )


def print_readiness(payload: dict[str, object], output_format: ReadinessOutputFormat) -> None:
    if output_format == "summary":
        print(readiness_summary_text(payload))
        return
    if output_format == "actions":
        print(readiness_action_text(payload))
        return
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = fetch_readiness(build_config_from_args(args))
        print_readiness(payload, args.format)
        return readiness_exit_code(
            payload,
            fail_on_warn=args.fail_on_warn,
            fail_on_fail=args.fail_on_fail,
        )
    except (AgentAdapterClientError, OSError, ValueError) as exc:
        print(f"agentbridge readiness failed: {exc}", file=sys.stderr)
        return 1


def run() -> None:
    raise SystemExit(main())
