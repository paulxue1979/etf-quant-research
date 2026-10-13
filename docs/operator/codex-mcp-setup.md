# Local Codex MCP bridge (Draft and Backtest proposals)

This bridge exposes read-only inspection tools and two proposal creation tools
over local stdio. It does not use the
public API, start a web listener, contact OpenAI, or fetch market data. The
Strategy Lab API server may be started separately for the browser UI, but is
not required for MCP. The strategy database must already exist and be at the
current schema. Strategy inspection uses SQLite read-only mode and never runs
repository initialization or migrations. Draft proposal records use a separate
`data/operator_change_sets.db`; backtest configuration preparation creates an
isolated `pending_backtest_configurations` table in the strategy/backtest SQLite
database so that a later human-approved BacktestRun and its terminal plan state
can be committed atomically. Strategy and Draft rows remain unchanged.

## Install and configure

From the repository root, install the project in its virtual environment:

```sh
.venv/bin/python -m pip install -e '.[operator]'
```

For Codex, add the following to this trusted project's `.codex/config.toml`
(create it if needed), adjusting the absolute path if the checkout differs:

```toml
[mcp_servers.etf_quant]
command = "/Users/gaozhiyong/Projects/etf-quant-research/.venv/bin/python"
args = ["-m", "backend.operator_mcp"]
cwd = "/Users/gaozhiyong/Projects/etf-quant-research"
enabled = true
```

Codex loads project-level `.codex/config.toml` only for trusted projects.
Do not put API keys or other credentials in this configuration. This file is
not installed or changed automatically by the bridge. For a one-off run,
use Codex `-c` overrides for these three values instead of changing global
`~/.codex/config.toml`.

## Verify

Restart Codex after adding the configuration, then check `codex mcp list`.
Ask Codex to call `quant_health` and `quant_list_strategies`. The tool list must
contain only these fifteen names:

- `quant_health`
- `quant_list_strategies`
- `quant_get_strategy`
- `quant_get_draft`
- `quant_get_strategy_structure`
- `quant_get_semantic_targets`
- `quant_validate_draft`
- `quant_get_data_availability`
- `quant_prepare_draft_changes`
- `quant_get_change_set`
- `quant_get_strategy_version`
- `quant_prepare_backtest_configuration`
- `quant_get_backtest_configuration`
- `quant_list_pending_backtest_configurations`
- `quant_get_backtest_result`

`quant_get_draft` reports `draft_id: null` because the current durable Draft
model has no separate Draft ID; its `strategy_id`, revision and hash identify
the observed state. Structure reads the active Draft by default or the exact
immutable version when `version_id` is supplied. Semantic targets and data
availability require an immutable version ID. Data availability uses only an
existing, exact-key local cache entry. A cache miss returns a blocking
availability projection with `cache_miss_symbols`; this is missing local
evidence, not proof that an asset has no market history. The tool never
triggers Tiingo or any other network call.

`quant_prepare_draft_changes` accepts a Draft revision and one to sixteen
typed, semantic-ID operations. It clones the active Draft, runs canonical
validation, and persists the hypothetical diff and hashes as a `PROPOSED`
ChangeSet. It **does not save the Draft**, create a StrategyVersion, or run a
backtest. Validation failures can be recorded as non-approvable proposals.
Invalid targets or forbidden Peak Drawdown exits are rejected without a
proposal. A later Draft revision makes a proposal appear `STALE` on reads;
the read does not mutate either database. Reconnecting the MCP server can
read the same proposal by ID.

`quant_prepare_draft_changes` is annotated as non-read-only because it writes
an isolated proposal record. `quant_prepare_backtest_configuration` is also
non-read-only because it writes a pending configuration, but it does not run a
backtest. It requires exact ISO requested dates, positive initial capital, and
an exact immutable StrategyVersion ID. To use the latest version, resolve it
with `quant_get_strategy_version` first and pin the returned ID/hash. Dates or
contribution instructions that cannot be uniquely resolved must be clarified
before calling prepare. Supported contributions are one-time and monthly;
monthly spans the whole backtest window. Weekly and independently bounded
contribution windows are rejected, not approximated. A cache-only Data
Availability miss leaves the plan blocked until verified data is present.

Backtest Lab lists the durable proposals and preloads the newest pending plan
into locked form fields on a fresh visit. Only its browser-origin-gated
`Approve & Run Backtest` action may execute. The plan and BacktestRun remain
readable after restart. `quant_get_backtest_result` returns stored production
analytics and does not recalculate them. No MCP approve, run, reject, Draft
save, version creation, experiment, Grid, Selection, Freeze, OOS, or delete
tool is registered. The local Codex process must still be trusted because it
can read the same local strategy data as the user.

## Disable

Remove the `[mcp_servers.etf_quant]` table, or set `enabled = false`, and
restart Codex. Do not edit the global Codex config for this project-specific
integration unless you intentionally want it available outside this checkout.

References: [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
