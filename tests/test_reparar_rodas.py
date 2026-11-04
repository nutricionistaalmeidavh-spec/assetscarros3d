import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.curadoria.restaurar_glb import read_glb
from scripts.curadoria.reparar_rodas import add_procedural_wheels, repair_variant, plan_wheels


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
    def test_ka_fox_wheels_use_xy_length_and_z_up(self):
        cfg={"length_axis":"y","up_axis":"z","width_axis":"x","axle_offsets":[.23,.76],"wheel_radius_ratio":.18}
        centers=plan_wheels({"min":[-1,-2,0],"max":[1,2,1.6]},cfg)
        self.assertEqual(len(centers),4)
        self.assertEqual(len({round(c[1],6) for c in centers}),2)
        self.assertEqual(len({round(c[0],6) for c in centers}),2)
        self.assertTrue(all(c[2]>.1 and c[2]<.65 for c in centers))
        self.assertLess(centers[0][1],centers[2][1])

    def test_palio_uses_z_length_y_up(self):
        cfg={"length_axis":"z","up_axis":"y","width_axis":"x","axle_offsets":[.24,.76],"wheel_radius_ratio":.19}
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
            cfg={"length_axis":"y","up_axis":"z","width_axis":"x","axle_offsets":[.23,.76],"wheel_radius_ratio":.18}
            meta=repair_variant(src,out,{"min":[-1,-2,0],"max":[1,2,1.6]},cfg,"ford-ka")
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

    def test_unsafe_coordinates_or_axis_config_is_rejected(self):
        for bounds,axes in [
          ({"min":[-1,-2,0],"max":[1,2,1.6]},{"length_axis":"x","up_axis":"x","width_axis":"z","axle_offsets":[.25,.75]}),
          ({"min":[0,0,0],"max":[0,0,0]},{"length_axis":"y","up_axis":"z","width_axis":"x","axle_offsets":[.25,.75]}),
          ({"min":[-1,-2,0],"max":[1,2,1.6]},{"length_axis":"y","up_axis":"z","width_axis":"x","axle_offsets":[-.1,2]})
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
            repair_variant(src,Path(folder)/"out.glb",{"min":[-1,-2,0],"max":[1,2,1.6]},{"length_axis":"y","up_axis":"z","width_axis":"x","axle_offsets":[.2,.8]},"ford-ka")


if __name__=="__main__":
    unittest.main()
