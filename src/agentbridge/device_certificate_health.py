from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentbridge.device_auth import normalize_certificate_fingerprint
from agentbridge.domain import (
    DeviceCertificateRecord,
    DeviceIdentity,
    DeviceIdentityStatus,
    utc_now,
)

DEFAULT_CERTIFICATE_EXPIRY_WARNING_DAYS = 14


def device_identity_certificate_health(
    identity: DeviceIdentity,
    *,
    now: datetime | None = None,
    warning_days: int = DEFAULT_CERTIFICATE_EXPIRY_WARNING_DAYS,
) -> dict[str, object]:
    current_time = aware_utc(now or utc_now())
    warning_window_days = max(1, warning_days)
    warning_cutoff = current_time + timedelta(days=warning_window_days)
    active_fingerprints = active_certificate_fingerprints(identity)
    active_records = active_certificate_records_by_fingerprint(identity)
    managed_ca_records = {
        fingerprint: record
        for fingerprint, record in active_records.items()
        if record.source == "managed_ca"
    }
    expiries = [
        aware_utc(record.not_after)
        for record in active_records.values()
        if record.not_after is not None
    ]
    expired_fingerprints = sorted(
        fingerprint
        for fingerprint, record in active_records.items()
        if record.not_after is not None
        and aware_utc(record.not_after) <= current_time
    )
    expiring_fingerprints = sorted(
        fingerprint
        for fingerprint, record in active_records.items()
        if record.not_after is not None
        and current_time < aware_utc(record.not_after) <= warning_cutoff
    )
    untracked_count = len(active_fingerprints.difference(active_records))
    missing_validity_count = sum(
        1 for record in active_records.values() if record.not_after is None
    )
    managed_ca_missing_validity_count = sum(
        1 for record in managed_ca_records.values() if record.not_after is None
    )
    renewal_overdue_fingerprints = sorted(
        fingerprint
        for fingerprint, record in managed_ca_records.items()
        if record.not_after is not None
        and aware_utc(record.not_after) <= current_time
    )
    renewal_due_fingerprints = sorted(
        fingerprint
        for fingerprint, record in managed_ca_records.items()
        if record.not_after is not None
        and current_time < aware_utc(record.not_after) <= warning_cutoff
    )
    renewal_due_times = [
        aware_utc(record.not_after) - timedelta(days=warning_window_days)
        for record in managed_ca_records.values()
        if record.not_after is not None
    ]
    if identity.status == DeviceIdentityStatus.REVOKED:
        status = "revoked"
    elif not active_fingerprints:
        status = "none"
    elif expired_fingerprints:
        status = "expired"
    elif expiring_fingerprints:
        status = "expiring"
    elif untracked_count or missing_validity_count:
        status = "unknown"
    else:
        status = "ok"
    if identity.status == DeviceIdentityStatus.REVOKED:
        renewal_status = "revoked"
    elif not active_fingerprints:
        renewal_status = "none"
    elif not managed_ca_records:
        renewal_status = "not_applicable"
    elif renewal_overdue_fingerprints:
        renewal_status = "overdue"
    elif renewal_due_fingerprints:
        renewal_status = "due"
    elif managed_ca_missing_validity_count:
        renewal_status = "unknown"
    else:
        renewal_status = "scheduled"
    next_expires_at = min(expiries) if expiries else None
    renewal_due_at = min(renewal_due_times) if renewal_due_times else None
    return {
        "status": status,
        "warning_days": warning_window_days,
        "active_certificate_count": len(active_fingerprints),
        "tracked_certificate_count": len(active_records),
        "untracked_certificate_count": untracked_count,
        "missing_validity_count": missing_validity_count,
        "expired_count": len(expired_fingerprints),
        "expiring_count": len(expiring_fingerprints),
        "expired_fingerprints": expired_fingerprints,
        "expiring_fingerprints": expiring_fingerprints,
        "next_expires_at": (
            datetime_payload(next_expires_at) if next_expires_at else None
        ),
        "managed_ca_active_certificate_count": len(managed_ca_records),
        "managed_ca_missing_validity_count": managed_ca_missing_validity_count,
        "renewal_status": renewal_status,
        "renewal_due_count": len(renewal_due_fingerprints),
        "renewal_overdue_count": len(renewal_overdue_fingerprints),
        "renewal_due_fingerprints": renewal_due_fingerprints,
        "renewal_overdue_fingerprints": renewal_overdue_fingerprints,
        "renewal_due_at": (
            datetime_payload(renewal_due_at) if renewal_due_at else None
        ),
    }


def managed_device_certificate_active(
    identity: DeviceIdentity,
    presented_fingerprint: str,
    *,
    now: datetime | None = None,
) -> bool:
    fingerprint = normalize_certificate_fingerprint(presented_fingerprint)
    active_fingerprints = active_certificate_fingerprints(identity)
    if not fingerprint or fingerprint not in active_fingerprints:
        return False
    record = active_certificate_records_by_fingerprint(identity).get(fingerprint)
    if record is None or record.not_after is None:
        return True
    return aware_utc(record.not_after) > aware_utc(now or utc_now())


def active_certificate_records_by_fingerprint(
    identity: DeviceIdentity,
) -> dict[str, DeviceCertificateRecord]:
    active_fingerprints = active_certificate_fingerprints(identity)
    records: dict[str, DeviceCertificateRecord] = {}
    for record in identity.certificate_records:
        if record.fingerprint in active_fingerprints and record.removed_at is None:
            records[record.fingerprint] = record
    return records


def active_certificate_fingerprints(identity: DeviceIdentity) -> set[str]:
    return {
        fingerprint
        for value in identity.certificate_fingerprints
        if (fingerprint := normalize_certificate_fingerprint(value))
    }


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def datetime_payload(value: datetime) -> str:
    return aware_utc(value).isoformat().replace("+00:00", "Z")
