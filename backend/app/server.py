"""Production entry point: API and dashboard on one origin.

    uvicorn app.server:root          (what the Docker image runs)

The API from app.main is mounted under /api, and the built React dashboard
(frontend/dist) is served for every other path, so page routes like /projects
never collide with API routes like /api/projects.

Local development doesn't use this file: run `uvicorn app.main:app` and `npm run dev`.
"""
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .main import app as api

DIST = os.getenv("FRONTEND_DIST", os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist"))
DIST = os.path.abspath(DIST)

root = FastAPI(title="Pay-on-Proof", docs_url=None, redoc_url=None, openapi_url=None)
root.mount("/api", api)


@root.get("/health")
def health():
    return {"ok": True}


if os.path.isdir(os.path.join(DIST, "assets")):
    root.mount("/assets", StaticFiles(directory=os.path.join(DIST, "assets")), name="assets")


@root.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    """Serve real files from dist, and index.html for client-side routes (/projects/3, /w/1, ...)."""
    if not os.path.isfile(os.path.join(DIST, "index.html")):
        raise HTTPException(503, "Dashboard not built. Run `npm run build` in frontend/.")
    candidate = os.path.abspath(os.path.join(DIST, path))
    if path and candidate.startswith(DIST + os.sep) and os.path.isfile(candidate):
        return FileResponse(candidate)
    return FileResponse(os.path.join(DIST, "index.html"))
