# PHASE 8E-1B — Experiment Result Compatibility Diagnostics

## Scope

PHASE 8E-1B adds a read-only diagnostic over the persisted PHASE 8E-1A
experiment result read model. It answers only whether completed candidate
results were produced under comparable research conditions. It does not rank
candidates, calculate a score, select a winner, create a `SelectionDecision`,
or recommend a strategy.

The diagnostic is computed dynamically and is not persisted. The existing
`BacktestRun` and `ExperimentResult` records remain the sources of truth.

## Compatibility Contract

The canonical reference is the completed candidate with the lowest persisted
`candidate_index`. Parameter values, parameter-set hashes, and derived strategy
version identities are intentionally excluded from the compatibility decision:
those fields are expected to differ between experiment candidates.

The following result-affecting dimensions are checked:

- experiment and research-protocol binding;
- IS start and end dates;
- explicit raw or adjusted price field;
- initial capital;
- proportional and per-order commission;
- slippage;
- execution rule;
- fractional-share policy;
- rebalance policy;
- backtest engine version;
- analytics version;
- backtest configuration hash;
- data snapshot / provenance reference.

Configuration hashes are not trusted blindly. If two candidates expose the same
configuration hash while their semantic configuration fields differ, the
diagnostic returns `INTEGRITY_MISMATCH`.

## Statuses

`COMPATIBLE` means all comparable completed candidates agree on the checked
dimensions and every completed result has sufficient data provenance.

`INCOMPATIBLE` means a deterministic semantic mismatch, binding violation, or
integrity mismatch was found. Failed, missing, running, and otherwise
non-terminal candidates are reported as unavailable and do not participate in
compatibility comparison.

`NOT_EVALUABLE` means there is not enough evidence to make a fair comparison.
This includes an experiment with no completed results and completed results
whose data provenance is missing. Missing provenance is not treated as proof
that the results are incompatible.

## Reason Codes

Reason codes are stable machine-readable values, including
`IS_RANGE_MISMATCH`, `PRICE_FIELD_MISMATCH`, `INITIAL_CAPITAL_MISMATCH`,
`COMMISSION_MISMATCH`, `SLIPPAGE_MISMATCH`, `EXECUTION_RULE_MISMATCH`,
`FRACTIONAL_SHARES_MISMATCH`, `REBALANCE_POLICY_MISMATCH`,
`ENGINE_VERSION_MISMATCH`, `ANALYTICS_VERSION_MISMATCH`,
`BACKTEST_CONFIGURATION_MISMATCH`, `DATA_PROVENANCE_MISMATCH`,
`EXPERIMENT_BINDING_MISMATCH`, `PROTOCOL_BINDING_MISMATCH`,
`RESULT_NOT_AVAILABLE`, `EXECUTION_FAILED`, and `INTEGRITY_MISMATCH`.

Each mismatch includes the candidate identity, dimension, reason code, safe
reference and candidate values, and a deterministic message. Secrets,
credentials, host paths, unsafe text, and non-finite numbers are rejected or
redacted before serialization.

## Determinism and Immutability

Candidates are ordered by `candidate_index`, and diagnostic serialization uses
the repository canonical JSON helper. The diagnostic is immutable and exposes
an explicit `is_compatible` flag. It contains no ranking, objective score,
winner, recommendation, OOS field, or mutable persistence handle.

## Boundaries

The service does not load or calculate OOS data, recompute performance metrics,
alter strategy versions, alter backtest accounting, or add database tables or
migrations. Frontend and API changes are intentionally deferred; a later read
surface may expose this diagnostic without changing its semantics.

## Verification

The dedicated unit suite covers compatible candidates, canonical reference
selection, all required mismatch classes, failed and missing results,
provenance-based `NOT_EVALUABLE`, configuration-hash integrity mismatches,
deterministic serialization, and secret / non-finite-value protection.
