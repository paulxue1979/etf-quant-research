"""Application entry point for the ETF Quant Research System API."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.backtest_lab import router as backtest_lab_router
from backend.app.strategy_lab import router as strategy_lab_router

app = FastAPI(
    title="ETF Quant Research System API",
    version="0.1.0",
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


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Return a minimal liveness response for development and deployment checks."""
    return {"status": "ok", "service": "etf-quant-research-api"}
