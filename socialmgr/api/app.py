"""One process serves the REST API and the built web app (IMPLEMENTATION_PLAN.md §3)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from socialmgr import __version__
from socialmgr.core.config import Settings
from socialmgr.db.session import make_engine, session_factory
from socialmgr.services import read_models

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(settings: Settings | None = None, web_dist: Path | None = WEB_DIST) -> FastAPI:
    settings = settings or Settings()
    factory = session_factory(make_engine(settings))
    app = FastAPI(
        title="Social Manager", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )

    def get_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    SessionDep = Annotated[Session, Depends(get_session)]

    @app.get("/api/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/shell", tags=["shell"])
    def shell(session: SessionDep) -> read_models.Shell:
        return read_models.shell(session, settings)

    @app.get("/api/posts", tags=["posts"])
    def posts(
        session: SessionDep,
        status: read_models.StatusFilter = "all",
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> read_models.PostsPage:
        return read_models.list_posts(session, settings, status=status, limit=limit, offset=offset)

    if web_dist is not None and (web_dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")
        index = web_dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str, request: Request) -> FileResponse:
            candidate = (web_dist / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(web_dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(index)

    return app
