from __future__ import annotations

import base64
import hashlib
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

AGENT_HEADER = "X-NetBox-SSoT-Agent"
TIMESTAMP_HEADER = "X-NetBox-SSoT-Timestamp"
SIGNATURE_HEADER = "X-NetBox-SSoT-Signature"
SIGNATURE_CONTEXT = "netbox-ssot-agent-v1"


class SignatureError(ValueError):
    """Raised when an agent signing key or signature is malformed or invalid."""


def _decode_base64url(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise SignatureError("value is not valid unpadded base64url") from exc


def decode_public_key(value: str) -> bytes:
    public_key = _decode_base64url(value)
    if len(public_key) != 32:
        raise SignatureError("Ed25519 public key must contain 32 bytes")
    return public_key


def public_key_fingerprint(value: str) -> str:
    return hashlib.sha256(decode_public_key(value)).hexdigest()


def signing_payload(agent_id: UUID, timestamp: int, body: bytes) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return f"{SIGNATURE_CONTEXT}\n{agent_id}\n{timestamp}\n{digest}".encode()


def verify_signature(*, public_key: str, agent_id: UUID, timestamp: int, body: bytes, signature: str) -> None:
    decoded_key = decode_public_key(public_key)
    decoded_signature = _decode_base64url(signature)
    if len(decoded_signature) != 64:
        raise SignatureError("Ed25519 signature must contain 64 bytes")
    try:
        Ed25519PublicKey.from_public_bytes(decoded_key).verify(
            decoded_signature,
            signing_payload(agent_id, timestamp, body),
        )
    except InvalidSignature as exc:
        raise SignatureError("agent signature is invalid") from exc
