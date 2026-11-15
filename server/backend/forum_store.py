"""Almacen del foro de hallazgos: publicaciones, votos y ficheros (GIF y espectro).

Solo usa la biblioteca estandar (sqlite3) para poder probarse sin FastAPI.
Todo queda en SPHEREX_FORUM_DIR (por defecto /var/lib/spherex/forum), fuera de
las carpetas que el instalador borra en cada despliegue:

    forum.db           publicaciones y votos (SQLite en modo WAL: lo comparten
                       los 4 workers de uvicorn)
    media/<id>.gif     animacion tal como la vio el autor
    media/<id>.json    espectro de 102 canales del objeto analizado
    salt               sal aleatoria para guardar IPs y votantes solo como hash
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import tempfile
import time

FORUM_DIR = os.environ.get("SPHEREX_FORUM_DIR", "/var/lib/spherex/forum")
POSTS_PER_HOUR = int(os.environ.get("SPHEREX_FORUM_POSTS_PER_HOUR", "6"))
VOTERS_PER_NET = int(os.environ.get("SPHEREX_FORUM_VOTERS_PER_NET", "5"))
STALE_SECONDS = 20 * 60          # una publicacion "procesando" mas vieja se da por fallida

BANDS = re.compile(r"^(D[1-6]|ch([1-9]|[1-9][0-9]|10[0-2]))$")
MODES = ("blink", "diff", "coadd")
STRETCHES = ("asinh", "log", "sqrt", "linear")
VOTER = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class ForumError(Exception):
    """Error con codigo HTTP y mensaje para el usuario."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --------------------------------------------------------------------------- rutas y conexion
def media_dir() -> str:
    return os.path.join(FORUM_DIR, "media")


def media_path(post_id: int, ext: str) -> str:
    return os.path.join(media_dir(), f"{int(post_id)}.{ext}")


_salt: bytes | None = None


def _get_salt() -> bytes:
    global _salt
    if _salt is None:
        path = os.path.join(FORUM_DIR, "salt")
        try:
            with open(path, "rb") as f:
                _salt = f.read()
        except FileNotFoundError:
            data = secrets.token_bytes(32)
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                _salt = data
            except FileExistsError:      # otro worker la creo a la vez
                with open(path, "rb") as f:
                    _salt = f.read()
    return _salt


def hash_id(value: str) -> str:
    return hashlib.sha256(_get_salt() + value.encode("utf-8", "replace")).hexdigest()[:32]


def net_of(ip: str) -> str:
    """Red del votante: /24 en IPv4 y /56 en IPv6 (cambiar de IP dentro de casa no suma votos)."""
    ip = (ip or "").strip()
    if ":" in ip:
        return ":".join(ip.split(":")[:4])
    parts = ip.split(".")
    return ".".join(parts[:3]) if len(parts) == 4 else ip


SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created    REAL    NOT NULL,
    title      TEXT    NOT NULL,
    body       TEXT    NOT NULL DEFAULT '',
    author     TEXT    NOT NULL DEFAULT '',
    target     TEXT    NOT NULL DEFAULT '',
    view       TEXT    NOT NULL,
    ra         REAL    NOT NULL,
    dec        REAL    NOT NULL,
    obj_ra     REAL,
    obj_dec    REAL,
    status     TEXT    NOT NULL DEFAULT 'processing',
    error      TEXT,
    frames     INTEGER,
    n_exp      INTEGER,
    h2o        REAL,
    co2        REAL,
    has_spec   INTEGER NOT NULL DEFAULT 0,
    ups        INTEGER NOT NULL DEFAULT 0,
    downs      INTEGER NOT NULL DEFAULT 0,
    owner_hash TEXT    NOT NULL,
    ip_hash    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_created ON posts(created);
CREATE INDEX IF NOT EXISTS posts_ip ON posts(ip_hash, created);
CREATE TABLE IF NOT EXISTS votes (
    post_id  INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    voter    TEXT    NOT NULL,
    net_hash TEXT    NOT NULL,
    value    INTEGER NOT NULL CHECK (value IN (-1, 1)),
    created  REAL    NOT NULL,
    PRIMARY KEY (post_id, voter)
);
CREATE INDEX IF NOT EXISTS votes_net ON votes(post_id, net_hash);
"""

_ready = False


def connect() -> sqlite3.Connection:
    global _ready
    if not _ready:
        os.makedirs(media_dir(), exist_ok=True)
    db = sqlite3.connect(os.path.join(FORUM_DIR, "forum.db"), timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 15000")
    db.execute("PRAGMA foreign_keys = ON")
    if not _ready:
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA)
        _ready = True
    return db


# --------------------------------------------------------------------------- validacion
def _clean_text(value, max_len: int, multiline: bool = False) -> str:
    text = str(value or "")
    text = "".join(ch for ch in text if ch in "\n\t" or ord(ch) >= 32)   # sin caracteres de control
    if multiline:
        text = re.sub(r"[ \t]+\n", "\n", text.replace("\r\n", "\n"))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    else:
        text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _num(d: dict, key: str, lo: float, hi: float, default=None, integer: bool = False):
    v = d.get(key, default)
    if v is None:
        if default is None:
            raise ForumError(422, f"Falta el campo {key}")
        v = default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ForumError(422, f"{key} no es un numero")
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ForumError(422, f"{key} fuera de rango ({lo}–{hi})")
    return int(round(v)) if integer else v


def clean_view(view) -> dict:
    """Opciones del visor que se publican: las mismas que salen en la URL."""
    if not isinstance(view, dict):
        raise ForumError(422, "Faltan las opciones de la vista")
    band = str(view.get("band", "D4"))
    mode = str(view.get("mode", "blink"))
    stretch = str(view.get("stretch", "asinh"))
    if not BANDS.match(band):
        raise ForumError(422, "Banda no valida")
    if mode not in MODES:
        raise ForumError(422, "Modo no valido")
    if stretch not in STRETCHES:
        raise ForumError(422, "Estirado no valido")
    out = {
        "ra": round(_num(view, "ra", 0, 360), 6),
        "dec": round(_num(view, "dec", -90, 90), 6),
        # el visor deja acercar por debajo de 60" (zoom visual), pero la API construye
        # fotogramas de 60" a 1800": se publica el campo valido mas cercano
        "fov": round(min(1800.0, max(60.0, _num(view, "fov", 1, 7200, 300))), 2),
        "band": band, "mode": mode, "stretch": stretch,
        "invert": bool(view.get("invert", False)),
        "speed": _num(view, "speed", 50, 2000, 500, integer=True),
        "ref": _num(view, "ref", 0, 10000, 0, integer=True),
        "common": bool(view.get("common", True)),
        "cross": bool(view.get("cross", True)),
        "vmin": None, "vmax": None,
    }
    if out["ra"] >= 360:
        out["ra"] = 0.0
    if out["common"] and view.get("vmin") is not None and view.get("vmax") is not None:
        vmin = _num(view, "vmin", -1e12, 1e12)
        vmax = _num(view, "vmax", -1e12, 1e12)
        if vmax > vmin:
            out["vmin"], out["vmax"] = vmin, vmax
    return out


def clean_object(obj) -> tuple[float, float] | None:
    if not obj:
        return None
    if not isinstance(obj, dict):
        raise ForumError(422, "Objeto no valido")
    return round(_num(obj, "ra", 0, 360), 6) % 360.0, round(_num(obj, "dec", -90, 90), 6)


# --------------------------------------------------------------------------- publicaciones
def create_post(data: dict, ip: str, now: float | None = None) -> tuple[int, str]:
    """Crea la publicacion en estado 'processing'. Devuelve (id, token del autor)."""
    now = time.time() if now is None else now
    title = _clean_text(data.get("title"), 120)
    if len(title) < 4:
        raise ForumError(422, "El título necesita al menos 4 caracteres")
    body = _clean_text(data.get("body"), 4000, multiline=True)
    author = _clean_text(data.get("author"), 40)
    target = _clean_text(data.get("target"), 80)
    view = clean_view(data.get("view"))
    obj = clean_object(data.get("object"))
    ip_hash = hash_id("ip:" + (ip or "?"))
    owner = secrets.token_urlsafe(24)
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        recent = db.execute("SELECT COUNT(*) FROM posts WHERE ip_hash = ? AND created > ?",
                            (ip_hash, now - 3600)).fetchone()[0]
        if recent >= POSTS_PER_HOUR:
            db.execute("ROLLBACK")
            raise ForumError(429, f"Máximo {POSTS_PER_HOUR} publicaciones por hora. Prueba más tarde.")
        cur = db.execute(
            "INSERT INTO posts (created, title, body, author, target, view, ra, dec, obj_ra, obj_dec,"
            " owner_hash, ip_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, title, body, author, target, json.dumps(view), view["ra"], view["dec"],
             obj[0] if obj else None, obj[1] if obj else None, hash_id("owner:" + owner), ip_hash))
        db.execute("COMMIT")
        return int(cur.lastrowid), owner
    finally:
        db.close()


def get_row(post_id: int) -> sqlite3.Row | None:
    db = connect()
    try:
        return db.execute("SELECT * FROM posts WHERE id = ?", (int(post_id),)).fetchone()
    finally:
        db.close()


def _write_atomic(path: str, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def render(post_id: int, make_gif, make_spectrum) -> None:
    """Genera y guarda el GIF y el espectro de una publicacion (se ejecuta en segundo plano).

    make_gif(view, obj) -> (bytes, n_fotogramas)
    make_spectrum(ra, dec) -> bytes JSON del espectro de 102 canales
    Un fallo del espectro no tumba la publicacion (sale sin grafica); uno del GIF si.
    """
    row = get_row(post_id)
    if row is None or row["status"] != "processing":
        return
    view = json.loads(row["view"])
    obj = (row["obj_ra"], row["obj_dec"]) if row["obj_ra"] is not None else None
    fields: dict = {}
    try:
        gif, frames = make_gif(view, obj)
        _write_atomic(media_path(post_id, "gif"), gif)
        fields.update(frames=int(frames))
    except Exception as e:
        _update(post_id, status="failed", error=_message(e, "No se pudo generar la animación"))
        return
    sra, sdec = obj if obj else (view["ra"], view["dec"])
    try:
        body = make_spectrum(sra, sdec)
        spec = json.loads(body)
        _write_atomic(media_path(post_id, "json"), body if isinstance(body, bytes) else body.encode())
        diag = spec.get("diagnostics") or {}
        fields.update(has_spec=1, n_exp=spec.get("n_exposures"),
                      h2o=diag.get("h2o_ice_3.05um_depth"), co2=diag.get("co2_4.27um_depth"))
    except Exception as e:
        fields.update(error="Espectro no disponible: " + _message(e, "error al medir"))
    _update(post_id, status="ready", **fields)


def _message(e: Exception, default: str) -> str:
    msg = getattr(e, "detail", None) or getattr(e, "message", None) or str(e) or default
    return str(msg)[:300]


def _update(post_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    db = connect()
    try:
        db.execute(f"UPDATE posts SET {cols} WHERE id = ?", (*fields.values(), int(post_id)))
    finally:
        db.close()


def _effective_status(row, now: float) -> str:
    if row["status"] == "processing" and now - row["created"] > STALE_SECONDS:
        return "failed"
    return row["status"]


def hot_rank(score: int, created: float) -> float:
    """Orden 'popular' al estilo de Reddit: los votos cuentan en escala logaritmica
    y cada 12,5 h de antiguedad pesan como multiplicar los votos por 10."""
    order = math.log10(max(abs(score), 1))
    sign = 1 if score > 0 else -1 if score < 0 else 0
    return sign * order + created / 45000.0


def serialize(row, my_vote: int = 0, owner: bool = False, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    status = _effective_status(row, now)
    pid = row["id"]
    out = {
        "id": pid, "created": row["created"], "title": row["title"], "body": row["body"],
        "author": row["author"], "target": row["target"], "view": json.loads(row["view"]),
        "object": {"ra": row["obj_ra"], "dec": row["obj_dec"]} if row["obj_ra"] is not None else None,
        "status": status,
        "error": row["error"] if status != "processing" else None,
        "frames": row["frames"], "n_exposures": row["n_exp"],
        "diagnostics": {"h2o_ice_3.05um_depth": row["h2o"], "co2_4.27um_depth": row["co2"]},
        "ups": row["ups"], "downs": row["downs"], "score": row["ups"] - row["downs"],
        "my_vote": my_vote, "owner": owner,
        "gif": f"/api/v1/forum/media/{pid}.gif" if status == "ready" else None,
        "spectrum": f"/api/v1/forum/media/{pid}.json" if status == "ready" and row["has_spec"] else None,
    }
    if status == "failed" and row["status"] == "processing":
        out["error"] = "El procesado se interrumpió. Vuelve a publicarlo."
    return out


def _my_votes(db, ids: list[int], voter: str | None) -> dict[int, int]:
    if not voter or not ids or not VOTER.match(voter):
        return {}
    vh = hash_id("voter:" + voter)
    q = f"SELECT post_id, value FROM votes WHERE voter = ? AND post_id IN ({','.join('?' * len(ids))})"
    return {r["post_id"]: r["value"] for r in db.execute(q, (vh, *ids))}


def _is_owner(row, tokens: list[str]) -> bool:
    return any(hash_id("owner:" + t) == row["owner_hash"] for t in tokens if t)


def list_posts(sort: str = "hot", limit: int = 20, offset: int = 0, voter: str | None = None,
               owner_tokens: list[str] | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    db = connect()
    try:
        # se ensenan las listas y las que aun se estan generando; las fallidas no
        base = ("SELECT * FROM posts WHERE status = 'ready' OR (status = 'processing' AND created > ?)")
        args: tuple = (now - STALE_SECONDS,)
        if sort == "new":
            rows = db.execute(base + " ORDER BY created DESC LIMIT ? OFFSET ?", (*args, limit + 1, offset)).fetchall()
        elif sort == "top":
            rows = db.execute(base + " ORDER BY (ups - downs) DESC, created DESC LIMIT ? OFFSET ?",
                              (*args, limit + 1, offset)).fetchall()
        else:
            # el orden "popular" se calcula en Python sobre las 2000 mas recientes
            recent = db.execute(base + " ORDER BY created DESC LIMIT 2000", args).fetchall()
            recent.sort(key=lambda r: hot_rank(r["ups"] - r["downs"], r["created"]), reverse=True)
            rows = recent[offset:offset + limit + 1]
        more = len(rows) > limit
        rows = rows[:limit]
        mine = _my_votes(db, [r["id"] for r in rows], voter)
        tokens = owner_tokens or []
        total = db.execute("SELECT COUNT(*) FROM posts WHERE status = 'ready'").fetchone()[0]
        return {"posts": [serialize(r, mine.get(r["id"], 0), _is_owner(r, tokens), now) for r in rows],
                "more": more, "total": total}
    finally:
        db.close()


def get_post(post_id: int, voter: str | None = None, owner_tokens: list[str] | None = None) -> dict:
    db = connect()
    try:
        row = db.execute("SELECT * FROM posts WHERE id = ?", (int(post_id),)).fetchone()
        if row is None:
            raise ForumError(404, "Esa publicación no existe o se ha borrado")
        mine = _my_votes(db, [row["id"]], voter)
        return serialize(row, mine.get(row["id"], 0), _is_owner(row, owner_tokens or []))
    finally:
        db.close()


def vote(post_id: int, voter: str, value: int, ip: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    if not voter or not VOTER.match(voter):
        raise ForumError(400, "Identificador de votante no válido")
    if value not in (-1, 0, 1):
        raise ForumError(422, "El voto debe ser -1, 0 o 1")
    vh = hash_id("voter:" + voter)
    nh = hash_id("net:" + net_of(ip))
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT status FROM posts WHERE id = ?", (int(post_id),)).fetchone()
            if row is None:
                raise ForumError(404, "Esa publicación no existe o se ha borrado")
            if row["status"] != "ready":
                raise ForumError(409, "Aún se está generando: vota cuando esté lista")
            prev = db.execute("SELECT value FROM votes WHERE post_id = ? AND voter = ?",
                              (int(post_id), vh)).fetchone()
            if value == 0:
                db.execute("DELETE FROM votes WHERE post_id = ? AND voter = ?", (int(post_id), vh))
            else:
                if prev is None:
                    # limite de votantes distintos desde la misma red para frenar el relleno de votos
                    same_net = db.execute("SELECT COUNT(*) FROM votes WHERE post_id = ? AND net_hash = ?",
                                          (int(post_id), nh)).fetchone()[0]
                    if same_net >= VOTERS_PER_NET:
                        raise ForumError(429, "Demasiados votos desde esta red para esta publicación")
                db.execute("INSERT INTO votes (post_id, voter, net_hash, value, created) VALUES (?,?,?,?,?)"
                           " ON CONFLICT(post_id, voter) DO UPDATE SET value = excluded.value",
                           (int(post_id), vh, nh, value, now))
            ups, downs = db.execute(
                "SELECT COALESCE(SUM(value = 1), 0), COALESCE(SUM(value = -1), 0) FROM votes WHERE post_id = ?",
                (int(post_id),)).fetchone()
            db.execute("UPDATE posts SET ups = ?, downs = ? WHERE id = ?", (ups, downs, int(post_id)))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        return {"id": int(post_id), "ups": ups, "downs": downs, "score": ups - downs, "my_vote": value}
    finally:
        db.close()


def delete_post(post_id: int, owner_token: str) -> None:
    db = connect()
    try:
        row = db.execute("SELECT owner_hash FROM posts WHERE id = ?", (int(post_id),)).fetchone()
        if row is None:
            raise ForumError(404, "Esa publicación no existe o se ha borrado")
        if not owner_token or hash_id("owner:" + owner_token) != row["owner_hash"]:
            raise ForumError(403, "Solo quien la publicó puede borrarla (desde el mismo navegador)")
        db.execute("DELETE FROM votes WHERE post_id = ?", (int(post_id),))
        db.execute("DELETE FROM posts WHERE id = ?", (int(post_id),))
    finally:
        db.close()
    for ext in ("gif", "json"):
        try:
            os.unlink(media_path(post_id, ext))
        except OSError:
            pass
