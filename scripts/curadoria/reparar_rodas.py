"""Restauro de rodas como geometria GLB 2.0 real, sem Blender ou API paga.

Mantém todos os bytes binários originais, acrescenta malhas reais de pneu,
aro e raios; oculta apenas os grupos de rodas antigos centralizados quando
presentes. A posição é heurística geométrica e EXIGE revisão visual.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from pathlib import Path

from scripts.curadoria.restaurar_glb import read_glb

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {"ford-ka", "vw-fox", "fiat-palio"}
DEFAULT_HIDE = {
    "ford-ka": ("fmka_wheels", "fmka_tires"),
    "vw-fox": ("fmfox_wheels", "fmfox_tires"),
    "fiat-palio": (),
}
LABELS = ("FL", "FR", "RL", "RR")


def _matrix_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _local_matrix(node):
    if "matrix" in node:
        vals = node["matrix"]
        if len(vals) != 16:
            raise ValueError("Transformação inválida")
        return [[float(vals[c * 4 + r]) for c in range(4)] for r in range(4)]
    x, y, z, w = node.get("rotation", [0, 0, 0, 1])
    sx, sy, sz = node.get("scale", [1, 1, 1])
    tx, ty, tz = node.get("translation", [0, 0, 0])
    rotation = [
        [1 - 2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
        [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
        [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
    ]
    return [[rotation[r][0]*sx, rotation[r][1]*sy,
             rotation[r][2]*sz, [tx, ty, tz][r]] for r in range(3)] + [[0, 0, 0, 1]]


IDENTITY = [[float(i == j) for j in range(4)] for i in range(4)]


def _point(matrix, p):
    return [sum(matrix[i][k]*p[k] for k in range(3)) + matrix[i][3] for i in range(3)]


def _normal_bound(accessor, component):
    value = float(component)
    if not accessor.get("normalized"):
        return value
    kind = accessor.get("componentType")
    if kind == 5122:  # signed int16
        return max(-1.0, value / 32767.0)
    if kind == 5120:  # signed int8
        return max(-1.0, value / 127.0)
    if kind == 5123:
        return value / 65535.0
    if kind == 5121:
        return value / 255.0
    return value


def scene_bounds(doc, exclude=()):
    """Caixa de limites real considerando todos os transforms, inclusive quantização."""
    nodes = doc["nodes"]
    scene = doc["scenes"][doc.get("scene", 0)]
    low = [float("inf")] * 3
    high = [float("-inf")] * 3
    count = 0

    def visit(i, parent):
        nonlocal count
        node = nodes[i]
        if node.get("name") in exclude:
            return
        transform = _matrix_mul(parent, _local_matrix(node))
        mesh_id = node.get("mesh")
        if mesh_id is not None:
            for primitive in doc["meshes"][mesh_id].get("primitives", []):
                idx = primitive.get("attributes", {}).get("POSITION")
                if idx is None:
                    continue
                accessor = doc["accessors"][idx]
                if not (accessor.get("min") and accessor.get("max")):
                    continue
                lower = [_normal_bound(accessor, v) for v in accessor["min"]]
                upper = [_normal_bound(accessor, v) for v in accessor["max"]]
                for mask in range(8):
                    point = [upper[k] if mask & (1 << k) else lower[k] for k in range(3)]
                    transformed = _point(transform, point)
                    for k in range(3):
                        low[k] = min(low[k], transformed[k])
                        high[k] = max(high[k], transformed[k])
                count += 1
        for child in node.get("children", []):
            visit(child, transform)

    for index in scene.get("nodes", []):
        visit(index, IDENTITY)
    if count == 0:
        raise ValueError("GLB sem limites geométricos válidos")
    size = [high[k] - low[k] for k in range(3)]
    if any(not math.isfinite(v) or v <= .15 or v > 50 for v in size):
        raise ValueError(f"Limites do veículo não plausíveis: {size}")
    # x largura, y altura, z comprimento, eixo do veículo rodando pelo mundo.
    if not (size[2] > size[0] * 1.1 and size[1] > .5):
        raise ValueError(f"Orientação/escala do GLB exigem ajuste manual: {size}")
    return {"min": low, "max": high, "size": size}


def _add_triangle(store, a, b, c):
    ab = [b[i]-a[i] for i in range(3)]
    ac = [c[i]-a[i] for i in range(3)]
    n = [ab[1]*ac[2]-ab[2]*ac[1],
         ab[2]*ac[0]-ab[0]*ac[2],
         ab[0]*ac[1]-ab[1]*ac[0]]
    norm = math.sqrt(sum(v*v for v in n)) or 1.0
    n = [v/norm for v in n]
    for v in (a,b,c):
        store["positions"].extend(v)
        store["normals"].extend(n)


def _quad(store,a,b,c,d):
    _add_triangle(store,a,b,c)
    _add_triangle(store,a,c,d)


def _vertex(x, radius, angle):
    return [x, math.cos(angle)*radius, math.sin(angle)*radius]


def _ring(store, x0, r0, x1, r1, segments=40):
    for i in range(segments):
        a = 2*math.pi*i/segments
        b = 2*math.pi*(i+1)/segments
        _quad(store,_vertex(x0,r0,a),_vertex(x0,r0,b),
              _vertex(x1,r1,b),_vertex(x1,r1,a))


def _disk_sector(store, x, inner, outer, a0, a1):
    _quad(store,_vertex(x,inner,a0),_vertex(x,outer,a0),
          _vertex(x,outer,a1),_vertex(x,inner,a1))


def _geometry(radius, thickness):
    stores=[{"positions":[],"normals":[]} for _ in range(3)]
    tire,rim,center=stores
    # Pneus reais em 3D: ombros arredondados e lateral com abertura para o aro.
    cross=[
        (-thickness*.50,radius*.69),(-thickness*.50,radius*.82),
        (-thickness*.40,radius*.97),(-thickness*.24,radius),
        (thickness*.24,radius),(thickness*.40,radius*.97),
        (thickness*.50,radius*.82),(thickness*.50,radius*.69)]
    for (x0,r0),(x1,r1) in zip(cross[:-1],cross[1:]):
        _ring(tire,x0,r0,x1,r1)
    for side in (-1,1):
        x=side*(thickness*.52)
        # Aro de liga, visível dos dois lados.
        _ring(rim,x,radius*.18,x,radius*.66)
        # Estrutura escura atrás dos raios.
        _ring(center,x-side*.003,radius*.10,x-side*.003,radius*.68)
        for i in range(7):
            a=2*math.pi*i/7
            _disk_sector(rim,x+side*.009,radius*.16,radius*.58,a-.065,a+.065)
        for i in range(20):
            a=2*math.pi*i/20
            b=2*math.pi*(i+1)/20
            _add_triangle(rim,[x+side*.012,0,0],
                          _vertex(x+side*.012,radius*.16,a),
                          _vertex(x+side*.012,radius*.16,b))
    return stores


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_binary(source):
    doc, tail = read_glb(source)
    if len(tail)<8:
        raise ValueError("GLB sem BIN")
    n, kind=struct.unpack_from("<I4s",tail,0)
    if kind!=b"BIN\0" or n+8!=len(tail):
        raise ValueError("GLB com chunks binários não suportados")
    buffers=doc.get("buffers",[])
    if not buffers or buffers[0].get("uri"):
        raise ValueError("GLB físico em buffer externo não suportado")
    # GLBs com EXT_meshopt_compression podem incluir buffer #1 virtual,
    # com bytes de fallback que nunca estão fisicamente presentes no BIN.
    for virtual in buffers[1:]:
        if virtual.get("uri") or not virtual.get("extensions",{}).get("EXT_meshopt_compression",{}).get("fallback"):
            raise ValueError("Buffer externo desconhecido ou não autorizado")
    if buffers[0]["byteLength"]>n:
        raise ValueError("BIN físico menor que o declarado")
    return doc,tail[8:]


def inspect_wheel_nodes(path):
    doc,_=_read_binary(path)
    names=set(LABELS)
    result=[]
    for n in doc.get("nodes", []):
        match=re.fullmatch(r"ARTISYS_NEW_WHEEL_(FL|FR|RL|RR)",n.get("name",""))
        if match:
            result.append({"name":n["name"],"translation":n.get("translation",[]),
                           "radius":n.get("extras",{}).get("radius",0)})
    return result


def validate_geometry(path):
    doc,blob=_read_binary(path)
    if doc["buffers"][0]["byteLength"]>len(blob):
        raise ValueError("BIN menor que os buffers declarados")
    if len(inspect_wheel_nodes(path)) != 4:
        raise ValueError("A geometria reparada deve ter quatro rodas")
    for view in doc.get("bufferViews", []):
        buffer_index=view.get("buffer",0)
        if buffer_index<0 or buffer_index>=len(doc.get("buffers",[])):
            raise ValueError("BufferView com origem inválida")
        if buffer_index!=0 and not view.get("extensions",{}).get("EXT_meshopt_compression"):
            raise ValueError("Buffer virtual sem a extensão Meshopt")
        if view.get("byteOffset",0)+view["byteLength"]>doc["buffers"][buffer_index]["byteLength"]:
            raise ValueError("Atributo fora do buffer")
        compressed=view.get("extensions",{}).get("EXT_meshopt_compression")
        if compressed:
            offset=compressed.get("byteOffset",0)
            if offset+compressed.get("byteLength",0)>len(blob):
                raise ValueError("Bloco Meshopt fora do BIN físico")
    for acc in doc.get("accessors", []):
        if acc.get("bufferView",0)>=len(doc.get("bufferViews",[])):
            raise ValueError("Accessor com bufferView inválido")
    return True


def repair_wheels(source, target, asset_id, *, hide_groups=None):
    if asset_id not in ALLOWED and not (asset_id=="test-fixture"):
        raise ValueError("Modelo não autorizado para reparo automático")
    source,target=Path(source),Path(target)
    if source.resolve()==target.resolve():
        raise ValueError("Nunca sobrescrever a fonte")
    doc,original_blob=_read_binary(source)
    old_mesh_count=len(doc["meshes"])
    old_node_count=len(doc["nodes"])
    hidden=DEFAULT_HIDE[asset_id] if hide_groups is None else tuple(hide_groups)
    bounds=scene_bounds(doc,exclude=hidden)
    width,height,length=bounds["size"]
    lo,hi=bounds["min"],bounds["max"]
    radius=min(height*.215,width*.195)
    thickness=min(width*.15,radius*.79)
    if radius<.17 or radius>.52:
        raise ValueError("Rodas fora da faixa de proporção esperada")
    middle_x=(lo[0]+hi[0])*.5
    left=middle_x-(width*.5-thickness*.43)
    right=middle_x+(width*.5-thickness*.43)
    # Centro ligeiramente acima do piso. Distâncias entre eixos aproximadas.
    level=lo[1]+radius*.98
    front=lo[2]+length*.19
    rear=lo[2]+length*.81
    if abs(rear-front)<radius*3.5:
        raise ValueError("Entre-eixos não plausível")
    positions=[
        [left,level,front],[right,level,front],
        [left,level,rear],[right,level,rear],
    ]
    for position in positions:
        if not all(math.isfinite(v) for v in position):
            raise ValueError("Posição de roda inválida")

    # Conservar malhas originais, mas desligar grupos antigos de roda centralizados.
    hidden_names=[]
    for node in doc.get("nodes", []):
        if node.get("name") in hidden:
            node["scale"]=[0,0,0]
            hidden_names.append(node["name"])
    if set(hidden_names)!=set(hidden):
        raise ValueError("Os grupos de rodas originais declarados não foram encontrados")

    generated=_geometry(radius,thickness)
    blob=bytearray(original_blob)
    material_offset=len(doc.setdefault("materials",[]))
    for name,color,roughness,metallic in [
        ("ARTISYS_TYRE_MATTE",[.044,.051,.063,1],.93,0),
        ("ARTISYS_ALLOY_RIM",[.66,.72,.76,1],.32,.60),
        ("ARTISYS_BRAKE_DARK",[.105,.12,.145,1],.78,.11),
    ]:
        doc["materials"].append({
            "name":name,"doubleSided":True,
            "pbrMetallicRoughness":{"baseColorFactor":color,
                "roughnessFactor":roughness,"metallicFactor":metallic}
        })
    mesh={"name":"ARTISYS_PROCEDURAL_WHEEL_ASSEMBLY","primitives":[]}
    triangles=0
    for material_id,data in enumerate(generated):
        vertices=len(data["positions"])//3
        if vertices<3 or vertices>40000 or vertices%3:
            raise ValueError("Falha na geometria das rodas")
        attributes={}
        for key,values in (("POSITION",data["positions"]),("NORMAL",data["normals"])):
            payload=struct.pack("<"+"f"*len(values),*values)
            while len(blob)%4:blob.append(0)
            offset=len(blob);blob.extend(payload)
            bv=len(doc.setdefault("bufferViews",[]))
            doc["bufferViews"].append({"buffer":0,"byteOffset":offset,"byteLength":len(payload)})
            acc={"bufferView":bv,"componentType":5126,"count":vertices,"type":"VEC3"}
            if key=="POSITION":
                vectors=[values[i:i+3] for i in range(0,len(values),3)]
                acc["min"]=[min(p[i] for p in vectors) for i in range(3)]
                acc["max"]=[max(p[i] for p in vectors) for i in range(3)]
            idx=len(doc.setdefault("accessors",[]))
            doc["accessors"].append(acc);attributes[key]=idx
        mesh["primitives"].append({"attributes":attributes,"material":material_offset+material_id,"mode":4})
        triangles+=vertices//3
    mesh_id=len(doc["meshes"]);doc["meshes"].append(mesh)
    scene=doc["scenes"][doc.get("scene",0)]
    for name,position in zip(LABELS,positions):
        node_idx=len(doc["nodes"])
        doc["nodes"].append({
            "name":"ARTISYS_NEW_WHEEL_"+name,
            "mesh":mesh_id,
            "translation":position,
            "extras":{"generated_by":"ArtiSys Oficina / wheel-repair-v1",
                      "radius":radius,"part_type":"procedural_wheel",
                      "model_status":"EXPERIMENTAL_NOT_OEM"}
        })
        scene.setdefault("nodes",[]).append(node_idx)
    doc["buffers"][0]["byteLength"]=len(blob)
    js=json.dumps(doc,ensure_ascii=False,separators=(",",":")).encode()
    js+=b" "*(-len(js)%4)
    while len(blob)%4:blob.append(0)
    header=struct.pack("<4sII",b"glTF",2,20+len(js)+8+len(blob))
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(header+struct.pack("<I4s",len(js),b"JSON")+js+
                       struct.pack("<I4s",len(blob),b"BIN\0")+blob)
    validate_geometry(target)
    if target.read_bytes()==source.read_bytes():
        raise ValueError("Reparo não alterou o GLB")
    return {
        "id":asset_id,"wheel_count":4,
        "wheel_centers":dict(zip(LABELS,positions)),
        "wheel_radius":radius,"wheel_thickness":thickness,
        "original_mesh_count":old_mesh_count,
        "repaired_mesh_count":len(doc["meshes"]),
        "original_node_count":old_node_count,
        "repaired_node_count":len(doc["nodes"]),
        "generated_triangle_count":triangles,
        "hidden_original_groups":hidden_names,
        "original_sha256":_sha(source),
        "repaired_sha256":_sha(target),
        "binary_original_preserved":bytes(blob).startswith(original_blob),
        "geometry_status":"RODAS_PROCEDURAIS_EXPERIMENTAIS",
        "review_status":"PENDENTE_REVISAO_VISUAL",
        "vehicle_bounds":bounds,
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--assets",type=Path,default=ROOT/"curadoria/restaurados")
    parser.add_argument("--output",type=Path,default=ROOT/"curadoria/geometria")
    args=parser.parse_args()
    catalog=json.loads((ROOT/"cars.json").read_text(encoding="utf-8"))
    records={car["id"]:car for car in catalog["cars"]}
    reports=[]
    for asset_id in sorted(ALLOWED):
        original=args.assets/(asset_id+"-restaurado.glb")
        if not original.exists():
            raise ValueError("Falta variante anterior: "+str(original))
        target=args.output/(asset_id+"-rodas.glb")
        report=repair_wheels(original,target,asset_id)
        report["author"]=records[asset_id]["credit"]["author"]
        report["license"]=records[asset_id]["credit"]["license"]
        report["sourceUrl"]=records[asset_id]["credit"]["sourceUrl"]
        report["path"]="curadoria/geometria/"+target.name
        reports.append(report)
        print(asset_id, "RADIUS",round(report["wheel_radius"],3),
              "BOUND", [round(v,3) for v in report["vehicle_bounds"]["size"]],
              "SHA",report["repaired_sha256"][:16])
    (args.output/"manifest.json").write_text(
        json.dumps({"schema_version":1,"status":"REVISAO_VISUAL_PENDENTE",
                    "note":"As rodas são geometria nova procedimental, não peças originais do fabricante.",
                    "models":reports},indent=2,ensure_ascii=False)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
