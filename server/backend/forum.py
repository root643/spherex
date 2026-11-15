"""Rutas del foro de hallazgos (/api/v1/forum/...). La logica esta en forum_store."""
from __future__ import annotations

import re
from typing import Callable, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse

import forum_store as store

router = APIRouter(prefix="/api/v1/forum", tags=["foro"])
_hooks: dict[str, Callable] = {}
NO_STORE = {"Cache-Control": "no-store"}    # nginx no debe cachear listas ni votos


def init(make_gif: Callable, make_spectrum: Callable) -> None:
    """main.py inyecta aqui como se genera el GIF y el espectro (evita importacion circular)."""
    _hooks["gif"] = make_gif
    _hooks["spectrum"] = make_spectrum


def _ip(request: Request) -> str:
    cf_ip = request.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "")


def _tokens(header: Optional[str]) -> list[str]:
    return [t.strip() for t in (header or "").split(",") if t.strip()][:200]


def _err(e: store.ForumError) -> JSONResponse:
    return JSONResponse({"detail": e.message}, status_code=e.status, headers=NO_STORE)


def _render(post_id: int) -> None:
    store.render(post_id, _hooks["gif"], _hooks["spectrum"])


@router.post("/posts")
def create_post(request: Request, background_tasks: BackgroundTasks, data: dict = Body(...)):
    """Publica un hallazgo. Responde enseguida; el GIF y el espectro se generan detras."""
    try:
        post_id, owner = store.create_post(data, _ip(request))
    except store.ForumError as e:
        return _err(e)
    background_tasks.add_task(_render, post_id)
    return JSONResponse({"id": post_id, "owner_token": owner, "status": "processing"},
                        status_code=201, headers=NO_STORE)


@router.get("/posts")
def list_posts(sort: str = Query("hot", pattern="^(hot|new|top)$"),
               limit: int = Query(15, ge=1, le=50), offset: int = Query(0, ge=0, le=100000),
               x_voter: Optional[str] = Header(None), x_owner_tokens: Optional[str] = Header(None)):
    return JSONResponse(store.list_posts(sort, limit, offset, x_voter, _tokens(x_owner_tokens)),
                        headers=NO_STORE)


@router.get("/posts/{post_id}")
def get_post(post_id: int, x_voter: Optional[str] = Header(None),
             x_owner_tokens: Optional[str] = Header(None)):
    try:
        return JSONResponse(store.get_post(post_id, x_voter, _tokens(x_owner_tokens)), headers=NO_STORE)
    except store.ForumError as e:
        return _err(e)


@router.post("/posts/{post_id}/vote")
def vote(post_id: int, request: Request, data: dict = Body(...), x_voter: Optional[str] = Header(None)):
    try:
        value = int(data.get("value", 0))
        return JSONResponse(store.vote(post_id, x_voter or "", value, _ip(request)), headers=NO_STORE)
    except (TypeError, ValueError):
        return _err(store.ForumError(422, "El voto debe ser -1, 0 o 1"))
    except store.ForumError as e:
        return _err(e)


@router.delete("/posts/{post_id}")
def delete_post(post_id: int, x_owner_token: Optional[str] = Header(None)):
    try:
        store.delete_post(post_id, x_owner_token or "")
    except store.ForumError as e:
        return _err(e)
    return JSONResponse({"deleted": post_id}, headers=NO_STORE)


_MEDIA = re.compile(r"^(\d{1,12})\.(gif|json)$")


@router.get("/media/{name}")
def media(name: str):
    """GIF y espectro guardados: no cambian nunca; se cachean una semana (si se borra la publicacion, deja de servirse en dias)."""
    m = _MEDIA.match(name)
    if not m:
        return JSONResponse({"detail": "No encontrado"}, status_code=404, headers=NO_STORE)
    path = store.media_path(int(m.group(1)), m.group(2))
    try:
        open(path, "rb").close()
    except OSError:
        return JSONResponse({"detail": "No encontrado"}, status_code=404, headers=NO_STORE)
    kind = "image/gif" if m.group(2) == "gif" else "application/json"
    return FileResponse(path, media_type=kind,
                        headers={"Cache-Control": "public, max-age=604800"})
