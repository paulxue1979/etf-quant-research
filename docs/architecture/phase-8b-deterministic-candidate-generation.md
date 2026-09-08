# PHASE 8B - Deterministic Candidate Generation

## Scope

PHASE 8B transforms a frozen PHASE 8A `Experiment` parameter space into an
ordered `ParameterCandidateSet`. It does not persist candidate sets, create
strategy versions, execute backtests, rank candidates, select a winner, inspect
OOS data, or expose an API or frontend.

## Preconditions

`generate_candidates()` accepts only an `Experiment` whose status is
`space_frozen`. It does not advance that status or modify the PHASE 7 research
protocol lifecycle. Protocol freeze, strategy-version freeze, and experiment
space freeze remain distinct contracts.

## Canonical Cartesian Product

`ParameterSpace` already canonicalizes parameter names and constraints. The
generator uses that parameter-name order. Each numeric range is ascending;
enum/discrete values are ordered by PHASE 8A canonical JSON. The last parameter
varies fastest. This makes candidate index, values, `ParameterSet` hash, and
candidate-set hash independent of insertion order, hash seed, machine, clock,
database ordering, and randomness.

Float ranges use decimal strings for range construction, then retain the normal
PHASE 8A finite-number and canonical JSON contracts. No epsilon adjustment or
implicit candidate-value rounding is applied.

## Constraints and Limits

Only PHASE 8A `ParameterConstraint` fixed comparison operators are evaluated:
`<`, `<=`, `>`, `>=`, `==`, and `!=`. Evaluation uses direct value comparisons,
never expressions or dynamic execution. The current model supports only
parameter-to-parameter constraints. Parameter-to-constant constraints are not
added in this phase because PHASE 8A models the right side as a defined parameter
name; a future domain-model phase must add an explicit typed constant reference.

The theoretical Cartesian count is calculated before materializing candidates.
If it exceeds `ParameterSpace.max_candidates`, generation raises
`PARAMETER_SPACE_TOO_LARGE`; it never truncates, samples, or reduces the space.
Constraints may subsequently leave zero candidates, represented by an empty
ordered sequence.

An empty parameter space deliberately produces one empty `ParameterSet` (`{}`).
This is the explicit Cartesian-product identity and lets a frozen experiment
represent one no-parameter baseline without hidden special cases.

## Hash Contract

`ParameterSet` continues to use PHASE 8A canonical JSON plus SHA-256. The
candidate-set hash contains only the parameter-space hash and the ordered
candidate parameter values. It contains no timestamp, UUID, database ID,
machine value, execution result, or OOS information.

## Security and Later Phases

The module contains no `eval`, `exec`, dynamic import, shell execution,
backtest execution, persistence, API, frontend, selection, or OOS logic.
PHASE 8C may persist the immutable candidate-set contract. PHASE 8D may execute
approved candidates. PHASE 8E may record a human-reviewed IS-only selection.
