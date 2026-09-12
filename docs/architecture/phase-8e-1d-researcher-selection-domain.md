# PHASE 8E-1D Researcher Selection Domain

PHASE 8E-1D defines an immutable, in-memory record of a researcher explicitly
selecting one candidate from an experiment's frozen IS candidate universe. It
does not persist selections, create an API, rank candidates, recommend a
candidate, or hand off to the PHASE 7 protocol.

## Boundary

`create_experiment_selection_decision` requires an explicit
`selected_candidate_id`. The domain resolves the candidate from the supplied
read model or candidate sequence and validates the frozen experiment, result,
objective, compatibility, and exact derived `StrategyVersion` identities.
The derived version is supplied by the caller's server-side resolver; this
domain never rematerializes it.

The accepted selection methods are human-directed:

- `researcher_judgment`
- `constraint_filtered_researcher_selection`
- `manual_research_selection`

There is no automatic selection method and no ranking or score field.

## Eligibility and evidence

Only a completed `ExperimentCandidateView` with a result identity, IS bounds,
and matching derived strategy identity can be selected. The objective
evaluation must be `PASS`, and experiment compatibility must be `COMPATIBLE`.
Failed, pending, running, missing, inconsistent, failed-objective,
not-evaluable-objective, incompatible, and not-evaluable candidates are
rejected with structured reason codes.

The immutable evidence snapshot binds candidate/result identity, candidate-set
hash, parameter-space hash, objective hash, IS bounds, backtest configuration
hash, engine/analytics versions, compatibility hash/status, and data
provenance. It intentionally does not include equity curves, trades, orders,
fills, or OOS data; the existing backtest/result records remain the source of
truth for those artifacts.

## Identity and future persistence

`selection_hash` is computed from canonical semantic fields with sorted keys,
finite numbers, rationale, and evidence. `created_at` and database row IDs do
not affect it. The frozen dataclass and recursively frozen evidence mappings
make the model immutable. Future persistence must enforce at most one official
selection per experiment at the database layer; that constraint is out of
scope here. A future selection persistence phase must also treat a different
candidate or changed provenance as a conflict rather than silently replacing a
record.

The model is deliberately distinct from PHASE 7's protocol-level
`SelectionDecision`; this phase neither creates that object nor changes
protocol state or strategy freeze state.
