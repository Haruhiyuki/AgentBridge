from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Literal

from agentbridge.acceptance_evidence import (
    ACCEPTANCE_EVIDENCE_SCHEMA_VERSION,
    ACCEPTANCE_SECTION_STATUSES,
    ACCEPTANCE_SECTIONS,
    acceptance_artifact_reference,
    acceptance_evidence_summary,
    empty_acceptance_manifest,
    load_acceptance_manifest,
    read_acceptance_evidence,
    verify_acceptance_artifact,
    write_acceptance_manifest,
)

ACCEPTANCE_EXIT_INCOMPLETE = 2
ACCEPTANCE_EXIT_INVALID = 3
ACCEPTANCE_BUNDLE_SCHEMA_VERSION = "agentbridge.acceptance_bundle.v1"
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

    attach_parser = subparsers.add_parser(
        "attach-artifact",
        help="Copy an artifact into the artifact root and add a sha256 reference",
    )
    attach_parser.add_argument("path", type=Path)
    attach_parser.add_argument("section", choices=sorted(ACCEPTANCE_SECTIONS))
    attach_parser.add_argument("source", type=Path)
    attach_parser.add_argument("--artifact-root", type=Path, required=True)
    attach_parser.add_argument(
        "--name",
        help=(
            "Root-relative artifact name. Defaults to "
            "<section-slug>/<source-filename>."
        ),
    )
    attach_parser.add_argument("--status", choices=sorted(ACCEPTANCE_SECTION_STATUSES))
    attach_parser.add_argument("--notes")
    attach_parser.add_argument("--replace-artifacts", action="store_true")
    attach_parser.add_argument("--force", action="store_true")

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

    bundle_parser = subparsers.add_parser(
        "bundle",
        help="Create a portable release-candidate acceptance evidence bundle",
    )
    bundle_parser.add_argument("path", type=Path)
    bundle_parser.add_argument("output", type=Path)
    bundle_parser.add_argument("--artifact-root", type=Path)
    bundle_parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Allow bundling not-ready evidence when all referenced artifacts verify",
    )
    bundle_parser.add_argument("--force", action="store_true")
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


def attach_artifact(args: argparse.Namespace) -> int:
    manifest_path = args.path.expanduser()
    source_path = args.source.expanduser()
    artifact_root = args.artifact_root.expanduser()
    try:
        payload = load_acceptance_manifest(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"acceptance artifact attach failed: {exc}", file=sys.stderr)
        return 1
    if not source_path.is_file():
        print(
            f"acceptance artifact attach failed: source is not a file: {source_path}",
            file=sys.stderr,
        )
        return 1
    try:
        artifact_reference = copy_acceptance_artifact(
            source_path,
            artifact_root=artifact_root,
            section=args.section,
            name=args.name,
            force=args.force,
        )
    except ValueError as exc:
        print(f"acceptance artifact attach failed: {exc}", file=sys.stderr)
        return 1
    sections = payload.setdefault("sections", {})
    if not isinstance(sections, dict):
        print("acceptance artifact attach failed: sections must be a JSON object", file=sys.stderr)
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
    artifacts.append(artifact_reference)
    section_payload = {
        **section_payload,
        "status": args.status or section_payload.get("status") or "not_run",
        "artifacts": artifacts,
    }
    if args.notes is not None:
        section_payload["notes"] = args.notes
    sections[args.section] = section_payload
    write_acceptance_manifest(manifest_path, payload)
    print(
        f"attached {artifact_reference['path']} to {args.section} in {manifest_path} "
        f"sha256={artifact_reference['sha256']}"
    )
    return 0


def copy_acceptance_artifact(
    source_path: Path,
    *,
    artifact_root: Path,
    section: str,
    name: str | None,
    force: bool,
) -> dict[str, str]:
    root = artifact_root.expanduser().resolve(strict=False)
    target_name = acceptance_artifact_target_name(source_path, section=section, name=name)
    target = (root / target_name).resolve(strict=False)
    try:
        relative_target = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("artifact name must stay within artifact root") from exc
    resolved_source = source_path.resolve(strict=True)
    if target.exists() and not target.is_file():
        raise ValueError(f"artifact target is not a file: {relative_target.as_posix()}")
    if target.exists() and target != resolved_source and not force:
        raise ValueError(
            f"artifact target already exists: {relative_target.as_posix()} "
            "(use --force to replace it)"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target != resolved_source:
        shutil.copy2(resolved_source, target)
    sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    return {"path": relative_target.as_posix(), "sha256": sha256}


def acceptance_artifact_target_name(
    source_path: Path,
    *,
    section: str,
    name: str | None,
) -> Path:
    if name is not None:
        candidate = Path(name.strip())
        if not str(candidate):
            raise ValueError("artifact name must not be empty")
        if candidate.is_absolute():
            raise ValueError("artifact name must be relative to artifact root")
        if candidate == Path(".") or not candidate.name:
            raise ValueError("artifact name must identify a file below artifact root")
        return candidate
    section_slug = str(ACCEPTANCE_SECTIONS[section]["slug"])
    return Path(section_slug) / source_path.name


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


def bundle_manifest(args: argparse.Namespace) -> int:
    manifest_path = args.path.expanduser()
    output_path = args.output.expanduser()
    artifact_root = acceptance_artifact_root(manifest_path, args.artifact_root)
    if output_path.exists() and not args.force:
        print(
            f"acceptance bundle failed: output already exists: {output_path}",
            file=sys.stderr,
        )
        return 1
    try:
        payload = load_acceptance_manifest(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"acceptance bundle failed: {exc}", file=sys.stderr)
        return ACCEPTANCE_EXIT_INVALID
    evidence = read_acceptance_evidence(
        manifest_path,
        artifact_root=artifact_root,
        verify_artifacts=True,
    )
    summary = acceptance_evidence_summary(evidence)
    if not evidence.get("valid"):
        print(f"acceptance bundle failed: {evidence.get('error')}", file=sys.stderr)
        return ACCEPTANCE_EXIT_INVALID
    if int(summary.get("artifact_error_count") or 0) > 0:
        print("acceptance bundle failed: fix artifact verification errors", file=sys.stderr)
        return ACCEPTANCE_EXIT_INVALID
    if not summary.get("ready") and not args.allow_incomplete:
        print(
            "acceptance bundle failed: evidence is not ready "
            "(use --allow-incomplete for a draft bundle)",
            file=sys.stderr,
        )
        return ACCEPTANCE_EXIT_INCOMPLETE
    try:
        artifacts = collect_acceptance_bundle_artifacts(payload, artifact_root=artifact_root)
    except ValueError as exc:
        print(f"acceptance bundle failed: {exc}", file=sys.stderr)
        return ACCEPTANCE_EXIT_INVALID
    manifest_bytes = acceptance_json_bytes(payload)
    bundle_payload = {
        "schema_version": ACCEPTANCE_BUNDLE_SCHEMA_VERSION,
        "manifest": "acceptance-evidence.json",
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "artifact_root": str(artifact_root),
        "summary": summary,
        "artifacts": [
            {
                "section": artifact["section"],
                "path": artifact["path"],
                "archive_path": artifact["archive_path"],
                "sha256": artifact["sha256"],
            }
            for artifact in artifacts
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        write_acceptance_bundle_entry(archive, "acceptance-evidence.json", manifest_bytes)
        write_acceptance_bundle_entry(
            archive,
            "acceptance-bundle.json",
            acceptance_json_bytes(bundle_payload),
        )
        for artifact in artifacts:
            write_acceptance_bundle_entry(
                archive,
                str(artifact["archive_path"]),
                Path(str(artifact["resolved_path"])).read_bytes(),
            )
    print(
        f"created {output_path} ready={str(bool(summary.get('ready'))).lower()} "
        f"artifacts={len(artifacts)}"
    )
    return 0


def acceptance_artifact_root(manifest_path: Path, artifact_root: Path | None) -> Path:
    if artifact_root is not None and str(artifact_root).strip():
        return artifact_root.expanduser().resolve(strict=False)
    return manifest_path.expanduser().parent.resolve(strict=False)


def collect_acceptance_bundle_artifacts(
    payload: dict[str, object],
    *,
    artifact_root: Path,
) -> list[dict[str, object]]:
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("sections must be a JSON object")
    artifacts_by_archive_path: dict[str, dict[str, object]] = {}
    for section_id in ACCEPTANCE_SECTIONS:
        section = sections.get(section_id)
        if not isinstance(section, dict):
            continue
        raw_artifacts = section.get("artifacts")
        if not isinstance(raw_artifacts, list):
            continue
        for raw_artifact in raw_artifacts:
            reference = acceptance_artifact_reference(raw_artifact)
            if reference is None:
                raise ValueError(f"section {section_id} has an invalid artifact reference")
            verification = verify_acceptance_artifact(
                str(reference["path"]),
                expected_sha256=reference.get("sha256"),
                artifact_root=artifact_root,
            )
            if verification.get("status") != "verified":
                raise ValueError(
                    f"section {section_id} artifact {reference['path']} "
                    f"status={verification.get('status')}"
                )
            resolved_path = Path(str(verification["resolved_path"]))
            archive_path = "artifacts/" + resolved_path.relative_to(artifact_root).as_posix()
            sha256 = str(verification["actual_sha256"])
            existing = artifacts_by_archive_path.get(archive_path)
            if existing is not None:
                if existing.get("sha256") != sha256:
                    raise ValueError(f"artifact archive path collision: {archive_path}")
                continue
            artifacts_by_archive_path[archive_path] = {
                "section": section_id,
                "path": str(reference["path"]),
                "resolved_path": str(resolved_path),
                "archive_path": archive_path,
                "sha256": sha256,
            }
    return list(artifacts_by_archive_path.values())


def acceptance_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def write_acceptance_bundle_entry(
    archive: zipfile.ZipFile,
    archive_path: str,
    content: bytes,
) -> None:
    info = zipfile.ZipInfo(archive_path)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (1980, 1, 1, 0, 0, 0)
    info.external_attr = 0o644 << 16
    archive.writestr(info, content)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return init_manifest(args)
    if args.command == "set-section":
        return set_section(args)
    if args.command == "attach-artifact":
        return attach_artifact(args)
    if args.command == "summary":
        return summarize_manifest(args)
    if args.command == "bundle":
        return bundle_manifest(args)
    parser.error(f"unknown command {args.command}")
    return 1


def run() -> None:
    raise SystemExit(main())
