# PHASE 13U Controlled Experiment Operator

## Scope

The MCP surface can prepare and read a bounded IS-only grid. It cannot approve,
execute, select a candidate, freeze a version, or start official OOS. An exact
immutable StrategyVersion ID/content hash and an explicitly selected frozen
ResearchProtocol are required. The protocol supplies the fixed IS/OOS boundary;
the operator cannot invent or change OOS dates.

## Canonical execution

Preparation validates semantic target IDs against the specified version,
normalizes percent inputs into canonical values, and runs the existing grid
preflight. Any invalid or duplicate combination blocks the entire proposal;
there is no silent valid-subset execution. The first release supports at most
two discrete axes and 16 combinations. Preparation persists only the pending
configuration and its immutable experiment/grid snapshot.

The local browser approval route revalidates version/protocol identity, grid
preflight, and cache-only data availability before claiming a lease. Only then
does it create the canonical Experiment and call the existing GridSearchService
for candidate execution, backtests, and result persistence. Candidate selection,
freeze, and official OOS remain separate human-controlled workflows. The
approval route uses a local Origin gate; this is not public-network authentication.

## Recovery and limits

Pending proposals, approval status, execution status, hashes, and audit events
are persisted in SQLite. A request key is idempotent; an identical active
proposal cannot be duplicated with a different key. A live worker owns a
five-minute lease and refreshes it during execution. A later browser retry can
resume the same canonical Experiment after a failed worker or expired lease.
This is request-driven recovery, not a background job system. The canonical
candidate/result repositories remain the source of truth after execution.

Current Experiment Builder semantics support only zero commission/slippage,
integer shares, no contribution schedule, and next-trading-day-open execution
for this operator path. Other protocol evaluation configurations are rejected
instead of being silently rewritten. Data preflight is cache-only and does not
fetch market data. Real browser UAT, real data coverage, and cold restart
verification are separate gates and are not claimed by the automated tests.
