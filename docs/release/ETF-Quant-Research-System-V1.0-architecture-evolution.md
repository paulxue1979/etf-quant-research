# ETF Quant Research System V1.0 Architecture Evolution

This appendix records the implemented V1.0 architecture in repository history. Commit hashes and messages below are taken from Git history.

| Phase | Commit range / commits | Purpose |
| --- | --- | --- |
| PHASE 0-2 | `ec0f03d`, `e811410`, `2739851` | Project initialization, Tiingo data engine, and MA/EMA indicators |
| Strategy architecture | `48a9e81`, `c0dacb3`, `1f9cd67` | Strategy/portfolio boundaries, relative thresholds, and strategy engine design |
| PHASE 3 | `0dfced6` | Deterministic backtest engine and portfolio accounting |
| PHASE 4A-4I | `5c90c8e` through `4e2edc4` | Strategy models, validation, evaluators, signals, integration, and analytics |
| PHASE 5A-5C | `be56760`, `525dd07`, `7192e9a` | Strategy persistence, Strategy Lab, Backtest Lab, and research UI |
| PHASE 7 | `d3e41a9`, `2ebe766`, `195969c` | Comparison guardrails, Research Protocol, and OOS integrity hardening |
| PHASE 8A-8C | `1a85877`, `c19a292`, `4ce6544` | Experiment model, deterministic candidates, persistence, and provenance |
| PHASE 8D | `3408901`, `95b90d0`, `5b8c06a`, `bed8404`, `2a93b80` | Parameter materialization, IS execution, result persistence, and API |
| PHASE 8E | `f451620` through `8a167d0` | Experiment integrity, exact versions, lifecycle atomicity, selection, handoff, API, frontend, and audit |
| PHASE 8F | `d11c2af` through `afae5af` | Controlled OOS execution, finalization, official view, and end-to-end integrity gate |

The complete authoritative timeline is available with `git log --reverse --oneline`. The release gate does not rewrite or amend history.

## Architectural Decisions

- Strategy evaluation and portfolio accounting remain separate.
- Strategy versions, experiment results, selection decisions, freezes, and official OOS results are immutable records.
- Researcher selection is explicit; the system does not choose a best or recommended strategy.
- Backtest and OOS analytics reuse the existing engines and persisted BacktestRun as source of truth.
- Warmup data is permitted only for indicator calculation and is excluded from the requested performance interval.
- SQLite and bounded provenance are deliberate V1.0 choices; dataset versioning is deferred.
