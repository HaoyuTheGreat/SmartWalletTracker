"""
FastAPI entrypoint — REST API exposing the BigQuery warehouse to the frontend.

Runs as a Cloud Run Service (separate from the daily Cloud Run Job that
populates the warehouse). The Job writes; this Service reads.

Local dev:
    uvicorn api.main:app --reload
    open http://localhost:8000/docs

Cloud Run: uvicorn binds to $PORT injected by the runtime.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import chat, stats, wallets

app = FastAPI(
    title="SmartWalletsTracker API",
    description="REST API for the Solana smart-wallet dataset built by the SWT pipeline.",
    version="0.1.0",
)

# Allow the Next.js dev server (3000 default, 3001 fallback when 3000 is taken)
# and, later, the Vercel deployment. Production override via CORS_ORIGINS env var
# (comma-separated list) — set on the Cloud Run Service.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:3001",
    ).split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(stats.router)
app.include_router(wallets.router)
app.include_router(chat.router)


@app.get("/")
def health():
    return {"status": "ok", "service": "swt-api"}
