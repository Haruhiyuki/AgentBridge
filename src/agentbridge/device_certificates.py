from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
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
    subject: str
    issuer: str

    def to_payload(self) -> dict[str, object]:
        return {
            "certificate_pem": self.certificate_pem,
            "certificate_fingerprint": self.certificate_fingerprint,
            "ca_certificate_pem": self.ca_certificate_pem,
            "serial_number": self.serial_number,
            "not_before": _datetime_payload(self.not_before),
            "not_after": _datetime_payload(self.not_after),
            "subject": self.subject,
            "issuer": self.issuer,
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
        csr = validated_device_certificate_csr(device_id=device_id, csr_pem=csr_pem)
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
            subject=certificate.subject.rfc4514_string(),
            issuer=certificate.issuer.rfc4514_string(),
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


class ExternalDeviceCertificateIssuer:
    """Device certificate issuer backed by an external KMS/HSM/Vault command."""

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        default_validity_days: int,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.command = command
        self.default_validity_days = default_validity_days
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_command(
        cls,
        *,
        command: str,
        default_validity_days: int,
        timeout_seconds: float = 10.0,
    ) -> ExternalDeviceCertificateIssuer:
        try:
            command_parts = tuple(shlex.split(command))
        except ValueError as exc:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书外部签发命令无效。",
                next_step="请检查 AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND 的引号配置。",
                status_code=503,
            ) from exc
        if not command_parts:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书外部签发命令为空。",
                next_step="请设置 AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND。",
                status_code=503,
            )
        return cls(
            command=command_parts,
            default_validity_days=default_validity_days,
            timeout_seconds=timeout_seconds,
        )

    def issue(
        self,
        *,
        device_id: str,
        csr_pem: str,
        validity_days: int | None = None,
    ) -> IssuedDeviceCertificate:
        csr = validated_device_certificate_csr(device_id=device_id, csr_pem=csr_pem)
        validity = validity_days or self.default_validity_days
        if validity <= 0:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书有效期必须为正整数天。",
                next_step="请提供大于 0 的 validity_days。",
            )
        request_payload = {
            "version": 1,
            "device_id": device_id,
            "csr_pem": csr_pem,
            "validity_days": validity,
            "required_extended_key_usage": "client_auth",
        }
        request_json = json.dumps(
            request_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        env = os.environ.copy()
        env["AGENTBRIDGE_DEVICE_CERT_DEVICE_ID"] = device_id
        env["AGENTBRIDGE_DEVICE_CERT_CSR_SHA256"] = hashlib.sha256(
            csr_pem.encode("utf-8")
        ).hexdigest()
        env["AGENTBRIDGE_DEVICE_CERT_VALIDITY_DAYS"] = str(validity)
        try:
            completed = subprocess.run(
                self.command,
                input=request_json,
                capture_output=True,
                env=env,
                check=False,
                timeout=self.timeout_seconds,
            )
        except OSError as exc:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书外部签发命令无法启动。",
                next_step=(
                    "请检查 AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND "
                    "和命令执行权限。"
                ),
                status_code=503,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书外部签发命令超时。",
                next_step=(
                    "请检查外部 CA/KMS/HSM 服务状态，或增大 "
                    "AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND_TIMEOUT_SECONDS。"
                ),
                status_code=503,
            ) from exc
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书外部签发命令失败。",
                next_step=stderr[:500] or "请检查外部签发器日志和 CA/KMS/HSM 权限。",
                status_code=503,
            )
        try:
            output = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书外部签发命令返回的 JSON 无效。",
                next_step="请返回包含 certificate_pem 和 ca_certificate_pem 的 JSON 对象。",
                status_code=503,
            ) from exc
        if not isinstance(output, dict):
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书外部签发命令返回的 JSON 无效。",
                next_step="请返回 JSON 对象，而不是数组或标量。",
                status_code=503,
            )
        certificate_pem = output.get("certificate_pem")
        ca_certificate_pem = output.get("ca_certificate_pem")
        if not isinstance(certificate_pem, str) or not certificate_pem.strip():
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书外部签发命令未返回证书 PEM。",
                next_step="请在 JSON 响应中返回非空 certificate_pem。",
                status_code=503,
            )
        if not isinstance(ca_certificate_pem, str) or not ca_certificate_pem.strip():
            raise AgentBridgeError(
                ErrorCode.COMMAND_ARGUMENT_INVALID,
                "设备证书外部签发命令未返回 CA 证书 PEM。",
                next_step="请在 JSON 响应中返回非空 ca_certificate_pem。",
                status_code=503,
            )
        certificate = validated_issued_device_certificate(
            device_id=device_id,
            csr=csr,
            certificate_pem=certificate_pem,
            ca_certificate_pem=ca_certificate_pem,
        )
        return IssuedDeviceCertificate(
            certificate_pem=certificate_pem,
            certificate_fingerprint=certificate.fingerprint(hashes.SHA256()).hex(),
            ca_certificate_pem=ca_certificate_pem,
            serial_number=str(certificate.serial_number),
            not_before=certificate.not_valid_before_utc,
            not_after=certificate.not_valid_after_utc,
            subject=certificate.subject.rfc4514_string(),
            issuer=certificate.issuer.rfc4514_string(),
        )


def validated_device_certificate_csr(
    *,
    device_id: str,
    csr_pem: str,
) -> x509.CertificateSigningRequest:
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
    return csr


def validated_issued_device_certificate(
    *,
    device_id: str,
    csr: x509.CertificateSigningRequest,
    certificate_pem: str,
    ca_certificate_pem: str,
) -> x509.Certificate:
    try:
        certificate = x509.load_pem_x509_certificate(certificate_pem.encode("utf-8"))
    except ValueError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书外部签发命令返回的证书 PEM 无效。",
            next_step="请返回有效的 PEM X.509 证书。",
            status_code=503,
        ) from exc
    ca_certificate = validated_external_ca_certificate(
        ca_certificate_pem=ca_certificate_pem
    )
    certificate_common_name = certificate_request_common_name(certificate)
    if certificate_common_name != device_id:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书外部签发结果的 Common Name 必须等于 device_id。",
            next_step="请检查外部签发器是否按 CSR subject 签发设备证书。",
            details={
                "certificate_common_name": certificate_common_name,
                "device_id": device_id,
            },
            status_code=503,
        )
    csr_public_key = csr.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    certificate_public_key = certificate.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    if certificate_public_key != csr_public_key:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书外部签发结果未使用 CSR 公钥。",
            next_step="请检查外部签发器是否按请求 CSR 签发证书。",
            status_code=503,
        )
    try:
        extended_key_usage = certificate.extensions.get_extension_for_class(
            x509.ExtendedKeyUsage
        )
    except x509.ExtensionNotFound as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书外部签发结果缺少 ExtendedKeyUsage。",
            next_step="请签发包含 clientAuth EKU 的设备客户端证书。",
            status_code=503,
        ) from exc
    if ExtendedKeyUsageOID.CLIENT_AUTH not in extended_key_usage.value:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书外部签发结果不是 clientAuth 证书。",
            next_step="请签发包含 clientAuth EKU 的设备客户端证书。",
            status_code=503,
        )
    try:
        certificate.verify_directly_issued_by(ca_certificate)
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书外部签发结果与 CA 证书不匹配。",
            next_step="请确认 ca_certificate_pem 是签发 certificate_pem 的 CA 证书。",
            status_code=503,
        ) from exc
    if certificate.not_valid_after_utc <= datetime.now(UTC):
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书外部签发结果已经过期。",
            next_step="请检查外部签发器的有效期配置和系统时间。",
            status_code=503,
        )
    return certificate


def validated_external_ca_certificate(*, ca_certificate_pem: str) -> x509.Certificate:
    try:
        ca_certificate = x509.load_pem_x509_certificate(
            ca_certificate_pem.encode("utf-8")
        )
    except ValueError as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书外部签发命令返回的 CA 证书 PEM 无效。",
            next_step="请返回有效的 PEM X.509 CA 证书。",
            status_code=503,
        ) from exc
    try:
        basic_constraints = ca_certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        )
    except x509.ExtensionNotFound as exc:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书外部签发命令返回的 CA 证书缺少 BasicConstraints。",
            next_step="请返回 BasicConstraints ca=true 的 CA 证书。",
            status_code=503,
        ) from exc
    if not basic_constraints.value.ca:
        raise AgentBridgeError(
            ErrorCode.COMMAND_ARGUMENT_INVALID,
            "设备证书外部签发命令返回的 CA 证书不是 CA。",
            next_step="请返回 BasicConstraints ca=true 的 CA 证书。",
            status_code=503,
        )
    return ca_certificate


def certificate_request_common_name(
    value: x509.Certificate | x509.CertificateSigningRequest,
) -> str | None:
    attributes = value.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not attributes:
        return None
    return attributes[0].value


def _datetime_payload(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
