# PHASE 13K: Selection, Freeze, and Official OOS

## Scope

PHASE 13K connects persisted IS candidate evidence to one explicit researcher
selection, the exact derived `StrategyVersion`, and one official OOS
observation. It does not rank candidates, select a winner, tune parameters, or
change strategy/backtest semantics.

## Selection

The Experiment Research API accepts only the candidate identifier, a
human-directed selection method, and optional rationale. All hashes, dates,
metrics, parameter bindings, and derived-version identity are resolved and
revalidated from persisted IS evidence. There is one active selection per
experiment. Before a protocol freeze, a replacement overwrites that active
row and appends a `SELECTION_REPLACED_BEFORE_FREEZE` lifecycle event containing
the previous selection provenance. After a freeze, replacement is rejected.

## Freeze

The existing atomic PHASE 7 handoff persists the selection decision and
`StrategyFreezeRecord` together. The freeze binds the exact derived version ID
and content hash, candidate set, IS backtest, configuration, and provenance.
The handoff is idempotent for the same identity and rejects mismatched
identities.

## Official OOS

The official OOS workflow has three controlled steps:

1. `POST /research/protocols/{protocol_id}/oos/prepare` creates or reloads the
   single execution from the frozen protocol. It accepts no overrides.
2. `POST /research/protocols/{protocol_id}/oos/execute` claims the persisted
   execution and delegates data, signal, backtest, and analytics work to the
   existing PHASE 8F service, then atomically finalizes the official result.
3. `GET /research/protocols/{protocol_id}/oos` exposes the immutable read view;
   the view contains frozen configuration, IS selection metrics, and official
   OOS metrics without recalculation or an automatic verdict.

Preparation and execution are gated by exactly one protocol selection and one
freeze. The execution identity is the frozen strategy/version/hash and cannot
be changed by the API. A completed execution is returned idempotently; a
retryable failure reuses the same execution identity and lease/recovery
contract.

## Research UI

The existing research page now shows the active selected candidate, permits a
new human selection only before handoff, and highlights the persisted
candidate in the optimization table. After handoff it shows the frozen
identity and offers prepare/run/open actions for official OOS. The read-only
OOS page compares persisted selected IS metrics with persisted OOS metrics and
shows the frozen configuration and provenance.

## Security and Integrity

The OOS workflow accepts no date, parameter, or configuration override. Error
responses contain stable safe codes only. Provenance excludes credentials and
is validated against persisted selection, freeze, backtest, and result hashes.
