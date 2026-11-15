"""Pruebas de la aritmetica de imagenes (solo numpy + Pillow).
Ejecutar:  python3 tests/test_imaging.py
"""
import io, os, sys
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server", "backend"))
import imaging as im


def gauss(n, x, y, amp=100.0, s=1.2):
    yy, xx = np.indices((n, n))
    return amp * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * s * s))


def test_channels():
    assert im.channel_range(1)[0] == 1 and im.channel_range(102)[0] == 6
    assert abs(im.channel_range(1)[1] - 0.75) < 1e-9 and abs(im.channel_range(102)[2] - 5.0) < 1e-9
    for ch in range(1, 103):
        det, lo, hi = im.channel_range(ch)
        assert im.channel_of((lo + hi) / 2) == ch
    assert im.channel_of(0.5) is None and im.channel_of(5.0) == 102
    assert im.parse_band("D4") == (4, None, None)
    assert im.parse_band("ch60")[0] == 4 and im.parse_band("18")[0] == 2
    for bad in ("D7", "x", "ch0", "ch103"):
        try: im.parse_band(bad); raise AssertionError(bad)
        except ValueError: pass


def test_epochs():
    # dos visitas semestrales + cobertura continua de 200 dias
    t = [60800, 60801, 60805, 60985, 60990] + list(range(61200, 61400, 5))
    lab = im.group_epochs([float(x) for x in t])
    assert lab[:5] == [0, 0, 0, 1, 1]
    assert lab[5] == 2 and lab[-1] >= 4 and lab == sorted(lab)
    assert im.mjd_to_iso(60676.0) == "2025-01-01" and im.mjd_to_iso(51544.5) == "2000-01-01"
    assert len(im.sample_evenly(list(range(100)), 16)) <= 16
    assert im.sample_evenly([1, 2], 16) == [1, 2]


def test_clean_and_stack():
    rng = np.random.default_rng(1)
    zodi = np.full((40, 40), 5.0, np.float32)
    frames = []
    for i in range(7):
        img = zodi + rng.normal(0, 0.1, (40, 40)).astype(np.float32) + gauss(40, 20, 20, 10)
        if i == 3: img[5, 5] = 1e4            # rayo cosmico sin marcar
        flags = np.zeros((40, 40), np.int32)
        flags[10, 10] = 1 << 10               # HOT -> enmascarado
        flags[30, 30] = 1 << 21               # SOURCE -> se conserva
        img[10, 10] = 999.0
        frames.append(im.clean_exposure(img, flags, zodi))
    assert np.isnan(frames[0][10, 10]) and np.isfinite(frames[0][30, 30])
    co = im.stack(frames)
    assert abs(co[5, 5]) < 1.0, "la mediana debe eliminar el rayo cosmico"
    assert np.isnan(co[10, 10]) and abs(co[20, 20] - 10) < 0.5
    assert abs(np.nanmedian(co)) < 0.05, "fondo zodiacal restado"


def test_dipole_and_render():
    a = gauss(64, 30, 32) ; b = gauss(64, 34, 32)       # el objeto se mueve 4 px
    d = b - a
    lo, hi = im.auto_limits(d, diff=True)
    assert lo == -hi and hi > 0
    g = im.to_gray(d, lo, hi, "linear", diff=True)
    row = 64 - 1 - 32                                    # fila volteada (norte arriba)
    assert g[row, 30] < 60 and g[row, 34] > 195, "dipolo negro/blanco"
    assert abs(int(g[0, 0]) - 128) <= 1, "cero = gris medio"
    # variable sin movimiento -> monopolo
    m = im.to_gray(gauss(64, 32, 32, 50) - gauss(64, 32, 32, 100), -100, 100, "linear", diff=True)
    assert m.min() < 70 and m.max() <= 129
    # orientacion: fuente en la fila alta de FITS (norte) aparece arriba
    n = im.to_gray(gauss(64, 32, 55), 0, 100, "linear")
    assert np.unravel_index(n.argmax(), n.shape)[0] == 64 - 1 - 55
    inv = im.to_gray(gauss(64, 32, 55), 0, 100, "linear", invert=True)
    assert np.array_equal(inv, 255 - n)
    for s in im.STRETCHES:
        w = im.render_webp(gauss(128, 64, 64), 0, 100, s)
        pic = Image.open(io.BytesIO(w))
        assert pic.format == "WEBP" and pic.size == (128, 128)
    assert len(im.render_webp(np.random.default_rng(0).normal(0, 1, (160, 160)), -2, 10)) < 25_000
    nan = np.full((8, 8), np.nan)
    assert im.auto_limits(nan) == (0.0, 1.0) and im.to_gray(nan, 0, 1).max() == 0


def test_photometry():
    rng = np.random.default_rng(2)
    total = 2 * np.pi * 1.2 ** 2 * 100.0
    img = gauss(31, 15, 15) + 3.0 + rng.normal(0, 0.05, (31, 31))
    f, e = im.aperture_photometry(img, None, 15, 15, r_ap=4.5, r_in=8, r_out=12)
    assert abs(f - total) / total < 0.03, (f, total)
    assert e > 0
    assert im.aperture_photometry(img, None, 0.5, 15) is None          # fuera de la imagen
    img2 = img.copy(); img2[15, 15] = np.nan
    assert im.aperture_photometry(img2, None, 15, 15) is None           # pixel enmascarado
    var = np.full_like(img, 0.25)
    f2, e2 = im.aperture_photometry(img, var, 15, 15)
    assert abs(e2 - np.sqrt(0.25 * 13)) < 1e-6                          # 13 px en r<=2


def test_spectrum():
    pts = []
    for wl in np.arange(0.76, 5.0, 0.01):
        flux = 100.0
        if 2.95 <= wl <= 3.15: flux = 40.0     # hielo de agua: 60 %
        if 4.20 <= wl <= 4.34: flux = 75.0     # CO2: 25 %
        pts.append({"wavelength": float(wl), "flux": flux, "err": 1.0})
    ch = im.bin_channels(pts)
    assert len(ch) == 102 and all(c["n"] > 0 for c in ch)
    d = im.diagnostics(pts)
    assert abs(d["h2o_ice_3.05um_depth"] - 0.60) < 0.01 and abs(d["co2_4.27um_depth"] - 0.25) < 0.01
    assert im.diagnostics(pts[:50]) == {"h2o_ice_3.05um_depth": None, "co2_4.27um_depth": None}


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok  ", name)
    print("TODO OK")
