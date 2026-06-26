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


def test_acceptance_cli_attach_artifact_copies_and_hashes_file(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    source = tmp_path / "native-session-run.json"
    source.write_text('{"result":"passed"}', encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()

    result = main(
        [
            "attach-artifact",
            str(manifest),
            "34.1",
            str(source),
            "--artifact-root",
            str(artifact_root),
            "--status",
            "passed",
            "--notes",
            "Native PTY acceptance passed.",
        ]
    )
    output = capsys.readouterr().out
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    target = artifact_root / "native_session" / "native-session-run.json"

    assert result == 0
    assert "attached native_session/native-session-run.json to 34.1" in output
    assert f"sha256={digest}" in output
    assert target.read_text(encoding="utf-8") == '{"result":"passed"}'
    assert payload["sections"]["34.1"] == {
        "status": "passed",
        "artifacts": [
            {
                "path": "native_session/native-session-run.json",
                "sha256": digest,
            }
        ],
        "notes": "Native PTY acceptance passed.",
    }

    summary_result = main(
        [
            "summary",
            str(manifest),
            "--verify-artifacts",
            "--artifact-root",
            str(artifact_root),
            "--fail-on-fail",
        ]
    )
    summary_output = capsys.readouterr().out

    assert summary_result == 0
    assert "34.1 status=passed artifacts=1 artifact_errors=0" in summary_output


def test_acceptance_cli_attach_artifact_rejects_target_escape(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    source = tmp_path / "native-session-run.json"
    source.write_text("{}", encoding="utf-8")
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()

    result = main(
        [
            "attach-artifact",
            str(manifest),
            "34.1",
            str(source),
            "--artifact-root",
            str(artifact_root),
            "--name",
            "../escape.json",
        ]
    )

    assert result == 1
    assert "artifact name must stay within artifact root" in capsys.readouterr().err
    assert not (tmp_path / "escape.json").exists()


def test_acceptance_cli_attach_artifact_rejects_root_target_name(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    source = tmp_path / "native-session-run.json"
    source.write_text("{}", encoding="utf-8")
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()

    result = main(
        [
            "attach-artifact",
            str(manifest),
            "34.1",
            str(source),
            "--artifact-root",
            str(artifact_root),
            "--name",
            ".",
        ]
    )

    assert result == 1
    assert "artifact name must identify a file" in capsys.readouterr().err
    assert not artifact_root.exists()


def test_acceptance_cli_attach_artifact_refuses_overwrite_without_force(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    source = tmp_path / "native-session-run.json"
    source.write_text('{"run":1}', encoding="utf-8")
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()
    assert (
        main(
            [
                "attach-artifact",
                str(manifest),
                "34.1",
                str(source),
                "--artifact-root",
                str(artifact_root),
            ]
        )
        == 0
    )
    source.write_text('{"run":2}', encoding="utf-8")
    capsys.readouterr()

    result = main(
        [
            "attach-artifact",
            str(manifest),
            "34.1",
            str(source),
            "--artifact-root",
            str(artifact_root),
        ]
    )

    assert result == 1
    assert "artifact target already exists" in capsys.readouterr().err
    assert (artifact_root / "native_session" / "native-session-run.json").read_text(
        encoding="utf-8"
    ) == '{"run":1}'


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
