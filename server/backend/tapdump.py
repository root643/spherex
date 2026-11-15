"""Volcado del catalogo SPHEREx de IRSA TAP a una SQLite local.

Para no depender de IRSA TAP en runtime: baja uri + fecha (mjd) + banda + huella
(poly) de todos los level2 y los guarda con un indice espacial R-Tree. El backend
consulta esta copia (list_exposures_local) cuando TAP no responde, y un timer
systemd la refresca cada noche (paginacion incremental por mjd, reanudable).

La primera pasada completa puede tardar un rato (millones de filas a traves de
TAP); se guarda el progreso en la propia base, asi que si se corta sigue donde
estaba. Sin TAP disponible el script falla en silencio (el timer reintenta).
"""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import sys
import time

import requests

TAP = os.environ.get("SPHEREX_TAP_URL", "https://irsa.ipac.caltech.edu/TAP/sync")
DB = os.environ.get("SPHEREX_CATALOG_DB", "/var/cache/spherex/fits/catalog.db")
PAGE = int(os.environ.get("SPHEREX_TAPDUMP_PAGE", "60000"))
PAUSE_S = float(os.environ.get("SPHEREX_TAPDUMP_PAUSE", "1.5"))

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "spherex-tapdump/1.0 (NASA Space Apps 2026)"

_COLS = "a.uri AS uri, p.time_bounds_lower AS mjd, p.energy_bandpassname AS band, p.poly AS poly"
_WHERE = "a.uri LIKE '%/level2/%fits'"
_KEYSET = f"WHERE {_WHERE} AND p.time_bounds_lower > %f"

# "CJ 269.45 4.69 270.10 4.70 ..." -> pares de coordenadas; poligonos de 4-12 vertices
_FLOAT = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _parse_poly(text: str) -> tuple[float, float, float, float, str] | None:
    """Bbox (ra0, ra1, dec0, dec1) + vertices normalizados a RA en (-180, 180].

    Devuelve None si el texto no se reconoce como lista de pares de coordenadas
    (la fila se guarda igual; solo quedaria sin acceso espacial).
    """
    if not text or not re.search(r"[\s,]", text):
        return None
    vals = [float(v) for v in _FLOAT.findall(text)]
    if len(vals) < 6 or len(vals) % 2:
        return None
    xs, ys = vals[0::2], vals[1::2]
    if not all(-91 <= y <= 91 for y in ys) or not all(-361 <= x <= 361 for x in xs):
        return None
    raw_xs = [(x + 360.0 if x < 0.0 else x) for x in xs]
    raw_span = max(raw_xs) - min(raw_xs)
    norm_xs = [(x - 360.0 if x > 180.0 else x) for x in raw_xs]
    norm_span = max(norm_xs) - min(norm_xs)
    final_xs = norm_xs if norm_span <= raw_span else raw_xs
    pairs = " ".join(f"{x:.5f} {y:.5f}" for x, y in zip(final_xs, ys))
    return min(final_xs), max(final_xs), min(ys), max(ys), pairs


def _pip(poly: list[float], ra: float, dec: float) -> bool:
    """Point-in-polygon (par impar), RA ya normalizado en ambos lados."""
    n = len(poly) // 2
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi, xj, yj = poly[2 * i], poly[2 * i + 1], poly[2 * j], poly[2 * j + 1]
        if (yi > dec) != (yj > dec) and ra < (xj - xi) * (dec - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _init_db(con: sqlite3.Connection) -> str:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS catalog (
        uri TEXT PRIMARY KEY, mjd REAL, band TEXT,
        ra_min REAL, ra_max REAL, dec_min REAL, dec_max REAL, poly TEXT);
    CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
    """)
    con.execute("CREATE INDEX IF NOT EXISTS cat_mjd ON catalog(mjd)")
    rtree = bool(con.execute("SELECT sqlite_compileoption_used('ENABLE_RTREE')").fetchone()[0])
    if rtree:
        con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS rtree_idx USING rtree"
                    "(id, ra_min, ra_max, dec_min, dec_max)")
    else:
        # respaldo si la SQLite no trae R-Tree: B-tree por bbox
        con.execute("CREATE TABLE IF NOT EXISTS rtree_idx AS SELECT rowid AS id, ra_min, ra_max, dec_min, dec_max FROM catalog WHERE 0")
        con.execute("CREATE INDEX IF NOT EXISTS idx_ra ON rtree_idx(ra_min, ra_max)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_dec ON rtree_idx(dec_min, dec_max)")
    return "rtree" if rtree else "btree"


def _get_meta(con: sqlite3.Connection, k: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
    return row[0] if row else default


def _set_meta(con: sqlite3.Connection, k: str, v: str) -> None:
    con.execute("INSERT INTO meta(k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, v))


def _page(last_mjd: float) -> str:
    q = f"SELECT {_COLS} FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid {_KEYSET % last_mjd} ORDER BY p.time_bounds_lower"
    for attempt in range(4):
        try:
            r = _SESSION.post(TAP, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "CSV",
                                         "QUERY": q}, timeout=(30, 600))
            if r.status_code == 200 and "QUERY_STATUS" not in r.text[:2000]:
                return r.text
            raise RuntimeError(f"TAP HTTP {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            wait = 20 * (attempt + 1)
            print(f"  TAP fallo ({e}); reintento en {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError("TAP no responde tras 4 intentos")


def _insert_rows(con: sqlite3.Connection, rows: list[dict]) -> tuple[int, int]:
    n, bad = 0, 0
    for r in rows:
        # replace completo (con rtree): la fila cambia de rowid, hay que limpiar el indice
        con.execute("DELETE FROM catalog WHERE uri = ?", (r["uri"],))
        poly = _parse_poly(r.get("poly") or "")
        if poly is None:
            bad += 1
            con.execute("INSERT INTO catalog(uri, mjd, band) VALUES (?, ?, ?)",
                        (r["uri"], r["mjd"], r["band"]))
            continue
        cur = con.execute("INSERT INTO catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (r["uri"], r["mjd"], r["band"], *poly))
        con.execute("INSERT INTO rtree_idx VALUES (?, ?, ?, ?, ?)",
                    (cur.lastrowid, poly[0], poly[1], poly[2], poly[3]))
        n += 1
    return n, bad


def _preflight() -> bool:
    """Un chequeo barato: si TAP no responde, salir enseguida sin esperar los reintentos."""
    try:
        r = _SESSION.post(TAP, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "CSV",
                                     "QUERY": "SELECT TOP 1 planeid FROM spherex.plane"},
                         timeout=(10, 20))
        return r.status_code == 200 and "QUERY_STATUS" not in r.text[:2000]
    except requests.RequestException:
        return False


def main() -> int:
    if not _preflight():
        print("IRSA TAP no responde (todavia caido); el timer lo reintenta.", flush=True)
        return 0
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    geom = _init_db(con)
    last = float(_get_meta(con, "last_mjd") or 0.0)
    total_new = 0
    t0 = time.time()
    try:
        while True:
            text = _page(last)
            rows = [dict(x) for x in csv.DictReader(io.StringIO(text))]
            for r in rows:
                try:
                    r["mjd"] = float(r["mjd"])
                except (TypeError, ValueError):
                    r["mjd"] = last
            if not rows:
                _set_meta(con, "complete", "1")
                con.commit()
                break
            with con:
                n, bad = _insert_rows(con, rows)
                total_new += len(rows)
                last = max(r["mjd"] for r in rows)
                _set_meta(con, "last_mjd", f"{last:.5f}")
            speed = total_new / max(time.time() - t0, 1e-9)
            print(f"  +{len(rows)} filas (total {total_new}, {speed:.0f} filas/s, sin poligono: {bad})", flush=True)
            time.sleep(PAUSE_S)
    except RuntimeError as e:
        print(f"PARCIAL (se reanuda en el proximo intento): {e}", flush=True)
    finally:
        con.close()
    total = sqlite3.connect(f"file:{DB}?mode=ro", uri=True).execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
    print(f"Catalogo: {total} filas (ultimas {total_new} nuevas) | modo espacial: {geom}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
