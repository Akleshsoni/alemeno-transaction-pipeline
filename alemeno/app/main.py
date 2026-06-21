from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import init_db
from app.api.routes import router as jobs_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB tables on startup."""
    await init_db()
    yield


app = FastAPI(
    title="AI-Powered Transaction Processing Pipeline",
    description=(
        "Async pipeline that cleans financial CSVs, detects anomalies, "
        "classifies transactions via LLM, and generates structured reports."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs_router)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "service": "transaction-pipeline"}
