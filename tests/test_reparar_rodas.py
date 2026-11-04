import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.curadoria.reparar_rodas import repair_wheels, inspect_wheel_nodes, validate_geometry


def sample(path):
    # Synthetic GLB with a valid cuboid body and zero wheel assets.
    vertices=struct.pack("<24f", *[-.8,0,-2,.8,0,-2,-.8,1.5,-2,.8,1.5,-2,
                                 -.8,0,2,.8,0,2,-.8,1.5,2,.8,1.5,2])
    doc={
      "asset":{"version":"2.0"},
      "scene":0,"scenes":[{"nodes":[0]}],
      "nodes":[{"name":"body","mesh":0}],
      "meshes":[{"primitives":[{"attributes":{"POSITION":0},"mode":4}]}],
      "accessors":[{"bufferView":0,"componentType":5126,"count":8,"type":"VEC3",
                    "min":[-.8,0,-2],"max":[.8,1.5,2]}],
      "bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":len(vertices)}],
      "buffers":[{"byteLength":len(vertices)}],
    }
    js=json.dumps(doc).encode()
    js+=b" "*(-len(js)%4)
    path.write_bytes(struct.pack("<4sII",b"glTF",2,20+len(js)+8+len(vertices))+
        struct.pack("<I4s",len(js),b"JSON")+js+
        struct.pack("<I4s",len(vertices),b"BIN\0")+vertices)
    return doc


class TestRepararRodas(unittest.TestCase):
    def test_adds_four_real_wheels_and_keeps_original_source_immutable(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/"original.glb"
            sample(src)
            before=src.read_bytes()
            dst=Path(d)/"repaired.glb"
            report=repair_wheels(src,dst,"ford-ka", hide_groups=())
            self.assertEqual(src.read_bytes(),before)
            self.assertEqual(report["wheel_count"],4)
            self.assertEqual(report["review_status"],"PENDENTE_REVISAO_VISUAL")
            self.assertEqual(report["original_sha256"],hashlib.sha256(before).hexdigest())
            self.assertNotEqual(report["original_sha256"],report["repaired_sha256"])
            validate_geometry(dst)
            wheels=inspect_wheel_nodes(dst)
            self.assertEqual(len(wheels),4)
            self.assertEqual(len({tuple(x["translation"]) for x in wheels}),4)
            self.assertEqual({x["name"] for x in wheels},{
                "ARTISYS_NEW_WHEEL_FL","ARTISYS_NEW_WHEEL_FR",
                "ARTISYS_NEW_WHEEL_RL","ARTISYS_NEW_WHEEL_RR"
            })
            self.assertTrue(all(x["radius"]>0.17 for x in wheels))
            self.assertEqual(report["geometry_status"],"RODAS_PROCEDURAIS_EXPERIMENTAIS")

    def test_too_small_or_flat_geometry_refused(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/"original.glb"
            sample(src)
            raw=src.read_bytes()
            self.assertRaises(ValueError,repair_wheels,src,src,"fiat-palio")
            self.assertEqual(src.read_bytes(),raw)
            self.assertRaises(ValueError,repair_wheels,src,Path(d)/"out.glb","../unsafe")

    def test_radius_and_wheel_spacing_within_vehicle_bounds(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/"source.glb";sample(src)
            dst=Path(d)/"derived.glb";repair_wheels(src,dst,"fiat-palio",hide_groups=())
            wheels=inspect_wheel_nodes(dst)
            self.assertTrue(all(abs(x["translation"][0])<1.0 for x in wheels))
            self.assertTrue(all(-2.0<x["translation"][2]<2.0 for x in wheels))
            self.assertTrue(all(x["translation"][1]>=.2 for x in wheels))

    def test_preserves_original_meshes_binary_prefix_and_credit_fields(self):
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/"source.glb";original=sample(src)
            dst=Path(d)/"derived.glb"
            report=repair_wheels(src,dst,"vw-fox",hide_groups=())
            self.assertEqual(report["original_mesh_count"],1)
            self.assertEqual(report["repaired_mesh_count"],2)
            self.assertTrue(report["binary_original_preserved"])
            self.assertGreater(report["generated_triangle_count"],300)


    def test_meshopt_virtual_fallback_buffer_is_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/"source.glb";sample(source)
            payload=source.read_bytes()
            n=struct.unpack_from("<I",payload,12)[0]
            doc=json.loads(payload[20:20+n])
            doc["buffers"].append({"byteLength":123456,"extensions":{
                "EXT_meshopt_compression":{"fallback":True}
            }})
            doc["extensionsUsed"]=["EXT_meshopt_compression"]
            doc["bufferViews"].append({"buffer":1,"byteOffset":0,
                "byteLength":2048,
                "extensions":{"EXT_meshopt_compression":{
                    "buffer":0,"byteOffset":0,"byteLength":16,
                    "byteStride":4,"count":4,"mode":"ATTRIBUTES"
                }}})
            js=json.dumps(doc).encode()
            js+=b" "*(-len(js)%4)
            old_bin=payload[20+n:]
            source.write_bytes(struct.pack("<4sII",b"glTF",2,20+len(js)+len(old_bin))+
                struct.pack("<I4s",len(js),b"JSON")+js+old_bin)
            output=Path(d)/"out.glb"
            report=repair_wheels(source,output,"fiat-palio",hide_groups=())
            validate_geometry(output)
            from scripts.curadoria.reparar_rodas import _read_binary
            result,_=_read_binary(output)
            self.assertEqual(len(result["buffers"]),2)
            self.assertEqual(result["buffers"][1],doc["buffers"][1])
            self.assertEqual(result["bufferViews"][1],doc["bufferViews"][1])
            self.assertEqual(report["wheel_count"],4)


if __name__=="__main__":
    unittest.main()
