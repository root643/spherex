"""Constructor del catalogo SPHEREx desde las cabeceras FITS del bucket S3.

Alternativa a tapdump.py que NO depende de IRSA TAP en absoluto: recorre el
bucket publico (dr1/ qr2/ qr3/, level2), lee los primeros ~29 KB de cada
fichero por HTTP Range (cabecera del HDU IMAGE, offset 2880), y guarda en la
MISMA SQLite que tapdump: uri, mjd (MJD-OBS), banda (DETECTOR) y huella
(poligono de 8 puntos de borde calculado con astropy WCS, SIP incluido).

Reanudable: guarda meta.last_key_<prefijo> tras cada pagina del listado, y el
vuelco siguiente empieza en start-after. Al terminar una pasada completa deja
meta.complete = 1 (el mismo marcador que consulta el backend).

Uso:  nohup python catalog_s3.py > catalog_s3.log 2>&1 &   (tarda ~1-3 h)
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import requests

import warnings
warnings.filterwarnings("ignore")

from tapdump import DB, _get_meta, _init_db, _insert_rows, _set_meta  # noqa: E402

S3 = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com/"
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
URI_PREFIX = "ibe/data/spherex/"
PREFIXES = ("dr1/level2/", "qr2/level2/", "qr3/level2/")
RANGE = "bytes=0-28799"          # PRIMARY (2880) + cabecera IMAGE completa (23440 B, con padding hasta 28800)
THREADS = int(os.environ.get("SPHEREX_S3CAT_THREADS", "40"))

_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "spherex-catalog-s3/1.0 (NASA Space Apps 2026)"
_adapter = requests.adapters.HTTPAdapter(pool_connections=THREADS, pool_maxsize=THREADS * 2)
_SESSION.mount("https://", _adapter)

_META_KEY = {p: "last_key_" + p.strip("/").replace("/", "_") for p in PREFIXES}


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


# --------------------------------------------------------------------------- cabeceras
def _fetch(key: str) -> bytes | None:
    url = S3 + requests.utils.quote(key)
    for attempt in range(3):
        try:
            with _SESSION.get(url, headers={"Range": RANGE}, timeout=(15, 40)) as r:
                if r.status_code in (200, 206):
                    # 206: tramo pedido; 200: fichero entero (proxy sin Range) -> recortar
                    body = r.content[:28800] if r.status_code == 200 else r.content
                    if len(body) >= 28800:
                        return body          # respuesta completa, valida
                    # truncada: reintentar en vez de descartar el fichero
            return None
        except Exception:
            if attempt == 2:
                return None
            time.sleep(2 * (attempt + 1))
    return None


_CARD_VAL = re.compile(r"^= '?([^'/]*)")


def _parse_image_header(data: bytes) -> str | None:
    """Texto de la cabecera del HDU IMAGE (para astropy), o None."""
    i = data.find(b"XTENSION= 'IMAGE")
    if i < 0:
        i = data.find(b"XTENSION= 'BINTABLE")   # no deberia pasar en level2
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


def _footprint(hdr_text: str) -> list[tuple[float, float]] | None:
    """8 puntos de borde (esquinas + puntos medios de lado) via astropy WCS."""
    from astropy.wcs import WCS
    from astropy.io.fits import Header
    w = WCS(Header.fromstring(hdr_text, sep="\n"), relax=True)
    if not w.has_celestial:
        return None
    nax1 = _hdr_val(hdr_text, "NAXIS1") or 2040
    nax2 = _hdr_val(hdr_text, "NAXIS2") or 2040
    pts = []
    for fy, fx in ((0.0, 0.0), (0.0, 0.5), (0.0, 1.0), (0.5, 0.0),
                   (0.5, 1.0), (1.0, 0.0), (1.0, 0.5), (1.0, 1.0)):
        pts.append((fx * (nax1 - 1) + 1.0, fy * (nax2 - 1) + 1.0))
    ra, dec = w.all_pix2world([p[0] for p in pts], [p[1] for p in pts], 0)
    return [(float(r), float(d)) for r, d in zip(ra, dec)]


def _row(key: str, data: bytes) -> dict | None:
    """Fila del catalogo para un fichero, o None si no se pudo."""
    hdr_text = _parse_image_header(data)
    if hdr_text is None:
        return None
    poly_pts = _footprint(hdr_text)
    if poly_pts is None:
        return None
    mjd = _hdr_val(hdr_text, "MJD-OBS")
    if mjd is None:
        return None
    det = _hdr_val(hdr_text, "DETECTOR")
    if det is None:
        m = re.search(r"_(\d)D([1-6])_", key)
        det = m.group(2) if m else None
    band = f"SPHEREx-D{int(det)}" if det is not None else ""
    poly = " ".join(f"{r:.5f} {d:.5f}" for r, d in poly_pts)
    return {"uri": URI_PREFIX + key, "mjd": float(mjd), "band": band, "poly": poly}


# --------------------------------------------------------------------------- pasadas
def process_prefix(con, prefix: str) -> tuple[int, int]:
    """Una pasada incremental por un prefijo. Devuelve (insertadas, falladas)."""
    last = _get_meta(con, _META_KEY[prefix]) or ""
    if last:
        print(f"[{prefix}] reanudando tras {last}", flush=True)
    done, bad, t0 = 0, 0, time.time()
    for keys in _list_pages(prefix, last):
        keys = [k for k in keys if k.endswith(".fits")]
        if not keys:
            continue
        with ThreadPoolExecutor(THREADS) as ex:
            data_list = list(ex.map(_fetch, keys))
        rows = []
        for key, data in zip(keys, data_list):
            if data is None:
                bad += 1
                continue
            try:
                r = _row(key, data)
            except Exception:
                bad += 1
                continue
            if r is None:
                bad += 1
            else:
                rows.append(r)
        with con:
            _insert_rows(con, rows)
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
    try:
        for prefix in PREFIXES:
            done, bad = process_prefix(con, prefix)
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
