from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

ACCEPTANCE_EVIDENCE_SCHEMA_VERSION = "agentbridge.acceptance_evidence.v1"
ACCEPTANCE_SECTION_STATUSES = {"passed", "failed", "blocked", "not_run"}
ACCEPTANCE_SECTIONS = {
    "34.1": {
        "slug": "native_session",
        "notes": "Native Claude Code PTY, no claude -p, Bot restart, and 20-turn Session run.",
    },
    "34.2": {
        "slug": "visible_terminal_takeover",
        "notes": (
            "Visible terminal, human lease takeover, Bot input blocking, release, "
            "and no crossed input."
        ),
    },
    "34.3": {
        "slug": "bot_experience",
        "notes": (
            "OneBot group create/bind, incremental answers, tool progress, questions, "
            "approvals, fallback text, and long code."
        ),
    },
    "34.4": {
        "slug": "permissions_management",
        "notes": (
            "Group role auth, callback re-authorization, Admin review, approval audit, "
            "and workdir restrictions."
        ),
    },
    "34.5": {
        "slug": "multi_project_management",
        "notes": (
            "Three projects in one group, default project, project commands, allowed "
            "roots, quotas, and Admin view."
        ),
    },
    "34.6": {
        "slug": "multi_session_management",
        "notes": (
            "Three independent Sessions, session commands, unsafe ambiguity handling, "
            "queues, restart recovery, and no cross-write."
        ),
    },
    "34.7": {
        "slug": "slash_commands",
        "notes": (
            "OneBot /agent commands, unified invocations, auth, idempotency, interaction "
            "fallback, trace IDs, and unknown commands."
        ),
    },
    "34.8": {
        "slug": "recovery",
        "notes": (
            "Control Plane disconnect, event replay, old epoch rejection, interaction "
            "expiry, and Bot retry/degradation."
        ),
    },
}


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
    sections = {
        section_id: acceptance_section_evidence(
            section_id,
            raw_sections.get(section_id),
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
) -> dict[str, object]:
    return {
        "configured": True,
        "valid": False,
        "path": str(path),
        "schema_version": schema_version,
        "error": error,
        "section_count": 0,
        "artifact_verification": acceptance_artifact_verification_payload(
            None,
            enabled=False,
        ),
        "sections": {},
    }


def acceptance_section_evidence(
    section_id: str,
    raw_section: object,
    *,
    artifact_root: Path | None = None,
    verify_artifacts: bool = False,
) -> dict[str, object]:
    section_name = str(ACCEPTANCE_SECTIONS[section_id]["slug"])
    if not isinstance(raw_section, dict):
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
        "notes_present": bool(isinstance(notes, str) and notes.strip()),
    }


def acceptance_artifact_payloads(
    artifacts: object,
    *,
    artifact_root: Path | None,
    verify_artifacts: bool,
) -> list[dict[str, object]]:
    if not isinstance(artifacts, list):
        return []
    payloads: list[dict[str, object]] = []
    for artifact in artifacts:
        reference = acceptance_artifact_reference(artifact)
        if reference is None:
            payloads.append({"path": None, "status": "invalid_reference"})
            continue
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
    if not evidence.get("valid"):
        return {
            "ready": False,
            "counts": counts,
            "total": len(ACCEPTANCE_SECTIONS),
            "error": evidence.get("error"),
            "artifact_error_count": artifact_error_count,
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
        }
    ready = True
    for section_id in ACCEPTANCE_SECTIONS:
        section = sections.get(section_id)
        section_payload = section if isinstance(section, dict) else {}
        status = str(section_payload.get("status") or "missing")
        if not bool(section_payload.get("status_valid", True)):
            status = "invalid"
        artifact_error_count += int(section_payload.get("artifact_error_count") or 0)
        counts[status] = counts.get(status, 0) + 1
        if (
            status != "passed"
            or int(section_payload.get("artifact_count") or 0) <= 0
            or int(section_payload.get("artifact_error_count") or 0) > 0
        ):
            ready = False
    return {
        "ready": ready,
        "counts": counts,
        "total": len(ACCEPTANCE_SECTIONS),
        "error": None,
        "artifact_error_count": artifact_error_count,
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
