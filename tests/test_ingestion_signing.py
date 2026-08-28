from __future__ import annotations

import base64
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from netbox_ssot.ingestion.signing import SignatureError, signing_payload, verify_signature


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def test_signed_batch_verifies_and_body_tampering_fails() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    agent_id = uuid4()
    timestamp = 1_787_877_600
    body = b'{"run_id":"test"}'
    signature = encode(private_key.sign(signing_payload(agent_id, timestamp, body)))

    verify_signature(
        public_key=encode(public_key),
        agent_id=agent_id,
        timestamp=timestamp,
        body=body,
        signature=signature,
    )
    with pytest.raises(SignatureError, match="invalid"):
        verify_signature(
            public_key=encode(public_key),
            agent_id=agent_id,
            timestamp=timestamp,
            body=body + b" ",
            signature=signature,
        )


@pytest.mark.parametrize("value", ["not+base64", encode(b"short")])
def test_public_key_validation_fails_closed(value: str) -> None:
    with pytest.raises(SignatureError):
        verify_signature(
            public_key=value,
            agent_id=uuid4(),
            timestamp=1,
            body=b"{}",
            signature=encode(b"x" * 64),
        )
