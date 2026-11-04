import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.curadoria.audit_models import inspect_glb, audit_catalog


def glb(path, nodes):
    doc = json.dumps({"asset": {"version": "2.0"}, "nodes": [{"name": n} for n in nodes], "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}], "accessors": [{"count": 60}]}).encode()
    doc += b" " * ((4 - len(doc) % 4) % 4)
    total = 12 + 8 + len(doc)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(doc), b"JSON") + doc)


def record(id="fiat-uno", model="Uno", file="fiat-uno.glb", years=(1984, 2013)):
    return {
        "id": id, "brand": "Fiat", "model": model, "category": "hatchback",
        "file": file, "bytes": 0, "years": list(years),
        "credit": {"author": "Autor Teste", "license": "CC Attribution",
                   "sourceName": "Fiat Uno", "sourceUrl": "https://sketchfab.com/3d-models/test"}
    }


class TestCuradoria(unittest.TestCase):
    def test_inspect_identifies_components_without_declaring_manual_approval(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "uno.glb"
            glb(p, ["door_lf", "door_rf", "hood", "front_bumper", "wheel_fl"])
            result = inspect_glb(p)
            self.assertTrue(result["valid"])
            self.assertEqual(result["node_count"], 5)
            self.assertIn("portas", result["component_groups"])
            self.assertIn("capo", result["component_groups"])

    def test_malformed_glb_fails(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "broken.glb"
            p.write_bytes(b"not a glb")
            self.assertFalse(inspect_glb(p)["valid"])

    def test_every_record_requires_visual_review_even_when_valid(self):
        with tempfile.TemporaryDirectory() as td:
            assets = Path(td)
            glb(assets / "fiat-uno.glb", ["door_lf", "wheel_fl"])
            result = audit_catalog({"cars": [record()]}, assets)
            entry = result["models"][0]
            self.assertEqual(entry["review_status"], "PENDENTE_REVISAO_VISUAL")
            self.assertFalse(entry["approved_for_officina"])
            self.assertTrue(entry["technical"]["valid"])

    def test_odd_metadata_and_duplicates_are_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            assets = Path(td)
            glb(assets / "a.glb", ["body"])
            glb(assets / "b.glb", ["body"])
            c1 = record("a", "", "a.glb", (0, 0))
            c2 = record("b", "Uno", "b.glb", (2000, 2024))
            c2["credit"]["sourceUrl"] = c1["credit"]["sourceUrl"]
            result = audit_catalog({"cars": [c1, c2]}, assets)
            issues1 = result["models"][0]["issues"]
            issues2 = result["models"][1]["issues"]
            self.assertIn("MODELO_VAZIO", issues1)
            self.assertIn("ANO_DESCONHECIDO", issues1)
            self.assertIn("ANOS_GENERICOS", issues2)
            self.assertIn("FONTE_REPETIDA", issues1)
            self.assertIn("FONTE_REPETIDA", issues2)

    def test_no_external_download_during_inspection(self):
        with tempfile.TemporaryDirectory() as td:
            result = audit_catalog({"cars": [record()]}, Path(td))
            self.assertIn("ARQUIVO_AUSENTE", result["models"][0]["issues"])
            self.assertFalse(result["models"][0]["technical"]["valid"])


if __name__ == "__main__":
    unittest.main()
