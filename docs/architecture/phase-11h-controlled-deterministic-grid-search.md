# PHASE 11H Controlled Deterministic Grid Search

## Scope

PHASE 11H adds a controlled, deterministic and recoverable IS-only parameter grid on top of the existing Experiment domain. It does not implement grid trading, candidate ranking, researcher selection, strategy freeze, official OOS execution, heatmaps or Pareto analysis.

The execution chain remains:

```text
Frozen Experiment and ParameterSpace
  -> Grid preflight
  -> Structured constraint pruning
  -> Semantic deduplication
  -> Explicit prepare
  -> Existing ExperimentExecutionService
  -> Existing strategy evaluation and backtest engine
  -> Canonical analytics and BacktestRun
  -> Immutable ExperimentResult
```

## Reused Contracts

- `ParameterDefinition`: `name`, `type`, `min`, `max`, `step`, `precision`, `allowed_values`.
- `ParameterSpace`: sorted parameter definitions, safe binary constraints and the frozen experiment limit.
- `ParameterSet`: canonical sorted values and SHA-256 content identity.
- `ParameterBinding` and `ParameterBindingSet`: existing allowlisted immutable StrategyVersion materialization.
- `Experiment`, `CandidateExecution`, `ExperimentResult`, `BacktestRun` and the PHASE 11G metadata projection.
- Existing IS boundary, warmup, stateful strategy initialization, signal, T+1 execution, portfolio ledger and analytics semantics.

No second candidate or backtest artifact model is introduced.

## Grid Definition

`GridSearchDefinition` binds one immutable experiment to:

- its `experiment_id` and content hash;
- explicit strategy-owned bindings;
- explicit BacktestConfig-owned bindings;
- fixed parameter audit metadata;
- typed structured constraints;
- an execution candidate limit;
- a theoretical expansion guard;
- schema version `phase-11h.1`.

Only parameters present in the frozen `ParameterSpace` are tunable. Every tunable parameter must have exactly one strategy or backtest owner. Fixed parameters are the frozen base strategy/configuration and the definition's immutable audit map; the engine never scans arbitrary numeric strategy fields.

## Deterministic Expansion

`ParameterSpace` sorts parameters by name. Numeric values are expanded from validated min/max/step domains; float values use the existing decimal precision/quantization behavior. Discrete and enum values use canonical scalar ordering. Cartesian enumeration therefore has a stable parameter order and stable value order and does not depend on mapping, set, filesystem or process order.

The counts are:

- `theoretical_count`: product of all discrete parameter-domain sizes before enumeration.
- `pruned_count`: combinations rejected by frozen ParameterSpace constraints, structured constraints or materialization validation.
- `valid_count`: combinations that passed ParameterSpace and structured constraints before semantic deduplication; a later materialization rejection remains visible in pruning reasons.
- `duplicate_count`: valid materializations equivalent to an earlier canonical strategy/configuration/IS candidate.
- `executable_count`: unique materialized candidates retained for execution.

Pruning precedes execution. Reason counts and at most 20 duplicate samples are recorded; full objects for every rejected combination are not persisted.

## Constraints

The structured constraint registry supports:

- `BoundConstraint`
- `SumConstraint`
- `OrderingConstraint`
- `BudgetConstraint`
- `MonotonicConstraint`

Constraints contain typed fields and parameter references. No expression, `eval`, `exec`, callable, import, arbitrary JSON pointer or arbitrary Python attribute access is accepted. Research preferences are applied only when explicitly present in the grid definition.

## Limits And Preflight

- Default executable candidate limit: 100.
- Controlled hard maximum: 1,000.
- Default theoretical combination guard: 1,000,000.

The theoretical product is checked before Cartesian enumeration and materialization. Within the guard, preflight computes exact pruning, deduplication and executable counts without running a backtest. If the executable count exceeds the selected allowed limit, preflight fails. It never truncates the candidate sequence.

Preflight and execution are separate commands. `prepare` atomically attaches the complete, preflighted candidate set and immutable grid definition. No API call that creates or reads a definition implicitly starts execution.

## Identity And Deduplication

The grid definition hash covers the frozen experiment identity, all binding sets, fixed audit metadata, structured constraints, limits and schema. The frozen experiment already covers base StrategyVersion, base BacktestConfig, ParameterSpace and IS boundary.

Each ParameterSet has a canonical content hash. Existing candidate identity remains derived from experiment identity, deterministic index and ParameterSet hash. Candidate index is ordering metadata, not the sole semantic identity. Candidate execution identity additionally covers binding identity and immutable strategy provenance.

Semantic deduplication hashes:

- the actual derived strategy configuration;
- normalized materialized BacktestConfig;
- IS start and end boundaries.

Equivalent materializations retain the first deterministic candidate and record later combinations as duplicates. They are not executed twice.

## Materialization

Strategy bindings continue through the existing immutable StrategyVersion materializer. Backtest policy bindings use a separate closed allowlist:

- `position_rebalance_policy.minimum_allocation_change`
- `position_rebalance_policy.drift_threshold`
- `position_rebalance_policy.maximum_turnover`
- `position_rebalance_policy.minimum_cash_reserve`

Materialization returns a new `BacktestConfig`; it never mutates the frozen base configuration. A single candidate may combine strategy and backtest policy parameters, and both binding sets contribute to execution provenance.

Existing strategy bindings remain compatible with indicator periods, state-machine allocation fields and ValueZone thresholds already registered by the StrategyVersion materializer. Generic SPY/UPRO/SGOV coverage confirms the grid engine contains no QQQ/TQQQ-specific behavior.

## IS Integrity

Every candidate executes through `ExperimentExecutionService` with the frozen Experiment's IS dates. Warmup data may precede IS only through the existing indicator preparation contract and is excluded from performance dates. Each candidate creates its own derived StrategyVersion and stateful strategy runtime; runtime state is never shared across candidates.

Grid planning and execution do not read official OOS data or OOS metrics. Completion produces ExperimentResults only. Researcher Selection, Freeze and Official OOS remain explicit later operations.

## Lifecycle, Failure And Recovery

Preparation moves the frozen Experiment to `CANDIDATES_GENERATED`; explicit execution moves it to `RUNNING`. Candidate state continues to use `PENDING`, `RUNNING`, `COMPLETED` and `FAILED` with the existing lease and retry metadata.

- One candidate failure does not discard completed results or stop later candidates.
- Retryable failure remains recoverable under the existing retry count and keeps the same candidate/execution identity.
- A completed candidate with an immutable result is skipped on restart.
- The narrow crash window in which execution completed before result finalization is recovered to pending only after transactionally proving that no immutable result exists.
- An active unexpired lease is not stolen.
- The Experiment completes only after all candidates are terminal and at least one immutable result exists; all failed candidates make it invalid.

Result finalization supports non-terminal candidate commits without prematurely completing the multi-candidate Experiment. The canonical BacktestRun is written as a complete immutable artifact before its immutable ExperimentResult. A crash between those commits is recoverable: deterministic run identity reuses the complete artifact and the completed-without-result recovery path retries finalization without duplicating BacktestRun. Final Experiment terminalization and its lifecycle event share a separate transaction.

## Cancellation

Cancellation is durable and idempotent. It transitions the Experiment through `CANCEL_REQUESTED` to `CANCELLED`. A currently executing candidate is allowed to finish and finalize atomically; after the cancellation flag is observed, no new candidate starts. Pending candidates are represented as cancelled in lightweight progress. Restarting a cancelled experiment never resumes execution.

## Persistence And Progress

Two additive SQLite tables store small immutable grid metadata:

- `grid_search_definitions`
- `grid_search_candidate_plans`

They store definition/preflight JSON plus per-candidate hashes and indexes. Canonical BacktestRun and ExperimentResult remain the only result artifacts. Progress queries use indexed scalar columns and aggregate counts from candidate execution/result tables; they do not load `run_json`, `result_json`, equity curves, positions or other full artifacts.

The existing experiment results read model exposes all persisted candidate results and completeness after execution. Retryable failures are reported separately from terminal failures and do not advance terminal progress; they cannot make a running grid appear 100% complete. PHASE 11G metadata is generated automatically when each canonical BacktestRun is saved.

## API And Frontend

The API provides separate template, preflight, prepare, execute, cancel and progress endpoints under `/research/experiments/{experiment_id}/grid`. Execute and cancel accept no overrides, preventing mutation of the prepared definition.

The Experiment Research frontend provides a minimal grid panel. It loads the frozen template, accepts structured JSON, displays theoretical/pruned/valid/duplicate/executable/allowed counts, requires explicit prepare and execute actions, and reports progress/cancellation. An over-limit or failed preflight disables preparation and execution. It contains no best/winner/ranking, heatmap, Pareto or OOS controls.

## Security And Data Integrity

- Binding targets are closed allowlists.
- Constraints are typed and non-executable.
- SQLite statements are parameterized.
- Request identities are checked against the path and frozen Experiment hash.
- Non-finite values and sensitive field names are rejected before persistence.
- Progress and failures expose safe codes/counts, not credentials or Tiingo tokens.
- No filesystem path, import, arbitrary callable or SQL fragment is accepted from a grid definition.

## Deferred

PHASE 11H intentionally defers result heatmaps, Pareto frontiers, stability and neighboring-parameter analysis, automatic ranking, automatic selection, Freeze/OOS transitions, walk-forward research, random/Bayesian/genetic/AI optimization and distributed workers. Those boundaries remain prerequisites for PHASE 11I rather than hidden behavior in this engine.
