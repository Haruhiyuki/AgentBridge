from __future__ import annotations

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


def read_acceptance_evidence(raw_path: str | Path | None) -> dict[str, object]:
    if raw_path is None or not str(raw_path).strip():
        return {
            "configured": False,
            "valid": False,
            "path": None,
            "schema_version": None,
            "error": None,
            "section_count": 0,
            "sections": {},
        }
    path = Path(raw_path).expanduser()
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
        "sections": {},
    }


def acceptance_section_evidence(
    section_id: str,
    raw_section: object,
) -> dict[str, object]:
    section_name = str(ACCEPTANCE_SECTIONS[section_id]["slug"])
    if not isinstance(raw_section, dict):
        return {
            "section": section_id,
            "name": section_name,
            "status": "missing",
            "status_valid": True,
            "artifact_count": 0,
            "notes_present": False,
        }
    raw_status = str(raw_section.get("status") or "").strip().lower()
    status_valid = raw_status in ACCEPTANCE_SECTION_STATUSES
    artifacts = raw_section.get("artifacts")
    artifact_count = (
        len([artifact for artifact in artifacts if isinstance(artifact, str) and artifact.strip()])
        if isinstance(artifacts, list)
        else 0
    )
    notes = raw_section.get("notes")
    return {
        "section": section_id,
        "name": section_name,
        "status": raw_status or "missing",
        "status_valid": status_valid,
        "artifact_count": artifact_count,
        "notes_present": bool(isinstance(notes, str) and notes.strip()),
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
    if not evidence.get("valid"):
        return {
            "ready": False,
            "counts": counts,
            "total": len(ACCEPTANCE_SECTIONS),
            "error": evidence.get("error"),
        }
    sections = evidence.get("sections")
    if not isinstance(sections, dict):
        counts["invalid"] = len(ACCEPTANCE_SECTIONS)
        return {
            "ready": False,
            "counts": counts,
            "total": len(ACCEPTANCE_SECTIONS),
            "error": "sections_missing",
        }
    ready = True
    for section_id in ACCEPTANCE_SECTIONS:
        section = sections.get(section_id)
        section_payload = section if isinstance(section, dict) else {}
        status = str(section_payload.get("status") or "missing")
        if not bool(section_payload.get("status_valid", True)):
            status = "invalid"
        counts[status] = counts.get(status, 0) + 1
        if status != "passed" or int(section_payload.get("artifact_count") or 0) <= 0:
            ready = False
    return {
        "ready": ready,
        "counts": counts,
        "total": len(ACCEPTANCE_SECTIONS),
        "error": None,
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
