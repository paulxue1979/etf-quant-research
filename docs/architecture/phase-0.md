# PHASE 0 架构记录

## 范围

本阶段只建立单仓库工程骨架和可验证的开发环境。任何 Tiingo 请求、行情缓存、指标、策略、回测与绩效计算都不在本阶段实现。

## 后端边界

`backend/app/main.py` 是 FastAPI 入口。`/health` 仅用于确认服务进程能够启动和响应；它不是量化业务 API。

## 前端边界

`frontend/` 使用 React、TypeScript 和 Vite。当前页面仅确认构建与渲染链路可用，不使用模拟市场数据。

## 运行时约定

- 后端最低版本：Python 3.12。
- 前端最低版本：Node.js 20、pnpm 10。
- 密钥只存放在本地 `.env`；`.env.example` 仅保留变量名。
- 本地缓存和生成数据应放在 `data/cache/`，并由 Git 忽略。

## 后续边界

PHASE 1 才可在 `data/` 中实现 Tiingo Client、标准化、验证与缓存。各阶段必须通过 Phase Gate 后才可进入下一阶段。
