"""Pruebas unitarias e integracion de catalogos externos (Gaia DR3, AllWISE) y generador aleatorio de coordenadas."""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server", "backend"))

import catalogs
import targets
from fastapi.testclient import TestClient
from main import app


class TestRandomCoordinates(unittest.TestCase):
    def test_random_celestial_bounds(self):
        """Comprueba que las coordenadas aleatorias sobre la esfera celeste esten siempre en rango."""
        for _ in range(500):
            c = targets.random_celestial()
            self.assertGreaterEqual(c["ra"], 0.0)
            self.assertLess(c["ra"], 360.0)
            self.assertGreaterEqual(c["dec"], -90.0)
            self.assertLessEqual(c["dec"], 90.0)
            self.assertIn(c["band"], ["D1", "D2", "D3", "D4", "D5", "D6"])
            self.assertEqual(c["category"], "uniform_celestial")
            self.assertEqual(c["source"], "random")

    def test_random_celestial_distribution(self):
        """Comprueba que la distribucion sin(Dec) sea uniforme en [-1, 1] (distribucion cos Dec)."""
        sins = []
        for _ in range(2000):
            c = targets.random_celestial()
            sins.append(math.sin(math.radians(c["dec"])))
        mean_sin = sum(sins) / len(sins)
        # La media de sin(dec) debe ser muy cercana a 0 (esfera isotropicamente simetrica)
        self.assertAlmostEqual(mean_sin, 0.0, delta=0.08)

    def test_random_target_modes(self):
        """Comprueba los modos de seleccion de objetivos aleatorios."""
        cand = targets.random_target("candidate")
        self.assertIn("name", cand)
        self.assertEqual(cand["source"], "scientific_interest")
        self.assertIn(cand["category"], ["proper_motion", "brown_dwarf", "planet_x", "deep_field", "exoplanets", "infrared_bright", "variable"])

        cel = targets.random_target("celestial")
        self.assertEqual(cel["category"], "uniform_celestial")
        self.assertEqual(cel["source"], "random")

        auto = targets.random_target("auto")
        self.assertIn("ra", auto)
        self.assertIn("dec", auto)


class TestCatalogsParsing(unittest.TestCase):
    def test_safe_float(self):
        self.assertEqual(catalogs._safe_float("12.34"), 12.34)
        self.assertEqual(catalogs._safe_float("-5.67"), -5.67)
        self.assertIsNone(catalogs._safe_float(""))
        self.assertIsNone(catalogs._safe_float("---"))
        self.assertIsNone(catalogs._safe_float("nan"))
        self.assertIsNone(catalogs._safe_float(None))

    def test_query_gaia_live(self):
        """Prueba una consulta real a Gaia DR3 alrededor de Barnard's Star."""
        stars = catalogs.query_gaia(269.4521, 4.6934, radius_arcsec=120, limit=5)
        self.assertIsInstance(stars, list)
        self.assertGreater(len(stars), 0)
        s = stars[0]
        self.assertIn("id", s)
        self.assertIn("ra", s)
        self.assertIn("dec", s)
        self.assertIn("gmag", s)
        self.assertIn("pm_total", s)
        if s.get("pmra") is not None and s.get("pmdec") is not None:
            calc_pm = round(math.hypot(s["pmra"], s["pmdec"]), 2)
            self.assertEqual(s["pm_total"], calc_pm)

    def test_query_wise_live(self):
        """Prueba una consulta real a AllWISE alrededor de Barnard's Star."""
        sources = catalogs.query_wise(269.4521, 4.6934, radius_arcsec=120, limit=5)
        self.assertIsInstance(sources, list)
        self.assertGreater(len(sources), 0)
        w = sources[0]
        self.assertIn("id", w)
        self.assertIn("ra", w)
        self.assertIn("dec", w)
        self.assertIn("w1", w)
        self.assertIn("w2", w)
        if w.get("w1") is not None and w.get("w2") is not None:
            self.assertAlmostEqual(w["w1_w2"], round(w["w1"] - w["w2"], 3), delta=1e-3)


class TestApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_random_endpoint(self):
        r = self.client.get("/api/v1/targets/random")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("name", data)
        self.assertIn("ra", data)
        self.assertIn("dec", data)

    def test_gaia_endpoint_and_cache(self):
        url = "/api/v1/catalogs/gaia?ra=269.4521&dec=4.6934&radius=100&limit=5"
        # Primera peticion (obtenida de vizier o cache si ya se consulto)
        r1 = self.client.get(url)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.headers["content-type"], "application/json")
        data1 = r1.json()
        self.assertEqual(data1["catalog"], "Gaia DR3 (I/355/gaiadr3)")
        self.assertGreater(data1["count"], 0)

        # Segunda peticion (debe venir obligatoriamente de la cache en memoria/Redis)
        r2 = self.client.get(url)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.headers.get("x-catalog-source"), "cache")
        data2 = r2.json()
        self.assertEqual(data1["count"], data2["count"])
        self.assertEqual(data1["sources"][0]["id"], data2["sources"][0]["id"])

    def test_wise_endpoint_and_cache(self):
        url = "/api/v1/catalogs/wise?ra=133.7951&dec=-7.2451&radius=100&limit=5"
        # WISE 0855-0714
        r1 = self.client.get(url)
        self.assertEqual(r1.status_code, 200)
        data1 = r1.json()
        self.assertEqual(data1["catalog"], "AllWISE (II/328/allwise)")
        self.assertGreater(data1["count"], 0)

        # Segunda peticion debe provenir de cache
        r2 = self.client.get(url)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.headers.get("x-catalog-source"), "cache")

    def test_catalog_boundary_params(self):
        # Parametros limite (radio muy pequeno, limite maximo)
        r = self.client.get("/api/v1/catalogs/gaia?ra=0&dec=0&radius=10&limit=1")
        self.assertEqual(r.status_code, 200)

        # Parametro invalido (RA >= 360)
        r_bad = self.client.get("/api/v1/catalogs/gaia?ra=400&dec=0")
        self.assertEqual(r_bad.status_code, 422)


if __name__ == "__main__":
    unittest.main()
