# Device Certificate Operations

AgentBridge managed device certificates are client-auth certificates issued from a
configured local CA certificate and private key. They are intended to be verified by a
trusted TLS-terminating proxy, which forwards only the verified SHA-256 client
certificate fingerprint to AgentBridge.

## Configuration

Enable CSR-based managed certificate issuance and renewal with an external issuer
command when production policy requires KMS/HSM/Vault or offline CA custody:

```bash
export AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND="/usr/local/bin/agentbridge-device-cert-issuer"
export AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND_TIMEOUT_SECONDS=10
export AGENTBRIDGE_DEVICE_CERT_DEFAULT_VALIDITY_DAYS=30
export AGENTBRIDGE_DEVICE_CERT_EXPIRY_WARNING_DAYS=14
```

If a local encrypted PEM issuing key is acceptable for the environment, configure:

```bash
export AGENTBRIDGE_DEVICE_CERT_CA_CERT_FILE=/etc/agentbridge/device-ca.crt
export AGENTBRIDGE_DEVICE_CERT_CA_KEY_FILE=/etc/agentbridge/device-ca.key
export AGENTBRIDGE_DEVICE_CERT_CA_KEY_PASSWORD_FILE=/etc/agentbridge/device-ca.pass
export AGENTBRIDGE_DEVICE_CERT_DEFAULT_VALIDITY_DAYS=30
export AGENTBRIDGE_DEVICE_CERT_EXPIRY_WARNING_DAYS=14
```

Enable periodic renewal/expiry scans with:

```bash
export AGENTBRIDGE_DEVICE_CERT_SCAN_WORKER_ENABLED=true
export AGENTBRIDGE_DEVICE_CERT_SCAN_INTERVAL_SECONDS=3600
export AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_CHAT_CONTEXT_IDS=ops-alerts
export AGENTBRIDGE_DEVICE_CERT_SCAN_NOTIFY_PLATFORM=onebot.v11
```

`AGENTBRIDGE_DEVICE_CERT_EXPIRY_WARNING_DAYS` is also the renewal planning window.
For active CA-issued certificates, `certificate_health.renewal_due_at` is calculated as
`not_after - warning_days`. The scan worker reports devices as:

- `renewal_status=scheduled`: the next managed CA certificate is outside the renewal
  window.
- `renewal_status=due`: at least one active managed CA certificate is inside the renewal
  window but not expired.
- `renewal_status=overdue`: at least one active managed CA certificate is already
  expired.
- `renewal_status=unknown`: an active managed CA record is missing validity metadata.

Treat `due`, `overdue`, and `unknown` as renewal action-required states.

## External Issuer Command

When `AGENTBRIDGE_DEVICE_CERT_ISSUER_COMMAND` is configured, AgentBridge validates the
CSR signature and Common Name first, then sends a JSON request to the command on stdin:

```json
{
  "version": 1,
  "device_id": "build-agent-1",
  "csr_pem": "-----BEGIN CERTIFICATE REQUEST-----\n...",
  "validity_days": 30,
  "required_extended_key_usage": "client_auth"
}
```

The child process also receives:

- `AGENTBRIDGE_DEVICE_CERT_DEVICE_ID`
- `AGENTBRIDGE_DEVICE_CERT_CSR_SHA256`
- `AGENTBRIDGE_DEVICE_CERT_VALIDITY_DAYS`

The command must return:

```json
{
  "certificate_pem": "-----BEGIN CERTIFICATE-----\n...",
  "ca_certificate_pem": "-----BEGIN CERTIFICATE-----\n..."
}
```

AgentBridge parses the returned certificate and rejects it unless the certificate Common
Name matches `device_id`, the certificate public key matches the CSR public key, the
certificate is not expired, the Extended Key Usage includes `clientAuth`, and the
returned `ca_certificate_pem` is a CA certificate that directly issued the returned
device certificate.

## Renewal Flow

1. The device generates a new private key and a PEM CSR whose Common Name exactly equals
   `device_id`.
2. An operator or the device calls
   `POST /api/v1/device-identities/{device_id}/certificates/renew` with the CSR and an
   actor that has device-management permission.
3. AgentBridge issues a new client-auth certificate, stores its fingerprint and validity
   metadata, retires the previously active managed CA fingerprint records, and returns
   the new certificate PEM once.
4. The device installs the returned certificate and uses it for subsequent mTLS
   connections.

If a device uses its old certificate to authenticate the renewal request, it must store
the response before making another API call: renewal immediately retires the old managed
CA fingerprint. Device-key or admin-token authentication avoids that cutover race.

## Monitoring

Use `POST /api/v1/device-identities/certificates/scan` for manual scans and
`GET /api/v1/device-identities/certificates/scan-worker` for scheduler state. The scan
result includes:

- `status_counts`: expiry/metadata health counts.
- `renewal_status_counts`: renewal planning counts.
- `renewal_action_required_count`: devices with `due`, `overdue`, or `unknown` renewal
  status.
- `action_required_devices[]`: per-device expiry and renewal details.

Rendered scan events are available through `GET /api/v1/events/rendered` with
`event_type=device_identity.certificates_scanned`. When notify chat contexts are
configured, the scan worker delivers the latest action-required event through the Bot
Gateway.

## CA Key Custody

Use an intermediate CA for AgentBridge device certificates. Keep the offline/root CA out
of the AgentBridge runtime and sign the intermediate certificate through the normal CA
process.

For the online AgentBridge issuing key:

- Store the CA key and password file outside the repository.
- Restrict both files to the AgentBridge service account, for example mode `0600` for
  the key and `0400` for a password file.
- Prefer an encrypted private key and `AGENTBRIDGE_DEVICE_CERT_CA_KEY_PASSWORD_FILE`.
- Keep API, admin, and worker processes on hosts that are authorized to hold the
  issuing key.
- Do not enable debug SQL/logging modes that could capture request payloads containing
  CSR or certificate material.

Provider-native CA SDK clients are not implemented yet. Environments that require
non-exportable CA keys should prefer the external issuer command and keep the command's
own KMS/HSM credentials outside the AgentBridge process environment where possible.

## TLS Proxy Boundary

The reverse proxy must verify client certificates before AgentBridge sees a request. It
must also strip any inbound `X-AgentBridge-Client-Cert-Fingerprint` header from
untrusted clients and set that header only after mTLS verification succeeds.

Keep the proxy trust bundle aligned with the CA certificates that issued currently active
device certificates. During CA rotation, keep the old issuing CA trusted until all active
device certificates have been renewed and retired in AgentBridge.
