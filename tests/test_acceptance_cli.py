import hashlib
import json

from agentbridge.acceptance_cli import (
    ACCEPTANCE_EXIT_INCOMPLETE,
    ACCEPTANCE_EXIT_INVALID,
    main,
)
from agentbridge.acceptance_evidence import ACCEPTANCE_EVIDENCE_SCHEMA_VERSION


def test_acceptance_cli_init_creates_manifest(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"

    result = main(
        [
            "init",
            str(manifest),
            "--checked-at",
            "2026-06-27T00:00:00Z",
            "--environment",
            "test",
        ]
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert result == 0
    assert "created" in capsys.readouterr().out
    assert payload["schema_version"] == ACCEPTANCE_EVIDENCE_SCHEMA_VERSION
    assert payload["checked_at"] == "2026-06-27T00:00:00Z"
    assert payload["environment"] == "test"
    assert sorted(payload["sections"]) == [
        "34.1",
        "34.2",
        "34.3",
        "34.4",
        "34.5",
        "34.6",
        "34.7",
        "34.8",
    ]


def test_acceptance_cli_init_refuses_to_overwrite_without_force(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    assert main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"]) == 0

    result = main(["init", str(manifest)])

    assert result == 1
    assert "already exists" in capsys.readouterr().err


def test_acceptance_cli_set_section_and_summary(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()

    result = main(
        [
            "set-section",
            str(manifest),
            "34.1",
            "--status",
            "passed",
            "--artifact",
            "artifacts/native-session.json",
            "--notes",
            "Native PTY acceptance passed.",
        ]
    )
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert result == 0
    assert payload["sections"]["34.1"] == {
        "status": "passed",
        "artifacts": ["artifacts/native-session.json"],
        "notes": "Native PTY acceptance passed.",
    }

    summary_result = main(["summary", str(manifest), "--fail-on-warn"])
    output = capsys.readouterr().out

    assert summary_result == ACCEPTANCE_EXIT_INCOMPLETE
    assert "ready=false passed=1 failed=0 blocked=0 not_run=7 missing=0 invalid=0" in output
    assert "34.1 status=passed artifacts=1" in output
    assert "34.8 status=not_run artifacts=0" in output


def test_acceptance_cli_summary_json_for_complete_manifest(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()
    for section in ("34.1", "34.2", "34.3", "34.4", "34.5", "34.6", "34.7", "34.8"):
        assert (
            main(
                [
                    "set-section",
                    str(manifest),
                    section,
                    "--status",
                    "passed",
                    "--artifact",
                    f"artifacts/{section.replace('.', '_')}.json",
                    "--replace-artifacts",
                ]
            )
            == 0
        )
    capsys.readouterr()

    result = main(["summary", str(manifest), "--format", "json", "--fail-on-warn"])
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["summary"]["ready"] is True
    assert output["summary"]["counts"]["passed"] == 8


def test_acceptance_cli_summary_verifies_artifact_hashes(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()
    for section in ("34.1", "34.2", "34.3", "34.4", "34.5", "34.6", "34.7", "34.8"):
        artifact_path = artifact_root / f"{section.replace('.', '_')}.json"
        artifact_path.write_text(f'{{"section":"{section}"}}', encoding="utf-8")
        digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        assert (
            main(
                [
                    "set-section",
                    str(manifest),
                    section,
                    "--status",
                    "passed",
                    "--artifact-sha256",
                    f"{artifact_path.name}={digest}",
                    "--replace-artifacts",
                ]
            )
            == 0
        )
    capsys.readouterr()

    result = main(
        [
            "summary",
            str(manifest),
            "--verify-artifacts",
            "--artifact-root",
            str(artifact_root),
            "--fail-on-warn",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "ready=true passed=8 failed=0 blocked=0 not_run=0 missing=0 invalid=0" in output
    assert "artifact_errors=0" in output


def test_acceptance_cli_summary_fails_for_missing_verified_artifact(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()
    assert (
        main(
            [
                "set-section",
                str(manifest),
                "34.1",
                "--status",
                "passed",
                "--artifact",
                "missing.json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    result = main(
        [
            "summary",
            str(manifest),
            "--verify-artifacts",
            "--artifact-root",
            str(artifact_root),
            "--fail-on-fail",
        ]
    )
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "artifact_errors=1" in output
    assert "34.1 status=passed artifacts=1 artifact_errors=1" in output


def test_acceptance_cli_summary_fails_for_invalid_manifest(tmp_path, capsys):
    manifest = tmp_path / "missing.json"

    result = main(["summary", str(manifest), "--fail-on-fail"])

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "error=read_error:" in capsys.readouterr().out
