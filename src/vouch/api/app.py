"""FastAPI app. Local v1 has no auth; the UI is the only client."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vouch import __version__

app = FastAPI(title="vouch", version=__version__)

# The Vite dev server is the only browser client in local v1.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8742", "http://127.0.0.1:8742"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
