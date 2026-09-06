# ETF Quant Research System

美股 ETF 量化研究与策略回测平台。

当前处于 **PHASE 0 - 项目初始化**。本阶段仅建立工程基础，不包含 Tiingo 数据接入、指标、策略、回测或业务 Dashboard 功能。

## 项目结构

```text
backend/       FastAPI 应用入口
frontend/      React + TypeScript + Vite 前端
data/          后续市场数据与本地缓存边界
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

API Key 仅通过本地 `.env` 配置；请从 `.env.example` 创建该文件，且不要提交凭据。

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

- `docs/architecture/phase-0.md` 记录本阶段的架构边界与运行约定。
- 后续阶段的产品、策略、研究和测试文档将按模块补充。
