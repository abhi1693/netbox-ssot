# Collector agent

The NetBox SSoT agent runs near source systems and makes outbound connections to the NetBox plugin. It resolves source
credentials locally, executes compiled provider collectors, signs results, and submits immutable observations. It has
no target-NetBox write capability.

## Build

Build the standalone binary from the repository root:

```shell
CGO_ENABLED=0 go build -trimpath -o dist/netbox-ssot-agent ./agent/cmd/netbox-ssot-agent
```

Confirm the binary and compiled providers:

```shell
dist/netbox-ssot-agent version
dist/netbox-ssot-agent providers
```

Tagged releases publish checksummed archives for Linux amd64, Linux arm64, Linux armv7, macOS amd64, macOS arm64,
Windows amd64, and Windows arm64. Release builds report the semantic version from their Git tag; ordinary local builds
continue to report the source development version.

## Enroll

Create an agent from **SSoT → Agents → Connect agent** in NetBox. The enrollment page generates the complete command,
including its short-lived token, endpoint, and private-key destination. Run that command once on the agent host.

The agent generates its signing key locally and sends only the public key to NetBox. The enrollment token is shown once
and should be removed from the shell environment after enrollment.

## Run as a service

The repository includes a hardened systemd unit and environment example:

- [`deploy/systemd/netbox-ssot-agent.service`](deploy/systemd/netbox-ssot-agent.service)
- [`deploy/systemd/agent.env.example`](deploy/systemd/agent.env.example)

Install the binary, copy these files to their system locations, and set the enrolled agent ID and control endpoint in
`/etc/netbox-ssot-agent/agent.env`. The supplied unit loads the signing key as a systemd credential and starts:

```shell
netbox-ssot-agent run
```

The principal runtime settings are:

| Environment variable | Purpose |
| --- | --- |
| `NETBOX_SSOT_CONTROL_ENDPOINT` | Plugin agent-control endpoint |
| `NETBOX_SSOT_AGENT_ID` | Enrolled agent UUID |
| `NETBOX_SSOT_PRIVATE_KEY_REF` | `file:///` or `env://` signing-key reference |
| `NETBOX_SSOT_LOG_LEVEL` | `debug`, `info`, `warn`, or `error` |

Each source provider can declare additional secret references. Define their environment variables or protected files on
the agent host; do not place resolved credentials in NetBox configuration.

After starting the service, the agent page should change to **Online** and show a current heartbeat. Assign the agent to
a source, then use **Test connection** before requesting the first collection.

## Runtime behavior

- The agent polls for assignments and administrator commands independently of collection schedules.
- A bounded worker pool runs collection work without blocking heartbeats or command pickup.
- Only one collection for a given source runs at a time.
- Control-plane failures use bounded exponential backoff and recover to the configured polling interval.
- Signed command progress and results are durable in the plugin UI.
- Key replacement and revocation are initiated from the agent page in NetBox.

## Commands

`netbox-ssot-agent` also exposes provider listing, connection testing, direct collection, key generation, enrollment,
key rotation, one-time synchronization, configuration inspection, signed submission, and version commands. The normal
production path is enrollment followed by the long-running `run` command; lower-level commands are intended for
diagnostics and automated tests.

## Development

The command entry point lives in `cmd/netbox-ssot-agent/`. Reusable runtime code lives under
[`../internal/`](../internal/README.md), and compiled collectors live under [`../providers/`](../providers/README.md).

Run agent tests from the repository root:

```shell
go test ./agent/... ./internal/... ./providers/...
go vet ./...
```
