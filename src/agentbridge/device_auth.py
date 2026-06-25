from __future__ import annotations

import hashlib
import hmac
import secrets

DEFAULT_DEVICE_KEY_ITERATIONS = 210000


def generate_device_key() -> str:
    return secrets.token_urlsafe(32)


def generate_device_key_salt() -> str:
    return secrets.token_hex(16)


def hash_device_key(
    device_key: str,
    *,
    salt: str,
    iterations: int = DEFAULT_DEVICE_KEY_ITERATIONS,
) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        device_key.encode("utf-8"),
        bytes.fromhex(salt),
        iterations,
    )
    return digest.hex()


def verify_device_key(
    presented_key: str,
    *,
    expected_hash: str,
    salt: str,
    iterations: int = DEFAULT_DEVICE_KEY_ITERATIONS,
) -> bool:
    presented_hash = hash_device_key(
        presented_key,
        salt=salt,
        iterations=iterations,
    )
    return hmac.compare_digest(presented_hash, expected_hash)


def normalize_certificate_fingerprint(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    prefix = "sha256:"
    if normalized.lower().startswith(prefix):
        normalized = normalized[len(prefix) :]
    hex_chars = set("0123456789abcdefABCDEF:")
    if all(char in hex_chars for char in normalized):
        return normalized.replace(":", "").lower()
    return normalized.lower()
