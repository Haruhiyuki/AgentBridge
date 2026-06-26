from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ACCEPTANCE_EVIDENCE_SCHEMA_VERSION = "agentbridge.acceptance_evidence.v1"
ACCEPTANCE_SECTION_MISSING = object()
ACCEPTANCE_SECTION_STATUSES = {"passed", "failed", "blocked", "not_run"}
ACCEPTANCE_SECTIONS = {
    "34.1": {
        "slug": "native_session",
        "notes": "Native Claude Code PTY, no claude -p, Bot restart, and 20-turn Session run.",
        "checklist": (
            {
                "id": "real_pty_claude",
                "label": "Start Claude Code in a real PTY without claude -p.",
            },
            {
                "id": "bot_restart_same_cli",
                "label": "Keep the same CLI alive across a Bot restart.",
            },
            {
                "id": "twenty_turn_session",
                "label": "Complete 20 consecutive turns in one Session.",
            },
        ),
    },
    "34.2": {
        "slug": "visible_terminal_takeover",
        "notes": (
            "Visible terminal, human lease takeover, Bot input blocking, release, "
            "and no crossed input."
        ),
        "checklist": (
            {
                "id": "visible_terminal_window",
                "label": "Open a local terminal window on the target OS with native TUI visible.",
            },
            {
                "id": "human_takeover_blocks_bot",
                "label": "Confirm first local key acquires the human lease and Bot input queues.",
            },
            {
                "id": "release_no_crossed_input",
                "label": "Release control back to Bot and confirm no crossed input.",
            },
        ),
    },
    "34.3": {
        "slug": "bot_experience",
        "notes": (
            "OneBot group create/bind, incremental answers, tool progress, questions, "
            "approvals, fallback text, and long code."
        ),
        "checklist": (
            {
                "id": "onebot_group_binding",
                "label": "Create and bind a Session in a real OneBot V11 group.",
            },
            {
                "id": "incremental_answer_tool_progress",
                "label": "Observe incremental answers and tool progress on the Bot platform.",
            },
            {
                "id": "interactions_and_long_code",
                "label": (
                    "Answer questions, approve or deny requests, and confirm long "
                    "code formatting."
                ),
            },
        ),
    },
    "34.4": {
        "slug": "permissions_management",
        "notes": (
            "Group role auth, callback re-authorization, Admin review, approval audit, "
            "and workdir restrictions."
        ),
        "checklist": (
            {
                "id": "group_role_mapping",
                "label": "Validate real group member role mappings.",
            },
            {
                "id": "callback_reauthorization",
                "label": "Confirm button callback re-authorization with platform user IDs.",
            },
            {
                "id": "admin_audit_workdir",
                "label": "Review approvals in Admin and verify workdir restrictions.",
            },
        ),
    },
    "34.5": {
        "slug": "multi_project_management",
        "notes": (
            "Three projects in one group, default project, project commands, allowed "
            "roots, quotas, and Admin view."
        ),
        "checklist": (
            {
                "id": "three_projects_bound",
                "label": "Bind at least three projects in one Bot chat.",
            },
            {
                "id": "unique_default_project",
                "label": "Set one unique default project.",
            },
            {
                "id": "project_commands",
                "label": "Verify /agent project list/use/info with names, aliases, and short IDs.",
            },
        ),
    },
    "34.6": {
        "slug": "multi_session_management",
        "notes": (
            "Three independent Sessions, session commands, unsafe ambiguity handling, "
            "queues, restart recovery, and no cross-write."
        ),
        "checklist": (
            {
                "id": "three_concurrent_sessions",
                "label": "Run at least three concurrent Sessions under one project.",
            },
            {
                "id": "restart_switching",
                "label": "Restart Bot or control clients and verify session switching.",
            },
            {
                "id": "no_cross_write",
                "label": "Confirm switching never closes or cross-writes other Sessions.",
            },
        ),
    },
    "34.7": {
        "slug": "slash_commands",
        "notes": (
            "OneBot /agent commands, unified invocations, auth, idempotency, interaction "
            "fallback, trace IDs, and unknown commands."
        ),
        "checklist": (
            {
                "id": "onebot_text_commands",
                "label": "Confirm OneBot text /agent commands in the deployed adapter.",
            },
            {
                "id": "action_callbacks",
                "label": "Confirm action callbacks route through the deployed adapter.",
            },
            {
                "id": "missing_argument_recovery",
                "label": "Verify missing-argument recovery paths.",
            },
        ),
    },
    "34.8": {
        "slug": "recovery",
        "notes": (
            "Control Plane disconnect, event replay, old epoch rejection, interaction "
            "expiry, and Bot retry/degradation."
        ),
        "checklist": (
            {
                "id": "control_plane_disconnect_reconnect",
                "label": "Disconnect and reconnect the Control Plane from a running local CLI.",
            },
            {
                "id": "event_replay",
                "label": "Replay missed events after reconnect.",
            },
            {
                "id": "os_terminal_recovery",
                "label": (
                    "Validate the OS-specific terminal backend recovery path used "
                    "in production."
                ),
            },
        ),
    },
}


def acceptance_section_checklist_manifest(section_id: str) -> list[dict[str, str]]:
    return [
        {
            "id": str(item["id"]),
            "label": str(item["label"]),
            "status": "not_run",
            "notes": "",
        }
        for item in ACCEPTANCE_SECTIONS[section_id]["checklist"]
    ]


def empty_acceptance_manifest(
    *,
    checked_at: str | None = None,
    environment: str = "staging",
) -> dict[str, object]:
    return {
        "schema_version": ACCEPTANCE_EVIDENCE_SCHEMA_VERSION,
        "checked_at": checked_at or datetime.now(UTC).replace(microsecond=0).isoformat(),
        "environment": environment,
        "sections": {
            section_id: {
                "status": "not_run",
                "artifacts": [],
                "checklist": acceptance_section_checklist_manifest(section_id),
                "notes": section["notes"],
            }
            for section_id, section in ACCEPTANCE_SECTIONS.items()
        },
    }


def read_acceptance_evidence(
    raw_path: str | Path | None,
    *,
    artifact_root: str | Path | None = None,
    verify_artifacts: bool = False,
) -> dict[str, object]:
    if raw_path is None or not str(raw_path).strip():
        return {
            "configured": False,
            "valid": False,
            "path": None,
            "schema_version": None,
            "error": None,
            "section_count": 0,
            "artifact_verification": acceptance_artifact_verification_payload(
                artifact_root,
                enabled=verify_artifacts,
            ),
            "sections": {},
        }
    path = Path(raw_path).expanduser()
    effective_artifact_root = (
        Path(artifact_root).expanduser()
        if artifact_root is not None and str(artifact_root).strip()
        else path.parent
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return acceptance_evidence_error(path, f"read_error:{exc.__class__.__name__}")
    except json.JSONDecodeError as exc:
        return acceptance_evidence_error(path, f"json_error:{exc.msg}")
    if not isinstance(payload, dict):
        return acceptance_evidence_error(path, "manifest_must_be_object")
    schema_version = payload.get("schema_version")
    if schema_version != ACCEPTANCE_EVIDENCE_SCHEMA_VERSION:
        return acceptance_evidence_error(
            path,
            "schema_version_mismatch",
            schema_version=schema_version,
        )
    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, dict):
        return acceptance_evidence_error(
            path,
            "sections_must_be_object",
            schema_version=schema_version,
        )
    unknown_sections = acceptance_unknown_section_ids(raw_sections)
    if unknown_sections:
        return acceptance_evidence_error(
            path,
            "unknown_sections:" + ",".join(unknown_sections),
            schema_version=schema_version,
            section_count=len(raw_sections),
        )
    sections = {
        section_id: acceptance_section_evidence(
            section_id,
            raw_sections.get(section_id, ACCEPTANCE_SECTION_MISSING),
            artifact_root=effective_artifact_root,
            verify_artifacts=verify_artifacts,
        )
        for section_id in ACCEPTANCE_SECTIONS
    }
    return {
        "configured": True,
        "valid": True,
        "path": str(path),
        "schema_version": schema_version,
        "error": None,
        "section_count": len(raw_sections),
        "artifact_verification": acceptance_artifact_verification_payload(
            effective_artifact_root,
            enabled=verify_artifacts,
        ),
        "sections": sections,
    }


def acceptance_evidence_error(
    path: Path,
    error: str,
    *,
    schema_version: object = None,
    section_count: int = 0,
) -> dict[str, object]:
    return {
        "configured": True,
        "valid": False,
        "path": str(path),
        "schema_version": schema_version,
        "error": error,
        "section_count": section_count,
        "artifact_verification": acceptance_artifact_verification_payload(
            None,
            enabled=False,
        ),
        "sections": {},
    }


def acceptance_unknown_section_ids(raw_sections: dict[object, object]) -> list[str]:
    return sorted(
        str(section_id)
        for section_id in raw_sections
        if not isinstance(section_id, str) or section_id not in ACCEPTANCE_SECTIONS
    )


def acceptance_section_evidence(
    section_id: str,
    raw_section: object,
    *,
    artifact_root: Path | None = None,
    verify_artifacts: bool = False,
) -> dict[str, object]:
    section_name = str(ACCEPTANCE_SECTIONS[section_id]["slug"])
    if raw_section is ACCEPTANCE_SECTION_MISSING:
        return {
            "section": section_id,
            "name": section_name,
            "status": "missing",
            "status_valid": True,
            "artifact_count": 0,
            "artifact_verified_count": 0,
            "artifact_error_count": 0,
            "artifact_verification_enabled": verify_artifacts,
            "artifact_errors": [],
            "notes_present": False,
        }
    if not isinstance(raw_section, dict):
        return {
            "section": section_id,
            "name": section_name,
            "status": "invalid",
            "status_valid": False,
            "error": "section_must_be_object",
            "artifact_count": 0,
            "artifact_verified_count": 0,
            "artifact_error_count": 0,
            "artifact_verification_enabled": verify_artifacts,
            "artifact_errors": [],
            "checklist": [],
            "checklist_present": False,
            "checklist_total": 0,
            "checklist_passed_count": 0,
            "checklist_failed_count": 0,
            "checklist_blocked_count": 0,
            "checklist_not_run_count": 0,
            "checklist_missing_count": 0,
            "checklist_error_count": 0,
            "notes_present": False,
        }
    raw_status = str(raw_section.get("status") or "").strip().lower()
    status_valid = raw_status in ACCEPTANCE_SECTION_STATUSES
    artifacts = raw_section.get("artifacts")
    artifact_payloads = acceptance_artifact_payloads(
        artifacts,
        artifact_root=artifact_root,
        verify_artifacts=verify_artifacts,
    )
    artifact_errors = [
        artifact
        for artifact in artifact_payloads
        if artifact.get("status") not in {"referenced", "verified"}
    ]
    checklist_payloads = acceptance_checklist_payloads(
        section_id,
        raw_section.get("checklist"),
    )
    expected_checklist_payloads = [
        item for item in checklist_payloads if bool(item.get("expected", False))
    ]
    checklist_errors = [
        item
        for item in checklist_payloads
        if not bool(item.get("expected", False))
        or not bool(item.get("status_valid", True))
    ]
    checklist_passed_count = sum(
        1
        for item in expected_checklist_payloads
        if item.get("status") == "passed" and bool(item.get("status_valid", True))
    )
    notes = raw_section.get("notes")
    return {
        "section": section_id,
        "name": section_name,
        "status": raw_status or "missing",
        "status_valid": status_valid,
        "artifact_count": len(artifact_payloads),
        "artifact_verified_count": sum(
            1 for artifact in artifact_payloads if artifact.get("status") == "verified"
        ),
        "artifact_error_count": len(artifact_errors),
        "artifact_verification_enabled": verify_artifacts,
        "artifact_errors": artifact_errors,
        "checklist": checklist_payloads,
        "checklist_present": isinstance(raw_section.get("checklist"), list),
        "checklist_total": len(expected_checklist_payloads),
        "checklist_passed_count": checklist_passed_count,
        "checklist_failed_count": sum(
            1 for item in expected_checklist_payloads if item.get("status") == "failed"
        ),
        "checklist_blocked_count": sum(
            1 for item in expected_checklist_payloads if item.get("status") == "blocked"
        ),
        "checklist_not_run_count": sum(
            1 for item in expected_checklist_payloads if item.get("status") == "not_run"
        ),
        "checklist_missing_count": sum(
            1 for item in expected_checklist_payloads if item.get("status") == "missing"
        ),
        "checklist_error_count": len(checklist_errors),
        "notes_present": bool(isinstance(notes, str) and notes.strip()),
    }


def acceptance_checklist_payloads(
    section_id: str,
    raw_checklist: object,
) -> list[dict[str, object]]:
    expected_items = [
        {"id": str(item["id"]), "label": str(item["label"])}
        for item in ACCEPTANCE_SECTIONS[section_id]["checklist"]
    ]
    raw_items_by_id: dict[str, dict[str, object]] = {}
    duplicate_ids: set[str] = set()
    unknown_items: list[dict[str, object]] = []
    if raw_checklist is not None and not isinstance(raw_checklist, list):
        unknown_items.append(
            {
                "id": None,
                "label": None,
                "status": "checklist_must_be_list",
                "status_valid": False,
                "expected": False,
                "notes_present": False,
            }
        )
    elif isinstance(raw_checklist, list):
        expected_ids = {item["id"] for item in expected_items}
        for raw_item in raw_checklist:
            if not isinstance(raw_item, dict):
                unknown_items.append(
                    {
                        "id": None,
                        "label": None,
                        "status": "invalid_item",
                        "status_valid": False,
                        "expected": False,
                        "notes_present": False,
                    }
                )
                continue
            raw_id = raw_item.get("id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                raw_label = raw_item.get("label")
                unknown_items.append(
                    {
                        "id": None,
                        "label": raw_label if isinstance(raw_label, str) else None,
                        "status": "missing_id",
                        "status_valid": False,
                        "expected": False,
                        "notes_present": False,
                    }
                )
                continue
            item_id = raw_id.strip()
            if item_id not in expected_ids:
                raw_label = raw_item.get("label")
                unknown_items.append(
                    {
                        "id": item_id,
                        "label": raw_label if isinstance(raw_label, str) else None,
                        "status": "unknown",
                        "status_valid": False,
                        "expected": False,
                        "notes_present": bool(
                            isinstance(raw_item.get("notes"), str)
                            and raw_item.get("notes", "").strip()
                        ),
                    }
                )
                continue
            if item_id in raw_items_by_id:
                duplicate_ids.add(item_id)
                continue
            raw_items_by_id[item_id] = raw_item
    payloads: list[dict[str, object]] = []
    for expected_item in expected_items:
        item_id = expected_item["id"]
        raw_item = raw_items_by_id.get(item_id)
        if raw_item is None:
            payloads.append(
                {
                    **expected_item,
                    "status": "missing",
                    "status_valid": True,
                    "expected": True,
                    "notes_present": False,
                }
            )
            continue
        raw_status = str(raw_item.get("status") or "").strip().lower()
        status = raw_status or "missing"
        payloads.append(
            {
                **expected_item,
                "status": status,
                "status_valid": (
                    status in ACCEPTANCE_SECTION_STATUSES and item_id not in duplicate_ids
                ),
                "expected": True,
                "notes_present": bool(
                    isinstance(raw_item.get("notes"), str)
                    and raw_item.get("notes", "").strip()
                ),
            }
        )
    payloads.extend(unknown_items)
    return payloads


def acceptance_artifact_payloads(
    artifacts: object,
    *,
    artifact_root: Path | None,
    verify_artifacts: bool,
) -> list[dict[str, object]]:
    if artifacts is None:
        return []
    if not isinstance(artifacts, list):
        return [{"path": None, "status": "artifacts_must_be_list"}]
    payloads: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    for artifact in artifacts:
        reference = acceptance_artifact_reference(artifact)
        if reference is None:
            payloads.append({"path": None, "status": "invalid_reference"})
            continue
        if not acceptance_artifact_path(reference["path"]):
            payloads.append(
                {
                    "path": reference["path"],
                    "sha256": reference.get("sha256"),
                    "status": "path_unsafe",
                }
            )
            continue
        expected_sha256 = reference.get("sha256")
        if expected_sha256 is not None and not acceptance_sha256_digest(expected_sha256):
            payloads.append(
                {
                    "path": reference["path"],
                    "sha256": expected_sha256,
                    "status": "sha256_invalid",
                }
            )
            continue
        if reference["path"] in seen_paths:
            payloads.append(
                {
                    "path": reference["path"],
                    "sha256": reference.get("sha256"),
                    "status": "duplicate_path",
                }
            )
            continue
        seen_paths.add(reference["path"])
        if not verify_artifacts:
            payloads.append(
                {
                    "path": reference["path"],
                    "sha256": reference.get("sha256"),
                    "status": "referenced",
                }
            )
            continue
        payloads.append(
            verify_acceptance_artifact(
                str(reference["path"]),
                expected_sha256=reference.get("sha256"),
                artifact_root=artifact_root,
            )
        )
    return payloads


def acceptance_artifact_reference(artifact: object) -> dict[str, str] | None:
    if isinstance(artifact, str) and artifact.strip():
        return {"path": artifact.strip()}
    if isinstance(artifact, dict):
        raw_path = artifact.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        reference = {"path": raw_path.strip()}
        raw_sha256 = artifact.get("sha256")
        if isinstance(raw_sha256, str) and raw_sha256.strip():
            reference["sha256"] = raw_sha256.strip().lower()
        return reference
    return None


def acceptance_artifact_path(value: str) -> bool:
    candidate = PurePosixPath(value)
    return (
        bool(value.strip())
        and "\\" not in value
        and candidate.as_posix() == value
        and bool(candidate.parts)
        and not candidate.is_absolute()
        and all(part not in {"", ".", ".."} for part in candidate.parts)
    )


def acceptance_sha256_digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value.lower())


def verify_acceptance_artifact(
    raw_path: str,
    *,
    expected_sha256: str | None,
    artifact_root: Path | None,
) -> dict[str, object]:
    root = (artifact_root or Path.cwd()).expanduser().resolve(strict=False)
    candidate = Path(raw_path).expanduser()
    artifact_path = candidate if candidate.is_absolute() else root / candidate
    resolved_artifact_path = artifact_path.resolve(strict=False)
    payload: dict[str, object] = {
        "path": raw_path,
        "resolved_path": str(resolved_artifact_path),
        "sha256": expected_sha256,
    }
    try:
        resolved_artifact_path.relative_to(root)
    except ValueError:
        payload["status"] = "outside_root"
        return payload
    if not resolved_artifact_path.exists():
        payload["status"] = "missing"
        return payload
    if not resolved_artifact_path.is_file():
        payload["status"] = "not_file"
        return payload
    actual_sha256 = hashlib.sha256(resolved_artifact_path.read_bytes()).hexdigest()
    payload["actual_sha256"] = actual_sha256
    if expected_sha256 and actual_sha256 != expected_sha256.lower():
        payload["status"] = "sha256_mismatch"
        return payload
    payload["status"] = "verified"
    return payload


def acceptance_artifact_verification_payload(
    artifact_root: str | Path | None,
    *,
    enabled: bool,
) -> dict[str, object]:
    return {
        "enabled": enabled,
        "root": str(Path(artifact_root).expanduser()) if artifact_root else None,
    }


def acceptance_evidence_summary(evidence: dict[str, object]) -> dict[str, object]:
    counts = {
        "passed": 0,
        "failed": 0,
        "blocked": 0,
        "not_run": 0,
        "missing": 0,
        "invalid": 0,
    }
    artifact_error_count = 0
    checklist_error_count = 0
    checklist_incomplete_count = 0
    if not evidence.get("valid"):
        return {
            "ready": False,
            "counts": counts,
            "total": len(ACCEPTANCE_SECTIONS),
            "error": evidence.get("error"),
            "artifact_error_count": artifact_error_count,
            "checklist_error_count": checklist_error_count,
            "checklist_incomplete_count": checklist_incomplete_count,
        }
    sections = evidence.get("sections")
    if not isinstance(sections, dict):
        counts["invalid"] = len(ACCEPTANCE_SECTIONS)
        return {
            "ready": False,
            "counts": counts,
            "total": len(ACCEPTANCE_SECTIONS),
            "error": "sections_missing",
            "artifact_error_count": artifact_error_count,
            "checklist_error_count": checklist_error_count,
            "checklist_incomplete_count": checklist_incomplete_count,
        }
    ready = True
    for section_id in ACCEPTANCE_SECTIONS:
        section = sections.get(section_id)
        section_payload = section if isinstance(section, dict) else {}
        status = str(section_payload.get("status") or "missing")
        if not bool(section_payload.get("status_valid", True)):
            status = "invalid"
        artifact_error_count += int(section_payload.get("artifact_error_count") or 0)
        section_checklist_total = int(section_payload.get("checklist_total") or 0)
        section_checklist_passed = int(section_payload.get("checklist_passed_count") or 0)
        section_checklist_errors = int(section_payload.get("checklist_error_count") or 0)
        checklist_error_count += section_checklist_errors
        if section_checklist_passed < section_checklist_total:
            checklist_incomplete_count += section_checklist_total - section_checklist_passed
        counts[status] = counts.get(status, 0) + 1
        if (
            status != "passed"
            or int(section_payload.get("artifact_count") or 0) <= 0
            or int(section_payload.get("artifact_error_count") or 0) > 0
            or section_checklist_passed < section_checklist_total
            or section_checklist_errors > 0
        ):
            ready = False
    return {
        "ready": ready,
        "counts": counts,
        "total": len(ACCEPTANCE_SECTIONS),
        "error": None,
        "artifact_error_count": artifact_error_count,
        "checklist_error_count": checklist_error_count,
        "checklist_incomplete_count": checklist_incomplete_count,
    }


def load_acceptance_manifest(path: Path) -> dict[str, object]:
    payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("acceptance manifest must contain a JSON object")
    if payload.get("schema_version") != ACCEPTANCE_EVIDENCE_SCHEMA_VERSION:
        raise ValueError(
            f"acceptance manifest schema_version must be {ACCEPTANCE_EVIDENCE_SCHEMA_VERSION}"
        )
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("acceptance manifest sections must be a JSON object")
    return payload


def write_acceptance_manifest(path: Path, payload: dict[str, object]) -> None:
    path.expanduser().parent.mkdir(parents=True, exist_ok=True)
    path.expanduser().write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
