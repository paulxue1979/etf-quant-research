# PHASE 13E Semantic Grid Binding

PHASE 13E adds an ID-based semantic descriptor layer for Regime State Machine
grid parameters. The descriptor is resolved against one immutable
`StrategyVersion` and then delegated to the existing PHASE 8D path-whitelisted
materializer. Existing `strategy_bindings` remain valid and are not migrated.

Supported targets are transition relative thresholds, MA/EMA periods (including
nested condition paths), state allocation weights by `state_id` and symbol, and
Value Zone entry/exit thresholds and periods. Resolution requires both the base
version ID and SHA-256 content hash. Missing, duplicate, deleted, or mismatched
targets fail closed before candidate expansion.

The discovery endpoint is:

`GET /strategy-lab/strategies/{strategy_id}/versions/{version_id}/semantic-targets`

It returns human-readable target labels and typed descriptors. Internal array
paths are intentionally omitted from the public preview. Grid preflight reports
the resolved path only in its audit payload, and materialized derived versions
retain descriptor, resolved path, base identity, and concrete parameter value in
immutable materialization provenance.

Unsupported structural changes are reported rather than ignored: transition or
Value Zone priority, logical AND/OR topology, comparators, symbol/timeframe
replacement, and state topology mutation. Candidate selection, optimization,
backtest, and OOS semantics remain outside this phase.
