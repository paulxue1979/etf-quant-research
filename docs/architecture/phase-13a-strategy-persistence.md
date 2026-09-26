# PHASE 13A Strategy Persistence

## Boundary

PHASE 13A separates a mutable Strategy identity and editable draft from
immutable executable `StrategyVersion` snapshots. Existing Backtest, Experiment,
Selection, Freeze, and OOS consumers continue to receive immutable version IDs
and content hashes; none of those runtime semantics are changed here.

## Persistence Model

The existing SQLite repository remains the persistence boundary. `strategies`
stores identity metadata, `strategy_versions` stores immutable executable
snapshots, and the additive `strategy_drafts` table stores the latest editable
state. A draft stores both Rule Based and Regime State Machine envelopes,
mode-specific asset lists, the active mode, optional serializable active payload,
source metadata, a deterministic draft hash, and an optimistic `draft_revision`.

The migration is additive and idempotent. Existing strategy rows receive
backfilled `updated_at`, `status`, and metadata defaults. New version provenance
records the source draft revision, draft hash, and active mode. No existing
version row is updated when a new version is created.

## Draft and Version Contract

- Draft save is allowed for incomplete or invalid editor data. The raw typed
  editor envelope is retained even when `active_strategy_payload` is null.
- `PUT /strategy-lab/strategies/{strategy_id}/draft` uses
  `expected_revision`; stale writes return `409 DRAFT_REVISION_CONFLICT`.
- `POST /strategy-lab/strategies/{strategy_id}/versions/from-draft` validates
  only the active serialized payload and creates one new immutable version in
  the same repository transaction as its version-number allocation.
- A version cannot be created from a missing, stale, or non-serializable draft.
- Existing `/strategy-lab/versions` remains available for compatibility with
  existing callers while the Strategy Lab UI uses the draft-first workflow.

## Mode and Asset Isolation

Rule and Regime editor state is stored independently. Switching modes changes
the active asset list without overwriting the other mode's assets. Asset
reference checks are scoped to the active mode, so an asset used only by the
Rule draft does not block removal from the Regime draft. An active Regime
version serializes only Regime semantics; the inactive Rule draft remains
persisted for later editing.

## UI Workflow

The Strategy Lab exposes `Save Draft`, `Validate strategy`, and
`Save new version`. Draft status shows `Draft not saved`, `Unsaved Changes`, or
the persisted revision. A version save is disabled until the current draft is
saved and clean. Loading an immutable version produces an editable dirty draft;
it does not mutate the historical snapshot.

## Out of Scope

Strategy Library browsing, version-history redesign, clone UI, backtest deletion,
Experiment Builder, Grid semantic binding, live trading, and PHASE 13B are not
part of this change.
