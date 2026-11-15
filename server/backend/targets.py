"""Resolucion de nombres: benchmark local -> coordenadas literales -> CDS Sesame."""
from __future__ import annotations

import math
import random
import re

import requests

# Los 5 objetivos obligatorios del desafio (J2000 aprox.; pm en mas/yr)
BENCHMARK = [
    {"name": "Barnard's Star", "ra": 269.4521, "dec": 4.6934, "pmra": -802.8, "pmdec": 10362.5,
     "type": "Estrella de alto movimiento propio (10.39\"/yr)",
     "aliases": ["barnard", "estrella de barnard", "gj 699", "v2500 oph"]},
    {"name": "WISE 0855-0714", "ra": 133.7951, "dec": -7.2451, "pmra": -8118.0, "pmdec": 680.0,
     "type": "Enana marron ultrafria Y (~250 K, 8.1\"/yr)",
     "aliases": ["wise 0855", "wise0855", "w0855", "wise j085510.83-071442.5"]},
    {"name": "Epsilon Indi B", "ra": 331.0440, "dec": -56.7830, "pmra": 3960.0, "pmdec": -2540.0,
     "type": "Par binario de enanas marrones (4.70\"/yr)",
     "aliases": ["eps ind b", "epsilon indi", "eps indi b", "epsilon ind b"]},
    {"name": "RW Aurigae", "ra": 76.9560, "dec": 30.4010, "pmra": 0.0, "pmdec": 0.0,
     "type": "Variable T Tauri (caidas de hasta 3 mag)",
     "aliases": ["rw aur", "rw aurigae"]},
    {"name": "R Draconis", "ra": 248.1680, "dec": 66.7550, "pmra": 0.0, "pmdec": 0.0,
     "type": "Variable Mira (P ~ 245.6 d)",
     "aliases": ["r dra", "r draconis"]},
]

# Objetivos ampliados de alto interes cientifico y cobertura validada SPHEREx
INTEREST_TARGETS = [
    {"name": "Barnard's Star", "ra": 269.4521, "dec": 4.6934, "fov": 240, "band": "D2",
     "type": "Estrella con mayor movimiento propio conocido (10.39\"/yr)", "category": "proper_motion"},
    {"name": "WISE 0855-0714", "ra": 133.7951, "dec": -7.2451, "fov": 240, "band": "D5",
     "type": "Enana marrón más fría del cielo (~250 K, 8.1\"/yr)", "category": "brown_dwarf"},
    {"name": "Epsilon Indi B", "ra": 331.0440, "dec": -56.7830, "fov": 300, "band": "D3",
     "type": "Binaria de enanas marrones frías (4.70\"/yr)", "category": "brown_dwarf"},
    {"name": "Luhman 16", "ra": 162.2988, "dec": -53.3194, "fov": 240, "band": "D4",
     "type": "Par de enanas marrones más cercano a la Tierra (6.5 al)", "category": "brown_dwarf"},
    {"name": "TRAPPIST-1", "ra": 346.6224, "dec": -5.0414, "fov": 240, "band": "D3",
     "type": "Enana roja ultrafria con 7 planetas templados", "category": "exoplanets"},
    {"name": "Polo Eclíptico Norte (NEP)", "ra": 270.0000, "dec": 66.5607, "fov": 360, "band": "D4",
     "type": "Campo profundo de máxima cobertura continua SPHEREx", "category": "deep_field"},
    {"name": "Polo Eclíptico Sur (SEP)", "ra": 90.0000, "dec": -66.5607, "fov": 360, "band": "D4",
     "type": "Campo profundo austral de SPHEREx (máxima profundidad)", "category": "deep_field"},
    {"name": "Zona Planeta X · Tauro", "ra": 62.5000, "dec": 18.2500, "fov": 300, "band": "D5",
     "type": "Corredor de búsqueda de Planeta X / TNOs en plano eclíptico", "category": "planet_x"},
    {"name": "Zona Planeta X · Cetus", "ra": 42.1500, "dec": -8.4500, "fov": 300, "band": "D5",
     "type": "Corredor predicho de afelio para el Planeta Nueve", "category": "planet_x"},
    {"name": "Zona Planeta X · Ofiuco", "ra": 255.3000, "dec": -15.8000, "fov": 300, "band": "D5",
     "type": "Sondeo infrarrojo de alta inclinación orbital", "category": "planet_x"},
    {"name": "Estrella de Teegarden", "ra": 43.2537, "dec": 16.8813, "fov": 240, "band": "D2",
     "type": "Enana M de altísimo movimiento propio (5.06\"/yr)", "category": "proper_motion"},
    {"name": "Wolf 359", "ra": 164.1203, "dec": 7.0147, "fov": 240, "band": "D2",
     "type": "Enana roja fulgurante vecina (4.7\"/yr)", "category": "proper_motion"},
    {"name": "Ross 128", "ra": 176.9350, "dec": 0.8045, "fov": 240, "band": "D2",
     "type": "Enana roja inactiva con exoplaneta de masa terrestre", "category": "exoplanets"},
    {"name": "CW Leonis (IRC +10216)", "ra": 146.9892, "dec": 13.2787, "fov": 300, "band": "D6",
     "type": "Estrella de carbono más brillante en el infrarrojo medio", "category": "infrared_bright"},
    {"name": "RW Aurigae", "ra": 76.9560, "dec": 30.4010, "fov": 300, "band": "D2",
     "type": "Variable T Tauri con caídas profundas de brillo", "category": "variable"},
    {"name": "R Draconis", "ra": 248.1680, "dec": 66.7550, "fov": 300, "band": "D2",
     "type": "Variable Mira clásica (período ~245.6 d)", "category": "variable"},
]

_COORD = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[, ]\s*([+-]?\d+(?:\.\d+)?)\s*$")
_SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/-oI/SNV?{}"


def random_celestial() -> dict:
    """Genera coordenadas isotropicas aleatorias en la esfera celeste (distribucion cos Dec)."""
    ra = round(random.uniform(0.0, 360.0), 4)
    u = random.uniform(-1.0, 1.0)
    dec = round(math.degrees(math.asin(u)), 4)
    band = f"D{random.randint(1, 6)}"
    return {
        "name": f"Esfera celeste ({ra:.4f}° {dec:+.4f}°)",
        "ra": ra,
        "dec": dec,
        "fov": 300,
        "band": band,
        "type": "Punto aleatorio uniforme sobre la esfera celeste (distribución cos Dec)",
        "category": "uniform_celestial",
        "source": "random",
    }


def random_target(mode: str = "auto") -> dict:
    """Devuelve un objetivo de interes cientifico o coordenadas celestes aleatorias."""
    if mode == "celestial":
        return random_celestial()
    if mode == "candidate":
        target = dict(random.choice(INTEREST_TARGETS))
        target["source"] = "scientific_interest"
        return target
    # modo auto: 75% objetivos cientificos / candidatos / polos SPHEREx, 25% punto celeste puro
    if random.random() < 0.75:
        target = dict(random.choice(INTEREST_TARGETS))
        target["source"] = "scientific_interest"
        return target
    return random_celestial()

_COORD = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[, ]\s*([+-]?\d+(?:\.\d+)?)\s*$")
_SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/-oI/SNV?{}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def search_local(q: str) -> list[dict]:
    nq = _norm(q)
    if not nq:
        return []
    out = []
    for t in BENCHMARK:
        names = [_norm(t["name"])] + [_norm(a) for a in t["aliases"]]
        if any(nq in n or n in nq for n in names):
            out.append({**{k: v for k, v in t.items() if k != "aliases"}, "source": "benchmark"})
    return out


def parse_coords(q: str) -> dict | None:
    m = _COORD.match(q)
    if not m:
        return None
    ra, dec = float(m.group(1)), float(m.group(2))
    if 0 <= ra < 360 and -90 <= dec <= 90:
        return {"name": f"{ra:.5f} {dec:+.5f}", "ra": ra, "dec": dec, "pmra": None, "pmdec": None,
                "type": "Coordenadas", "source": "coords"}
    return None


def search_sesame(q: str, timeout: float = 8.0) -> dict | None:
    """CDS Sesame (SIMBAD/NED/VizieR). Devuelve None si no resuelve."""
    r = requests.get(_SESAME.format(requests.utils.quote(q)), timeout=timeout)
    r.raise_for_status()
    ra = dec = None
    pm = (None, None)
    otype = ""
    for line in r.text.splitlines():
        if line.startswith("%J ") and ra is None:
            p = line.split()
            ra, dec = float(p[1]), float(p[2])
        elif line.startswith("%P ") and pm[0] is None:
            p = line.split()
            try:
                pm = (float(p[1]), float(p[2]))
            except (IndexError, ValueError):
                pass
        elif line.startswith("%C.0 ") and not otype:
            otype = line[5:].strip()
    if ra is None:
        return None
    return {"name": q.strip(), "ra": ra, "dec": dec, "pmra": pm[0], "pmdec": pm[1],
            "type": otype or "Objeto (CDS Sesame)", "source": "sesame"}
