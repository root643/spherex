"""Resolucion de nombres: benchmark local -> coordenadas literales -> CDS Sesame."""
from __future__ import annotations

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
