"""Aplicação FastAPI: API + eventos (SSE) + arquivos de mídia + interface web estática."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from studio.api import routes_media, routes_misc, routes_projects, routes_publish
from studio.config import REPO_ROOT, get_settings
from studio.db import init_db
from studio.events import bus

log = logging.getLogger(__name__)


BUFFER_WATCH_S = 10 * 60


async def _buffer_watch() -> None:
    """De 10 em 10 minutos confere os posts pendentes no Buffer e apaga do R2 os vídeos dos que já saíram."""
    from studio.workers.publish import buffer_flow

    while True:
        await asyncio.sleep(BUFFER_WATCH_S)
        await asyncio.to_thread(buffer_flow.sync_quietly)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from studio.workers.jobs import get_runner

    get_runner().recover()
    settings = get_settings()
    watcher = asyncio.create_task(_buffer_watch()) if settings.workers_enabled and not settings.offline else None
    yield
    if watcher is not None:
        watcher.cancel()
    from studio.media.render import get_render_client

    get_render_client().shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Vulto", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(routes_projects.router)
    app.include_router(routes_misc.router)
    app.include_router(routes_publish.router)
    app.include_router(routes_media.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        queue = bus.subscribe()

        async def stream():
            try:
                yield "retry: 3000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    data_root = settings.data_dir.resolve()

    @app.get("/files/{path:path}")
    def files(path: str) -> FileResponse:
        full = (data_root / path).resolve()
        if not full.is_relative_to(data_root) or not full.is_file():
            raise HTTPException(status_code=404, detail="arquivo não encontrado")
        if full.name.startswith(".") or full.suffix in (".db", ".key"):
            raise HTTPException(status_code=403, detail="acesso negado")
        return FileResponse(full)

    fonts_root = (REPO_ROOT / "assets" / "fonts").resolve()

    @app.get("/fonts/{name}")
    def fonts(name: str) -> FileResponse:
        full = (fonts_root / name).resolve()
        if not full.is_relative_to(fonts_root) or not full.is_file():
            raise HTTPException(status_code=404, detail="fonte não encontrada")
        return FileResponse(full)

    textures_root = (REPO_ROOT / "assets" / "textures").resolve()

    @app.get("/textures/{name}")
    def textures(name: str) -> FileResponse:
        full = (textures_root / name).resolve()
        if not full.is_relative_to(textures_root) or not full.is_file():
            raise HTTPException(status_code=404, detail="textura não encontrada")
        return FileResponse(full, headers={"Cache-Control": "public, max-age=86400"})

    web_dist = settings.web_dist_dir

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "files/", "fonts/", "textures/")):
            raise HTTPException(status_code=404)
        candidate = (web_dist / full_path).resolve() if full_path else None
        if candidate and candidate.is_file() and candidate.is_relative_to(web_dist.resolve()):
            if candidate.name == "index.html":
                return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
            # Os arquivos de assets/ têm o hash do conteúdo no nome: nunca mudam.
            immutable = full_path.startswith("assets/")
            return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable"} if immutable else None)
        index = web_dist / "index.html"
        if index.exists():
            # Sempre confere com o servidor: depois de atualizar o app, a página não fica presa na versão antiga.
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(
            "<h1>Vulto</h1><p>A interface web ainda não foi compilada. Rode <code>npm run web:build</code> "
            "na raiz do projeto, ou use <code>npm run web:dev</code> e abra http://127.0.0.1:5173.</p>"
        )

    return app


def _web_dist_exists() -> bool:
    return (Path(get_settings().web_dist_dir) / "index.html").exists()
