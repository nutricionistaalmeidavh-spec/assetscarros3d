import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.curadoria.reparar_rodas import (
    duplicate_original_wheels, generate_palio_wheels,
    read_bin_chunk, repair_file, validate_wheel_positions,
)
from scripts.curadoria.restaurar_glb import parse_glb, write_glb, read_glb

ROOT=Path(__file__).resolve().parents[1]


def sample_ka():
    return {"asset":{"version":"2.0"},"buffers":[{"byteLength":4}],
            "scenes":[{"nodes":[0]}],"scene":0,
            "nodes":[
              {"name":"Sketchfab_model","children":[1]},
              {"name":"Collada visual scene group","children":[2,4,6]},
              {"name":"fmka_wheels","children":[3]},
              {"name":"rim","mesh":0},
              {"name":"fmka_tires","children":[5]},
              {"name":"rubber","mesh":1},
              {"name":"fmka_body","mesh":2}
            ],
            "meshes":[{"primitives":[{"attributes":{"POSITION":0},"material":0}]} for _ in range(3)],
            "accessors":[{"type":"VEC3","componentType":5126,"count":3,"min":[-1,0,-1],"max":[1,1,1]}],
            "materials":[{"name":"rim"},{"name":"tire"},{"name":"body"}]
           }


def write_fixture(path,doc):
    bin_data=b'\0'*4
    tail=struct.pack("<I4s",len(bin_data),b"BIN\0")+bin_data
    write_glb(path,doc,tail)


class TestWheelRepair(unittest.TestCase):
    def test_ka_wheels_have_four_positions_and_original_wheel_geometry(self):
        gltf=sample_ka()
        old_meshes=copy.deepcopy(gltf["meshes"])
        old_materials=copy.deepcopy(gltf["materials"])
        info=duplicate_original_wheels(gltf,"fmka", {
          "half_track":0.79,"front_world_z":1.24,"rear_world_z":-1.12,"hub_height":0.30
        })
        self.assertEqual(info["wheels_created"],4)
        self.assertEqual(info["groups_cloned"],8)
        self.assertEqual(gltf["meshes"],old_meshes)
        self.assertEqual(gltf["materials"],old_materials)
        self.assertNotIn(2,gltf["nodes"][1]["children"])
        self.assertNotIn(4,gltf["nodes"][1]["children"])
        wheel_nodes=[n for n in gltf["nodes"] if n["name"].startswith("ArtiSys_WheelPosition_")]
        self.assertEqual(len(wheel_nodes),4)
        self.assertEqual(len({tuple(n["translation"]) for n in wheel_nodes}),4)
        for wheel in wheel_nodes:
            self.assertEqual(len(wheel["children"]),2)
            self.assertGreater(len(wheel["name"]),20)

    def test_fox_requires_actual_wheel_and_tire_nodes(self):
        gltf=sample_ka()
        with self.assertRaisesRegex(ValueError,"grupo"):
            duplicate_original_wheels(gltf,"fmfox", {
              "half_track":0.79,"front_world_z":1.24,"rear_world_z":-1.12,"hub_height":0.30
            })

    def test_palio_creates_four_local_wheel_meshes_without_touching_existing_meshes(self):
        doc=sample_ka()
        old_meshes=copy.deepcopy(doc["meshes"])
        old_nodes=copy.deepcopy(doc["nodes"])
        settings={"half_track":0.98,"front_world_z":1.33,
                  "rear_world_z":-1.39,"hub_height":-0.27,"radius":0.32,"width":0.22}
        result,bin_chunk=generate_palio_wheels(doc,b'\0'*4,settings)
        self.assertEqual(result["wheels_created"],4)
        self.assertEqual(result["origin"],"GEOMETRIA_PROCEDURAL_ARTISYS")
        self.assertEqual(doc["meshes"][:len(old_meshes)],old_meshes)
        self.assertEqual(doc["nodes"][:len(old_nodes)],old_nodes)
        self.assertEqual(len([n for n in doc["nodes"] if n.get("name","").startswith("ArtiSys_Wheel_")]),4)
        self.assertGreater(len(bin_chunk),4)
        self.assertEqual(doc["buffers"][0]["byteLength"],len(bin_chunk))
        self.assertGreaterEqual(len(doc["materials"]),len(old_meshes)+1)
        self.assertTrue(all(a.get("count",0)>0 for a in doc["accessors"][-8:]))

    def test_palio_output_is_complete_glb_with_binary_chunk_and_original_stays_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            a=Path(td)/"source.glb"
            b=Path(td)/"repaired.glb"
            write_fixture(a,sample_ka())
            orig=a.read_bytes()
            settings={"half_track":0.98,"front_world_z":1.33,
                      "rear_world_z":-1.39,"hub_height":-0.27,"radius":0.32,"width":0.22}
            result=repair_file(a,b,"fiat-palio",settings)
            repaired,tail=read_glb(b)
            data=read_bin_chunk(tail)
            self.assertEqual(repaired["buffers"][0]["byteLength"],len(data))
            self.assertGreater(result["added_meshes"],0)
            self.assertEqual(result["wheels_created"],4)
            self.assertEqual(a.read_bytes(),orig)
            self.assertNotEqual(result["sha256_original"],result["sha256_restaurado"])

    def test_rejects_out_of_bounds_wheel_position(self):
        with self.assertRaises(ValueError):
            validate_wheel_positions({"half_track":200,
                    "front_world_z":1.33,"rear_world_z":-1.39,"hub_height":-0.27})

    def test_original_ka_bin_chunk_unchanged_in_repair(self):
        with tempfile.TemporaryDirectory() as td:
            a=Path(td)/"ka.glb"
            b=Path(td)/"ka-fixed.glb"
            write_fixture(a,sample_ka())
            before=read_bin_chunk(read_glb(a)[1])
            result=repair_file(a,b,"ford-ka",{
                "half_track":0.79,"front_world_z":1.24,"rear_world_z":-1.12,"hub_height":0.30})
            after=read_bin_chunk(read_glb(b)[1])
            self.assertEqual(before,after)
            self.assertEqual(result["added_meshes"],0)
            self.assertEqual(result["wheels_created"],4)

if __name__=="__main__":
    unittest.main()
