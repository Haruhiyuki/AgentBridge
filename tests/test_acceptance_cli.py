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


def append_unknown_checklist_item(manifest, section: str = "34.1") -> None:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sections"][section]["checklist"].append(
        {
            "id": "unexpected_manual_item",
            "status": "passed",
            "notes": "This item is not part of the design acceptance checklist.",
        }
    )
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def create_complete_acceptance_bundle(tmp_path):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    bundle_path = tmp_path / "acceptance-bundle.zip"
    assert main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"]) == 0
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
    return manifest, artifact_root, bundle_path


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


def test_acceptance_cli_summary_fails_for_invalid_checklist_item(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()
    append_unknown_checklist_item(manifest)

    result = main(["summary", str(manifest), "--show-checklist", "--fail-on-fail"])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "checklist_errors=1" in output
    assert (
        "34.1 checklist id=unexpected_manual_item status=unknown "
        "expected=false status_valid=false"
    ) in output


def test_acceptance_cli_summary_fails_for_duplicate_section_artifact_path(
    tmp_path,
    capsys,
):
    manifest = tmp_path / "acceptance-evidence.json"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    assert (
        main(
            [
                "set-section",
                str(manifest),
                "34.1",
                "--status",
                "passed",
                "--artifact",
                "artifacts/native-session.json",
                "--artifact",
                "artifacts/native-session.json",
            ]
        )
        == 0
    )
    mark_section_checklist_passed(manifest, "34.1")
    capsys.readouterr()

    result = main(["summary", str(manifest), "--fail-on-fail"])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "artifact_errors=1" in output
    assert "34.1 status=passed artifacts=2 artifact_errors=1" in output


def test_acceptance_cli_summary_fails_for_invalid_artifact_sha256(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    assert (
        main(
            [
                "set-section",
                str(manifest),
                "34.1",
                "--status",
                "passed",
                "--artifact-sha256",
                "artifacts/native-session.json=not-a-sha256",
            ]
        )
        == 0
    )
    mark_section_checklist_passed(manifest, "34.1")
    capsys.readouterr()

    result = main(["summary", str(manifest), "--fail-on-fail"])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "artifact_errors=1" in output
    assert "34.1 status=passed artifacts=1 artifact_errors=1" in output


def test_acceptance_cli_summary_fails_for_unsafe_artifact_path(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    assert (
        main(
            [
                "set-section",
                str(manifest),
                "34.1",
                "--status",
                "passed",
                "--artifact",
                "../native-session.json",
            ]
        )
        == 0
    )
    mark_section_checklist_passed(manifest, "34.1")
    capsys.readouterr()

    result = main(["summary", str(manifest), "--fail-on-fail"])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "artifact_errors=1" in output
    assert "34.1 status=passed artifacts=1 artifact_errors=1" in output


def test_acceptance_cli_summary_fails_for_non_list_artifacts(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sections"]["34.1"]["status"] = "passed"
    payload["sections"]["34.1"]["artifacts"] = "artifacts/native-session.json"
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    mark_section_checklist_passed(manifest, "34.1")
    capsys.readouterr()

    result = main(["summary", str(manifest), "--fail-on-fail"])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "artifact_errors=1" in output
    assert "34.1 status=passed artifacts=1 artifact_errors=1" in output


def test_acceptance_cli_summary_fails_for_unknown_manifest_section(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sections"]["34.9"] = {
        "status": "passed",
        "artifacts": [],
        "checklist": [],
    }
    manifest.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    capsys.readouterr()

    result = main(["summary", str(manifest), "--fail-on-fail"])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "ready=false" in output
    assert "error=unknown_sections:34.9" in output


def test_acceptance_cli_attaches_system_health_export_with_evidence_summary(
    tmp_path,
    capsys,
):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    export_path = tmp_path / "system-health-export.json"
    export_path.write_text(
        json.dumps(
            {
                "schema_version": "agentbridge.admin_system_health_export.v1",
                "status": "1 endpoint checks failed",
                "endpoints": [],
                "readiness_actions": [
                    {
                        "status": "warn",
                        "category": "acceptance",
                        "id": "acceptance.native_session",
                        "summary": "Design-document section 34.1 is signed off.",
                        "next_step": "Mark every checklist item passed.",
                        "evidence_summary": (
                            "section=34.1 status=passed artifacts=1 "
                            "artifact_errors=0 checklist=1/3 "
                            "checklist_incomplete=2 checklist_errors=0"
                        ),
                        "evidence": {
                            "section": "34.1",
                            "status": "passed",
                            "artifact_count": 1,
                            "checklist_total": 3,
                            "checklist_passed_count": 1,
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()

    result = main(
        [
            "attach-admin-export",
            str(manifest),
            "34.1",
            str(export_path),
            "--artifact-root",
            str(artifact_root),
            "--status",
            "passed",
        ]
    )

    assert result == 0
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["sections"]["34.1"]["artifacts"][0]["path"] == (
        "native_session/admin-system-health.json"
    )
    assert (artifact_root / "native_session" / "admin-system-health.json").is_file()


def test_acceptance_cli_rejects_system_health_export_missing_acceptance_summary(
    tmp_path,
    capsys,
):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    export_path = tmp_path / "system-health-export.json"
    export_path.write_text(
        json.dumps(
            {
                "schema_version": "agentbridge.admin_system_health_export.v1",
                "status": "1 endpoint checks failed",
                "endpoints": [],
                "readiness_actions": [
                    {
                        "status": "warn",
                        "category": "acceptance",
                        "id": "acceptance.native_session",
                        "summary": "Design-document section 34.1 is signed off.",
                        "next_step": "Mark every checklist item passed.",
                        "evidence": {
                            "section": "34.1",
                            "status": "passed",
                            "artifact_count": 1,
                        },
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()

    result = main(
        [
            "attach-admin-export",
            str(manifest),
            "34.1",
            str(export_path),
            "--artifact-root",
            str(artifact_root),
            "--status",
            "passed",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert result == 1
    assert "must include evidence_summary" in captured.err
    assert payload["sections"]["34.1"]["status"] == "not_run"
    assert payload["sections"]["34.1"]["artifacts"] == []
    assert not artifact_root.exists()


def test_acceptance_cli_rejects_incomplete_bot_delivery_admin_export(
    tmp_path,
    capsys,
):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    export_path = tmp_path / "bot-delivery-export.json"
    export_path.write_text(
        json.dumps(
            {
                "schema_version": "agentbridge.admin_bot_delivery_export.v1",
                "records": [],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    main(["init", str(manifest), "--checked-at", "2026-06-27T00:00:00Z"])
    capsys.readouterr()

    result = main(
        [
            "attach-admin-export",
            str(manifest),
            "34.3",
            str(export_path),
            "--artifact-root",
            str(artifact_root),
            "--status",
            "passed",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert result == 1
    assert "bot delivery admin export record_count" in captured.err
    assert payload["sections"]["34.3"]["status"] == "not_run"
    assert payload["sections"]["34.3"]["artifacts"] == []
    assert not artifact_root.exists()


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
    assert "artifact_root" not in bundle_index
    assert bundle_index["summary"]["ready"] is True
    assert len(bundle_index["artifacts"]) == 8
    assert manifest_payload["sections"]["34.1"]["status"] == "passed"
    assert bundled_native == b'{"section":"34.1"}'

    verify_result = main(["verify-bundle", str(bundle_path)])
    verify_output = capsys.readouterr().out
    verify_json_result = main(["verify-bundle", str(bundle_path), "--format", "json"])
    verify_json_output = json.loads(capsys.readouterr().out)

    assert verify_result == 0
    assert "valid=true ready=true artifacts=8 errors=0" in verify_output
    assert "checklist_incomplete=0" in verify_output
    assert verify_json_result == 0
    assert verify_json_output["summary"]["ready"] is True
    assert verify_json_output["summary"]["checklist_incomplete_count"] == 0


def test_acceptance_cli_verify_bundle_rejects_tampered_summary(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    bundle_path = tmp_path / "acceptance-bundle.zip"
    tampered_bundle_path = tmp_path / "acceptance-bundle-summary-tampered.zip"
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

    with (
        zipfile.ZipFile(bundle_path) as source_bundle,
        zipfile.ZipFile(
            tampered_bundle_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as target_bundle,
    ):
        for entry in source_bundle.infolist():
            entry_bytes = source_bundle.read(entry.filename)
            if entry.filename == "acceptance-bundle.json":
                bundle_index = json.loads(entry_bytes)
                bundle_index["summary"]["counts"]["passed"] = 7
                entry_bytes = (
                    json.dumps(bundle_index, ensure_ascii=False, sort_keys=True) + "\n"
                ).encode("utf-8")
            target_bundle.writestr(entry.filename, entry_bytes)

    result = main(["verify-bundle", str(tampered_bundle_path)])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "valid=false ready=false artifacts=8 errors=1" in output
    assert "error=bundle_summary_count_passed_mismatch" in output


def test_acceptance_cli_verify_bundle_rejects_unindexed_zip_entry(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    bundle_path = tmp_path / "acceptance-bundle.zip"
    tampered_bundle_path = tmp_path / "acceptance-bundle-extra-entry.zip"
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
    with zipfile.ZipFile(bundle_path) as source_bundle, zipfile.ZipFile(
        tampered_bundle_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as target_bundle:
        for entry in source_bundle.infolist():
            target_bundle.writestr(entry.filename, source_bundle.read(entry.filename))
        target_bundle.writestr("notes/local-paths.txt", b"/tmp/agentbridge")

    result = main(["verify-bundle", str(tampered_bundle_path)])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "valid=false ready=false artifacts=8 errors=1" in output
    assert "error=unexpected_bundle_entry:notes/local-paths.txt" in output


def test_acceptance_cli_verify_bundle_rejects_artifact_section_mismatch(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    bundle_path = tmp_path / "acceptance-bundle.zip"
    tampered_bundle_path = tmp_path / "acceptance-bundle-section-tampered.zip"
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
    with (
        zipfile.ZipFile(bundle_path) as source_bundle,
        zipfile.ZipFile(
            tampered_bundle_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as target_bundle,
    ):
        for entry in source_bundle.infolist():
            entry_bytes = source_bundle.read(entry.filename)
            if entry.filename == "acceptance-bundle.json":
                bundle_index = json.loads(entry_bytes)
                for artifact in bundle_index["artifacts"]:
                    if artifact["section"] == "34.1":
                        artifact["section"] = "34.2"
                        break
                entry_bytes = (
                    json.dumps(bundle_index, ensure_ascii=False, sort_keys=True) + "\n"
                ).encode("utf-8")
            target_bundle.writestr(entry.filename, entry_bytes)

    result = main(["verify-bundle", str(tampered_bundle_path)])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "valid=false ready=false artifacts=8 errors=1" in output
    assert "error=bundle_artifact_section_mismatch:native_session/34.1.json" in output


def test_acceptance_cli_verify_bundle_rejects_unknown_manifest_section(tmp_path, capsys):
    manifest = tmp_path / "acceptance-evidence.json"
    artifact_root = tmp_path / "artifacts"
    bundle_path = tmp_path / "acceptance-bundle.zip"
    tampered_bundle_path = tmp_path / "acceptance-bundle-unknown-section.zip"
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
    with (
        zipfile.ZipFile(bundle_path) as source_bundle,
        zipfile.ZipFile(
            tampered_bundle_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as target_bundle,
    ):
        manifest_payload = json.loads(source_bundle.read("acceptance-evidence.json"))
        manifest_payload["sections"]["34.9"] = {
            "status": "passed",
            "artifacts": [],
            "checklist": [],
        }
        tampered_manifest_bytes = (
            json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        bundle_index = json.loads(source_bundle.read("acceptance-bundle.json"))
        bundle_index["manifest_sha256"] = hashlib.sha256(
            tampered_manifest_bytes
        ).hexdigest()
        tampered_index_bytes = (
            json.dumps(bundle_index, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        for entry in source_bundle.infolist():
            if entry.filename == "acceptance-evidence.json":
                entry_bytes = tampered_manifest_bytes
            elif entry.filename == "acceptance-bundle.json":
                entry_bytes = tampered_index_bytes
            else:
                entry_bytes = source_bundle.read(entry.filename)
            target_bundle.writestr(entry.filename, entry_bytes)

    result = main(["verify-bundle", str(tampered_bundle_path)])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "valid=false ready=false artifacts=8 errors=1" in output
    assert "error=manifest_sections_unknown:34.9" in output


def test_acceptance_cli_verify_bundle_rejects_duplicate_artifact_path(tmp_path, capsys):
    _manifest, _artifact_root, bundle_path = create_complete_acceptance_bundle(tmp_path)
    tampered_bundle_path = tmp_path / "acceptance-bundle-duplicate-path.zip"
    capsys.readouterr()
    with (
        zipfile.ZipFile(bundle_path) as source_bundle,
        zipfile.ZipFile(
            tampered_bundle_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as target_bundle,
    ):
        bundle_index = json.loads(source_bundle.read("acceptance-bundle.json"))
        native_artifact = next(
            artifact
            for artifact in bundle_index["artifacts"]
            if artifact["section"] == "34.1"
        )
        duplicate_artifact = {
            **native_artifact,
            "archive_path": "artifacts/native_session/34.1-copy.json",
        }
        bundle_index["artifacts"].append(duplicate_artifact)
        tampered_index_bytes = (
            json.dumps(bundle_index, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        for entry in source_bundle.infolist():
            entry_bytes = (
                tampered_index_bytes
                if entry.filename == "acceptance-bundle.json"
                else source_bundle.read(entry.filename)
            )
            target_bundle.writestr(entry.filename, entry_bytes)
        target_bundle.writestr(
            duplicate_artifact["archive_path"],
            source_bundle.read(native_artifact["archive_path"]),
        )

    result = main(["verify-bundle", str(tampered_bundle_path)])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "valid=false ready=false artifacts=8 errors=1" in output
    assert "error=artifact[8]_path_duplicate" in output


def test_acceptance_cli_verify_bundle_rejects_duplicate_artifact_archive_path(
    tmp_path,
    capsys,
):
    _manifest, _artifact_root, bundle_path = create_complete_acceptance_bundle(tmp_path)
    tampered_bundle_path = tmp_path / "acceptance-bundle-duplicate-archive-path.zip"
    capsys.readouterr()
    with (
        zipfile.ZipFile(bundle_path) as source_bundle,
        zipfile.ZipFile(
            tampered_bundle_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as target_bundle,
    ):
        bundle_index = json.loads(source_bundle.read("acceptance-bundle.json"))
        native_artifact = next(
            artifact
            for artifact in bundle_index["artifacts"]
            if artifact["section"] == "34.1"
        )
        duplicate_artifact = {
            **native_artifact,
            "path": "native_session/34.1-copy.json",
        }
        bundle_index["artifacts"].append(duplicate_artifact)
        tampered_index_bytes = (
            json.dumps(bundle_index, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        for entry in source_bundle.infolist():
            entry_bytes = (
                tampered_index_bytes
                if entry.filename == "acceptance-bundle.json"
                else source_bundle.read(entry.filename)
            )
            target_bundle.writestr(entry.filename, entry_bytes)

    result = main(["verify-bundle", str(tampered_bundle_path)])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "valid=false ready=false artifacts=8 errors=1" in output
    assert "error=artifact[8]_archive_path_duplicate" in output


def test_acceptance_cli_verify_bundle_rejects_invalid_artifact_sha256(tmp_path, capsys):
    _manifest, _artifact_root, bundle_path = create_complete_acceptance_bundle(tmp_path)
    tampered_bundle_path = tmp_path / "acceptance-bundle-invalid-sha256.zip"
    capsys.readouterr()
    with (
        zipfile.ZipFile(bundle_path) as source_bundle,
        zipfile.ZipFile(
            tampered_bundle_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as target_bundle,
    ):
        bundle_index = json.loads(source_bundle.read("acceptance-bundle.json"))
        bundle_index["artifacts"][0]["sha256"] = "not-a-sha256"
        tampered_index_bytes = (
            json.dumps(bundle_index, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        for entry in source_bundle.infolist():
            entry_bytes = (
                tampered_index_bytes
                if entry.filename == "acceptance-bundle.json"
                else source_bundle.read(entry.filename)
            )
            target_bundle.writestr(entry.filename, entry_bytes)

    result = main(["verify-bundle", str(tampered_bundle_path)])
    output = capsys.readouterr().out

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "valid=false ready=false artifacts=7 errors=3" in output
    assert "error=artifact[0]_sha256_invalid" in output
    assert "error=section_34.1_artifact_missing_from_bundle" in output
    assert "error=bundle_summary_ready_mismatch" in output


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
    assert "checklist_incomplete=24" in draft_verify_output
    assert allowed_draft_result == 0
    assert "valid=true ready=false artifacts=1 errors=0" in allowed_draft_output
    assert "checklist_incomplete=24" in allowed_draft_output


def test_acceptance_cli_bundle_rejects_invalid_checklist_even_for_draft(tmp_path, capsys):
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
    append_unknown_checklist_item(manifest)
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

    assert result == ACCEPTANCE_EXIT_INVALID
    assert "fix invalid checklist entries" in capsys.readouterr().err
    assert not bundle_path.exists()


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
