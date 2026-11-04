import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.curadoria.restaurar_glb import parse_glb, create_variant, parse_hex, apply_profile


def sample_glb(path):
    body = {
        "asset": {"version":"2.0"},
        "nodes": [
            {"name":"fmka_wheels", "children":[2]},
            {"name":"fmka_body", "children":[3]},
            {"name":"tire_01a-material", "mesh":0},
            {"name":"am5eunew1-material", "mesh":1}
        ],
        "materials":[
            {"name":"tire_01a", "pbrMetallicRoughness":{}},
            {"name":"am5eunew1", "pbrMetallicRoughness":{"baseColorFactor":[0.84,0,0,1]}}
        ],
        "meshes":[
            {"primitives":[{"attributes":{"POSITION":0,"COLOR_0":1},"material":0}]},
            {"primitives":[{"attributes":{"POSITION":0,"COLOR_0":1},"material":1}]}
        ]
    }
    json_bytes=json.dumps(body).encode()
    json_bytes+=b' ' * (-len(json_bytes)%4)
    binary=b"\0"*8
    path.write_bytes(struct.pack('<4sII',b'glTF',2,20+len(json_bytes)+8+len(binary))+
                     struct.pack('<I4s',len(json_bytes),b'JSON')+json_bytes+
                     struct.pack('<I4s',len(binary),b'BIN\0')+binary)
    return body


class TestRestauro(unittest.TestCase):
    def test_color_is_rgb_not_white_and_rejects_unsafe(self):
        self.assertEqual(parse_hex('#17385B'),[23/255,56/255,91/255,1])
        for color in ('red','#fff','notacolor','#ff0044\n'):
            with self.assertRaises(ValueError):
                parse_hex(color)

    def test_restores_wheels_and_paint_without_changing_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'ford-ka.glb'
            expected=sample_glb(source)
            before=source.read_bytes()
            output=Path(folder)/'ford-ka-restaurado.glb'
            profile={"paint":"#627D8C","rules":[
                {"match":"^tire_01a$","color":"#171D22","roughness":0.9},
                {"match":"^am5eunew1$","role":"paint","roughness":0.45}
            ]}
            meta=create_variant(source,output,profile,'ford-ka')
            modified=parse_glb(output)
            self.assertEqual(modified["meshes"], expected["meshes"])
            self.assertEqual(modified["nodes"], expected["nodes"])
            self.assertEqual(modified["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"],parse_hex('#171D22'))
            self.assertEqual(modified["materials"][1]["pbrMetallicRoughness"]["baseColorFactor"],parse_hex('#627D8C'))
            self.assertEqual(before,source.read_bytes())
            self.assertEqual(meta["review_status"],"PENDENTE_REVISAO_VISUAL")
            self.assertNotEqual(meta["sha256_original"],meta["sha256_restaurado"])
            self.assertEqual(meta["mesh_count_before"],meta["mesh_count_after"])

    def test_single_original_material_is_cloned_for_body_only(self):
        gltf={
            "materials":[{"name":"Scene_-_Root","pbrMetallicRoughness":{}}],
            "nodes":[{"name":"capo","children":[1]},{"name":"capo__0","mesh":0},
                     {"name":"pneus_dianteiros","children":[3]},{"name":"pneus__0","mesh":1}],
            "meshes":[{"primitives":[{"material":0,"attributes":{"POSITION":0,"COLOR_0":1}}]},
                      {"primitives":[{"material":0,"attributes":{"POSITION":0,"COLOR_0":1}}]}],
        }
        profile={"paint":"#477E8C","mesh_groups":["capo"],"disable_vertex_color_for_mesh_groups":True}
        out=copy.deepcopy(gltf)
        changes=apply_profile(out,profile)
        self.assertEqual(out["meshes"][1],gltf["meshes"][1])
        self.assertEqual(out["meshes"][0]["primitives"][0]["material"],1)
        self.assertNotIn("COLOR_0",out["meshes"][0]["primitives"][0]["attributes"])
        self.assertEqual(out["meshes"][1]["primitives"][0]["material"],0)
        self.assertEqual(len(changes),1)

    def test_neon_green_rule_does_not_recolor_other_materials(self):
        gltf={"materials":[
            {"name":"body_neon","pbrMetallicRoughness":{"baseColorFactor":[.2,1,.1,1]}},
            {"name":"pneu","pbrMetallicRoughness":{"baseColorFactor":[0,0,0,1]}}],
            "meshes":[],"nodes":[]}
        changes=apply_profile(gltf,{"paint":"#667B93","rules":[{"match":".*","role":"paint","when":"NEON_GREEN"}]})
        self.assertEqual(len(changes),1)
        self.assertEqual(gltf["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"],parse_hex('#667B93'))
        self.assertEqual(gltf["materials"][1]["pbrMetallicRoughness"]["baseColorFactor"],[0,0,0,1])

    def test_glb_parser_rejects_truncated_and_wrong_format(self):
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder)/'bad.glb'
            f.write_bytes(b'hello')
            with self.assertRaises(ValueError):
                parse_glb(f)
            f.write_bytes(b'glTF'+b'\0'*20)
            with self.assertRaises(ValueError):
                parse_glb(f)


if __name__=='__main__': unittest.main()
