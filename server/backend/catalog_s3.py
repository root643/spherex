"""Constructor del catalogo SPHEREx desde las cabeceras FITS del bucket S3.

Alternativa a tapdump.py que NO depende de IRSA TAP en absoluto: recorre el
bucket publico (dr1/ qr2/ qr3/, level2), lee los primeros ~29 KB de cada
fichero por HTTP Range (cabecera del HDU IMAGE, offset 2880), y guarda en la
MISMA SQLite que tapdump: uri, mjd (MJD-OBS), banda (DETECTOR) y huella
(poligono de 8 puntos de borde calculado con astropy WCS, SIP incluido).

Reanudable: guarda meta.last_key_<prefijo> tras cada pagina del listado, y el
vuelco siguiente empieza en start-after. Al terminar una pasada completa deja
meta.complete = 1 (el mismo marcador que consulta el backend).

Optimizado:
- Multiprocesamiento paralelo a traves de 4 vCPUs (ProcessPoolExecutor(4)).
- Concurrencia de red ampliada (4 procesos x 25-30 hilos = 100-120 hilos I/O).
- Huella de 8 vertices en orden perimetral horario (clockwise perimeter) para
  eliminar poligonos auto-intersecantes.
- Correccion de antimeridiano (180 deg) y meridiano (0 deg) que elimina bboxes
  de 359 deg de ancho.
- Soporte para declinaciones en todo el rango celeste (-90 deg a +90 deg).

Uso:  nohup /opt/spherex-env/bin/python catalog_s3.py > /tmp/catalog_s3.log 2>&1 &
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import requests

import warnings
warnings.filterwarnings("ignore")

from tapdump import DB, _get_meta, _init_db, _set_meta  # noqa: E402

S3 = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/"
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
URI_PREFIX = "ibe/data/spherex/"
PREFIXES = ("dr1/level2/", "qr2/level2/", "qr3/level2/")
RANGE = "bytes=0-28799"
PROCESSES = int(os.environ.get("SPHEREX_S3CAT_PROCS", "4"))
THREADS_PER_PROC = int(os.environ.get("SPHEREX_S3CAT_THREADS", "25"))

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "spherex-catalog-s3/2.0 (NASA Space Apps 2026)"
_adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=20)
_SESSION.mount("https://", _adapter)

_META_KEY = {p: "last_key_" + p.strip("/").replace("/", "_") for p in PREFIXES}

# --------------------------------------------------------------------------- worker state
_worker_session: requests.Session | None = None


def _init_worker():
    """Inicializa la sesion HTTP y precarga WCS en cada worker del pool."""
    global _worker_session
    _worker_session = requests.Session()
    _worker_session.headers["User-Agent"] = "spherex-catalog-s3-worker/2.0"
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=THREADS_PER_PROC + 10,
        pool_maxsize=THREADS_PER_PROC * 2
    )
    _worker_session.mount("https://", adapter)
    from astropy.wcs import WCS
    from astropy.io.fits import Header
    _ = (WCS, Header)


# --------------------------------------------------------------------------- listado S3
def _list_pages(prefix: str, start_after: str):
    """Paginas de claves (1000/pag) bajo prefix, empezando tras start_after."""
    token = None
    while True:
        url = (S3 + "?list-type=2&prefix=" + requests.utils.quote(prefix) + "&max-keys=1000")
        if token:
            url += "&continuation-token=" + token
        if start_after:
            url += "&start-after=" + requests.utils.quote(start_after)
        for attempt in range(5):
            try:
                with _SESSION.get(url, timeout=(15, 60)) as r:
                    r.raise_for_status()
                    root = ET.fromstring(r.content)
                break
            except Exception as e:
                if attempt == 4:
                    raise
                print(f"  listado fallo ({e}); reintento", flush=True)
                time.sleep(10 * (attempt + 1))
        keys = [c.find(NS + "Key").text for c in root.findall(NS + "Contents")]
        tok = root.find(NS + "NextContinuationToken")
        token = requests.utils.quote(tok.text) if tok is not None else None
        yield keys
        if token is None:
            return


# --------------------------------------------------------------------------- parsing y WCS
def _hdr_val(hdr_text: str, kw: str):
    m = re.search(rf"^{re.escape(kw)}\s*=\s*(.+?)\s*/.*$", hdr_text, re.M)
    if not m:
        m = re.search(rf"^{re.escape(kw)}\s*=\s*(\S+)", hdr_text, re.M)
    if not m:
        return None
    v = m.group(1).strip().strip("'").strip()
    try:
        return float(v)
    except ValueError:
        return v


def _parse_image_header(data: bytes) -> str | None:
    """Texto de la cabecera del HDU IMAGE (para astropy), o None."""
    i = data.find(b"XTENSION= 'IMAGE")
    if i < 0:
        i = data.find(b"XTENSION= 'BINTABLE")
    if i < 0:
        return None
    cards = []
    for off in range(i, min(len(data), i + 40000), 80):
        card = data[off:off + 80].decode("ascii", "replace")
        if card[:8].strip() == "END":
            cards.append("END".ljust(80))
            break
        cards.append(card.rstrip().ljust(80))
    if not any(c.startswith("END") for c in cards):
        return None
    return "\n".join(cards)


def _worker_fetch_and_row(key: str) -> dict | None:
    """Descarga por HTTP Range y computa WCS + huella en un hilo de proceso worker."""
    global _worker_session
    url = S3 + requests.utils.quote(key)
    body = None
    for attempt in range(3):
        try:
            with _worker_session.get(url, headers={"Range": RANGE}, timeout=(12, 35)) as r:
                if r.status_code in (200, 206):
                    body = r.content[:28800] if r.status_code == 200 else r.content
                    if len(body) >= 28800:
                        break
        except Exception:
            if attempt == 2:
                return None
            time.sleep(1.5 * (attempt + 1))
    if body is None or len(body) < 28800:
        return None

    hdr_text = _parse_image_header(body)
    if hdr_text is None:
        return None

    try:
        from astropy.wcs import WCS
        from astropy.io.fits import Header
        w = WCS(Header.fromstring(hdr_text, sep="\n"), relax=True)
        if not w.has_celestial:
            return None
        nax1 = _hdr_val(hdr_text, "NAXIS1") or 2040.0
        nax2 = _hdr_val(hdr_text, "NAXIS2") or 2040.0

        # Orden perimetral horario (clockwise): bottom-left -> bottom-mid -> bottom-right ->
        # right-mid -> top-right -> top-mid -> top-left -> left-mid (poligono continuo)
        pts = []
        for fy, fx in ((0.0, 0.0), (0.0, 0.5), (0.0, 1.0), (0.5, 1.0),
                       (1.0, 1.0), (1.0, 0.5), (1.0, 0.0), (0.5, 0.0)):
            pts.append((fx * (nax1 - 1.0) + 1.0, fy * (nax2 - 1.0) + 1.0))
        ra, dec = w.all_pix2world([p[0] for p in pts], [p[1] for p in pts], 0)

        xs = [float(r) for r in ra]
        ys = [float(d) for d in dec]

        # Antimeridiano (180 deg) y Meridiano (0 deg): seleccion del marco con menor dispersion
        raw_xs = [(x + 360.0 if x < 0.0 else x) for x in xs]
        raw_span = max(raw_xs) - min(raw_xs)
        norm_xs = [(x - 360.0 if x > 180.0 else x) for x in raw_xs]
        norm_span = max(norm_xs) - min(norm_xs)
        final_xs = norm_xs if norm_span <= raw_span else raw_xs

        ra_min, ra_max = min(final_xs), max(final_xs)
        dec_min, dec_max = min(ys), max(ys)
        poly = " ".join(f"{x:.5f} {y:.5f}" for x, y in zip(final_xs, ys))

        mjd = _hdr_val(hdr_text, "MJD-OBS")
        if mjd is None:
            return None

        det = _hdr_val(hdr_text, "DETECTOR")
        if det is None:
            m = re.search(r"_(\d)D([1-6])_", key)
            det = m.group(2) if m else "1"
        band = f"SPHEREx-D{int(det)}"

        return {
            "uri": URI_PREFIX + key,
            "mjd": float(mjd),
            "band": band,
            "ra_min": ra_min,
            "ra_max": ra_max,
            "dec_min": dec_min,
            "dec_max": dec_max,
            "poly": poly
        }
    except Exception:
        return None


def _process_chunk(keys: list[str]) -> list[dict]:
    """Descarga y procesa un bloque de claves en paralelo con el pool de hilos local."""
    with ThreadPoolExecutor(THREADS_PER_PROC) as ex:
        rows = list(ex.map(_worker_fetch_and_row, keys))
    return [r for r in rows if r is not None]


# --------------------------------------------------------------------------- insercion en SQLite
def _insert_rows_optimized(con: sqlite3.Connection, rows: list[dict]) -> tuple[int, int]:
    """Inserta filas en SQLite y actualiza el indice R-Tree."""
    n = 0
    for r in rows:
        con.execute("DELETE FROM catalog WHERE uri = ?", (r["uri"],))
        cur = con.execute("INSERT INTO catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (r["uri"], r["mjd"], r["band"], r["ra_min"], r["ra_max"],
                           r["dec_min"], r["dec_max"], r["poly"]))
        con.execute("INSERT INTO rtree_idx VALUES (?, ?, ?, ?, ?)",
                    (cur.lastrowid, r["ra_min"], r["ra_max"], r["dec_min"], r["dec_max"]))
        n += 1
    return n, 0


# --------------------------------------------------------------------------- pasadas por prefijo
def process_prefix(con: sqlite3.Connection, pool: ProcessPoolExecutor, prefix: str) -> tuple[int, int]:
    """Una pasada incremental por un prefijo utilizando el pool de 4 procesos."""
    last = _get_meta(con, _META_KEY[prefix]) or ""
    if last:
        print(f"[{prefix}] reanudando tras {last}", flush=True)
    done, bad, t0 = 0, 0, time.time()
    for keys in _list_pages(prefix, last):
        keys = [k for k in keys if k.endswith(".fits")]
        if not keys:
            continue
        chunk_size = (len(keys) + PROCESSES - 1) // PROCESSES
        chunks = [keys[i:i + chunk_size] for i in range(0, len(keys), chunk_size)]
        results = list(pool.map(_process_chunk, chunks))
        rows = [r for sub in results for r in sub]
        bad_in_page = len(keys) - len(rows)
        bad += bad_in_page

        with con:
            _insert_rows_optimized(con, rows)
            _set_meta(con, _META_KEY[prefix], keys[-1])

        done += len(keys)
        rate = done / max(time.time() - t0, 1e-9)
        print(f"[{prefix}] +{len(rows)} filas (total {done}, {rate:.0f} fich/s, "
              f"fallidas {bad})", flush=True)
    return done, bad


def main() -> int:
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB, timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    geom = _init_db(con)
    t0 = time.time()
    print(f"Iniciando catalog_s3 optimizado: {PROCESSES} vCPUs x {THREADS_PER_PROC} hilos ({PROCESSES*THREADS_PER_PROC} hilos de red totales)...", flush=True)
    try:
        with ProcessPoolExecutor(PROCESSES, initializer=_init_worker) as pool:
            for prefix in PREFIXES:
                done, bad = process_prefix(con, pool, prefix)
                print(f"[{prefix}] pasada completa: {done} ficheros, {bad} fallidos "
                      f"({time.time() - t0:.0f}s)", flush=True)
            _set_meta(con, "complete", "1")
            _set_meta(con, "source", "s3")
            con.commit()
    finally:
        con.close()
    total = sqlite3.connect(f"file:{DB}?mode=ro", uri=True) \
        .execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
    print(f"Catalogo completo: {total} filas | modo espacial: {geom} | "
          f"{time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
