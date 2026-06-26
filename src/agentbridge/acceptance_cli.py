from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Literal

from agentbridge.acceptance_evidence import (
    ACCEPTANCE_EVIDENCE_SCHEMA_VERSION,
    ACCEPTANCE_SECTION_STATUSES,
    ACCEPTANCE_SECTIONS,
    acceptance_artifact_reference,
    acceptance_evidence_summary,
    acceptance_section_checklist_manifest,
    acceptance_section_evidence,
    empty_acceptance_manifest,
    load_acceptance_manifest,
    read_acceptance_evidence,
    verify_acceptance_artifact,
    write_acceptance_manifest,
)

ACCEPTANCE_EXIT_INCOMPLETE = 2
ACCEPTANCE_EXIT_INVALID = 3
ACCEPTANCE_BUNDLE_SCHEMA_VERSION = "agentbridge.acceptance_bundle.v1"
ACCEPTANCE_ADMIN_EXPORT_ARTIFACT_NAMES = {
    "agentbridge.admin_system_health_export.v1": "admin-system-health.json",
    "agentbridge.admin_project_session_export.v1": "admin-project-session.json",
    "agentbridge.admin_interaction_export.v1": "admin-interaction.json",
    "agentbridge.admin_terminal_lifecycle_export.v1": "admin-terminal-lifecycle.json",
    "agentbridge.admin_device_identity_export.v1": "admin-device-identity.json",
    "agentbridge.admin_bot_delivery_export.v1": "admin-bot-delivery.json",
}
ACCEPTANCE_ADMIN_EXPORT_SECTIONS = {
    "agentbridge.admin_system_health_export.v1": ("34.1", "34.4", "34.8"),
    "agentbridge.admin_project_session_export.v1": ("34.1", "34.5", "34.6"),
    "agentbridge.admin_interaction_export.v1": ("34.3", "34.4", "34.7"),
    "agentbridge.admin_terminal_lifecycle_export.v1": ("34.1", "34.2", "34.8"),
    "agentbridge.admin_device_identity_export.v1": ("34.4",),
    "agentbridge.admin_bot_delivery_export.v1": ("34.3", "34.7", "34.8"),
}
ACCEPTANCE_BUNDLE_SUMMARY_COUNT_KEYS = (
    "passed",
    "failed",
    "blocked",
    "not_run",
    "missing",
    "invalid",
)
ACCEPTANCE_BUNDLE_SUMMARY_SCALAR_KEYS = (
    "ready",
    "total",
    "artifact_error_count",
    "checklist_error_count",
    "checklist_incomplete_count",
)
AcceptanceOutputFormat = Literal["json", "summary"]
AcceptanceBundleOutputFormat = Literal["json", "summary"]


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

    checklist_parser = subparsers.add_parser(
        "set-checklist",
        help="Update a manual acceptance checklist item in a manifest",
    )
    checklist_parser.add_argument("path", type=Path)
    checklist_parser.add_argument("section", choices=sorted(ACCEPTANCE_SECTIONS))
    checklist_parser.add_argument(
        "item",
        help="Checklist item id for the section, or 'all' to update every item",
    )
    checklist_parser.add_argument(
        "--status",
        choices=sorted(ACCEPTANCE_SECTION_STATUSES),
        required=True,
    )
    checklist_parser.add_argument("--notes")

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

    admin_export_parser = subparsers.add_parser(
        "attach-admin-export",
        help="Validate and attach a built-in Admin JSON export as acceptance evidence",
    )
    admin_export_parser.add_argument("path", type=Path)
    admin_export_parser.add_argument("section", choices=sorted(ACCEPTANCE_SECTIONS))
    admin_export_parser.add_argument("source", type=Path)
    admin_export_parser.add_argument("--artifact-root", type=Path, required=True)
    admin_export_parser.add_argument(
        "--name",
        help=(
            "Root-relative artifact name. Defaults to "
            "<section-slug>/<schema-based-name>."
        ),
    )
    admin_export_parser.add_argument(
        "--status",
        choices=sorted(ACCEPTANCE_SECTION_STATUSES),
    )
    admin_export_parser.add_argument("--notes")
    admin_export_parser.add_argument("--replace-artifacts", action="store_true")
    admin_export_parser.add_argument(
        "--allow-section-mismatch",
        action="store_true",
        help="Allow attaching a known Admin export outside its recommended sections",
    )
    admin_export_parser.add_argument("--force", action="store_true")

    summary_parser = subparsers.add_parser("summary", help="Summarize acceptance status")
    summary_parser.add_argument("path", type=Path)
    summary_parser.add_argument("--artifact-root", type=Path)
    summary_parser.add_argument("--verify-artifacts", action="store_true")
    summary_parser.add_argument(
        "--show-checklist",
        action="store_true",
        help="Show incomplete or invalid manual checklist items in summary output",
    )
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

    verify_bundle_parser = subparsers.add_parser(
        "verify-bundle",
        help="Verify a portable acceptance evidence bundle without extracting it",
    )
    verify_bundle_parser.add_argument("path", type=Path)
    verify_bundle_parser.add_argument(
        "--format",
        choices=["json", "summary"],
        default="summary",
    )
    verify_bundle_parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Allow verified draft bundles whose manifest is not ready",
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
    artifacts: list[object] = [artifact for artifact in args.artifact if artifact.strip()]
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
    try:
        update_acceptance_manifest_section(
            payload,
            section=args.section,
            status=args.status,
            artifacts=artifacts,
            notes=args.notes,
            replace_artifacts=args.replace_artifacts,
        )
    except ValueError as exc:
        print(f"acceptance manifest update failed: {exc}", file=sys.stderr)
        return 1
    write_acceptance_manifest(path, payload)
    print(f"updated {args.section} in {path}")
    return 0


def set_checklist(args: argparse.Namespace) -> int:
    path = args.path.expanduser()
    try:
        payload = load_acceptance_manifest(path)
        updated_items = update_acceptance_manifest_checklist(
            payload,
            section=args.section,
            item=args.item,
            status=args.status,
            notes=args.notes,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"acceptance checklist update failed: {exc}", file=sys.stderr)
        return 1
    write_acceptance_manifest(path, payload)
    print(f"updated {len(updated_items)} checklist item(s) in {args.section} in {path}")
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
    try:
        update_acceptance_manifest_section(
            payload,
            section=args.section,
            status=args.status,
            artifacts=[artifact_reference],
            notes=args.notes,
            replace_artifacts=args.replace_artifacts,
        )
    except ValueError as exc:
        print(f"acceptance artifact attach failed: {exc}", file=sys.stderr)
        return 1
    write_acceptance_manifest(manifest_path, payload)
    print(
        f"attached {artifact_reference['path']} to {args.section} in {manifest_path} "
        f"sha256={artifact_reference['sha256']}"
    )
    return 0


def attach_admin_export(args: argparse.Namespace) -> int:
    manifest_path = args.path.expanduser()
    source_path = args.source.expanduser()
    artifact_root = args.artifact_root.expanduser()
    try:
        payload = load_acceptance_manifest(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"acceptance admin export attach failed: {exc}", file=sys.stderr)
        return 1
    if not source_path.is_file():
        print(
            f"acceptance admin export attach failed: source is not a file: {source_path}",
            file=sys.stderr,
        )
        return 1
    try:
        schema_version = read_acceptance_admin_export_schema(source_path)
        validate_acceptance_admin_export_section(
            schema_version,
            args.section,
            allow_mismatch=args.allow_section_mismatch,
        )
        artifact_reference = copy_acceptance_artifact(
            source_path,
            artifact_root=artifact_root,
            section=args.section,
            name=args.name
            or acceptance_admin_export_artifact_name(
                schema_version,
                section=args.section,
            ),
            force=args.force,
        )
        update_acceptance_manifest_section(
            payload,
            section=args.section,
            status=args.status,
            artifacts=[artifact_reference],
            notes=args.notes,
            replace_artifacts=args.replace_artifacts,
        )
    except ValueError as exc:
        print(f"acceptance admin export attach failed: {exc}", file=sys.stderr)
        return 1
    write_acceptance_manifest(manifest_path, payload)
    print(
        f"attached {artifact_reference['path']} to {args.section} in {manifest_path} "
        f"schema_version={schema_version} sha256={artifact_reference['sha256']}"
    )
    return 0


def update_acceptance_manifest_section(
    payload: dict[str, object],
    *,
    section: str,
    status: str | None,
    artifacts: list[object],
    notes: str | None,
    replace_artifacts: bool,
) -> None:
    sections = payload.setdefault("sections", {})
    if not isinstance(sections, dict):
        raise ValueError("sections must be a JSON object")
    current_section = sections.get(section)
    section_payload = current_section if isinstance(current_section, dict) else {}
    existing_artifacts = section_payload.get("artifacts")
    section_artifacts = (
        []
        if replace_artifacts or not isinstance(existing_artifacts, list)
        else [
            artifact
            for artifact in existing_artifacts
            if isinstance(artifact, str | dict)
        ]
    )
    section_artifacts.extend(artifacts)
    next_status = status or section_payload.get("status") or "not_run"
    if not isinstance(next_status, str) or not next_status.strip():
        next_status = "not_run"
    section_payload = {
        **section_payload,
        "status": next_status,
        "artifacts": section_artifacts,
    }
    if notes is not None:
        section_payload["notes"] = notes
    sections[section] = section_payload


def update_acceptance_manifest_checklist(
    payload: dict[str, object],
    *,
    section: str,
    item: str,
    status: str,
    notes: str | None,
) -> list[str]:
    sections = payload.setdefault("sections", {})
    if not isinstance(sections, dict):
        raise ValueError("sections must be a JSON object")
    current_section = sections.get(section)
    section_payload = current_section if isinstance(current_section, dict) else {}
    checklist = merge_acceptance_checklist(section, section_payload.get("checklist"))
    expected_ids = {str(entry["id"]) for entry in checklist}
    requested_item = item.strip()
    if requested_item == "all":
        selected_ids = expected_ids
    elif requested_item in expected_ids:
        selected_ids = {requested_item}
    else:
        raise ValueError(
            f"unknown checklist item for section {section}: {requested_item or '<empty>'}"
        )
    for entry in checklist:
        if str(entry["id"]) not in selected_ids:
            continue
        entry["status"] = status
        if notes is not None:
            entry["notes"] = notes
    section_payload = {
        **section_payload,
        "checklist": checklist,
    }
    sections[section] = section_payload
    return sorted(selected_ids)


def merge_acceptance_checklist(
    section: str,
    raw_checklist: object,
) -> list[dict[str, str]]:
    defaults = acceptance_section_checklist_manifest(section)
    existing_by_id: dict[str, dict[str, object]] = {}
    if isinstance(raw_checklist, list):
        for raw_item in raw_checklist:
            if not isinstance(raw_item, dict):
                continue
            raw_id = raw_item.get("id")
            if isinstance(raw_id, str) and raw_id.strip():
                existing_by_id[raw_id.strip()] = raw_item
    merged: list[dict[str, str]] = []
    for default_item in defaults:
        item_id = default_item["id"]
        existing = existing_by_id.get(item_id, {})
        raw_status = existing.get("status")
        raw_notes = existing.get("notes")
        merged.append(
            {
                **default_item,
                "status": (
                    raw_status.strip().lower()
                    if isinstance(raw_status, str) and raw_status.strip()
                    else default_item["status"]
                ),
                "notes": (
                    raw_notes
                    if isinstance(raw_notes, str)
                    else default_item["notes"]
                ),
            }
        )
    return merged


def read_acceptance_admin_export_schema(source_path: Path) -> str:
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read admin export: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("admin export is not UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"admin export JSON is invalid: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("admin export must be a JSON object")
    raw_schema_version = payload.get("schema_version")
    if not isinstance(raw_schema_version, str) or not raw_schema_version.strip():
        raise ValueError("admin export missing schema_version")
    schema_version = raw_schema_version.strip()
    if schema_version not in ACCEPTANCE_ADMIN_EXPORT_ARTIFACT_NAMES:
        raise ValueError(f"unsupported admin export schema_version: {schema_version}")
    validate_acceptance_admin_export_payload(schema_version, payload)
    return schema_version


def validate_acceptance_admin_export_payload(
    schema_version: str,
    payload: dict[str, object],
) -> None:
    if schema_version == "agentbridge.admin_system_health_export.v1":
        validate_acceptance_system_health_export(payload)
    elif schema_version == "agentbridge.admin_project_session_export.v1":
        validate_acceptance_project_session_export(payload)
    elif schema_version == "agentbridge.admin_interaction_export.v1":
        validate_acceptance_interaction_export(payload)
    elif schema_version == "agentbridge.admin_terminal_lifecycle_export.v1":
        validate_acceptance_terminal_lifecycle_export(payload)
    elif schema_version == "agentbridge.admin_device_identity_export.v1":
        validate_acceptance_device_identity_export(payload)
    elif schema_version == "agentbridge.admin_bot_delivery_export.v1":
        validate_acceptance_bot_delivery_export(payload)


def validate_admin_export_array(
    payload: dict[str, object],
    export_name: str,
    field_name: str,
) -> None:
    if not isinstance(payload.get(field_name), list):
        raise ValueError(f"{export_name} admin export {field_name} must be a JSON array")


def validate_admin_export_object(
    payload: dict[str, object],
    export_name: str,
    field_name: str,
    *,
    allow_null: bool = False,
) -> None:
    value = payload.get(field_name)
    if allow_null and value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"{export_name} admin export {field_name} must be a JSON object")


def validate_admin_export_integer(
    payload: dict[str, object],
    export_name: str,
    field_name: str,
) -> None:
    value = payload.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(
            f"{export_name} admin export {field_name} must be a non-negative integer"
        )


def validate_acceptance_system_health_export(payload: dict[str, object]) -> None:
    validate_admin_export_array(payload, "system health", "endpoints")
    validate_admin_export_array(payload, "system health", "readiness_actions")
    readiness_actions = payload["readiness_actions"]
    for index, raw_action in enumerate(readiness_actions):
        if not isinstance(raw_action, dict):
            raise ValueError(
                f"system health admin export readiness_actions[{index}] "
                "must be a JSON object"
            )
        validate_acceptance_system_health_readiness_action(index, raw_action)


def validate_acceptance_system_health_readiness_action(
    index: int,
    action: dict[str, object],
) -> None:
    raw_id = action.get("id")
    action_id = raw_id if isinstance(raw_id, str) else ""
    evidence = action.get("evidence")
    if evidence is not None and not isinstance(evidence, dict):
        raise ValueError(
            f"system health admin export readiness_actions[{index}].evidence "
            "must be a JSON object"
        )
    raw_summary = action.get("evidence_summary")
    if raw_summary is not None and not isinstance(raw_summary, str):
        raise ValueError(
            f"system health admin export readiness_actions[{index}].evidence_summary "
            "must be a string"
        )
    if not action_id.startswith("acceptance."):
        return
    if not isinstance(evidence, dict):
        raise ValueError(
            f"system health admin export acceptance readiness action {action_id} "
            "must include evidence"
        )
    if not isinstance(raw_summary, str) or not raw_summary.strip():
        raise ValueError(
            f"system health admin export acceptance readiness action {action_id} "
            "must include evidence_summary"
        )


def validate_acceptance_project_session_export(payload: dict[str, object]) -> None:
    validate_admin_export_integer(payload, "project session", "project_count")
    validate_admin_export_integer(payload, "project session", "workspace_count")
    validate_admin_export_integer(payload, "project session", "session_count")
    for field_name in ("projects", "workspaces", "sessions"):
        validate_admin_export_array(payload, "project session", field_name)
    validate_admin_export_object(
        payload,
        "project session",
        "project_bindings",
        allow_null=True,
    )
    for field_name in (
        "queues_by_session",
        "leases_by_session",
        "pending_approvals_by_session",
    ):
        validate_admin_export_object(payload, "project session", field_name)


def validate_acceptance_interaction_export(payload: dict[str, object]) -> None:
    validate_admin_export_integer(payload, "interaction", "interaction_count")
    validate_admin_export_object(payload, "interaction", "filters")
    validate_admin_export_object(payload, "interaction", "actor")
    validate_admin_export_array(payload, "interaction", "interactions")
    validate_admin_export_object(
        payload,
        "interaction",
        "selected_interaction",
        allow_null=True,
    )


def validate_acceptance_terminal_lifecycle_export(payload: dict[str, object]) -> None:
    for field_name in (
        "monitor",
        "observed",
        "agent_probe_profiles",
        "agent_adapters",
    ):
        validate_admin_export_object(payload, "terminal lifecycle", field_name)


def validate_acceptance_device_identity_export(payload: dict[str, object]) -> None:
    validate_admin_export_integer(payload, "device identity", "device_count")
    validate_admin_export_object(payload, "device identity", "actor")
    validate_admin_export_object(payload, "device identity", "auth_device")
    validate_admin_export_array(payload, "device identity", "devices")
    validate_admin_export_object(
        payload,
        "device identity",
        "selected_device",
        allow_null=True,
    )
    validate_admin_export_object(
        payload,
        "device identity",
        "latest_operation",
        allow_null=True,
    )


def validate_acceptance_bot_delivery_export(payload: dict[str, object]) -> None:
    validate_admin_export_integer(payload, "bot delivery", "record_count")
    validate_admin_export_object(payload, "bot delivery", "filters")
    validate_admin_export_array(payload, "bot delivery", "records")
    for field_name in ("retry_worker", "capabilities", "rate_limits"):
        validate_admin_export_object(payload, "bot delivery", field_name)
    validate_admin_export_array(
        payload,
        "bot delivery",
        "command_registration_results",
    )
    validate_admin_export_object(
        payload,
        "bot delivery",
        "latest_action",
        allow_null=True,
    )


def validate_acceptance_admin_export_section(
    schema_version: str,
    section: str,
    *,
    allow_mismatch: bool,
) -> None:
    allowed_sections = ACCEPTANCE_ADMIN_EXPORT_SECTIONS[schema_version]
    if section in allowed_sections or allow_mismatch:
        return
    raise ValueError(
        f"admin export schema_version {schema_version} is intended for sections "
        f"{', '.join(allowed_sections)}; use --allow-section-mismatch to attach anyway"
    )


def acceptance_admin_export_artifact_name(
    schema_version: str,
    *,
    section: str,
) -> str:
    section_slug = str(ACCEPTANCE_SECTIONS[section]["slug"])
    artifact_name = ACCEPTANCE_ADMIN_EXPORT_ARTIFACT_NAMES[schema_version]
    return (Path(section_slug) / artifact_name).as_posix()


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


def acceptance_summary_text(
    evidence: dict[str, object],
    *,
    show_checklist: bool = False,
) -> str:
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
                f"checklist_incomplete={summary.get('checklist_incomplete_count', 0)}",
                f"checklist_errors={summary.get('checklist_error_count', 0)}",
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
            checklist_passed = section_payload.get("checklist_passed_count", 0)
            checklist_total = section_payload.get("checklist_total", 0)
            checklist_errors = section_payload.get("checklist_error_count", 0)
            lines.append(
                f"{section_id} status={status} artifacts={artifacts} "
                f"artifact_errors={artifact_errors} "
                f"checklist={checklist_passed}/{checklist_total} "
                f"checklist_errors={checklist_errors}"
            )
            if show_checklist:
                lines.extend(acceptance_checklist_gap_lines(section_id, section_payload))
    return "\n".join(lines)


def acceptance_checklist_gap_lines(
    section_id: str,
    section_payload: dict[str, object],
) -> list[str]:
    raw_checklist = section_payload.get("checklist")
    if not isinstance(raw_checklist, list):
        return []
    lines: list[str] = []
    for raw_item in raw_checklist:
        item = raw_item if isinstance(raw_item, dict) else {}
        item_id = item.get("id")
        status = str(item.get("status") or "missing")
        expected = bool(item.get("expected", True))
        status_valid = bool(item.get("status_valid", True))
        if expected and status_valid and status == "passed":
            continue
        lines.append(
            " ".join(
                [
                    f"{section_id} checklist",
                    (
                        f"id={item_id}"
                        if isinstance(item_id, str) and item_id
                        else "id=<missing>"
                    ),
                    f"status={status}",
                    f"expected={str(expected).lower()}",
                    f"status_valid={str(status_valid).lower()}",
                ]
            )
        )
    return lines


def print_summary(
    evidence: dict[str, object],
    output_format: AcceptanceOutputFormat,
    *,
    show_checklist: bool = False,
) -> None:
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
    print(acceptance_summary_text(evidence, show_checklist=show_checklist))


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
    if (
        int(summary.get("artifact_error_count") or 0) > 0
        or int(summary.get("checklist_error_count") or 0) > 0
    ):
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
    print_summary(evidence, args.format, show_checklist=args.show_checklist)
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
    if int(summary.get("checklist_error_count") or 0) > 0:
        print("acceptance bundle failed: fix invalid checklist entries", file=sys.stderr)
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


def verify_bundle(args: argparse.Namespace) -> int:
    verification = verify_acceptance_bundle(args.path.expanduser())
    print_bundle_verification(verification, args.format)
    if not verification.get("valid"):
        return ACCEPTANCE_EXIT_INVALID
    if not verification.get("ready") and not args.allow_incomplete:
        return ACCEPTANCE_EXIT_INCOMPLETE
    return 0


def verify_acceptance_bundle(path: Path) -> dict[str, object]:
    try:
        with zipfile.ZipFile(path) as archive:
            duplicate_entries = duplicate_zip_entries(archive)
            index_bytes = archive.read("acceptance-bundle.json")
            manifest_bytes = archive.read("acceptance-evidence.json")
            bundle_index = json.loads(index_bytes.decode("utf-8"))
            manifest_payload = json.loads(manifest_bytes.decode("utf-8"))
            result = verify_acceptance_bundle_payload(
                archive,
                bundle_index=bundle_index,
                manifest_payload=manifest_payload,
                manifest_bytes=manifest_bytes,
                duplicate_entries=duplicate_entries,
            )
    except KeyError as exc:
        return acceptance_bundle_verification_error(
            path,
            f"missing_bundle_entry:{exc.args[0]}",
        )
    except (OSError, zipfile.BadZipFile) as exc:
        return acceptance_bundle_verification_error(
            path,
            f"zip_error:{exc.__class__.__name__}",
        )
    except UnicodeDecodeError:
        return acceptance_bundle_verification_error(path, "bundle_json_not_utf8")
    except json.JSONDecodeError as exc:
        return acceptance_bundle_verification_error(path, f"bundle_json_error:{exc.msg}")
    result["path"] = str(path)
    return result


def verify_acceptance_bundle_payload(
    archive: zipfile.ZipFile,
    *,
    bundle_index: object,
    manifest_payload: object,
    manifest_bytes: bytes,
    duplicate_entries: list[str],
) -> dict[str, object]:
    errors: list[str] = []
    if duplicate_entries:
        errors.append("duplicate_zip_entries:" + ",".join(duplicate_entries))
    if not isinstance(bundle_index, dict):
        return acceptance_bundle_verification_payload_error("bundle_index_must_be_object")
    if not isinstance(manifest_payload, dict):
        return acceptance_bundle_verification_payload_error("manifest_must_be_object")
    if bundle_index.get("schema_version") != ACCEPTANCE_BUNDLE_SCHEMA_VERSION:
        errors.append("bundle_schema_version_mismatch")
    if bundle_index.get("manifest") != "acceptance-evidence.json":
        errors.append("bundle_manifest_entry_mismatch")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if bundle_index.get("manifest_sha256") != manifest_sha256:
        errors.append("manifest_sha256_mismatch")
    if manifest_payload.get("schema_version") != ACCEPTANCE_EVIDENCE_SCHEMA_VERSION:
        errors.append("manifest_schema_version_mismatch")
    artifact_entries = bundle_index.get("artifacts")
    if not isinstance(artifact_entries, list):
        errors.append("bundle_artifacts_must_be_list")
        artifact_entries = []
    summary = bundle_index.get("summary")
    if not isinstance(summary, dict):
        errors.append("bundle_summary_must_be_object")
        summary = {}
    artifact_index = verify_acceptance_bundle_artifacts(
        archive,
        artifact_entries=artifact_entries,
        errors=errors,
    )
    ready = verify_acceptance_bundle_manifest(
        manifest_payload,
        artifact_index=artifact_index,
        errors=errors,
    )
    manifest_summary = acceptance_bundle_manifest_summary(manifest_payload, ready=ready)
    if summary:
        validate_acceptance_bundle_summary(summary, manifest_summary, errors=errors)
    valid = not errors
    verification_summary = acceptance_bundle_manifest_summary(
        manifest_payload,
        ready=ready if valid else False,
    )
    return {
        "schema_version": ACCEPTANCE_BUNDLE_SCHEMA_VERSION,
        "valid": valid,
        "ready": ready if valid else False,
        "artifact_count": len(artifact_index),
        "errors": errors,
        "manifest_sha256": manifest_sha256,
        "summary": verification_summary,
    }


def acceptance_bundle_verification_error(path: Path, error: str) -> dict[str, object]:
    return {
        "schema_version": ACCEPTANCE_BUNDLE_SCHEMA_VERSION,
        "valid": False,
        "ready": False,
        "path": str(path),
        "artifact_count": 0,
        "errors": [error],
        "summary": {},
    }


def acceptance_bundle_verification_payload_error(error: str) -> dict[str, object]:
    return {
        "schema_version": ACCEPTANCE_BUNDLE_SCHEMA_VERSION,
        "valid": False,
        "ready": False,
        "artifact_count": 0,
        "errors": [error],
        "summary": {},
    }


def duplicate_zip_entries(archive: zipfile.ZipFile) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in archive.namelist():
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return sorted(duplicates)


def verify_acceptance_bundle_artifacts(
    archive: zipfile.ZipFile,
    *,
    artifact_entries: list[object],
    errors: list[str],
) -> dict[str, dict[str, str]]:
    artifact_index: dict[str, dict[str, str]] = {}
    for index, raw_artifact in enumerate(artifact_entries):
        if not isinstance(raw_artifact, dict):
            errors.append(f"artifact[{index}]_must_be_object")
            continue
        raw_path = raw_artifact.get("path")
        raw_archive_path = raw_artifact.get("archive_path")
        raw_sha256 = raw_artifact.get("sha256")
        if not isinstance(raw_path, str) or not raw_path.strip():
            errors.append(f"artifact[{index}]_path_missing")
            continue
        if not acceptance_bundle_relative_path(raw_path):
            errors.append(f"artifact[{index}]_path_unsafe")
            continue
        if not isinstance(raw_archive_path, str) or not acceptance_bundle_archive_path(
            raw_archive_path,
            required_prefix="artifacts",
        ):
            errors.append(f"artifact[{index}]_archive_path_unsafe")
            continue
        if not isinstance(raw_sha256, str) or not raw_sha256.strip():
            errors.append(f"artifact[{index}]_sha256_missing")
            continue
        try:
            artifact_bytes = archive.read(raw_archive_path)
        except KeyError:
            errors.append(f"artifact[{index}]_archive_entry_missing")
            continue
        actual_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
        expected_sha256 = raw_sha256.strip().lower()
        if actual_sha256 != expected_sha256:
            errors.append(f"artifact[{index}]_sha256_mismatch")
            continue
        existing = artifact_index.get(raw_path)
        if existing is not None:
            if existing["sha256"] != actual_sha256:
                errors.append(f"artifact[{index}]_path_collision")
            continue
        artifact_index[raw_path] = {
            "archive_path": raw_archive_path,
            "sha256": actual_sha256,
        }
    return artifact_index


def verify_acceptance_bundle_manifest(
    manifest_payload: dict[str, object],
    *,
    artifact_index: dict[str, dict[str, str]],
    errors: list[str],
) -> bool:
    sections = manifest_payload.get("sections")
    if not isinstance(sections, dict):
        errors.append("manifest_sections_must_be_object")
        return False
    ready = True
    referenced_paths: set[str] = set()
    for section_id in ACCEPTANCE_SECTIONS:
        section = sections.get(section_id)
        if not isinstance(section, dict):
            ready = False
            continue
        raw_status = str(section.get("status") or "").strip().lower()
        if raw_status not in ACCEPTANCE_SECTION_STATUSES:
            errors.append(f"section_{section_id}_status_invalid")
            ready = False
        raw_artifacts = section.get("artifacts")
        artifact_count = 0
        if isinstance(raw_artifacts, list):
            for raw_artifact in raw_artifacts:
                reference = acceptance_artifact_reference(raw_artifact)
                if reference is None:
                    errors.append(f"section_{section_id}_artifact_reference_invalid")
                    continue
                artifact_path = reference["path"]
                if not acceptance_bundle_relative_path(artifact_path):
                    errors.append(f"section_{section_id}_artifact_path_unsafe")
                    continue
                indexed_artifact = artifact_index.get(artifact_path)
                if indexed_artifact is None:
                    errors.append(f"section_{section_id}_artifact_missing_from_bundle")
                    continue
                expected_sha256 = reference.get("sha256")
                if expected_sha256 and expected_sha256.lower() != indexed_artifact["sha256"]:
                    errors.append(f"section_{section_id}_artifact_sha256_mismatch")
                    continue
                referenced_paths.add(artifact_path)
                artifact_count += 1
        elif raw_artifacts is not None:
            errors.append(f"section_{section_id}_artifacts_must_be_list")
        checklist_ready = verify_acceptance_bundle_section_checklist(
            section_id,
            section.get("checklist"),
            errors=errors,
        )
        if raw_status != "passed" or artifact_count <= 0 or not checklist_ready:
            ready = False
    for artifact_path in artifact_index:
        if artifact_path not in referenced_paths:
            errors.append(f"bundle_artifact_unreferenced:{artifact_path}")
    return ready


def acceptance_bundle_manifest_summary(
    manifest_payload: dict[str, object],
    *,
    ready: bool,
) -> dict[str, object]:
    sections = manifest_payload.get("sections")
    if not isinstance(sections, dict):
        return {}
    section_evidence = {
        section_id: acceptance_section_evidence(
            section_id,
            sections.get(section_id),
            artifact_root=None,
            verify_artifacts=False,
        )
        for section_id in ACCEPTANCE_SECTIONS
    }
    summary = acceptance_evidence_summary(
        {
            "valid": True,
            "sections": section_evidence,
        }
    )
    summary["ready"] = ready
    return summary


def validate_acceptance_bundle_summary(
    summary: dict[str, object],
    expected_summary: dict[str, object],
    *,
    errors: list[str],
) -> None:
    if not expected_summary:
        return
    for key in ACCEPTANCE_BUNDLE_SUMMARY_SCALAR_KEYS:
        if summary.get(key) != expected_summary.get(key):
            errors.append(f"bundle_summary_{key}_mismatch")
    raw_counts = summary.get("counts")
    expected_counts = expected_summary.get("counts")
    if not isinstance(raw_counts, dict):
        errors.append("bundle_summary_counts_must_be_object")
        return
    expected_counts_payload = expected_counts if isinstance(expected_counts, dict) else {}
    for key in ACCEPTANCE_BUNDLE_SUMMARY_COUNT_KEYS:
        if raw_counts.get(key) != expected_counts_payload.get(key):
            errors.append(f"bundle_summary_count_{key}_mismatch")


def verify_acceptance_bundle_section_checklist(
    section_id: str,
    raw_checklist: object,
    *,
    errors: list[str],
) -> bool:
    expected_ids = {
        str(item["id"])
        for item in ACCEPTANCE_SECTIONS[section_id]["checklist"]
    }
    if not isinstance(raw_checklist, list):
        return False
    seen_ids: set[str] = set()
    passed_ids: set[str] = set()
    checklist_ready = True
    for raw_item in raw_checklist:
        if not isinstance(raw_item, dict):
            errors.append(f"section_{section_id}_checklist_item_invalid")
            checklist_ready = False
            continue
        raw_id = raw_item.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            errors.append(f"section_{section_id}_checklist_item_id_missing")
            checklist_ready = False
            continue
        item_id = raw_id.strip()
        if item_id not in expected_ids:
            errors.append(f"section_{section_id}_checklist_item_unknown:{item_id}")
            checklist_ready = False
            continue
        if item_id in seen_ids:
            errors.append(f"section_{section_id}_checklist_item_duplicate:{item_id}")
            checklist_ready = False
            continue
        seen_ids.add(item_id)
        raw_status = raw_item.get("status")
        if not isinstance(raw_status, str):
            checklist_ready = False
            continue
        status = raw_status.strip().lower()
        if status not in ACCEPTANCE_SECTION_STATUSES:
            errors.append(f"section_{section_id}_checklist_item_status_invalid:{item_id}")
            checklist_ready = False
            continue
        if status == "passed":
            passed_ids.add(item_id)
        else:
            checklist_ready = False
    return checklist_ready and passed_ids == expected_ids


def acceptance_bundle_relative_path(raw_path: str) -> bool:
    candidate = PurePosixPath(raw_path)
    return (
        bool(raw_path.strip())
        and not candidate.is_absolute()
        and all(part not in {"", ".", ".."} for part in candidate.parts)
    )


def acceptance_bundle_archive_path(raw_path: str, *, required_prefix: str) -> bool:
    candidate = PurePosixPath(raw_path)
    return (
        acceptance_bundle_relative_path(raw_path)
        and bool(candidate.parts)
        and candidate.parts[0] == required_prefix
    )


def print_bundle_verification(
    verification: dict[str, object],
    output_format: AcceptanceBundleOutputFormat,
) -> None:
    if output_format == "json":
        print(json.dumps(verification, ensure_ascii=False, sort_keys=True))
        return
    errors = verification.get("errors")
    error_count = len(errors) if isinstance(errors, list) else 0
    print(
        " ".join(
            [
                f"valid={str(bool(verification.get('valid'))).lower()}",
                f"ready={str(bool(verification.get('ready'))).lower()}",
                f"artifacts={int(verification.get('artifact_count') or 0)}",
                f"errors={error_count}",
                *acceptance_bundle_summary_fields(verification),
            ]
        )
    )
    if isinstance(errors, list):
        for error in errors:
            print(f"error={error}")


def acceptance_bundle_summary_fields(verification: dict[str, object]) -> list[str]:
    summary = verification.get("summary")
    if not isinstance(summary, dict):
        return []
    counts = summary.get("counts")
    counts_payload = counts if isinstance(counts, dict) else {}
    return [
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return init_manifest(args)
    if args.command == "set-section":
        return set_section(args)
    if args.command == "set-checklist":
        return set_checklist(args)
    if args.command == "attach-artifact":
        return attach_artifact(args)
    if args.command == "attach-admin-export":
        return attach_admin_export(args)
    if args.command == "summary":
        return summarize_manifest(args)
    if args.command == "bundle":
        return bundle_manifest(args)
    if args.command == "verify-bundle":
        return verify_bundle(args)
    parser.error(f"unknown command {args.command}")
    return 1


def run() -> None:
    raise SystemExit(main())
