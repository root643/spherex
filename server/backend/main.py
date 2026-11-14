"""Servidor traductor SPHEREx estilo WiseView (FastAPI).

Convierte recortes FITS de NASA/IPAC IRSA en fotogramas WebP reproyectados a una
rejilla TAN comun, con modo diferencia, coadicion y espectro de 102 canales.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response

import imaging
import targets
import upstream
from cache import Cache, pack, unpack

VERSION = "1.0.0"
MAX_PER_FRAME = int(os.environ.get("SPHEREX_MAX_PER_FRAME", "16"))
MAX_PER_COADD = int(os.environ.get("SPHEREX_MAX_PER_COADD", "48"))
MAX_SPEC_PER_DET = int(os.environ.get("SPHEREX_MAX_SPEC_PER_DET", "24"))
TTL_OBS = 3600            # los metadatos cambian cuando IRSA publica datos nuevos
TTL_ARR = 14 * 86400

app = FastAPI(title="SPHEREx WiseView Translation Server", version=VERSION,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
cache = Cache()
pool = ThreadPoolExecutor(max_workers=int(os.environ.get("SPHEREX_FETCH_THREADS", "8")))

RA = Query(..., ge=0, lt=360, description="Ascension recta J2000 (grados)")
DEC = Query(..., ge=-90, le=90, description="Declinacion J2000 (grados)")
FOV = Query(300, ge=60, le=1800, description="Campo de vision (segundos de arco)")


def _r(ra: float, dec: float) -> tuple[float, float]:
    return round(ra, 5), round(dec, 5)


def _hash(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()


# --------------------------------------------------------------------------- observaciones
def get_exposures(ra: float, dec: float) -> list[dict]:
    """Exposures que cubren la posicion, con su indice de epoca. Cache 1 h."""
    key = f"obs:{ra:.5f}:{dec:.5f}"
    raw = cache.get(key)
    if raw:
        return json.loads(raw)
    try:
        exps = upstream.list_exposures(ra, dec)
    except Exception as e:
        raise HTTPException(502, f"No se pudo consultar IRSA TAP: {e}")
    labels = imaging.group_epochs([e["mjd"] for e in exps])
    for e, lab in zip(exps, labels):
        e["epoch"] = lab
    cache.set(key, json.dumps(exps).encode(), TTL_OBS)
    return exps


def summarize_epochs(exps: list[dict]) -> list[dict]:
    out: dict[int, dict] = {}
    for e in exps:
        ep = out.setdefault(e["epoch"], {"epoch": e["epoch"], "mjd_start": e["mjd"], "mjd_end": e["mjd"],
                                         "n_exposures": 0, "detectors": {}})
        ep["mjd_end"] = e["mjd"]
        ep["n_exposures"] += 1
        d = f"D{e['detector']}"
        ep["detectors"][d] = ep["detectors"].get(d, 0) + 1
    for ep in out.values():
        ep["mjd_mid"] = round((ep["mjd_start"] + ep["mjd_end"]) / 2, 3)
        ep["date"] = imaging.mjd_to_iso(ep["mjd_mid"])
        ep["date_start"] = imaging.mjd_to_iso(ep["mjd_start"])
        ep["date_end"] = imaging.mjd_to_iso(ep["mjd_end"])
    return [out[k] for k in sorted(out)]


# --------------------------------------------------------------------------- construccion de matrices
def build_array(ra: float, dec: float, fov: float, det: int, lmin: float | None, lmax: float | None,
                exps: list[dict], method: str, cap: int) -> tuple[np.ndarray, dict]:
    """Descarga, limpia, reproyecta y apila los exposures dados. Cache en Redis."""
    exps = [e for e in exps if e["detector"] == det]
    exps = imaging.sample_evenly(exps, cap if lmin is None else max(cap, 40))
    if not exps:
        raise HTTPException(404, f"No hay imagenes de SPHEREx en D{det} para esa posicion/epoca")
    n, scale = upstream.grid_for(fov)
    key = "arr:" + _hash(ra, dec, fov, det, lmin, lmax, method, n, ",".join(e["obs"] for e in exps))
    raw, meta_raw = cache.get(key), cache.get(key + ":m")
    if raw and meta_raw:
        return unpack(raw), json.loads(meta_raw)

    tw = upstream.target_wcs(ra, dec, n, scale)
    # margen 1.5x: el angulo de giro del satelite cambia entre epocas
    size_deg = (fov * 1.5 + 4 * upstream.NATIVE_SCALE) / 3600.0

    def one(e: dict):
        data = upstream.fetch(upstream.cutout_url(e["uri"], ra, dec, size_deg))
        exp = upstream.read_cutout(data, ra, dec)
        wl = exp["wavelength"]
        if lmin is not None and (wl is None or not lmin <= wl < lmax):
            return None
        arr = upstream.reproject_to(exp, tw, n)
        return (arr, wl, e["mjd"]) if np.isfinite(arr).any() else None

    frames, wls, mjds, errors = [], [], [], []
    for fut in [pool.submit(one, e) for e in exps]:
        try:
            res = fut.result()
        except Exception as ex:  # un exposure roto no tumba el fotograma
            errors.append(str(ex))
            continue
        if res is not None:
            frames.append(res[0])
            if res[1] is not None:
                wls.append(res[1])
            mjds.append(res[2])
    if not frames:
        if errors:
            raise HTTPException(502, f"IRSA no devolvio recortes validos ({errors[0]})")
        raise HTTPException(404, "Sin datos validos en esa banda/canal para esa epoca")

    arr = imaging.stack(frames, method)
    meta = {"n": len(frames), "size": n, "scale": round(scale, 4),
            "wavelength": round(float(np.mean(wls)), 4) if wls else None,
            "mjd": round(float(np.mean(mjds)), 3), "date": imaging.mjd_to_iso(float(np.mean(mjds))),
            "failed": len(errors)}
    cache.set(key, pack(arr), TTL_ARR)
    cache.set(key + ":m", json.dumps(meta).encode(), TTL_ARR)
    return arr, meta


def _epoch_exps(exps: list[dict], epoch: int) -> list[dict]:
    sel = [e for e in exps if e["epoch"] == epoch]
    if not sel:
        raise HTTPException(404, f"La epoca {epoch} no existe para esa posicion")
    return sel


def _image_response(arr: np.ndarray, meta: dict, stretch: str, invert: bool, diff: bool,
                    vmin: float | None, vmax: float | None) -> Response:
    if stretch not in imaging.STRETCHES:
        raise HTTPException(422, "stretch debe ser asinh, linear, log o sqrt")
    if vmin is None or vmax is None:
        vmin, vmax = imaging.auto_limits(arr, diff)
    body = imaging.render_webp(arr, vmin, vmax, stretch, invert, diff)
    headers = {
        "Cache-Control": "public, max-age=43200",
        "X-Spherex-Vmin": f"{vmin:.6g}", "X-Spherex-Vmax": f"{vmax:.6g}",
        "X-Spherex-N": str(meta["n"]), "X-Spherex-Date": str(meta["date"]),
        "X-Spherex-Mjd": str(meta["mjd"]), "X-Spherex-Scale": str(meta["scale"]),
        "X-Spherex-Size": str(meta["size"]),
        "X-Spherex-Wavelength": "" if meta["wavelength"] is None else str(meta["wavelength"]),
    }
    return Response(body, media_type="image/webp", headers=headers)


def _band(band: str) -> tuple[int, float | None, float | None]:
    try:
        return imaging.parse_band(band)
    except ValueError as e:
        raise HTTPException(422, str(e))


# --------------------------------------------------------------------------- endpoints
@app.get("/api/v1/health")
def health():
    return JSONResponse({"status": "ok", "version": VERSION, "redis": cache.redis_ok},
                        headers={"Cache-Control": "no-store"})


@app.get("/api/v1/targets/search")
def targets_search(q: str = Query(..., min_length=1, max_length=120)):
    results = targets.search_local(q)
    if not results:
        c = targets.parse_coords(q)
        if c:
            results = [c]
    if not results:
        key = "ses:" + _hash(q.strip().lower())
        raw = cache.get(key)
        if raw:
            results = json.loads(raw)
        else:
            try:
                s = targets.search_sesame(q)
            except Exception as e:
                raise HTTPException(502, f"CDS Sesame no responde: {e}")
            results = [s] if s else []
            cache.set(key, json.dumps(results).encode(), 30 * 86400 if results else 3600)
    return JSONResponse({"query": q, "results": results}, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/v1/observations")
def observations(ra: float = RA, dec: float = DEC):
    ra, dec = _r(ra, dec)
    exps = get_exposures(ra, dec)
    dets: dict[str, int] = {}
    for e in exps:
        dets[f"D{e['detector']}"] = dets.get(f"D{e['detector']}", 0) + 1
    return JSONResponse({"ra": ra, "dec": dec, "n_exposures": len(exps), "detectors": dets,
                         "epochs": summarize_epochs(exps)},
                        headers={"Cache-Control": "public, max-age=1800"})


@app.get("/api/v1/cutout/frame")
def cutout_frame(ra: float = RA, dec: float = DEC, fov: float = FOV,
                 epoch: int = Query(0, ge=0), band: str = Query("D4"),
                 stretch: str = Query("asinh"), invert: bool = False,
                 diff: bool = False, ref: int = Query(0, ge=0),
                 vmin: Optional[float] = None, vmax: Optional[float] = None):
    """Fotograma de una epoca (o diferencia epoca - ref), reproyectado y estirado, en WebP."""
    ra, dec = _r(ra, dec)
    fov = float(round(fov))
    det, lmin, lmax = _band(band)
    exps = get_exposures(ra, dec)
    arr, meta = build_array(ra, dec, fov, det, lmin, lmax, _epoch_exps(exps, epoch), "median", MAX_PER_FRAME)
    if diff:
        ref_arr, _ = build_array(ra, dec, fov, det, lmin, lmax, _epoch_exps(exps, ref), "median", MAX_PER_FRAME)
        arr = arr - ref_arr
    return _image_response(arr, meta, stretch, invert, diff, vmin, vmax)


@app.get("/api/v1/cutout/coadd")
def cutout_coadd(ra: float = RA, dec: float = DEC, fov: float = FOV, band: str = Query("D4"),
                 stretch: str = Query("asinh"), invert: bool = False,
                 method: str = Query("median", pattern="^(median|mean)$"),
                 vmin: Optional[float] = None, vmax: Optional[float] = None):
    """Coadicion temporal de todas las epocas."""
    ra, dec = _r(ra, dec)
    fov = float(round(fov))
    det, lmin, lmax = _band(band)
    arr, meta = build_array(ra, dec, fov, det, lmin, lmax, get_exposures(ra, dec), method, MAX_PER_COADD)
    return _image_response(arr, meta, stretch, invert, False, vmin, vmax)


@app.get("/api/v1/spectrum")
def spectrum(ra: float = RA, dec: float = DEC):
    """Fotometria de apertura en todos los exposures -> SED de 102 canales (uJy)."""
    ra, dec = _r(ra, dec)
    exps = get_exposures(ra, dec)
    if not exps:
        raise HTTPException(404, "SPHEREx aun no tiene imagenes publicas de esa posicion")
    sel: list[dict] = []
    for det in range(1, 7):
        sel += imaging.sample_evenly([e for e in exps if e["detector"] == det], MAX_SPEC_PER_DET)
    key = "sed:" + _hash(ra, dec, ",".join(e["obs"] for e in sel))
    raw = cache.get(key)
    if raw:
        return Response(raw, media_type="application/json", headers={"Cache-Control": "public, max-age=43200"})

    size_deg = 24 * upstream.NATIVE_SCALE / 3600.0

    def one(e: dict):
        exp = upstream.read_cutout(upstream.fetch(upstream.cutout_url(e["uri"], ra, dec, size_deg)), ra, dec)
        if exp["wavelength"] is None:
            return None
        ph = imaging.aperture_photometry(exp["image"], exp["variance"], exp["x"], exp["y"])
        if ph is None:
            return None
        k = exp["omega"] * 1e12  # MJy/sr por pixel -> uJy
        return {"wavelength": round(exp["wavelength"], 5), "bandwidth": exp["bandwidth"],
                "flux": ph[0] * k, "err": ph[1] * k, "mjd": e["mjd"], "detector": e["detector"]}

    points, failed = [], 0
    for fut in [pool.submit(one, e) for e in sel]:
        try:
            p = fut.result()
        except Exception:
            failed += 1
            continue
        if p is not None and np.isfinite(p["flux"]) and np.isfinite(p["err"]):
            points.append(p)
    if not points:
        raise HTTPException(502 if failed else 404, "No se pudo medir el espectro en esa posicion")
    points.sort(key=lambda p: p["wavelength"])
    body = json.dumps({
        "ra": ra, "dec": dec, "units": "uJy", "aperture_radius_px": 2.0,
        "n_exposures": len(points), "n_failed": failed,
        "points": [{"wavelength": p["wavelength"], "flux_ujy": round(p["flux"], 3),
                    "err_ujy": round(p["err"], 3), "mjd": p["mjd"], "detector": p["detector"]} for p in points],
        "channels": imaging.bin_channels(points),
        "diagnostics": imaging.diagnostics(points),
    }).encode()
    cache.set(key, body, TTL_ARR)
    return Response(body, media_type="application/json", headers={"Cache-Control": "public, max-age=43200"})


_disk = {"t": 0.0, "v": {}}


def _disk_stats() -> dict:
    if time.time() - _disk["t"] > 60:
        files = size = 0
        try:
            for sub in os.scandir(upstream.FITS_DIR):
                if sub.is_dir():
                    for f in os.scandir(sub.path):
                        files += 1
                        size += f.stat().st_size
            du = shutil.disk_usage(upstream.FITS_DIR)
            _disk["v"] = {"fits_files": files, "fits_bytes": size, "disk_free_bytes": du.free,
                          "disk_total_bytes": du.total}
        except OSError as e:
            _disk["v"] = {"error": str(e)}
        _disk["t"] = time.time()
    return _disk["v"]


@app.get("/api/v1/cache/stats")
def cache_stats():
    return JSONResponse({"version": VERSION, "redis": cache.info(), "disk": _disk_stats()},
                        headers={"Cache-Control": "no-store"})
