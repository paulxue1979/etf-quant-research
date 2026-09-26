# PHASE 13C Active-Semantics Validation and Asset Isolation

## Scope

Only the active execution mode participates in blocking validation and asset
reference checks. Rule-based and Regime State Machine drafts remain persisted
independently and are not deleted when the other mode is edited.

## Domain Contract

- Rule-based execution validates rules, rule allocations, active fallback,
  no-match behavior, and starting allocation.
- `USE_FALLBACK` requires a present, valid fallback allocation.
- `HOLD_PREVIOUS_ALLOCATION` does not require or validate fallback content.
  Starting allocation remains the initial seed when no previous target exists.
- Regime execution validates initial state, states, transitions, conditions,
  state allocations, and Value Zones. Rule fallback and rule-tree content are
  inactive for this validation path.
- An inactive fallback may be absent or preserved as invalid draft content.
  Switching back to `USE_FALLBACK` makes it blocking again.

## Asset Isolation

The editor keeps `ruleBasedAssets` and `regimeAssets` separately. Asset
removal checks only references in the active mode and returns typed descriptors
for rule conditions, allocations, fallback, starting allocation, state
allocations, transitions, and Value Zones. The UI displays the descriptor and
can locate the relevant editor section.

## Version and Backtest Boundaries

The active strategy payload is the only payload sent to domain validation,
immutable version creation, and backtest entry points. Inactive mode drafts
remain in the draft envelope but cannot block active-mode version creation or
backtest validation.

## Out of Scope

Backtest deletion, grid semantic binding, experiment builder changes, and
changes to runtime accounting or signal semantics are not part of PHASE 13C.
