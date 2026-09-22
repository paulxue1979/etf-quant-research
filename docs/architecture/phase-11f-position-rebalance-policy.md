# PHASE 11F Position and Rebalance Policy

## Execution Boundary

The execution contract is deliberately ordered:

`Strategy intent -> TargetAllocation -> PositionRebalancePolicy -> RebalanceDecision -> Orders -> Actual allocation`.

`TargetAllocation` remains the canonical strategy intent. A policy can suppress
or schedule its execution, but never rewrites the target, regime, or signal
provenance. `BacktestResult.rebalance_decisions` records the decision at the
signal/evaluation date and keeps `execution_date` separate for next-open
execution.

## Policy Versioning

`PositionRebalancePolicy` is immutable and serialized only when it is enabled,
so legacy V1.2 configurations retain their previous snapshot shape and
behavior. Its version, minimum allocation change, drift threshold, maximum
turnover, minimum cash reserve, and per-asset allocation constraints are part
of custom BacktestConfig identity. The same payload is carried through frozen
research and OOS configuration snapshots.

## Deterministic Decision Rules

- Target change is the maximum absolute change across assets and implicit CASH.
- Drift is the maximum absolute actual-versus-target weight difference.
- Turnover estimate is one half of the L1 target gap. This is a pre-trade
  estimate and is intentionally distinct from analytics turnover, which uses
  executed fills over average raw equity.
- A configured minimum allocation change suppresses a smaller target change.
- A configured drift threshold permits a rebalance when drift reaches the
  threshold; exact equality is eligible.
- A configured maximum turnover is a hard suppress limit. No partial order is
  generated when the estimate exceeds it.
- A target whose implicit cash weight is below the minimum reserve, or which
  violates an allocation constraint, is hard-suppressed and retains its
  canonical intent in allocation history.

Suppression reasons are explicit and immutable. The current safe partial
semantics are limited to reserve-aware integer affordability: commission and
slippage are included when computing affordable shares, and cash cannot fall
below the configured reserve. Turnover caps use hard suppression rather than
silently clipping the strategy target.

## Execution Invariants

The existing engine invariants remain unchanged: integer shares, SELL-first
ordering, FIFO lots, commission/slippage accounting, non-negative cash,
signal-date to next-trading-day-open execution, and contribution-triggered
execution. `CASH` is ledger cash; an ETF such as `SGOV` remains a normal asset.

## Legacy and OOS Compatibility

Missing policy payloads deserialize as legacy-compatible defaults. Custom
policy payloads are included in BacktestConfig identity and frozen
ResearchEvaluationConfig/OOS configuration hashes. This prevents a replay
with different position constraints from being treated as the same experiment.

## Explicitly Deferred

Partial turnover optimization, broker integration, live trading, UI action or
regime markers, metadata remediation, grid/heatmap/Pareto views, walk-forward,
and AI optimization remain out of scope for PHASE 11F.
