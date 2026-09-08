# PHASE 8C Experiment Persistence & Provenance

PHASE 8C stores immutable research inputs only. It does not execute a
backtest, evaluate IS/OOS results, rank candidates, select a strategy, or
recommend a strategy.

## Persistence Boundary

`ExperimentRepository` uses the existing SQLite repository conventions:

- short-lived connections;
- foreign keys enabled;
- `BEGIN IMMEDIATE` for atomic writes;
- canonical JSON and the PHASE 8A/8B SHA-256 helpers;
- structured persistence errors without secrets.

One experiment write stores the experiment definition, its parameter space,
optional parameter set, optional candidate set, candidates, and audit events in
one transaction. A candidate failure rolls the complete write back.

## Stored Objects

The repository stores canonical representations for:

- `ParameterSpace`, keyed by `parameter_space_hash`;
- `ParameterSet`, keyed by `parameter_set_hash`;
- `Experiment`, keyed by `experiment_id` and checked against `content_hash`;
- the generated candidate set metadata and its candidate rows;
- append-only `EXPERIMENT_CREATED`, `SPACE_FROZEN`,
  `CANDIDATES_GENERATED`, or `STATUS_CHANGED` events.

Candidate order is persisted as `candidate_index` and every read uses
`ORDER BY candidate_index ASC`. It is therefore part of the reproducibility
contract and does not depend on SQLite's default row order.

## External Bindings

An experiment must reference an existing Research Protocol and an existing
Strategy Version. The stored Strategy Version content hash is compared with the
requested base hash. Missing protocol/version and hash mismatches fail
explicitly. The experiment stores provenance and a backtest configuration
snapshot, but never copies a `BacktestResult`; later phases continue to use
`BacktestRun` as the result source of truth.

## Integrity and Security

Reads reconstruct domain models and recompute hashes. Non-finite JSON values,
non-canonical JSON, corrupted rows, hash mismatches, and missing foreign
records are rejected. Provenance is checked for credential-like keys and known
API-key/private-key markers; environment dumps and credentials are not part of
the persistence contract.

This phase intentionally has no update or delete API for experiment content.
The existing domain models remain frozen, and later status changes must be
represented as new immutable snapshots/events rather than overwriting the
research configuration.
