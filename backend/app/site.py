"""Serve the built research UI when running the combined deployment image."""
from pathlib import Path
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def register_site(application: FastAPI) -> None:
    setting = os.getenv("EARTHSCOPE_FRONTEND_DIR")
    if not setting:
        return
    root = Path(setting).resolve()
    if not (root / "index.html").is_file():
        raise RuntimeError("EARTHSCOPE_FRONTEND_DIR must contain the built index.html")
    application.mount("/assets", StaticFiles(directory=root / "assets"), name="assets")

    @application.get("/{path:path}", include_in_schema=False)
    def site_file(path: str) -> FileResponse:
        # API errors must retain their API envelope, including unknown routes.
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(404, "File not found")
        if candidate.is_file():
            return FileResponse(candidate)
        if path == "" or path == "research" or path.startswith("research/"):
            return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
        raise HTTPException(404, "File not found")
