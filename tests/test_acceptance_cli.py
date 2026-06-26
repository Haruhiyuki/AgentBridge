import hashlib
import json
import zipfile

from agentbridge.acceptance_cli import (
    ACCEPTANCE_BUNDLE_SCHEMA_VERSION,
    ACCEPTANCE_EXIT_INCOMPLETE,
    ACCEPTANCE_EXIT_INVALID,
    main,
)
from agentbridge.acceptance_evidence import ACCEPTANCE_EVIDENCE_SCHEMA_VERSION

ACCEPTANCE_TEST_SECTIONS = ("34.1", "34.2", "34.3", "34.4", "34.5", "34.6", "34.7", "34.8")


def mark_section_checklist_passed(manifest, section: str) -> None:
    assert (
        main(
            [
                "set-checklist",
                str(manifest),
                section,
                "all",
                "--status",
                "passed",
            ]
        )
        == 0
    )


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
    assert payload["sections"]["34.1"]["checklist"][0]["id"] == "real_pty_claude"
    assert payload["sections"]["34.1"]["checklist"][0]["status"] == "not_run"


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
    assert payload["sections"]["34.1"]["status"] == "passed"
    assert payload["sections"]["34.1"]["artifacts"] == [
        "artifacts/native-session.json"
    ]
    assert payload["sections"]["34.1"]["notes"] == "Native PTY acceptance passed."
    assert payload["sections"]["34.1"]["checklist"][0]["status"] == "not_run"

    summary_result = main(["summary", str(manifest), "--fail-on-warn"])
    output = capsys.readouterr().out

    assert summary_result == ACCEPTANCE_EXIT_INCOMPLETE
    assert "ready=false passed=1 failed=0 blocked=0 not_run=7 missing=0 invalid=0" in output
    assert "34.1 status=passed artifacts=1" in output
    assert "checklist=0/3" in output
    assert "34.8 status=not_run artifacts=0" in output

    checklist_result = main(["summary", str(manifest), "--show-checklist"])
    checklist_output = capsys.readouterr().out

    assert checklist_result == 0
    assert "34.1 checklist id=real_pty_claude status=not_run" in checklist_output
    assert "34.1 checklist id=bot_restart_same_cli status=not_run" in checklist_output


def test_acceptance_cli_summary_json_for_complete_manifest(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()
    for section in ACCEPTANCE_TEST_SECTIONS:
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
        mark_section_checklist_passed(manifest, section)
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
    assert payload["sections"]["34.1"]["status"] == "passed"
    assert payload["sections"]["34.1"]["artifacts"] == [
        {
            "path": "native_session/native-session-run.json",
            "sha256": digest,
        }
    ]
    assert payload["sections"]["34.1"]["notes"] == "Native PTY acceptance passed."
    assert payload["sections"]["34.1"]["checklist"][0]["status"] == "not_run"

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
    for section in ACCEPTANCE_TEST_SECTIONS:
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
        mark_section_checklist_passed(manifest, section)
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


def test_acceptance_cli_bundle_creates_portable_verified_zip(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    bundle_path = tmp_path / "acceptance-bundle.zip"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()
    for section in ACCEPTANCE_TEST_SECTIONS:
        source = tmp_path / f"{section}.json"
        source.write_text(f'{{"section":"{section}"}}', encoding="utf-8")
        assert (
            main(
                [
                    "attach-artifact",
                    str(manifest),
                    section,
                    str(source),
                    "--artifact-root",
                    str(artifact_root),
                    "--status",
                    "passed",
                ]
            )
            == 0
        )
        mark_section_checklist_passed(manifest, section)
    capsys.readouterr()

    result = main(
        [
            "bundle",
            str(manifest),
            str(bundle_path),
            "--artifact-root",
            str(artifact_root),
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "ready=true artifacts=8" in output
    with zipfile.ZipFile(bundle_path) as bundle:
        names = set(bundle.namelist())
        assert "acceptance-evidence.json" in names
        assert "acceptance-bundle.json" in names
        assert "artifacts/native_session/34.1.json" in names
        bundle_index = json.loads(bundle.read("acceptance-bundle.json"))
        manifest_payload = json.loads(bundle.read("acceptance-evidence.json"))
        bundled_native = bundle.read("artifacts/native_session/34.1.json")

    assert bundle_index["schema_version"] == ACCEPTANCE_BUNDLE_SCHEMA_VERSION
    assert bundle_index["summary"]["ready"] is True
    assert len(bundle_index["artifacts"]) == 8
    assert manifest_payload["sections"]["34.1"]["status"] == "passed"
    assert bundled_native == b'{"section":"34.1"}'

    verify_result = main(["verify-bundle", str(bundle_path)])
    verify_output = capsys.readouterr().out

    assert verify_result == 0
    assert "valid=true ready=true artifacts=8 errors=0" in verify_output


def test_acceptance_cli_bundle_refuses_incomplete_evidence_by_default(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    bundle_path = tmp_path / "acceptance-bundle.zip"
    source = tmp_path / "native-session-run.json"
    source.write_text("{}", encoding="utf-8")
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
                "--status",
                "passed",
            ]
        )
        == 0
    )
    capsys.readouterr()

    result = main(
        [
            "bundle",
            str(manifest),
            str(bundle_path),
            "--artifact-root",
            str(artifact_root),
        ]
    )

    assert result == ACCEPTANCE_EXIT_INCOMPLETE
    assert "evidence is not ready" in capsys.readouterr().err
    assert not bundle_path.exists()


def test_acceptance_cli_bundle_allows_draft_incomplete_evidence(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    bundle_path = tmp_path / "acceptance-draft.zip"
    source = tmp_path / "native-session-run.json"
    source.write_text("{}", encoding="utf-8")
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
                "--status",
                "passed",
            ]
        )
        == 0
    )
    capsys.readouterr()

    result = main(
        [
            "bundle",
            str(manifest),
            str(bundle_path),
            "--artifact-root",
            str(artifact_root),
            "--allow-incomplete",
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "ready=false artifacts=1" in output
    with zipfile.ZipFile(bundle_path) as bundle:
        bundle_index = json.loads(bundle.read("acceptance-bundle.json"))
    assert bundle_index["summary"]["ready"] is False
    assert len(bundle_index["artifacts"]) == 1

    draft_verify_result = main(["verify-bundle", str(bundle_path)])
    draft_verify_output = capsys.readouterr().out
    allowed_draft_result = main(["verify-bundle", str(bundle_path), "--allow-incomplete"])
    allowed_draft_output = capsys.readouterr().out

    assert draft_verify_result == ACCEPTANCE_EXIT_INCOMPLETE
    assert "valid=true ready=false artifacts=1 errors=0" in draft_verify_output
    assert allowed_draft_result == 0
    assert "valid=true ready=false artifacts=1 errors=0" in allowed_draft_output


def test_acceptance_cli_verify_bundle_fails_for_tampered_artifact(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    bundle_path = tmp_path / "acceptance-bundle.zip"
    tampered_bundle_path = tmp_path / "acceptance-bundle-tampered.zip"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()
    for section in ACCEPTANCE_TEST_SECTIONS:
        source = tmp_path / f"{section}.json"
        source.write_text(f'{{"section":"{section}"}}', encoding="utf-8")
        assert (
            main(
                [
                    "attach-artifact",
                    str(manifest),
                    section,
                    str(source),
                    "--artifact-root",
                    str(artifact_root),
                    "--status",
                    "passed",
                ]
            )
            == 0
        )
        mark_section_checklist_passed(manifest, section)
    assert (
        main(
            [
                "bundle",
                str(manifest),
                str(bundle_path),
                "--artifact-root",
                str(artifact_root),
            ]
        )
        == 0
    )
    capsys.readouterr()
    with zipfile.ZipFile(bundle_path) as original, zipfile.ZipFile(
        tampered_bundle_path,
        "w",
    ) as tampered:
        for info in original.infolist():
            content = original.read(info.filename)
            if info.filename == "artifacts/native_session/34.1.json":
                content = b'{"section":"34.1","tampered":true}'
            tampered.writestr(info, content)

    result = main(["verify-bundle", str(tampered_bundle_path)])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "valid=false ready=false" in output
    assert "artifact[0]_sha256_mismatch" in output


def test_acceptance_cli_bundle_fails_for_missing_artifact(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    bundle_path = tmp_path / "acceptance-bundle.zip"
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

    result = main(["bundle", str(manifest), str(bundle_path), "--allow-incomplete"])

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "fix artifact verification errors" in capsys.readouterr().err
    assert not bundle_path.exists()


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
