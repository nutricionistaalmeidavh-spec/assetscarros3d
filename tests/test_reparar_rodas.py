import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.curadoria.restaurar_glb import read_glb
from scripts.curadoria.reparar_rodas import repair_variant, plan_wheels, world_to_local_parent


def sample(path):
    doc={
       "asset":{"version":"2.0"},
       "buffers":[{"byteLength":8}],
       "bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":8}],
       "accessors":[],
       "nodes":[{"name":"Root","children":[]}],
       "scenes":[{"nodes":[0]}],
       "scene":0,
       "meshes":[],
       "materials":[]
    }
    raw=json.dumps(doc).encode()
    raw+=b" "*(-len(raw)%4)
    bin_=b"ORIGDATA"
    path.write_bytes(struct.pack("<4sII",b"glTF",2,20+len(raw)+8+len(bin_))+
                     struct.pack("<I4s",len(raw),b"JSON")+raw+
                     struct.pack("<I4s",len(bin_),b"BIN\0")+bin_)
    return doc,bin_


class TestReparo(unittest.TestCase):
    def test_ka_fox_wheels_use_z_length_and_y_up_measured_from_real_glbs(self):
        cfg={"length_axis":"z","up_axis":"y","width_axis":"x","axle_offsets":[.18,.78],"wheel_radius":.298,"ground_offset":.015}
        centers=plan_wheels({"min":[-.955,-.298,-1.465],"max":[.955,1.777,2.151]},cfg)
        self.assertEqual(len(centers),4)
        self.assertEqual(len({round(c[2],6) for c in centers}),2)
        self.assertEqual(len({round(c[0],6) for c in centers}),2)
        self.assertTrue(all(.28<c[1]<.37 for c in centers))
        self.assertLess(centers[0][2],centers[2][2])

    def test_palio_uses_z_length_y_up(self):
        cfg={"length_axis":"z","up_axis":"y","width_axis":"x","axle_offsets":[.24,.76],"wheel_radius":.325}
        centers=plan_wheels({"min":[-1,0,-2.1],"max":[1,1.4,2.1]},cfg)
        self.assertEqual(len(centers),4)
        self.assertEqual(len({round(c[2],6) for c in centers}),2)
        self.assertEqual(len({round(c[1],6) for c in centers}),1)
        self.assertTrue(all(c[1]>.15 for c in centers))

    def test_preserves_existing_nodes_meshes_and_binary_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/"orig.glb"
            expected,old_bin=sample(src)
            out=Path(folder)/"derived.glb"
            cfg={"length_axis":"z","up_axis":"y","width_axis":"x","axle_offsets":[.23,.76],"wheel_radius":.325}
            meta=repair_variant(src,out,{"min":[-1,0,-2],"max":[1,1.6,2]},cfg,"fiat-palio")
            doc,tail=read_glb(out)
            bin_length,chunk_type=struct.unpack_from("<I4s",tail,0)
            self.assertEqual(chunk_type,b"BIN\0")
            self.assertEqual(tail[8:8+len(old_bin)],old_bin)
            self.assertEqual(doc["nodes"][0],expected["nodes"][0])
            self.assertEqual(len(doc["meshes"]),1)
            self.assertEqual(len(doc["nodes"]),5)
            self.assertEqual(doc["meshes"][0]["primitives"][0]["mode"],4)
            self.assertEqual(len({tuple(n["translation"]) for n in doc["nodes"][1:]}),4)
            self.assertTrue(all(n["mesh"]==0 for n in doc["nodes"][1:]))
            self.assertEqual(meta["status"],"PROCEDURAL_APPROXIMATION_PENDING_VISUAL_REVIEW")
            self.assertFalse(meta["approved_for_officina"])
            self.assertTrue(meta["source_binary_prefix_preserved"])


    def test_sketchfab_root_rotation_moves_wheels_into_wheel_arches(self):
        # A raiz do Ka/Fox está girada -90 graus em X. As medições são
        # feitas em world space, mas os novos nós pertencem à scene group
        # no espaço local anterior à rotação.
        import math
        parent={"rotation":[-math.sqrt(.5),0,0,math.sqrt(.5)]}
        location=world_to_local_parent([.8246,.31896,1.355278],"sketchfab_x_minus_90",parent)
        self.assertAlmostEqual(location[0],.8246,places=5)
        self.assertAlmostEqual(location[1],-1.355278,places=5)
        self.assertAlmostEqual(location[2],.31896,places=5)
        with self.assertRaises(ValueError):
            world_to_local_parent([.8,.3,1.2],"sketchfab_x_minus_90",{"rotation":[0,0,0,1]})

    def test_ka_reuses_original_rim_and_tire_meshes_at_four_positions(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/"ka.glb"
            sample(src)
            doc,binchunk=read_glb(src)
            doc["nodes"]=[
                {"name":"Root","children":[1,3]},
                {"name":"fmka_wheels","children":[2]},
                {"name":"rim","mesh":0},
                {"name":"fmka_tires","children":[4]},
                {"name":"tire","mesh":1}
            ]
            doc["meshes"]=[{"primitives":[]},{"primitives":[]}]
            from scripts.curadoria.restaurar_glb import write_glb
            write_glb(src,doc,binchunk)
            original=src.read_bytes()
            output=Path(folder)/"ka-rodas.glb"
            meta=repair_variant(src,output,{"min":[-.955,-.298,-1.465],"max":[.955,1.777,2.151]},
                {"length_axis":"z","up_axis":"y","width_axis":"x","wheel_radius":.298,
                 "axle_offsets":[.18,.78],"ground_offset":.015,
                 "reuse_groups":["fmka_wheels","fmka_tires"]},"ford-ka")
            changed,binary=read_glb(output)
            self.assertEqual(len(changed["meshes"]),2)
            self.assertEqual(len([n for n in changed["nodes"] if n.get("name","").startswith("ArtiSys_Wheel_")]),4)
            self.assertEqual(len([n for n in changed["nodes"] if n.get("name")=="fmka_tires"]),4)
            self.assertEqual(len([n for n in changed["nodes"] if n.get("name")=="fmka_wheels"]),4)
            self.assertEqual(changed["nodes"][0]["children"].count(1),0)
            self.assertEqual(changed["nodes"][0]["children"].count(3),0)
            self.assertEqual(original,src.read_bytes())
            self.assertEqual(binary,binchunk)
            self.assertEqual(meta["status"],"REUSED_ORIGINAL_WHEELS_PENDING_VISUAL_REVIEW")
            self.assertFalse(meta["approved_for_officina"])

    def test_unsafe_coordinates_or_axis_config_is_rejected(self):
        for bounds,axes in [
          ({"min":[-1,-2,0],"max":[1,2,1.6]},{"length_axis":"x","up_axis":"x","width_axis":"z","axle_offsets":[.25,.75],"wheel_radius":.3}),
          ({"min":[0,0,0],"max":[0,0,0]},{"length_axis":"z","up_axis":"y","width_axis":"x","axle_offsets":[.25,.75],"wheel_radius":.3}),
          ({"min":[-1,-2,0],"max":[1,2,1.6]},{"length_axis":"z","up_axis":"y","width_axis":"x","axle_offsets":[-.1,2],"wheel_radius":.3})
        ]:
          with self.assertRaises(ValueError):
            plan_wheels(bounds,axes)

    def test_invalid_no_original_bin_chunk_must_not_be_modified(self):
        with tempfile.TemporaryDirectory() as folder:
          src=Path(folder)/"empty.glb"
          doc={"asset":{"version":"2.0"},"buffers":[],"nodes":[],"scenes":[{"nodes":[]}],"scene":0}
          payload=json.dumps(doc).encode()
          payload+=b" "*(-len(payload)%4)
          src.write_bytes(struct.pack("<4sII",b"glTF",2,20+len(payload))+struct.pack("<I4s",len(payload),b"JSON")+payload)
          with self.assertRaises(ValueError):
            repair_variant(src,Path(folder)/"out.glb",{"min":[-1,-2,0],"max":[1,2,1.6]},{"length_axis":"z","up_axis":"y","width_axis":"x","axle_offsets":[.2,.8],"wheel_radius":.3},"fiat-palio")


if __name__=="__main__":
    unittest.main()
