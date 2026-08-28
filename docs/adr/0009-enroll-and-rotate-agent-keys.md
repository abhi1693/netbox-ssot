# ADR 0009: Enroll and rotate agent keys without moving private material

## Status

Accepted

## Context

Manual public-key registration requires an administrator to move key material between interfaces and provides no
bounded key lifecycle. Storing a bootstrap or private key in NetBox would cross the customer-edge trust boundary.

## Decision

An administrator creates a random 15-minute, single-use enrollment token for a name and source set. NetBox displays
the raw token once and stores only its SHA-256 digest and safe prefix. The Go agent generates an Ed25519 key locally,
writes the private seed to a new mode-`0600` file, and exchanges the token plus public key for its agent ID and control
endpoint. Token failures are deliberately indistinguishable.

Each public key is stored as a durable signing-key record with its SHA-256 fingerprint, state, activation, use,
retirement, and revocation timestamps. Rotation requires a request signed by a currently usable key. The new key
becomes active and previous usable keys overlap for ten minutes, allowing the local file to be replaced atomically and
the running service to restart. Reuse of retired or revoked keys is rejected. Administrator revocation immediately
disables the agent, revokes all usable keys, and fails its outstanding commands.

Existing agents are migrated by treating their registered public key as the first active signing key. The legacy field
remains an active-key compatibility mirror until a later schema migration can remove it safely.

Enrollment may create a source-less standby identity. A separate replacement enrollment targets an existing agent.
The current identity remains usable until the replacement presents a valid one-time token, public key, and compatible
provider capabilities. That successful transaction revokes the prior usable keys, installs the replacement key,
preserves the agent UUID and source ownership, re-enables the agent, and cancels in-flight actions.

## Consequences

- NetBox never receives a signing private key and cannot recover one from enrollment state.
- Enrollment replay, expiry, rotation, overlap, and revocation have explicit database state and audit events.
- Private-key files must be regular, absolute-path files with no group or other permission bits.
- A running agent holds its signing key in memory; operators must restart it within the rotation overlap after replacing
  the file. The systemd unit uses `LoadCredential`, so restart also refreshes its read-only credential snapshot.
- Losing the private key uses a replacement enrollment; private material is never recovered or moved through NetBox.
