from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

from agentbridge.acceptance_evidence import (
    ACCEPTANCE_EVIDENCE_SCHEMA_VERSION,
    ACCEPTANCE_SECTION_STATUSES,
    ACCEPTANCE_SECTIONS,
    acceptance_evidence_summary,
    empty_acceptance_manifest,
    load_acceptance_manifest,
    read_acceptance_evidence,
    write_acceptance_manifest,
)

ACCEPTANCE_EXIT_INCOMPLETE = 2
ACCEPTANCE_EXIT_INVALID = 3
AcceptanceOutputFormat = Literal["json", "summary"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage AgentBridge MVP acceptance evidence manifests."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create an acceptance manifest")
    init_parser.add_argument("path", type=Path)
    init_parser.add_argument("--environment", default="staging")
    init_parser.add_argument("--checked-at")
    init_parser.add_argument("--force", action="store_true")

    set_parser = subparsers.add_parser(
        "set-section",
        help="Update one design-document section in an acceptance manifest",
    )
    set_parser.add_argument("path", type=Path)
    set_parser.add_argument("section", choices=sorted(ACCEPTANCE_SECTIONS))
    set_parser.add_argument("--status", choices=sorted(ACCEPTANCE_SECTION_STATUSES), required=True)
    set_parser.add_argument("--artifact", action="append", default=[])
    set_parser.add_argument(
        "--artifact-sha256",
        action="append",
        default=[],
        metavar="PATH=SHA256",
        help="Add an artifact reference with an expected sha256 digest",
    )
    set_parser.add_argument("--notes")
    set_parser.add_argument("--replace-artifacts", action="store_true")

    summary_parser = subparsers.add_parser("summary", help="Summarize acceptance status")
    summary_parser.add_argument("path", type=Path)
    summary_parser.add_argument("--artifact-root", type=Path)
    summary_parser.add_argument("--verify-artifacts", action="store_true")
    summary_parser.add_argument(
        "--format",
        choices=["json", "summary"],
        default="summary",
    )
    exit_policy = summary_parser.add_mutually_exclusive_group()
    exit_policy.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Exit non-zero for incomplete or invalid acceptance evidence",
    )
    exit_policy.add_argument(
        "--fail-on-fail",
        action="store_true",
        help="Exit non-zero only for invalid or failed acceptance evidence",
    )
    return parser


def init_manifest(args: argparse.Namespace) -> int:
    path = args.path.expanduser()
    if path.exists() and not args.force:
        print(
            f"acceptance manifest already exists: {path}",
            file=sys.stderr,
        )
        return 1
    payload = empty_acceptance_manifest(
        checked_at=args.checked_at,
        environment=args.environment,
    )
    write_acceptance_manifest(path, payload)
    print(f"created {path}")
    return 0


def set_section(args: argparse.Namespace) -> int:
    path = args.path.expanduser()
    try:
        payload = load_acceptance_manifest(path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"acceptance manifest update failed: {exc}", file=sys.stderr)
        return 1
    sections = payload.setdefault("sections", {})
    if not isinstance(sections, dict):
        print("acceptance manifest update failed: sections must be a JSON object", file=sys.stderr)
        return 1
    current_section = sections.get(args.section)
    section_payload = current_section if isinstance(current_section, dict) else {}
    existing_artifacts = section_payload.get("artifacts")
    artifacts = (
        []
        if args.replace_artifacts or not isinstance(existing_artifacts, list)
        else [
            artifact
            for artifact in existing_artifacts
            if isinstance(artifact, str | dict)
        ]
    )
    artifacts.extend(artifact for artifact in args.artifact if artifact.strip())
    for artifact_sha256 in args.artifact_sha256:
        try:
            artifact_path, sha256 = artifact_sha256.split("=", 1)
        except ValueError:
            print(
                "acceptance manifest update failed: --artifact-sha256 must use PATH=SHA256",
                file=sys.stderr,
            )
            return 1
        if not artifact_path.strip() or not sha256.strip():
            print(
                "acceptance manifest update failed: --artifact-sha256 requires path and digest",
                file=sys.stderr,
            )
            return 1
        artifacts.append({"path": artifact_path.strip(), "sha256": sha256.strip().lower()})
    section_payload = {
        **section_payload,
        "status": args.status,
        "artifacts": artifacts,
    }
    if args.notes is not None:
        section_payload["notes"] = args.notes
    sections[args.section] = section_payload
    write_acceptance_manifest(path, payload)
    print(f"updated {args.section} in {path}")
    return 0


def acceptance_summary_text(evidence: dict[str, object]) -> str:
    summary = acceptance_evidence_summary(evidence)
    counts = summary["counts"] if isinstance(summary.get("counts"), dict) else {}
    lines = [
        " ".join(
            [
                f"ready={str(bool(summary.get('ready'))).lower()}",
                f"passed={counts.get('passed', 0)}",
                f"failed={counts.get('failed', 0)}",
                f"blocked={counts.get('blocked', 0)}",
                f"not_run={counts.get('not_run', 0)}",
                f"missing={counts.get('missing', 0)}",
                f"invalid={counts.get('invalid', 0)}",
                f"artifact_errors={summary.get('artifact_error_count', 0)}",
            ]
        )
    ]
    if evidence.get("error"):
        lines.append(f"error={evidence.get('error')}")
    sections = evidence.get("sections")
    if isinstance(sections, dict):
        for section_id in ACCEPTANCE_SECTIONS:
            section = sections.get(section_id)
            section_payload = section if isinstance(section, dict) else {}
            status = section_payload.get("status", "missing")
            artifacts = section_payload.get("artifact_count", 0)
            artifact_errors = section_payload.get("artifact_error_count", 0)
            lines.append(
                f"{section_id} status={status} artifacts={artifacts} "
                f"artifact_errors={artifact_errors}"
            )
    return "\n".join(lines)


def print_summary(evidence: dict[str, object], output_format: AcceptanceOutputFormat) -> None:
    if output_format == "json":
        print(
            json.dumps(
                {
                    "schema_version": ACCEPTANCE_EVIDENCE_SCHEMA_VERSION,
                    "evidence": evidence,
                    "summary": acceptance_evidence_summary(evidence),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return
    print(acceptance_summary_text(evidence))


def summary_exit_code(
    evidence: dict[str, object],
    *,
    fail_on_warn: bool,
    fail_on_fail: bool,
) -> int:
    summary = acceptance_evidence_summary(evidence)
    if not evidence.get("valid"):
        return ACCEPTANCE_EXIT_INVALID
    sections = evidence.get("sections")
    failed = False
    if isinstance(sections, dict):
        for section in sections.values():
            section_payload = section if isinstance(section, dict) else {}
            if section_payload.get("status") == "failed" or not bool(
                section_payload.get("status_valid", True)
            ):
                failed = True
                break
    if failed:
        return ACCEPTANCE_EXIT_INVALID if fail_on_fail or fail_on_warn else 0
    if int(summary.get("artifact_error_count") or 0) > 0:
        return ACCEPTANCE_EXIT_INVALID if fail_on_fail or fail_on_warn else 0
    if not summary.get("ready") and fail_on_warn:
        return ACCEPTANCE_EXIT_INCOMPLETE
    return 0


def summarize_manifest(args: argparse.Namespace) -> int:
    evidence = read_acceptance_evidence(
        args.path,
        artifact_root=args.artifact_root,
        verify_artifacts=args.verify_artifacts,
    )
    print_summary(evidence, args.format)
    return summary_exit_code(
        evidence,
        fail_on_warn=args.fail_on_warn,
        fail_on_fail=args.fail_on_fail,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return init_manifest(args)
    if args.command == "set-section":
        return set_section(args)
    if args.command == "summary":
        return summarize_manifest(args)
    parser.error(f"unknown command {args.command}")
    return 1


def run() -> None:
    raise SystemExit(main())
