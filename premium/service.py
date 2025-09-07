"""Simple REST service for distributing precomputed ranges and receiving telemetry."""
from __future__ import annotations

from typing import Any, Dict, List

try:  # FastAPI is optional at runtime unless the service is started.
    from fastapi import FastAPI, HTTPException
except ModuleNotFoundError:  # pragma: no cover - handled if FastAPI not installed
    FastAPI = None  # type: ignore
    HTTPException = Exception  # type: ignore

from .license import PremiumManager

app = FastAPI(title="AllInKeys Premium Service") if FastAPI else None


if app:
    @app.get("/ranges")
    def get_ranges(token: str) -> Dict[str, List[List[int]]]:
        pm = PremiumManager(token)
        if not pm.distributed_gpu_enabled():
            raise HTTPException(status_code=403, detail="Premium license required")
        # Placeholder ranges; real implementation would fetch from database.
        ranges = [[0, 1000], [1000, 2000]]
        return {"ranges": ranges}

    @app.post("/telemetry")
    def telemetry(data: Dict[str, Any]) -> Dict[str, str]:
        # In reality, this would persist telemetry for analytics.
        print("Telemetry received", data)  # noqa: T201 - diagnostic output
        return {"status": "ok"}


def run(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the premium FastAPI service."""
    if not app:
        raise RuntimeError("FastAPI is required to run the premium service")
    import uvicorn

    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    run()

