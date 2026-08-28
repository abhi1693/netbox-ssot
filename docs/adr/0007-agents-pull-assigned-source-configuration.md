# ADR 0007: Agents pull assigned source configuration

## Status

Accepted

## Context

Manually exporting collection requests makes every source edit an out-of-band deployment task. Storing provider
credentials in NetBox would remove that friction but would also move customer-side secrets across the control-plane
boundary. Allowing several agents to schedule the same source would create duplicate collection runs without a lease
protocol.

## Decision

Each source has one execution agent, a collection interval, and an update revision. An enrolled agent uses its existing
Ed25519 identity to pull only its enabled assigned sources from a signed control endpoint. NetBox returns provider
configuration, datasets, schedule, and opaque secret references; it never returns provider credentials.

The Go agent polls the control endpoint, resolves secrets locally, runs changed sources immediately, schedules
unchanged sources locally, and submits batches through the signed ingest endpoint. NetBox supplies the ingest endpoint,
but the agent rejects it unless it has the same origin as the control endpoint. Manual collection and submission remain
available for diagnosis.

Protocol 1.1 treats every successful poll as a heartbeat and may return durable provider-independent commands. The
initial command kinds are Test connection and Run now. Commands are scoped to one enabled source and its assigned
agent, leased when dispatched, and reported through a same-origin endpoint with the agent's existing Ed25519 identity.
The plugin validates the command, agent, source, and kind before accepting a terminal result. One active command of each
kind is allowed per source. Run now reuses the command UUID as the collection run UUID, making batch ingestion
idempotent if a dispatched command is recovered after an agent restart.

Control polling is deliberately independent from source collection scheduling. The default control interval is five
seconds, bounded to two through thirty seconds, while collection intervals remain one minute or longer. The CLI value
is a bootstrap fallback; after connecting, the agent adopts the administrator-managed setting returned by NetBox. It
reports its effective interval on every subsequent poll so NetBox can show desired-versus-reported state and evaluate
heartbeat health against the actual cadence.

The agent retains an executed result in memory until NetBox acknowledges it, so a temporary control-plane failure does
not immediately repeat provider work. Protocol 1.0 responses remain available to older agents but do not include the
command endpoint or commands.

Each agent reports the provider ID, implementation version, and contract version compiled into its binary during
enrollment and every check-in. NetBox permits a source assignment only when those values match the installed provider
manifest and the agent meets its minimum version. If a later check-in becomes incompatible, NetBox withholds that
assignment, marks the source unhealthy, and records the capability change for operator review.

The optional `pause_scheduled_collections_until_resolved` control-plane setting adds review backpressure and defaults
to false. When enabled, NetBox keeps the source assignment and command channel present but marks scheduled collection
as paused after a complete run. The newest complete run is resolved when its newest comparison has no actionable or
conflicting items, or when that comparison has an application receipt. Test connection and Run now remain available;
a successful Run now snapshot supersedes the previously pending snapshot. Agents older than 0.6.8 cannot be assigned
while this policy is enabled because they do not understand schedule state.

Consecutive control fetch failures back off exponentially from the configured control interval to a five-minute cap.
At the cap, the agent retries every five minutes. The first successful fetch resets the failure count and restores the
normal configured interval so administrative actions become responsive immediately after recovery.

Provider work executes independently from polling in a bounded pool of four workers. The scheduler permits only one
active job per source and gives queued administrator commands priority over scheduled collections as workers become
available. This keeps heartbeats, configuration changes, and command pickup responsive during long collections without
allowing concurrent access to the same source.

Protocol 1.1 agents at version 0.6.2 or newer report Running and Reporting progress and include every locally active
command ID in each poll. The server renews those leases and may redispatch an active command after five minutes without
progress. Before redispatching Run now, it checks for an accepted collection with the command UUID and completes the
command from that durable evidence. Terminal result reporting remains idempotent.

## Consequences

- Source configuration and schedules can be edited in the NetBox UI without redeploying an agent configuration file.
- Agent bootstrap uses the one-time enrollment flow from ADR 0009; steady-state operation requires the returned agent
  UUID, its local file-backed signing key, the control endpoint, and provider secret references.
- Exactly one agent schedules a source, preventing duplicate automatic runs. Agents may enroll without sources as
  standbys, and failover is an explicit capability-checked reassignment recorded in Activity.
- Restarting an agent runs each assignment immediately; schedules are intentionally not persisted on the customer host.
  Expired command leases recover independently, with accepted Run now batches protecting against duplicate collection.
- Agent health is derived from heartbeat recency. Source health also considers enablement, agent health, latest run
  state, and whether the next interval is overdue.
- Test connection and Run now remain outbound operations performed by the Go agent; provider credentials never move to
  the plugin, and neither command grants target mutation authority.
