# PHASE 13B Strategy Library / Draft / Version Workflow

## Scope

PHASE 13B adds the researcher-facing library workflow on top of the PHASE 13A persistence boundary. Strategy identity remains mutable metadata, drafts remain editable and revisioned, and strategy versions remain immutable snapshots.

## Workflow

1. Strategy Library lists identities with draft state and latest version metadata. Search matches name, ID, and description; mode filtering is optional and local to the read model.
2. New Strategy creates a new identity and a clean QQQ-only draft. It does not copy the catalog default assets, rules, regimes, or version lineage.
3. Open Draft loads the persisted draft. Invalid drafts remain editable and persistable; only a valid serialized payload can create an immutable version.
4. Historical versions are read-only. The view exposes the full serialized semantic configuration, mode, assets, draft provenance, and content hash.
5. Create Draft From Version is an explicit persistence operation. If a current draft exists, the API returns `DRAFT_REPLACE_CONFIRMATION_REQUIRED` until the researcher confirms replacement. The resulting draft has a new revision and records source version identity/hash.
6. Clone / Save As New Strategy creates a new strategy identity and draft atomically. No `strategy_versions` row is created and no version lineage is inherited.
7. Version diff recursively compares semantic Rule and Regime payload fields. It never mutates either snapshot.
8. Backtest results and history expose Open Strategy Version only when both strategy and version identity are present. Legacy records show `Strategy version reference unavailable` and are never guessed.

## Concurrency and Integrity

Draft writes and draft-from-version replacement use the existing SQLite `BEGIN IMMEDIATE` boundary and expected revision. Version creation continues to use the PHASE 13A immutable snapshot contract. Clone is one transaction for the new identity and draft, so a failed clone cannot leave a partial strategy identity.

## Out Of Scope

Strategy deletion, mode-scoped validation changes, asset diagnostics, backtest deletion/trash, grid binding, experiment builder, and PHASE 13C work are intentionally excluded.
