# Audit Archive Signing

AgentBridge can export filtered audit records as a signed archive through
`GET /api/v1/audit/export?format=archive`. The archive is canonicalized with stable JSON
ordering before signing, and the response includes `archive_sha256` so verifiers can
bind the signature to the exact exported payload.

## Signer Precedence

The archive signer is selected in this order:

1. `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND`
2. `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_FILE`
3. `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY(_FILE)`

Use the external signing command for production KMS/HSM/Vault integration. Local PEM
private keys are useful for controlled single-node deployments, and HMAC is intended for
low-friction internal integrity checks where shared-secret verification is acceptable.

## External Signing Command

Configure an external signer with:

```bash
export AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND="/usr/local/bin/agentbridge-audit-signer"
export AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND_ALGORITHM="AWS-KMS-RSASSA-PSS-SHA256"
export AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID="arn:aws:kms:region:acct:key/key-id"
export AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_COMMAND_TIMEOUT_SECONDS=10
```

AgentBridge sends the canonical archive JSON bytes to the command on stdin. It also
sets these environment variables for the child process:

- `AGENTBRIDGE_AUDIT_ARCHIVE_SHA256`: SHA-256 hex digest of stdin.
- `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_ALGORITHM`: configured algorithm label.
- `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID`: configured key ID.

The command must return a JSON object on stdout:

```json
{
  "encoding": "base64",
  "value": "<signature>",
  "public_key_sha256": "<optional verifier key digest>",
  "kms_key_version": "<optional key version>",
  "signature_id": "<optional signer request id>",
  "metadata": {
    "provider": "aws-kms"
  }
}
```

`encoding` must be `base64` or `hex`, and `value` must be non-empty. Optional metadata is
included in the archive response so offline verifiers can select the correct public key
or KMS key version.

## Local PEM Signing

Set `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_FILE` to a PEM Ed25519, RSA, or ECDSA
private key. Encrypted keys can be unlocked with
`AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_PASSWORD` or
`AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_PRIVATE_KEY_PASSWORD_FILE`.

AgentBridge signs Ed25519 directly, RSA with RSA-PSS-SHA256, and ECDSA with SHA-256. The
response includes `public_key_sha256`, computed from the DER SubjectPublicKeyInfo, for
offline verifier key selection.

## HMAC Signing

Set `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY` or
`AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_FILE` for HMAC-SHA256 signing. This mode is easy
to verify but does not provide asymmetric non-repudiation.

## Operational Guidance

- Keep signer commands outside the repository and deploy them with the same controls as
  other production secrets tooling.
- Prefer non-exportable KMS/HSM keys for production audit archives.
- Keep `AGENTBRIDGE_AUDIT_ARCHIVE_SIGNING_KEY_ID` stable enough for offline verifiers to
  select the right verification key.
- Log signer request IDs in the signer process and return `signature_id` when the
  provider supplies one.
- Treat signer failures as release-blocking for environments where signed audit export is
  part of the compliance boundary.
