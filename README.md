# ETF Quant Research System

美股 ETF 量化研究与策略回测平台。

当前 V1.3 提供 Strategy Lab、Backtest Lab、日线与完成周线研究、显式 Regime State Machine、多资产 Target Allocation、受控 Grid Search、参数稳健性分析、Research Protocol、研究员选择与冻结后的官方 OOS Research View。系统定位为历史数据研究与回测工具，不是投资建议、收益保证或实盘交易系统。

## 项目结构

```text
backend/       FastAPI 应用入口
frontend/      React + TypeScript + Vite 前端
data/          Tiingo 数据客户端、标准化、验证与本地缓存
indicators/    MA、EMA 与指标数据契约
strategies/    版本化策略、Regime、Value Zone 与信号求值
backtest/      Target Allocation 回测执行与组合结果
analytics/     绩效、基准、TWR、XIRR 与交易分析
api/           API 契约边界
tests/         Python 测试
docs/          产品、架构、策略、研究与测试文档
```

## 环境要求

- Python 3.12+
- Node.js 20+
- pnpm 11.19.0（与 `frontend/package.json` 声明一致）

API Key 仅通过本地 `.env` 或运行环境中的 `TIINGO_API_KEY` 配置；不得提交凭据。历史数据缓存位于 `data/cache/`，也不得提交。

## 后端

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/uvicorn backend.app.main:app --reload
```

健康检查地址：`http://127.0.0.1:8000/health`

运行后端质量检查：

```bash
.venv/bin/pytest
.venv/bin/ruff check .
```

运行真实 Tiingo 集成验证（配置 `TIINGO_API_KEY` 后）：

```bash
.venv/bin/pytest -m integration
```

## 前端

```bash
cd frontend
pnpm install
pnpm dev
```

运行前端质量检查：

```bash
pnpm lint
pnpm typecheck
pnpm build
```

## 文档

- `docs/architecture/phase-0.md` 记录项目初始化边界与运行约定。
- `docs/architecture/phase-1-data-engine.md` 记录 Tiingo Data Engine 的数据、验证与缓存契约。
- `docs/architecture/phase-2-indicator-engine.md` 记录 MA、EMA 与价格字段契约。
- `docs/architecture/phase-3-backtest-engine.md` 记录回测执行、再平衡与组合会计契约。
- `docs/release/v1.0-final-release-gate.md` 记录 V1.0 发布验收范围、测试矩阵与已知限制。
- `docs/release/v1.2-release-gate.md` 记录 V1.2 多策略比较与长历史验收。
- `docs/release/v1.3.0.md` 记录 V1.3 功能、兼容性、升级路径、限制与最终验证证据。
- `docs/release/ETF-Quant-Research-System-V1.0-architecture-evolution.md` 记录各阶段架构演进与提交历史。

## 研究语义

- Backtest 是历史数据模拟，不是未来预测。
- OOS 结果是冻结研究协议下的一次性历史观察，不代表收益保证。
- 策略信号在交易日 `T` 形成，订单在下一个交易日开盘执行。
- 周线数据只由日线聚合已完成交易周；日内求值只能读取当时已经完成的周线。
- `RAW_CLOSE` 与 `ADJUSTED_CLOSE` 必须在数据、指标、策略和回测链路中保持一致。
- Grid Search 和 Heatmap/Pareto/Stability/Sensitivity 只使用冻结的 IS 结果；候选选择必须由研究员显式完成。
- 当前版本不提供 walk-forward、Monte Carlo、AI 自动选优、税务模型、日内回测或实盘券商执行。
