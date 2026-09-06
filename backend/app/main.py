"""Application entry point for the Phase 0 backend foundation."""

from fastapi import FastAPI

app = FastAPI(
    title="ETF Quant Research System API",
    version="0.1.0",
    description="Backend foundation. Quantitative research endpoints are added in later phases.",
)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Return a minimal liveness response for development and deployment checks."""
    return {"status": "ok", "service": "etf-quant-research-api"}
