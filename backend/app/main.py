"""Application entry point for the ETF Quant Research System API."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.backtest_lab import router as backtest_lab_router
from backend.app.backtest_report_api import router as backtest_report_router
from backend.app.grid_search_api import router as grid_search_router
from backend.app.oos_research_api import router as oos_research_router
from backend.app.optimization_research_api import router as optimization_research_router
from backend.app.research_execution_api import router as research_execution_router
from backend.app.research_experiment_api import router as research_experiment_router
from backend.app.research_protocol_api import router as research_protocol_router
from backend.app.strategy_lab import router as strategy_lab_router

app = FastAPI(
    title="ETF Quant Research System API",
    version="1.3.0",
    description="Quantitative research API with persistent Strategy Lab version endpoints.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(strategy_lab_router)
app.include_router(backtest_lab_router)
app.include_router(backtest_report_router)
app.include_router(research_protocol_router)
app.include_router(research_execution_router)
app.include_router(grid_search_router)
app.include_router(research_experiment_router)
app.include_router(oos_research_router)
app.include_router(optimization_research_router)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Return a minimal liveness response for development and deployment checks."""
    return {"status": "ok", "service": "etf-quant-research-api"}
