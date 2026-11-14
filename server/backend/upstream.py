"""Acceso a los datos de NASA/IPAC IRSA: metadatos por TAP, recortes FITS por IBE,
lectura de los MEF de SPHEREx y reproyeccion WCS a una rejilla TAN comun."""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import tempfile
import time
import warnings

import numpy as np
import requests
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning
from astropy.wcs.utils import proj_plane_pixel_area
from reproject import reproject_interp

import imaging

TAP_SYNC = os.environ.get("SPHEREX_TAP_URL", "https://irsa.ipac.caltech.edu/TAP/sync")
IRSA_BASE = "https://irsa.ipac.caltech.edu/"
FITS_DIR = os.environ.get("SPHEREX_FITS_DIR", "/var/cache/spherex/fits")
NATIVE_SCALE = 6.15  # arcsec/pixel

_session = requests.Session()
_session.headers["User-Agent"] = "spherex-wiseview/1.0 (NASA Space Apps 2026)"
_adapter = requests.adapters.HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=2)
_session.mount("https://", _adapter)

_OBS_ID = re.compile(r"level2_(.+?)_spx")
_RELEASE = re.compile(r"/spherex/([a-z]+)(\d*)(_deep)?/")


# --------------------------------------------------------------------------- TAP
def _release_rank(uri: str) -> tuple[int, int]:
    """Prioridad entre publicaciones del mismo exposure: DR > QR, numero mayor primero."""
    m = _RELEASE.search(uri)
    if not m:
        return (0, 0)
    return (2 if m.group(1) == "dr" else 1, int(m.group(2) or 1))


def list_exposures(ra: float, dec: float) -> list[dict]:
    """Todas las imagenes espectrales de SPHEREx que contienen la posicion.

    Devuelve [{uri, mjd, detector, obs}] ordenado por tiempo y sin duplicados entre releases.
    """
    q = (
        "SELECT a.uri AS uri, p.time_bounds_lower AS mjd, p.energy_bandpassname AS band "
        "FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid "
        f"WHERE 1 = CONTAINS(POINT('ICRS', {ra:.6f}, {dec:.6f}), p.poly) "
        "ORDER BY p.time_bounds_lower"
    )
    r = _session.post(TAP_SYNC, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "CSV", "QUERY": q},
                      timeout=90)
    r.raise_for_status()
    text = r.text
    if "QUERY_STATUS" in text[:2000] and "ERROR" in text[:2000]:
        raise RuntimeError("IRSA TAP devolvio un error: " + text[:300])
    best: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(text)):
        uri = (row.get("uri") or "").strip().strip('"')
        band = (row.get("band") or "").strip().strip('"')
        m = re.search(r"D([1-6])\b", band)
        if not uri or not uri.endswith(".fits") or uri.startswith("s3:") or not m:
            continue
        try:
            mjd = float(row["mjd"])
        except (TypeError, ValueError):
            continue
        oid = _OBS_ID.search(uri)
        key = oid.group(1) if oid else uri
        e = {"uri": uri, "mjd": mjd, "detector": int(m.group(1)), "obs": key}
        if key not in best or _release_rank(uri) > _release_rank(best[key]["uri"]):
            best[key] = e
    return sorted(best.values(), key=lambda e: e["mjd"])


# --------------------------------------------------------------------------- recortes
def cutout_url(uri: str, ra: float, dec: float, size_deg: float) -> str:
    base = uri if uri.startswith("http") else IRSA_BASE + uri.lstrip("/")
    return f"{base}?center={ra:.6f},{dec:.6f}d&size={size_deg:.5f}"


def fetch(url: str) -> bytes:
    """Descarga con cache en disco NVMe (escritura atomica)."""
    h = hashlib.sha1(url.encode()).hexdigest()
    path = os.path.join(FITS_DIR, h[:2], h + ".fits")
    try:
        with open(path, "rb") as f:
            data = f.read()
        os.utime(path, None)  # marca de uso para la poda LRU
        return data
    except OSError:
        pass
    last = None
    for attempt in range(3):
        try:
            r = _session.get(url, timeout=(10, 90))
            if r.status_code == 200 and r.content[:6] == b"SIMPLE":
                data = r.content
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
                    with os.fdopen(fd, "wb") as f:
                        f.write(data)
                    os.replace(tmp, path)
                except OSError:
                    pass
                return data
            last = RuntimeError(f"HTTP {r.status_code} en recorte IRSA")
            if 400 <= r.status_code < 500:
                break
        except requests.RequestException as e:
            last = e
        time.sleep(0.5 * (attempt + 1))
    raise last or RuntimeError("fallo de descarga")


def read_cutout(data: bytes, ra: float, dec: float) -> dict:
    """Lee un recorte MEF: imagen limpia, varianza, WCS espacial y longitud de onda en (ra, dec)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        warnings.simplefilter("ignore", RuntimeWarning)
        with fits.open(io.BytesIO(data), memmap=False) as hdul:
            hdr = hdul["IMAGE"].header
            image = np.asarray(hdul["IMAGE"].data, dtype=np.float32)
            flags = hdul["FLAGS"].data if "FLAGS" in hdul else None
            zodi = hdul["ZODI"].data if "ZODI" in hdul else None
            var = np.asarray(hdul["VARIANCE"].data, dtype=np.float32) if "VARIANCE" in hdul else None
            wcs = WCS(hdr)
            x, y = wcs.world_to_pixel(SkyCoord(ra, dec, unit="deg"))
            x, y = float(x), float(y)
            wl = bw = None
            try:
                sw = WCS(hdr, fobj=hdul, key="W")
                sw.sip = None
                w_, b_ = sw.pixel_to_world(x, y)
                wl, bw = float(w_.to(u.micrometer).value), float(b_.to(u.micrometer).value)
                if not np.isfinite(wl):
                    wl = bw = None
            except Exception:
                pass
            omega = float(proj_plane_pixel_area(wcs)) * (np.pi / 180.0) ** 2  # sr / pixel
    return {"image": imaging.clean_exposure(image, flags, zodi), "variance": var, "wcs": wcs,
            "x": x, "y": y, "wavelength": wl, "bandwidth": bw, "omega": omega}


# --------------------------------------------------------------------------- rejilla comun
def grid_for(fov_arcsec: float, max_pixels: int = 512) -> tuple[int, float]:
    """(N, escala arcsec/px): 2x sobremuestreo de la escala nativa, limitado a max_pixels."""
    scale = max(NATIVE_SCALE / 2.0, fov_arcsec / max_pixels)
    n = max(8, int(np.ceil(fov_arcsec / scale)))
    return n, scale


def target_wcs(ra: float, dec: float, n: int, scale_arcsec: float) -> WCS:
    """TAN estandar, norte arriba, este a la izquierda, centrada en (ra, dec)."""
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(n + 1) / 2.0, (n + 1) / 2.0]
    w.wcs.cdelt = [-scale_arcsec / 3600.0, scale_arcsec / 3600.0]
    w.wcs.cunit = ["deg", "deg"]
    return w


def reproject_to(exp: dict, tw: WCS, n: int) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        arr, _ = reproject_interp((exp["image"], exp["wcs"]), tw, shape_out=(n, n), order="bilinear")
    return arr.astype(np.float32)
