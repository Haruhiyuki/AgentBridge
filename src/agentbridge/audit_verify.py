from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa


class AuditArchiveVerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuditArchiveVerificationResult:
    algorithm: str
    key_id: str
    archive_sha256: str
    record_count: int

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": True,
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "archive_sha256": self.archive_sha256,
            "record_count": self.record_count,
        }


def canonical_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def verify_audit_archive_payload(
    payload: dict[str, Any],
    *,
    hmac_key: str | None = None,
    public_key_pem: bytes | None = None,
    certificate_pem: bytes | None = None,
    signature_kind: str | None = None,
) -> AuditArchiveVerificationResult:
    archive = payload.get("archive")
    signature = payload.get("signature")
    if not isinstance(archive, dict) or not isinstance(signature, dict):
        raise AuditArchiveVerificationError(
            "archive payload must contain archive and signature objects"
        )
    if archive.get("format") != "signed_audit_archive":
        raise AuditArchiveVerificationError("archive format is not signed_audit_archive")
    canonical_archive = canonical_json(archive)
    canonical_bytes = canonical_archive.encode("utf-8")
    archive_sha256 = hashlib.sha256(canonical_bytes).hexdigest()
    if signature.get("archive_sha256") != archive_sha256:
        raise AuditArchiveVerificationError("archive_sha256 does not match canonical archive")
    algorithm = str(signature.get("algorithm") or archive.get("algorithm") or "")
    if archive.get("algorithm") and archive.get("algorithm") != algorithm:
        raise AuditArchiveVerificationError("archive algorithm does not match signature algorithm")
    key_id = str(signature.get("key_id") or "")
    signature_bytes = decoded_signature_bytes(signature)
    if algorithm == "HMAC-SHA256":
        if hmac_key is None:
            raise AuditArchiveVerificationError("HMAC key is required for HMAC-SHA256 archives")
        expected = hmac.new(hmac_key.encode("utf-8"), canonical_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, signature_bytes):
            raise AuditArchiveVerificationError("HMAC signature is invalid")
    else:
        public_key = public_key_from_material(
            public_key_pem=public_key_pem,
            certificate_pem=certificate_pem,
        )
        validate_public_key_digest(public_key, signature)
        verify_asymmetric_signature(
            public_key=public_key,
            algorithm=algorithm,
            signature_kind=signature_kind,
            signature=signature_bytes,
            data=canonical_bytes,
        )
    return AuditArchiveVerificationResult(
        algorithm=algorithm,
        key_id=key_id,
        archive_sha256=archive_sha256,
        record_count=int(archive.get("record_count") or 0),
    )


def decoded_signature_bytes(signature: dict[str, Any]) -> bytes:
    encoding = str(signature.get("encoding") or "")
    value = signature.get("value")
    if not isinstance(value, str) or not value:
        raise AuditArchiveVerificationError("signature value is missing")
    try:
        if encoding == "hex":
            return bytes.fromhex(value)
        if encoding == "base64":
            return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise AuditArchiveVerificationError("signature value cannot be decoded") from exc
    raise AuditArchiveVerificationError("signature encoding must be hex or base64")


def public_key_from_material(
    *,
    public_key_pem: bytes | None,
    certificate_pem: bytes | None,
) -> ed25519.Ed25519PublicKey | rsa.RSAPublicKey | ec.EllipticCurvePublicKey:
    if certificate_pem:
        try:
            certificate = x509.load_pem_x509_certificate(certificate_pem)
        except ValueError as exc:
            raise AuditArchiveVerificationError("certificate PEM is invalid") from exc
        public_key = certificate.public_key()
    elif public_key_pem:
        try:
            public_key = serialization.load_pem_public_key(public_key_pem)
        except (ValueError, UnsupportedAlgorithm) as exc:
            raise AuditArchiveVerificationError("public key PEM is invalid") from exc
    else:
        raise AuditArchiveVerificationError("public key or certificate is required")
    if isinstance(
        public_key,
        (ed25519.Ed25519PublicKey, rsa.RSAPublicKey, ec.EllipticCurvePublicKey),
    ):
        return public_key
    raise AuditArchiveVerificationError("public key type is not supported")


def validate_public_key_digest(
    public_key: ed25519.Ed25519PublicKey | rsa.RSAPublicKey | ec.EllipticCurvePublicKey,
    signature: dict[str, Any],
) -> None:
    expected = signature.get("public_key_sha256")
    if not isinstance(expected, str) or not expected:
        return
    public_key_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    actual = hashlib.sha256(public_key_der).hexdigest()
    if not hmac.compare_digest(actual, expected):
        raise AuditArchiveVerificationError("public_key_sha256 does not match verifier key")


def verify_asymmetric_signature(
    *,
    public_key: ed25519.Ed25519PublicKey | rsa.RSAPublicKey | ec.EllipticCurvePublicKey,
    algorithm: str,
    signature_kind: str | None,
    signature: bytes,
    data: bytes,
) -> None:
    kind = normalized_signature_kind(signature_kind or algorithm)
    try:
        if kind == "ed25519" and isinstance(public_key, ed25519.Ed25519PublicKey):
            public_key.verify(signature, data)
            return
        if kind == "rsa-pss-sha256" and isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(
                signature,
                data,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return
        if kind == "ecdsa-sha256" and isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature, data, ec.ECDSA(hashes.SHA256()))
            return
    except InvalidSignature as exc:
        raise AuditArchiveVerificationError("archive signature is invalid") from exc
    raise AuditArchiveVerificationError(
        f"signature algorithm {algorithm!r} is not supported by the verifier key"
    )


def normalized_signature_kind(value: str) -> str:
    normalized = value.strip().upper().replace("_", "-")
    if "ED25519" in normalized:
        return "ed25519"
    if "PSS" in normalized and "SHA256" in normalized:
        return "rsa-pss-sha256"
    if "RSA-PSS-SHA256" in normalized:
        return "rsa-pss-sha256"
    if "ECDSA" in normalized and "SHA256" in normalized:
        return "ecdsa-sha256"
    raise AuditArchiveVerificationError(f"unsupported signature algorithm {value!r}")


def verify_archive_file(
    archive_path: Path,
    *,
    hmac_key: str | None = None,
    hmac_key_file: Path | None = None,
    public_key_file: Path | None = None,
    certificate_file: Path | None = None,
    signature_kind: str | None = None,
) -> AuditArchiveVerificationResult:
    try:
        payload = json.loads(archive_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditArchiveVerificationError("archive file is not readable JSON") from exc
    if not isinstance(payload, dict):
        raise AuditArchiveVerificationError("archive file must contain a JSON object")
    effective_hmac_key = hmac_key
    if hmac_key_file is not None:
        effective_hmac_key = hmac_key_file.read_text(encoding="utf-8").strip()
    public_key_pem = public_key_file.read_bytes() if public_key_file else None
    certificate_pem = certificate_file.read_bytes() if certificate_file else None
    return verify_audit_archive_payload(
        payload,
        hmac_key=effective_hmac_key,
        public_key_pem=public_key_pem,
        certificate_pem=certificate_pem,
        signature_kind=signature_kind,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify an AgentBridge signed audit archive.")
    parser.add_argument("archive", type=Path, help="Path to agentbridge-audit-archive.json")
    parser.add_argument("--hmac-key", help="HMAC-SHA256 verification key")
    parser.add_argument("--hmac-key-file", type=Path, help="File containing the HMAC key")
    parser.add_argument(
        "--public-key-file",
        type=Path,
        help="PEM public key for asymmetric signatures",
    )
    parser.add_argument(
        "--certificate-file",
        type=Path,
        help="PEM certificate containing the verifier public key",
    )
    parser.add_argument(
        "--signature-kind",
        choices=["ed25519", "rsa-pss-sha256", "ecdsa-sha256"],
        help="Verifier algorithm override for external signer algorithm labels",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON result")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = verify_archive_file(
            args.archive,
            hmac_key=args.hmac_key,
            hmac_key_file=args.hmac_key_file,
            public_key_file=args.public_key_file,
            certificate_file=args.certificate_file,
            signature_kind=args.signature_kind,
        )
    except (OSError, AuditArchiveVerificationError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result.to_payload(), ensure_ascii=False, sort_keys=True))
    else:
        print(
            "OK "
            f"algorithm={result.algorithm} "
            f"key_id={result.key_id} "
            f"archive_sha256={result.archive_sha256} "
            f"record_count={result.record_count}"
        )
    return 0


def run() -> None:
    raise SystemExit(main())
