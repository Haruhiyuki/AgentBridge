from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from agentbridge.audit_verify import (
    AuditArchiveVerificationError,
    canonical_json,
    main,
    verify_audit_archive_payload,
)


def signed_archive_payload(
    *,
    algorithm: str,
    key_id: str = "test-key",
    signature: dict[str, object],
) -> dict[str, object]:
    archive = {
        "format": "signed_audit_archive",
        "version": 1,
        "algorithm": algorithm,
        "exported_at": "2026-06-26T00:00:00Z",
        "filters": {"action": "project.created"},
        "record_count": 1,
        "newest_entry_hash": "hash-new",
        "oldest_entry_hash": "hash-old",
        "records": [
            {
                "id": "aud_1",
                "action": "project.created",
                "actor_id": "admin",
                "outcome": "allowed",
                "created_at": "2026-06-26T00:00:00Z",
                "details": {"project_id": "prj_1"},
                "entry_hash": "hash-new",
            }
        ],
    }
    canonical = canonical_json(archive).encode("utf-8")
    return {
        "archive": archive,
        "signature": {
            "algorithm": algorithm,
            "key_id": key_id,
            "archive_sha256": hashlib.sha256(canonical).hexdigest(),
            **signature,
        },
    }


def test_verify_audit_archive_hmac_payload():
    archive = {
        "format": "signed_audit_archive",
        "version": 1,
        "algorithm": "HMAC-SHA256",
        "exported_at": "2026-06-26T00:00:00Z",
        "filters": {},
        "record_count": 0,
        "newest_entry_hash": None,
        "oldest_entry_hash": None,
        "records": [],
    }
    canonical = canonical_json(archive).encode("utf-8")
    payload = {
        "archive": archive,
        "signature": {
            "algorithm": "HMAC-SHA256",
            "key_id": "hmac-key",
            "archive_sha256": hashlib.sha256(canonical).hexdigest(),
            "encoding": "hex",
            "value": hmac.new(b"secret", canonical, hashlib.sha256).hexdigest(),
        },
    }

    result = verify_audit_archive_payload(payload, hmac_key="secret")

    assert result.algorithm == "HMAC-SHA256"
    assert result.key_id == "hmac-key"
    assert result.record_count == 0
    with pytest.raises(AuditArchiveVerificationError, match="HMAC signature"):
        verify_audit_archive_payload(payload, hmac_key="wrong")


def test_verify_audit_archive_external_rsa_pss_payload():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_key_der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    unsigned_payload = signed_archive_payload(
        algorithm="AWS-KMS-RSASSA-PSS-SHA256",
        signature={"encoding": "base64", "value": "placeholder"},
    )
    canonical = canonical_json(unsigned_payload["archive"]).encode("utf-8")
    signature = private_key.sign(
        canonical,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    payload = signed_archive_payload(
        algorithm="AWS-KMS-RSASSA-PSS-SHA256",
        key_id="aws-kms-key",
        signature={
            "encoding": "base64",
            "value": base64.b64encode(signature).decode("ascii"),
            "public_key_sha256": hashlib.sha256(public_key_der).hexdigest(),
        },
    )

    result = verify_audit_archive_payload(payload, public_key_pem=public_key_pem)

    assert result.algorithm == "AWS-KMS-RSASSA-PSS-SHA256"
    assert result.key_id == "aws-kms-key"
    assert result.record_count == 1

    payload["signature"]["public_key_sha256"] = "0" * 64
    with pytest.raises(AuditArchiveVerificationError, match="public_key_sha256"):
        verify_audit_archive_payload(payload, public_key_pem=public_key_pem)


def test_audit_archive_verify_cli_reports_json(tmp_path, capsys):
    archive = {
        "format": "signed_audit_archive",
        "version": 1,
        "algorithm": "HMAC-SHA256",
        "exported_at": "2026-06-26T00:00:00Z",
        "filters": {},
        "record_count": 0,
        "newest_entry_hash": None,
        "oldest_entry_hash": None,
        "records": [],
    }
    canonical = canonical_json(archive).encode("utf-8")
    payload = {
        "archive": archive,
        "signature": {
            "algorithm": "HMAC-SHA256",
            "key_id": "cli-key",
            "archive_sha256": hashlib.sha256(canonical).hexdigest(),
            "encoding": "hex",
            "value": hmac.new(b"secret", canonical, hashlib.sha256).hexdigest(),
        },
    }
    archive_path = tmp_path / "agentbridge-audit-archive.json"
    archive_path.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main([str(archive_path), "--hmac-key", "secret", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["ok"] is True
    assert output["algorithm"] == "HMAC-SHA256"
    assert output["key_id"] == "cli-key"

    failed_exit_code = main([str(archive_path), "--hmac-key", "wrong"])
    error = capsys.readouterr().err

    assert failed_exit_code == 1
    assert "verification failed" in error
