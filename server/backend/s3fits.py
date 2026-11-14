"""Lectura parcial de ficheros FITS remotos por rangos de bytes (solo numpy).

Los level2 de SPHEREx del bucket publico de AWS pesan ~71 MB y un recorte solo
necesita unas pocas filas de cada HDU. Este modulo localiza los HDU leyendo
unicamente las cabeceras y despues trae las filas pedidas de cada imagen.

No depende de astropy ni de la red: recibe una funcion get_range(a, b) que
devuelve los bytes [a, b] (ambos incluidos), asi que se puede probar en local.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

BLOCK = 2880
CARD = 80
_DTYPES = {8: "u1", 16: ">i2", 32: ">i4", 64: ">i8", -32: ">f4", -64: ">f8"}

GetRange = Callable[[int, int], bytes]


def _value(text: str):
    s = text.strip()
    if s.startswith("'"):
        out, i = [], 1
        while i < len(s):
            if s[i] == "'":
                if i + 1 < len(s) and s[i + 1] == "'":   # comilla escapada
                    out.append("'")
                    i += 2
                    continue
                break
            out.append(s[i])
            i += 1
        return "".join(out).rstrip()
    s = s.split("/", 1)[0].strip()
    if s == "T":
        return True
    if s == "F":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s.replace("D", "E").replace("d", "e"))
    except ValueError:
        return s


def parse_cards(raw: bytes | str) -> dict:
    """Cabecera FITS (cards de 80 caracteres seguidos) -> {clave: valor}."""
    text = raw.decode("latin1") if isinstance(raw, (bytes, bytearray)) else raw
    cards: dict = {}
    for i in range(0, len(text) - CARD + 1, CARD):
        card = text[i:i + CARD]
        key = card[:8].strip()
        if key == "END":
            break
        if not key or card[8:10] != "= ":
            continue   # COMMENT, HISTORY, CONTINUE, HIERARCH...
        cards.setdefault(key, _value(card[10:]))
    return cards


def _end_offset(buf: bytes) -> int:
    """Posicion del card END dentro de buf, o -1 si aun no ha llegado."""
    for i in range(0, len(buf) - CARD + 1, CARD):
        if buf[i:i + 8] == b"END     " and not buf[i + 8:i + CARD].strip():
            return i
    return -1


def read_header(get_range: GetRange, off: int, total: int, chunk_blocks: int = 4) -> tuple[str, int]:
    """Lee la cabecera que empieza en off. Devuelve (texto de los cards, bytes que ocupa)."""
    buf = b""
    while True:
        a = off + len(buf)
        if a >= total:
            raise ValueError("cabecera FITS sin END antes del final del fichero")
        part = get_range(a, min(a + chunk_blocks * BLOCK, total) - 1)
        if not part:
            raise ValueError("lectura vacia al leer una cabecera FITS")
        buf += part
        end = _end_offset(buf)
        if end >= 0:
            hsize = (end + CARD + BLOCK - 1) // BLOCK * BLOCK
            return buf[:end + CARD].decode("latin1"), hsize
        if len(buf) > 200 * BLOCK:
            raise ValueError("cabecera FITS demasiado larga o corrupta")


def data_size(cards: dict) -> int:
    """Bytes de datos de un HDU, sin el relleno a multiplo de 2880."""
    naxis = int(cards.get("NAXIS", 0) or 0)
    if naxis == 0:
        return 0
    nelem = 1
    for ax in range(1, naxis + 1):
        nelem *= int(cards[f"NAXIS{ax}"])
    gcount = int(cards.get("GCOUNT", 1) or 1)
    pcount = int(cards.get("PCOUNT", 0) or 0)
    return abs(int(cards["BITPIX"])) // 8 * gcount * (pcount + nelem)


def padded(n: int) -> int:
    return (n + BLOCK - 1) // BLOCK * BLOCK


def walk(get_range: GetRange, total: int) -> dict:
    """Recorre todos los HDU leyendo solo cabeceras.

    Devuelve {nombre: {text, hdroff, hsize, dataoff, dsize}}; el HDU primario
    se llama "PRIMARY" y el resto por su EXTNAME.
    """
    exts: dict[str, dict] = {}
    off, index = 0, 0
    while off < total:
        text, hsize = read_header(get_range, off, total, 8 if index == 0 else 4)
        cards = parse_cards(text)
        if index == 0 and cards.get("SIMPLE") is not True:
            raise ValueError("no es un fichero FITS")
        dsize = data_size(cards)
        name = "PRIMARY" if index == 0 else str(cards.get("EXTNAME", f"HDU{index}")).strip()
        exts.setdefault(name, {"text": text, "hdroff": off, "hsize": hsize,
                               "dataoff": off + hsize, "dsize": dsize})
        off += hsize + padded(dsize)
        index += 1
    return exts


def is_plain_image(cards: dict) -> bool:
    """True si el HDU es una imagen 2D sin comprimir (legible por filas)."""
    if cards.get("ZIMAGE") is True or str(cards.get("XTENSION", "IMAGE")).strip() not in ("IMAGE", ""):
        return False
    return int(cards.get("NAXIS", 0) or 0) == 2 and int(cards.get("BITPIX", 0)) in _DTYPES


def rows(get_range: GetRange, ext: dict, y0: int, y1: int, cards: dict | None = None) -> np.ndarray:
    """Filas [y0, y1) completas de un HDU imagen 2D, ya en orden de bytes nativo.

    FITS guarda los datos en big-endian y por filas, asi que un bloque de filas
    es un unico rango de bytes contiguo. Aplica BSCALE/BZERO si los hay.
    """
    cards = cards if cards is not None else parse_cards(ext["text"])
    if not is_plain_image(cards):
        raise ValueError("el HDU no es una imagen 2D sin comprimir")
    n1, n2 = int(cards["NAXIS1"]), int(cards["NAXIS2"])
    if not 0 <= y0 < y1 <= n2:
        raise ValueError(f"filas fuera de la imagen: {y0}-{y1} de {n2}")
    dtype = np.dtype(_DTYPES[int(cards["BITPIX"])])
    row_bytes = n1 * dtype.itemsize
    a = ext["dataoff"] + y0 * row_bytes
    want = (y1 - y0) * row_bytes
    data = get_range(a, a + want - 1)
    if len(data) != want:
        raise ValueError(f"rango incompleto: {len(data)} de {want} bytes")
    arr = np.frombuffer(data, dtype=dtype).reshape(y1 - y0, n1)
    bscale, bzero = cards.get("BSCALE", 1), cards.get("BZERO", 0)
    if bscale in (1, 1.0) and bzero in (0, 0.0):
        return arr.astype(dtype.newbyteorder("="))
    if dtype.kind in "iu" and float(bscale).is_integer() and float(bzero).is_integer():
        return arr.astype(np.int64) * int(bscale) + int(bzero)   # p. ej. enteros sin signo
    return arr.astype(np.float64) * float(bscale) + float(bzero)


def minimal_primary() -> bytes:
    """HDU primario vacio, para anteponerlo a una extension suelta y que sea un FITS valido."""
    def card(key: str, val: str) -> str:
        return f"{key:<8}= {val:>20}".ljust(CARD)
    text = card("SIMPLE", "T") + card("BITPIX", "8") + card("NAXIS", "0") + card("EXTEND", "T") + "END".ljust(CARD)
    return text.ljust(BLOCK).encode("ascii")
