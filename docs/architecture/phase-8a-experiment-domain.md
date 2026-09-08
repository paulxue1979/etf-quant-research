# PHASE 8A — Experiment Domain Model

## Scope

PHASE 8A defines immutable experiment contracts only. It does not enumerate a
parameter space, generate candidates, run backtests, rank results, select a
strategy, expose an API, persist experiments, or evaluate OOS data.

## Domain Model

`Experiment` binds a `protocol_id`, strategy definition identity, immutable base
strategy version identity/hash, `ParameterSpace`, `ObjectiveSpecification`, IS
date range, and the existing `BacktestConfig`. The backtest configuration is
reused rather than redefined, so result-affecting fields retain the PHASE 3/4I
contract: price field, capital, commission, slippage, execution rule,
rebalance policy, fractional-share flag, and engine version.

`ExperimentStatus` is deliberately separate from the PHASE 7
`ProtocolStatus`. The experiment lifecycle describes experiment definition and
future execution state; protocol state remains the authority for IS/OOS
governance and research freeze rules.

## Parameters

`ParameterDefinition` supports integer, float, enum, and discrete domains with
strict bounds, finite numeric values, positive steps, precision, or explicit
allowed values. `ParameterSet` is one immutable concrete mapping and validates
against an optional `ParameterSpace`. No candidate enumeration is performed.

`ParameterConstraint` is a serializable binary relation between named
parameters. Only a fixed comparison operator enum is accepted. Payloads that
contain expression/code/formula fields are rejected; no `eval`, `exec`, lambda,
dynamic import, or shell execution is involved.

`ParameterSpace` requires unique definitions, valid constraint references, and
a positive `max_candidates` limit. It does not calculate a candidate count.

## Objective and Provenance

`ObjectiveSpecification` defines primary/secondary metrics, explicit maximize
or minimize directions, hard metric limits, and tie-break metric order. It does
not calculate metrics or rank candidates.

`ExperimentProvenance` records protocol and strategy bindings, parameter/objective
hashes, IS boundaries, result-affecting backtest settings, engine/analysis
versions, and a data snapshot reference. Metadata is retained separately from
the result-affecting configuration; no dataset versioning is introduced.

## Canonical Hash Contract

Domain records use one shared utility:

```text
domain object -> sorted-key JSON, stable separators, allow_nan=false
             -> UTF-8 SHA-256 hex digest
```

The contract is used by `ParameterSet`, `ParameterSpace`, and
`ObjectiveSpecification`. Field ordering does not affect the digest; semantic
content changes do. NaN and Infinity are rejected.

## Immutability and Boundaries

All models are frozen dataclasses. `Experiment.with_status()` returns a new
record and permits only forward lifecycle transitions. After a space is frozen,
changing its parameters, objective, base version, backtest configuration, or IS
range requires a new `Experiment` record. The base `StrategyVersion` itself is
never modified.

Experiment IS dates must match the reused `BacktestConfig` dates. The model
does not execute a backtest and does not make the IS/OOS protocol decision;
those contracts remain with the future experiment service and PHASE 7 research
protocol implementation. Observed OOS data is therefore outside this phase.

## PHASE 8B Preparation

The immutable and hash-ready models provide inputs for later candidate
generation. PHASE 8B may define enumeration and derived strategy versions, but
must preserve base-version immutability, protocol freeze rules, append-only
research records, and the existing backtest/accounting semantics.
