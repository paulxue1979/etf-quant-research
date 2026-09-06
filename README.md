# ETF Quant Research System

美股 ETF 量化研究与策略回测平台。

当前处于 **PHASE 1 - Tiingo Data Engine**。本阶段仅包含 Tiingo 日线数据获取、标准化、严格数据质量验证和本地磁盘持久化缓存；不包含指标、策略、回测、绩效分析或业务前端功能。

## 项目结构

```text
backend/       FastAPI 应用入口
frontend/      React + TypeScript + Vite 前端
data/          Tiingo 数据客户端、标准化、验证与本地缓存
indicators/    后续指标模块边界
strategies/    后续策略模块边界
backtest/      后续回测模块边界
analytics/     后续绩效分析模块边界
api/           后续 API 契约边界
tests/         Python 测试
docs/          产品、架构、策略、研究与测试文档
```

## 环境要求

- Python 3.12+
- Node.js 20+
- pnpm 10+

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
- 后续阶段的产品、策略、研究和测试文档将按模块补充。
