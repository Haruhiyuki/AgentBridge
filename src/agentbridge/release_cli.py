from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from agentbridge import __version__

RELEASE_PREFLIGHT_SCHEMA_VERSION = "agentbridge.release_preflight.v1"
RELEASE_EXIT_WARN = 2
RELEASE_EXIT_FAIL = 3
ReleaseOutputFormat = Literal["json", "summary", "actions"]
ReleaseProfile = Literal["local", "rc"]

REQUIRED_CONSOLE_SCRIPTS = {
    "agentbridge-api",
    "agentbridge-acceptance",
    "agentbridge-adapter-client",
    "agentbridge-audit-verify",
    "agentbridge-console",
    "agentbridge-pty-host",
    "agentbridge-readiness",
    "agentbridge-release",
    "agentbridge-terminal-agent",
}

REQUIRED_RELEASE_FILES = (
    "README.md",
    "docs/DEVELOPMENT_STATE.md",
    "docs/operations/AUDIT_ARCHIVE_SIGNING.md",
    "docs/operations/DATABASE_DEPLOYMENT.md",
    "docs/operations/DEVICE_CERTIFICATE_OPERATIONS.md",
    "docs/operations/MVP_ACCEPTANCE_RUNBOOK.md",
    "docs/operations/PTY_HOST_SERVICE_MANAGER.md",
    "docs/operations/RELEASE_CANDIDATE.md",
    "docs/operations/templates/acceptance_evidence.example.json",
    "docs/operations/templates/agentbridge-pty-host.env.example",
    "docs/operations/templates/agentbridge-pty-host.systemd.user.service",
    "docs/operations/templates/com.agentbridge.pty-host.launchd.plist",
)


def release_preflight_report(
    project_root: Path,
    *,
    profile: ReleaseProfile,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    environment = env if env is not None else os.environ
    root = project_root.expanduser().resolve(strict=False)
    checks = [
        release_version_check(root),
        release_console_script_check(root),
        release_file_check(root),
        release_database_check(environment, profile=profile),
        release_http_auth_check(environment, profile=profile),
        release_admin_auth_check(environment, profile=profile),
        release_websocket_auth_check(environment, profile=profile),
        release_terminal_backend_check(environment, profile=profile),
        release_acceptance_check(environment, profile=profile),
    ]
    counts = {"pass": 0, "warn": 0, "fail": 0}
    for check in checks:
        status = str(check.get("status") or "fail")
        counts[status] = counts.get(status, 0) + 1
    if counts["fail"] > 0:
        status = "not_ready"
    elif counts["warn"] > 0:
        status = "degraded"
    else:
        status = "ready"
    return {
        "schema_version": RELEASE_PREFLIGHT_SCHEMA_VERSION,
        "profile": profile,
        "project_root": str(root),
        "status": status,
        "summary": {
            "total": len(checks),
            "counts": counts,
        },
        "checks": checks,
    }


def release_version_check(project_root: Path) -> dict[str, object]:
    payload, error = read_pyproject(project_root)
    if error:
        return release_check(
            "package.version",
            "package",
            "fail",
            "Package metadata is readable.",
            evidence={"error": error},
            next_step="Restore pyproject.toml before building a release candidate.",
        )
    project = payload.get("project")
    project_payload = project if isinstance(project, dict) else {}
    pyproject_version = project_payload.get("version")
    status = "pass" if pyproject_version == __version__ else "fail"
    next_step = (
        None
        if status == "pass"
        else "Keep pyproject.toml project.version and agentbridge.__version__ in sync."
    )
    return release_check(
        "package.version",
        "package",
        status,
        "Package version metadata is consistent.",
        evidence={
            "pyproject_version": pyproject_version,
            "package_version": __version__,
        },
        next_step=next_step,
    )


def release_console_script_check(project_root: Path) -> dict[str, object]:
    payload, error = read_pyproject(project_root)
    if error:
        return release_check(
            "package.console_scripts",
            "package",
            "fail",
            "Required console scripts are declared.",
            evidence={"error": error},
            next_step="Restore pyproject.toml before building a release candidate.",
        )
    scripts = pyproject_scripts(payload)
    missing = sorted(REQUIRED_CONSOLE_SCRIPTS.difference(scripts))
    status = "pass" if not missing else "fail"
    return release_check(
        "package.console_scripts",
        "package",
        status,
        "Required console scripts are declared.",
        evidence={
            "required": sorted(REQUIRED_CONSOLE_SCRIPTS),
            "missing": missing,
        },
        next_step=(
            None
            if not missing
            else "Add the missing project.scripts entries before publishing the package."
        ),
    )


def release_file_check(project_root: Path) -> dict[str, object]:
    missing = [
        relative_path
        for relative_path in REQUIRED_RELEASE_FILES
        if not (project_root / relative_path).is_file()
    ]
    status = "pass" if not missing else "fail"
    return release_check(
        "release.required_files",
        "release",
        status,
        "Release docs and service templates are present.",
        evidence={
            "required": list(REQUIRED_RELEASE_FILES),
            "missing": missing,
        },
        next_step=(
            None
            if not missing
            else "Restore the missing runbooks/templates before handing off a release."
        ),
    )


def release_database_check(
    env: Mapping[str, str],
    *,
    profile: ReleaseProfile,
) -> dict[str, object]:
    configured = env_present(env, "AGENTBRIDGE_DATABASE_URL")
    status = "pass" if configured else required_status(profile)
    return release_check(
        "config.database",
        "configuration",
        status,
        "Persistent database URL is configured.",
        evidence={"configured": configured},
        next_step=(
            None
            if configured
            else "Set AGENTBRIDGE_DATABASE_URL and run alembic upgrade head."
        ),
    )


def release_http_auth_check(
    env: Mapping[str, str],
    *,
    profile: ReleaseProfile,
) -> dict[str, object]:
    sources = configured_sources(
        env,
        (
            "AGENTBRIDGE_API_TOKEN",
            "AGENTBRIDGE_API_TOKEN_FILE",
            "AGENTBRIDGE_DEVICE_KEYS",
            "AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS",
            "AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS_FILE",
        ),
    )
    status = "pass" if sources else required_status(profile)
    return release_check(
        "config.http_auth",
        "configuration",
        status,
        "HTTP API authentication has a configured credential source.",
        evidence={"sources": sources},
        next_step=(
            None
            if sources
            else "Configure API token, device credentials, or trusted client certificates."
        ),
    )


def release_admin_auth_check(
    env: Mapping[str, str],
    *,
    profile: ReleaseProfile,
) -> dict[str, object]:
    sources = configured_sources(
        env,
        (
            "AGENTBRIDGE_ADMIN_TOKEN",
            "AGENTBRIDGE_ADMIN_TOKEN_FILE",
            "AGENTBRIDGE_API_TOKEN",
            "AGENTBRIDGE_API_TOKEN_FILE",
        ),
    )
    status = "pass" if sources else required_status(profile)
    return release_check(
        "config.admin_auth",
        "configuration",
        status,
        "Admin Web authentication has a configured token source.",
        evidence={"sources": sources},
        next_step=(
            None
            if sources
            else "Configure AGENTBRIDGE_ADMIN_TOKEN(_FILE) or reuse AGENTBRIDGE_API_TOKEN(_FILE)."
        ),
    )


def release_websocket_auth_check(
    env: Mapping[str, str],
    *,
    profile: ReleaseProfile,
) -> dict[str, object]:
    sources = configured_sources(
        env,
        (
            "AGENTBRIDGE_WS_TOKEN",
            "AGENTBRIDGE_WS_TOKEN_FILE",
            "AGENTBRIDGE_DEVICE_KEYS",
            "AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS",
            "AGENTBRIDGE_CLIENT_CERT_FINGERPRINTS_FILE",
        ),
    )
    status = "pass" if sources else required_status(profile)
    return release_check(
        "config.websocket_auth",
        "configuration",
        status,
        "WebSocket authentication has a configured credential source.",
        evidence={"sources": sources},
        next_step=(
            None
            if sources
            else "Configure WebSocket token, device credentials, or trusted client certificates."
        ),
    )


def release_terminal_backend_check(
    env: Mapping[str, str],
    *,
    profile: ReleaseProfile,
) -> dict[str, object]:
    backend = env.get("AGENTBRIDGE_TERMINAL_BACKEND", "").strip()
    usable_backends = {"pty", "pty_host", "tmux"}
    backend_usable = backend in usable_backends
    missing: list[str] = []
    if backend == "pty_host":
        if not env_present(env, "AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET"):
            missing.append("AGENTBRIDGE_TERMINAL_PTY_HOST_SOCKET")
        if not (
            env_present(env, "AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN")
            or env_present(env, "AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN_FILE")
        ):
            missing.append("AGENTBRIDGE_TERMINAL_PTY_HOST_TOKEN(_FILE)")
    status = "pass" if backend_usable and not missing else required_status(profile)
    return release_check(
        "config.terminal_backend",
        "configuration",
        status,
        "Product-like terminal backend is configured.",
        evidence={
            "backend": backend or None,
            "usable_backends": sorted(usable_backends),
            "missing": missing,
        },
        next_step=terminal_backend_next_step(backend, missing),
    )


def terminal_backend_next_step(backend: str, missing: list[str]) -> str | None:
    if missing:
        return "Set the missing PTY host socket/token variables for pty_host deployments."
    if backend in {"pty", "pty_host", "tmux"}:
        return None
    return "Set AGENTBRIDGE_TERMINAL_BACKEND to pty_host, pty, or tmux before RC testing."


def release_acceptance_check(
    env: Mapping[str, str],
    *,
    profile: ReleaseProfile,
) -> dict[str, object]:
    evidence_path = env_path(env, "AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE")
    bundle_path = env_path(env, "AGENTBRIDGE_ACCEPTANCE_BUNDLE_FILE")
    artifact_root = env_path(env, "AGENTBRIDGE_ACCEPTANCE_ARTIFACT_ROOT")
    missing = []
    if evidence_path is None or not evidence_path.is_file():
        missing.append("AGENTBRIDGE_ACCEPTANCE_EVIDENCE_FILE")
    if bundle_path is None or not bundle_path.is_file():
        missing.append("AGENTBRIDGE_ACCEPTANCE_BUNDLE_FILE")
    status = "pass" if not missing else required_status(profile)
    return release_check(
        "acceptance.release_evidence",
        "acceptance",
        status,
        "Release acceptance evidence files are configured.",
        evidence={
            "evidence_file": str(evidence_path) if evidence_path else None,
            "bundle_file": str(bundle_path) if bundle_path else None,
            "artifact_root": str(artifact_root) if artifact_root else None,
            "missing": missing,
        },
        next_step=(
            None
            if not missing
            else "Run the MVP acceptance flow and configure the signed-off manifest and bundle."
        ),
    )


def release_check(
    check_id: str,
    category: str,
    status: str,
    summary: str,
    *,
    evidence: dict[str, object],
    next_step: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": check_id,
        "category": category,
        "status": status,
        "summary": summary,
        "evidence": evidence,
    }
    if next_step:
        payload["next_step"] = next_step
    return payload


def required_status(profile: ReleaseProfile) -> str:
    return "fail" if profile == "rc" else "warn"


def read_pyproject(project_root: Path) -> tuple[dict[str, object], str | None]:
    path = project_root / "pyproject.toml"
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return {}, f"read_error:{exc.__class__.__name__}"
    except tomllib.TOMLDecodeError as exc:
        return {}, f"toml_error:{exc.__class__.__name__}"
    return payload, None


def pyproject_scripts(payload: dict[str, object]) -> set[str]:
    project = payload.get("project")
    project_payload = project if isinstance(project, dict) else {}
    scripts = project_payload.get("scripts")
    if not isinstance(scripts, dict):
        return set()
    return {key for key in scripts if isinstance(key, str)}


def env_present(env: Mapping[str, str], name: str) -> bool:
    return bool(env.get(name, "").strip())


def configured_sources(env: Mapping[str, str], names: tuple[str, ...]) -> list[str]:
    return [name for name in names if env_present(env, name)]


def env_path(env: Mapping[str, str], name: str) -> Path | None:
    raw_path = env.get(name, "").strip()
    if not raw_path:
        return None
    return Path(raw_path).expanduser()


def release_preflight_exit_code(
    report: dict[str, object],
    *,
    fail_on_warn: bool,
) -> int:
    status = str(report.get("status") or "")
    if status == "not_ready":
        return RELEASE_EXIT_FAIL
    if status == "degraded" and fail_on_warn:
        return RELEASE_EXIT_WARN
    return 0


def release_summary_text(report: dict[str, object]) -> str:
    summary = report.get("summary")
    counts = summary.get("counts") if isinstance(summary, dict) else {}
    if not isinstance(counts, dict):
        counts = {}
    return " ".join(
        [
            f"profile={report.get('profile')}",
            f"status={report.get('status')}",
            f"pass={counts.get('pass', 0)}",
            f"warn={counts.get('warn', 0)}",
            f"fail={counts.get('fail', 0)}",
        ]
    )


def release_action_text(report: dict[str, object]) -> str:
    checks = report.get("checks")
    check_items = (
        [check for check in checks if isinstance(check, dict)]
        if isinstance(checks, list)
        else []
    )
    problem_checks = [
        check for check in check_items if str(check.get("status") or "fail") != "pass"
    ]
    status_order = {"fail": 0, "warn": 1}
    ordered_checks = sorted(
        problem_checks,
        key=lambda check: status_order.get(str(check.get("status")), 2),
    )
    lines = [release_summary_text(report)]
    if not ordered_checks:
        lines.append("all release preflight checks passed")
        return "\n".join(lines)
    for check in ordered_checks:
        lines.append(
            " ".join(
                [
                    str(check.get("status") or "unknown"),
                    f"{check.get('category')}/{check.get('id')}:",
                    str(check.get("summary") or ""),
                ]
            )
        )
        evidence = check.get("evidence")
        evidence_text = release_evidence_text(evidence if isinstance(evidence, dict) else {})
        if evidence_text:
            lines.append(f"  evidence: {evidence_text}")
        next_step = check.get("next_step")
        if next_step:
            lines.append(f"  next: {next_step}")
    return "\n".join(lines)


def release_evidence_text(evidence: dict[str, object]) -> str | None:
    missing = evidence.get("missing")
    if isinstance(missing, list) and missing:
        return "missing=" + ",".join(str(item) for item in missing)
    sources = evidence.get("sources")
    if isinstance(sources, list):
        return "sources=" + (",".join(str(item) for item in sources) or "<none>")
    backend = evidence.get("backend")
    if backend is not None:
        return f"backend={backend}"
    error = evidence.get("error")
    if error:
        return f"error={error}"
    return None


def print_release_preflight(
    report: dict[str, object],
    output_format: ReleaseOutputFormat,
) -> None:
    if output_format == "json":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    elif output_format == "summary":
        print(release_summary_text(report))
    else:
        print(release_action_text(report))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run AgentBridge release-candidate preflight checks."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Repository or source tree root containing pyproject.toml",
    )
    parser.add_argument(
        "--profile",
        choices=["local", "rc"],
        default="rc",
        help="Use rc for handoff/release candidates or local for developer smoke checks.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "summary", "actions"],
        default="actions",
        help="Output format",
    )
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Exit non-zero for degraded release preflight status",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = release_preflight_report(
        args.project_root,
        profile=args.profile,
    )
    print_release_preflight(report, args.format)
    return release_preflight_exit_code(report, fail_on_warn=args.fail_on_warn)


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
