from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from agentbridge.domain import AgentBridgeError, ErrorCode


@dataclass(frozen=True)
class IssuedDeviceCertificate:
    certificate_pem: str
    certificate_fingerprint: str
    ca_certificate_pem: str
    serial_number: str
    not_before: datetime
    not_after: datetime

    def to_payload(self) -> dict[str, object]:
        return {
            "certificate_pem": self.certificate_pem,
            "certificate_fingerprint": self.certificate_fingerprint,
            "ca_certificate_pem": self.ca_certificate_pem,
            "serial_number": self.serial_number,
            "not_before": self.not_before.isoformat(),
            "not_after": self.not_after.isoformat(),
        }


class DeviceCertificateIssuer:
    def __init__(
        self,
        *,
        ca_certificate: x509.Certificate,
        ca_private_key: Any,
        ca_certificate_pem: str,
        default_validity_days: int,
    ) -> None:
        self.ca_certificate = ca_certificate
        self.ca_private_key = ca_private_key
        self.ca_certificate_pem = ca_certificate_pem
        self.default_validity_days = default_validity_days
        self._validate_ca_certificate()

    @classmethod
    def from_files(
        cls,
        *,
        ca_certificate_path: Path,
        ca_private_key_path: Path,
        ca_private_key_password: str | None,
        default_validity_days: int,
    ) -> DeviceCertificateIssuer:
        try:
            ca_certificate_pem = ca_certificate_path.read_text(encoding="utf-8")
            ca_certificate = x509.load_pem_x509_certificate(
                ca_certificate_pem.encode("utf-8")
            )
            ca_private_key = serialization.load_pem_private_key(
                ca_private_key_path.read_bytes(),
                password=(
                    ca_private_key_password.encode("utf-8")
                    if ca_private_key_password
                    else None
                ),
            )
        except (OSError, ValueError) as exc:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书 CA 配置不可用。",
                next_step=(
                    "请检查 AGENTBRIDGE_DEVICE_CERT_CA_CERT_FILE、"
                    "AGENTBRIDGE_DEVICE_CERT_CA_KEY_FILE 和私钥密码配置。"
                ),
                status_code=503,
            ) from exc
        return cls(
            ca_certificate=ca_certificate,
            ca_private_key=ca_private_key,
            ca_certificate_pem=ca_certificate_pem,
            default_validity_days=default_validity_days,
        )

    def issue(
        self,
        *,
        device_id: str,
        csr_pem: str,
        validity_days: int | None = None,
    ) -> IssuedDeviceCertificate:
        try:
            csr = x509.load_pem_x509_csr(csr_pem.encode("utf-8"))
        except ValueError as exc:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书 CSR 不是有效的 PEM X.509 CSR。",
                next_step="请用设备私钥重新生成 PEM CSR 后重试。",
            ) from exc
        if not csr.is_signature_valid:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书 CSR 签名无效。",
                next_step="请确认 CSR 使用对应的设备私钥签名。",
            )
        csr_common_name = certificate_request_common_name(csr)
        if csr_common_name != device_id:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书 CSR 的 Common Name 必须等于 device_id。",
                next_step=(
                    "请使用 CN 与目标 device_id 完全一致的 CSR，"
                    f"当前 device_id 为 {device_id}。"
                ),
                details={"csr_common_name": csr_common_name, "device_id": device_id},
            )
        validity = validity_days or self.default_validity_days
        if validity <= 0:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书有效期必须为正整数天。",
                next_step="请提供大于 0 的 validity_days。",
            )
        not_before = datetime.now(UTC) - timedelta(minutes=5)
        not_after = datetime.now(UTC) + timedelta(days=validity)
        builder = (
            x509.CertificateBuilder()
            .subject_name(csr.subject)
            .issuer_name(self.ca_certificate.subject)
            .public_key(csr.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                False,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(csr.public_key()),
                False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    self.ca_certificate.public_key()
                ),
                False,
            )
        )
        try:
            subject_alt_name = csr.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            )
        except x509.ExtensionNotFound:
            subject_alt_name = None
        if subject_alt_name is not None:
            builder = builder.add_extension(
                subject_alt_name.value,
                subject_alt_name.critical,
            )
        certificate = builder.sign(
            private_key=self.ca_private_key,
            algorithm=hashes.SHA256(),
        )
        certificate_pem = certificate.public_bytes(
            serialization.Encoding.PEM
        ).decode("utf-8")
        return IssuedDeviceCertificate(
            certificate_pem=certificate_pem,
            certificate_fingerprint=certificate.fingerprint(hashes.SHA256()).hex(),
            ca_certificate_pem=self.ca_certificate_pem,
            serial_number=str(certificate.serial_number),
            not_before=not_before,
            not_after=not_after,
        )

    def _validate_ca_certificate(self) -> None:
        ca_public_key = self.ca_certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        private_public_key = self.ca_private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if ca_public_key != private_public_key:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书 CA 私钥与 CA 证书不匹配。",
                next_step="请配置同一 CA 证书对应的私钥。",
                status_code=503,
            )
        try:
            basic_constraints = self.ca_certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            )
        except x509.ExtensionNotFound as exc:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书 CA 证书缺少 BasicConstraints。",
                next_step="请配置 ca=true 的本地 CA 证书。",
                status_code=503,
            ) from exc
        if not basic_constraints.value.ca:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书 CA 证书不是 CA 证书。",
                next_step="请配置 BasicConstraints ca=true 的本地 CA 证书。",
                status_code=503,
            )


def certificate_request_common_name(csr: x509.CertificateSigningRequest) -> str | None:
    attributes = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not attributes:
        return None
    return attributes[0].value
