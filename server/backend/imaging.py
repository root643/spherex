"""Aritmetica de imagenes pura (solo numpy + Pillow): apilado, estirado, render WebP,
fotometria de apertura, agrupacion en epocas y canales espectrales.

No depende de astropy para poder probarse de forma aislada.
"""
from __future__ import annotations

import io
import math
import warnings

import numpy as np
from PIL import Image, ImageDraw

# Rangos nominales de los 6 detectores LVF de SPHEREx (micras)
DETECTORS = {
    1: (0.75, 1.10),
    2: (1.10, 1.63),
    3: (1.63, 2.42),
    4: (2.42, 3.83),
    5: (3.83, 4.42),
    6: (4.42, 5.00),
}
CHANNELS_PER_DETECTOR = 17  # 6 x 17 = 102 canales

# Bits de FLAGS que invalidan un pixel (TRANSIENT, OVERFLOW, SUR_ERROR, NONFUNC,
# MISSING_DATA, HOT, COLD, NONLINEAR, PERSIST, OUTLIER). SOURCE (21) NO se enmascara.
BAD_BITS = (0, 1, 2, 6, 9, 10, 11, 15, 17, 19)
BAD_MASK = sum(1 << b for b in BAD_BITS)

STRETCHES = ("asinh", "linear", "log", "sqrt")


# --------------------------------------------------------------------------- canales
def channel_range(channel: int) -> tuple[int, float, float]:
    """Canal 1..102 -> (detector, lambda_min, lambda_max)."""
    if not 1 <= channel <= 6 * CHANNELS_PER_DETECTOR:
        raise ValueError("canal fuera de rango (1-102)")
    det = (channel - 1) // CHANNELS_PER_DETECTOR + 1
    k = (channel - 1) % CHANNELS_PER_DETECTOR
    lo, hi = DETECTORS[det]
    step = (hi - lo) / CHANNELS_PER_DETECTOR
    return det, lo + k * step, lo + (k + 1) * step


def channel_of(wavelength: float) -> int | None:
    """Longitud de onda (um) -> canal 1..102 (None si cae fuera)."""
    for det, (lo, hi) in DETECTORS.items():
        if lo <= wavelength < hi or (det == 6 and wavelength == hi):
            step = (hi - lo) / CHANNELS_PER_DETECTOR
            k = min(int((wavelength - lo) / step), CHANNELS_PER_DETECTOR - 1)
            return (det - 1) * CHANNELS_PER_DETECTOR + k + 1
    return None


def parse_band(band: str) -> tuple[int, float | None, float | None]:
    """'D4' -> (4, None, None) ; 'ch60' o '60' -> (det, lmin, lmax)."""
    b = band.strip().lower()
    if b.startswith("d") and b[1:].isdigit() and 1 <= int(b[1:]) <= 6:
        return int(b[1:]), None, None
    if b.startswith("ch"):
        b = b[2:]
    if b.isdigit():
        return channel_range(int(b))
    raise ValueError("band debe ser D1..D6 o ch1..ch102")


# --------------------------------------------------------------------------- epocas
def group_epochs(times: list[float], gap_days: float = 20.0, max_span: float = 60.0) -> list[int]:
    """Asigna un indice de epoca a cada MJD (lista ordenada ascendente).

    Se corta una epoca cuando hay un hueco > gap_days. Las zonas con cobertura
    continua (cerca de los polos de la ecliptica) se trocean en ventanas de max_span dias.
    """
    labels: list[int] = []
    epoch = -1
    start = prev = None
    for t in times:
        if prev is None or t - prev > gap_days or t - start > max_span:
            epoch += 1
            start = t
        labels.append(epoch)
        prev = t
    return labels


def sample_evenly(items: list, n: int) -> list:
    """Como mucho n elementos repartidos uniformemente."""
    if len(items) <= n:
        return list(items)
    idx = np.linspace(0, len(items) - 1, n).round().astype(int)
    return [items[i] for i in sorted(set(idx.tolist()))]


def mjd_to_iso(mjd: float) -> str:
    """MJD -> 'YYYY-MM-DD' (sin astropy)."""
    jd = mjd + 2400000.5 + 0.5
    z = int(jd)
    a = z
    if z >= 2299161:
        alpha = int((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - alpha // 4
    b = a + 1524
    c = int((b - 122.1) / 365.25)
    d = int(365.25 * c)
    e = int((b - d) / 30.6001)
    day = b - d - int(30.6001 * e)
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    return f"{year:04d}-{month:02d}-{day:02d}"


# --------------------------------------------------------------------------- limpieza y apilado
def clean_exposure(image: np.ndarray, flags: np.ndarray | None, zodi: np.ndarray | None) -> np.ndarray:
    """Resta la luz zodiacal, enmascara pixeles malos (NaN) y resta el fondo residual."""
    img = np.asarray(image, dtype=np.float32).copy()
    if zodi is not None and zodi.shape == img.shape:
        img -= np.asarray(zodi, dtype=np.float32)
    if flags is not None and flags.shape == img.shape:
        img[(np.asarray(flags).astype(np.int64) & BAD_MASK) != 0] = np.nan
    if np.isfinite(img).any():
        img -= np.nanmedian(img)
    return img


def stack(frames: list[np.ndarray], method: str = "median") -> np.ndarray:
    """Coadicion pixel a pixel ignorando NaN."""
    cube = np.stack(frames).astype(np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        if method == "mean" or len(frames) < 3:
            return np.nanmean(cube, axis=0)
        return np.nanmedian(cube, axis=0)


def robust_stats(arr: np.ndarray) -> tuple[float, float]:
    """(mediana, sigma robusta por MAD)."""
    v = arr[np.isfinite(arr)]
    if v.size == 0:
        return 0.0, 1.0
    med = float(np.median(v))
    sigma = 1.4826 * float(np.median(np.abs(v - med)))
    if not sigma > 0:
        sigma = float(np.std(v)) or 1.0
    return med, sigma


def auto_limits(arr: np.ndarray, diff: bool = False) -> tuple[float, float]:
    """Limites de visualizacion automaticos."""
    med, sigma = robust_stats(arr)
    v = arr[np.isfinite(arr)]
    if v.size == 0:
        return (-1.0, 1.0) if diff else (0.0, 1.0)
    if diff:
        hi = max(8.0 * sigma, float(np.percentile(np.abs(v), 99.7)))
        return -hi, hi
    lo = med - 2.0 * sigma
    hi = max(med + 10.0 * sigma, float(np.percentile(v, 99.7)))
    return lo, hi


# --------------------------------------------------------------------------- estirado y render
def _curve(x: np.ndarray, stretch: str) -> np.ndarray:
    """Funcion de transferencia sobre x en [0, 1]."""
    if stretch == "asinh":
        return np.arcsinh(10.0 * x) / math.asinh(10.0)
    if stretch == "log":
        return np.log10(1.0 + 1000.0 * x) / math.log10(1001.0)
    if stretch == "sqrt":
        return np.sqrt(x)
    return x


def to_gray(arr: np.ndarray, vmin: float, vmax: float, stretch: str = "asinh",
            invert: bool = False, diff: bool = False) -> np.ndarray:
    """Matriz float -> uint8 (fila 0 = norte). En modo diff el cero es gris medio."""
    if stretch not in STRETCHES:
        raise ValueError("stretch no valido")
    a = np.asarray(arr, dtype=np.float64)
    nan = ~np.isfinite(a)
    if diff:
        scale = max(abs(vmin), abs(vmax)) or 1.0
        x = np.clip(a / scale, -1.0, 1.0)
        y = 0.5 + 0.5 * np.sign(x) * _curve(np.abs(x), stretch)
        y[nan] = 0.5
    else:
        span = (vmax - vmin) or 1.0
        y = _curve(np.clip((a - vmin) / span, 0.0, 1.0), stretch)
        y[nan] = 0.0
    if invert:
        y = 1.0 - y
    g = (y * 255.0 + 0.5).astype(np.uint8)
    return g[::-1]  # FITS tiene el sur en la fila 0


def render_webp(arr: np.ndarray, vmin: float, vmax: float, stretch: str = "asinh",
                invert: bool = False, diff: bool = False, quality: int = 88) -> bytes:
    g = to_gray(arr, vmin, vmax, stretch, invert, diff)
    buf = io.BytesIO()
    Image.fromarray(g).save(buf, format="WEBP", quality=quality, method=4)
    return buf.getvalue()


def render_gif(frames_data: list[tuple[np.ndarray, dict]],
               stretch: str = "asinh", invert: bool = False, diff: bool = False,
               duration_ms: int = 500, watermark: bool = True,
               vmin: float | None = None, vmax: float | None = None) -> bytes:
    """Genera un GIF animado ultra-rapido a partir de una secuencia de epocas (arr, meta)."""
    if not frames_data:
        raise ValueError("Sin fotogramas para generar GIF")

    if vmin is None or vmax is None:
        all_vmins, all_vmaxs = [], []
        for arr, _ in frames_data:
            lo, hi = auto_limits(arr, diff)
            all_vmins.append(lo)
            all_vmaxs.append(hi)
        vmin = min(all_vmins) if all_vmins else -0.05
        vmax = max(all_vmaxs) if all_vmaxs else 1.0

    pil_images: list[Image.Image] = []
    for arr, meta in frames_data:
        g = to_gray(arr, vmin, vmax, stretch, invert, diff)
        im = Image.fromarray(g).convert("RGB")
        w, h = im.size

        if watermark:
            draw = ImageDraw.Draw(im)
            date_str = str(meta.get("date", ""))
            if date_str:
                draw.text((9, 9), date_str, fill=(0, 0, 0))
                draw.text((8, 8), date_str, fill=(255, 180, 84))

            sub = str(meta.get("label_sub", "SPHEREx"))
            if sub:
                draw.text((9, h - 19), sub, fill=(0, 0, 0))
                draw.text((8, h - 20), sub, fill=(180, 195, 210))

        pal = im.convert("P", palette=Image.Palette.ADAPTIVE)
        pil_images.append(pal)

    buf = io.BytesIO()
    first = pil_images[0]
    rest = pil_images[1:] if len(pil_images) > 1 else []
    first.save(buf, format="GIF", save_all=True, append_images=rest,
               duration=max(50, duration_ms), loop=0, optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- fotometria
def aperture_photometry(image: np.ndarray, variance: np.ndarray | None, x: float, y: float,
                        r_ap: float = 2.0, r_in: float = 4.0, r_out: float = 7.0):
    """Fotometria de apertura circular con anillo de fondo.

    Devuelve (suma, error) en las unidades de la imagen por pixel, o None si la
    apertura se sale de la imagen o contiene pixeles enmascarados.
    """
    ny, nx = image.shape
    if not (r_ap <= x <= nx - 1 - r_ap and r_ap <= y <= ny - 1 - r_ap):
        return None
    yy, xx = np.indices(image.shape)
    r = np.hypot(xx - x, yy - y)
    ap = r <= r_ap
    if not np.isfinite(image[ap]).all():
        return None
    ann = image[(r >= r_in) & (r <= r_out)]
    ann = ann[np.isfinite(ann)]
    if ann.size < 10:
        return None
    bkg = float(np.median(ann))
    n = int(ap.sum())
    total = float(image[ap].sum()) - bkg * n
    if variance is not None and variance.shape == image.shape and np.isfinite(variance[ap]).all():
        err = math.sqrt(max(float(variance[ap].sum()), 0.0))
    else:
        err = 1.4826 * float(np.median(np.abs(ann - bkg))) * math.sqrt(n)
    return total, err


# --------------------------------------------------------------------------- espectro
def bin_channels(points: list[dict]) -> list[dict]:
    """Agrupa puntos {wavelength, flux, err} en los 102 canales (mediana por canal)."""
    bins: dict[int, list[dict]] = {}
    for p in points:
        ch = channel_of(p["wavelength"])
        if ch is not None:
            bins.setdefault(ch, []).append(p)
    out = []
    for ch in range(1, 6 * CHANNELS_PER_DETECTOR + 1):
        det, lo, hi = channel_range(ch)
        ps = bins.get(ch, [])
        if ps:
            f = np.array([p["flux"] for p in ps])
            e = np.array([p["err"] for p in ps])
            flux = float(np.median(f))
            err = float(np.sqrt(np.mean(e ** 2) / len(ps)))
        else:
            flux = err = None
        out.append({"channel": ch, "detector": det, "wavelength": round((lo + hi) / 2, 4),
                    "flux_ujy": flux, "err_ujy": err, "n": len(ps)})
    return out


def _band_mean(points: list[dict], lo: float, hi: float) -> float | None:
    v = [p["flux"] for p in points if lo <= p["wavelength"] <= hi]
    return float(np.median(v)) if len(v) >= 2 else None


def absorption_depth(points: list[dict], band: tuple[float, float],
                     cont: tuple[tuple[float, float], tuple[float, float]]) -> float | None:
    """Profundidad = 1 - F(banda) / F(continuo); None si faltan datos o el continuo es ~0."""
    fb = _band_mean(points, *band)
    c1 = _band_mean(points, *cont[0])
    c2 = _band_mean(points, *cont[1])
    cs = [c for c in (c1, c2) if c is not None]
    if fb is None or not cs:
        return None
    fc = sum(cs) / len(cs)
    if fc <= 0:
        return None
    return round(1.0 - fb / fc, 4)


def diagnostics(points: list[dict]) -> dict:
    return {
        "h2o_ice_3.05um_depth": absorption_depth(points, (2.95, 3.15), ((2.60, 2.80), (3.50, 3.70))),
        "co2_4.27um_depth": absorption_depth(points, (4.20, 4.34), ((4.05, 4.15), (4.40, 4.50))),
    }
