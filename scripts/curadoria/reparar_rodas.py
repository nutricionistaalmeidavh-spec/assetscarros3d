"""Reparo reversível das rodas GLB 2.0 dos veículos Ka, Fox e Palio.

Ka/Fox: reutiliza malhas originais e material de rodas/pneus por instanciamento
nos quatro eixos. Palio: cria geometria inédita ArtiSys para rodas ausentes.
Nenhum serviço pago, Blender ou GPU necessários; opera diretamente no GLB
preservando toda a geometria original e o bloco binário existente.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import struct
from pathlib import Path

from scripts.curadoria.restaurar_glb import read_glb,write_glb

ROOT=Path(__file__).resolve().parents[2]
VALID_IDS={"ford-ka","vw-fox","fiat-palio"}
ANGLES=40


def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def validate_wheel_positions(p):
    for key in ("half_track","front_world_z","rear_world_z","hub_height"):
        value=p.get(key)
        if not isinstance(value,(float,int)) or not math.isfinite(value):
            raise ValueError("Posição inválida de roda: "+key)
        if abs(value)>8:raise ValueError("Posição fora do limite: "+key)
    if not 0.50<=p["half_track"]<=1.35:raise ValueError("Bitola incompatível")
    if p["front_world_z"]-p["rear_world_z"]<1.5:
        raise ValueError("Distância entre eixos incompatível")
    if "radius" in p and not 0.20<=p["radius"]<=0.45:
        raise ValueError("Raio de roda incompatível")
    if "width" in p and not 0.10<=p["width"]<=0.38:
        raise ValueError("Largura de pneu incompatível")


def position_names(p):
    """Coordenadas mundiais no sistema Y-up usado pelo Three.js."""
    for axle,z in (("FRONT",p["front_world_z"]),("REAR",p["rear_world_z"])):
        for side,x in (("LEFT",-p["half_track"]),("RIGHT",p["half_track"])):
            yield axle,side,(x,p["hub_height"],z)


def duplicate_original_wheels(doc,prefix,positions):
    """Instancia quatro rodas a partir de um único conjunto de malhas original.

    Os pais originais são desligados da cena para remover a roda solta na origem,
    mas nós, malhas, materiais e os dados BIN não são apagados.
    """
    validate_wheel_positions(positions)
    nodes=doc.get("nodes",[])
    required=[prefix+"_wheels",prefix+"_tires"]
    found={}
    for idx,node in enumerate(nodes):
        if node.get("name") in required:
            found[node["name"]]=idx
    if set(required)!=set(found):
        raise ValueError("Faltam grupos originais de roda/pneu: "+prefix)
    original_parent=None
    for node in nodes:
        children=node.get("children",[])
        if all(found[name] in children for name in required):
            original_parent=node
            break
    if original_parent is None:
        raise ValueError("Os grupos originais não compartilham um pai confiável")
    old_mesh_count=len(doc.get("meshes",[]))
    old_materials=copy.deepcopy(doc.get("materials",[]))
    original_indices=[found[name] for name in required]
    original_parent["children"]=[i for i in original_parent["children"] if i not in original_indices]
    base_len=len(nodes)
    def clone_subtree(index,suffix):
        # Nunca reaproveitar um índice de nó com vários pais, proibido em glTF.
        source=copy.deepcopy(nodes[index])
        children=source.pop("children",[])
        source["name"]=source.get("name","node")+"__ArtiSys_"+suffix
        new_index=len(nodes)
        nodes.append(source)
        if children:
            source["children"]=[clone_subtree(child,suffix) for child in children]
        return new_index

    positions_list=[]
    for axle,side,(x,y,z) in position_names(positions):
        suffix=side+"_"+axle
        group_index=len(nodes)
        # Collada exportado é Z-up e Sketchfab_model o gira -90° em X:
        # world [X,Y,Z] -> local [X,-Z,Y]
        nodes.append({
            "name":"ArtiSys_WheelPosition_"+suffix,
            "translation":[round(x,6),round(-z,6),round(y,6)],
            "children":[]
        })
        for group_id in original_indices:
            nodes[group_index]["children"].append(clone_subtree(group_id,suffix))
        original_parent["children"].append(group_index)
        positions_list.append({"name":suffix,"world":[x,y,z]})
    if len(doc.get("meshes",[]))!=old_mesh_count or doc.get("materials",[])!=old_materials:
        raise AssertionError("Instanciar rodas não pode modificar malhas ou materiais originais")
    return {"wheels_created":4,"groups_cloned":8,
            "origin":"REPOSICIONAMENTO_MALHAS_ORIGINAIS",
            "added_meshes":0,"positions_world":positions_list,
            "orphan_nodes":original_indices,
            "nodes_before":base_len,"nodes_after":len(nodes)}


def read_bin_chunk(tail):
    if len(tail)<8:
        raise ValueError("GLB sem chunk BIN")
    length,kind=struct.unpack_from("<I4s",tail,0)
    if kind!=b"BIN\0" or length+8>len(tail):
        raise ValueError("Chunk BIN inválido")
    if len(tail)!=8+length:
        raise ValueError("GLB com múltiplos chunks não suportado para reescrita")
    return tail[8:8+length]


class MeshBuilder:
    def __init__(self):
        self.positions=[]
        self.normals=[]
        self.indices=[]

    def triangle(self,p0,p1,p2):
        ab=[p1[k]-p0[k] for k in range(3)]
        ac=[p2[k]-p0[k] for k in range(3)]
        normal=(ab[1]*ac[2]-ab[2]*ac[1],
                ab[2]*ac[0]-ab[0]*ac[2],
                ab[0]*ac[1]-ab[1]*ac[0])
        length=max(math.sqrt(sum(v*v for v in normal)),1e-12)
        normal=[v/length for v in normal]
        base=len(self.positions)
        for p in (p0,p1,p2):
            self.positions.append(tuple(float(v) for v in p))
            self.normals.append(tuple(normal))
        self.indices.extend((base,base+1,base+2))

    def quad(self,a,b,c,d):
        self.triangle(a,b,c)
        self.triangle(a,c,d)

    def radial_band(self,outer_segments):
        """Recebe anéis de pares (X, raio), eixo da roda = X."""
        for k in range(len(outer_segments)-1):
            xa,ra=outer_segments[k]
            xb,rb=outer_segments[k+1]
            for i in range(ANGLES):
                u=2*math.pi*i/ANGLES
                v=2*math.pi*(i+1)/ANGLES
                self.quad((xa,ra*math.cos(u),ra*math.sin(u)),
                          (xa,ra*math.cos(v),ra*math.sin(v)),
                          (xb,rb*math.cos(v),rb*math.sin(v)),
                          (xb,rb*math.cos(u),rb*math.sin(u)))

    def spokes(self,r_inner,r_outer,face_x,width=0.045):
        # Raios metálicos sólidos aparentes dos dois lados da roda.
        for n in range(5):
            a=n*2*math.pi/5
            t=(-math.sin(a),math.cos(a))
            outward=(math.cos(a),math.sin(a))
            def pt(r,w,dx=0):
                return (face_x+dx,
                        r*outward[0]+w*t[0],
                        r*outward[1]+w*t[1])
            self.quad(pt(r_inner,-width*.47),pt(r_outer,-width*.75),
                      pt(r_outer,width*.75),pt(r_inner,width*.47))
            self.quad(pt(r_inner,-width*.47,0.008),pt(r_outer,-width*.75,0.008),
                      pt(r_outer,width*.75,0.008),pt(r_inner,width*.47,0.008))


def _pack_vertices(builder):
    if not builder.positions or len(builder.positions)>=65535:
        raise ValueError("Malha de roda inválida")
    positions=b"".join(struct.pack("<3f",*v) for v in builder.positions)
    normals=b"".join(struct.pack("<3f",*v) for v in builder.normals)
    indices=b"".join(struct.pack("<H",i) for i in builder.indices)
    return positions,normals,indices


def generate_palio_wheels(doc,original_bin,p):
    """Constrói quatro pneus/jantes originais da ArtiSys, sem remover carroceria."""
    validate_wheel_positions(p)
    if not doc.get("buffers") or len(doc["buffers"])!=1:
        raise ValueError("GLB sem buffer único adequado")
    if len(original_bin)<doc["buffers"][0]["byteLength"]:
        raise ValueError("Chunk BIN original inconsistente")
    radius=p["radius"]
    width=p["width"]
    half=width/2
    parts=[]

    tyre=MeshBuilder()
    tyre.radial_band([(-half,0.235),(-half,0.30),(-half*.87,radius),
                       (half*.87,radius),(half,0.30),(half,0.235)])
    parts.append(("ArtiSys_Rubber",tyre,{"baseColorFactor":[0.045,0.053,0.061,1],
                                              "metallicFactor":0,"roughnessFactor":0.94}))

    rim=MeshBuilder()
    rim.radial_band([(-half-0.005,0.23),(-half-0.012,0.195),
                     (-half-0.018,0.08),(half+0.018,0.08),
                     (half+0.012,0.195),(half+0.005,0.23)])
    parts.append(("ArtiSys_Aluminium_Rim",rim,{"baseColorFactor":[0.63,0.69,0.73,1],
                                              "metallicFactor":0.70,"roughnessFactor":0.35}))

    spoke=MeshBuilder()
    spoke.spokes(0.065,0.185,-half-0.024)
    spoke.spokes(0.065,0.185,half+0.024)
    spoke.radial_band([(-half-0.03,0.057),(-half-0.032,0.012),
                       (half+0.032,0.012),(half+0.03,0.057)])
    parts.append(("ArtiSys_Five_Spoke_Hub",spoke,{"baseColorFactor":[0.73,0.77,0.79,1],
                                              "metallicFactor":0.76,"roughnessFactor":0.32}))

    # Append as unhasigned float32 and uint16 buffers without altering original
    # compressed meshes / EXT_meshopt_compression references.
    data=bytearray(original_bin)
    while len(data)%4:data.append(0)
    doc.setdefault("bufferViews",[])
    doc.setdefault("accessors",[])
    doc.setdefault("materials",[])
    doc.setdefault("meshes",[])
    primitives=[]
    for name,geometry,pbr in parts:
        b_position,b_normal,b_indices=_pack_vertices(geometry)
        accessors=[]
        for chunk,component,type_name,target,count in (
            (b_position,5126,"VEC3",34962,len(geometry.positions)),
            (b_normal,5126,"VEC3",34962,len(geometry.normals)),
            (b_indices,5123,"SCALAR",34963,len(geometry.indices))
        ):
            while len(data)%4:data.append(0)
            offset=len(data)
            data.extend(chunk)
            view=len(doc["bufferViews"])
            doc["bufferViews"].append({
                "buffer":0,"byteOffset":offset,"byteLength":len(chunk),"target":target
            })
            acc={"bufferView":view,"byteOffset":0,
                 "componentType":component,"type":type_name,"count":count}
            if type_name=="VEC3" and chunk is b_position:
                acc["min"]=[min(a[k] for a in geometry.positions) for k in range(3)]
                acc["max"]=[max(a[k] for a in geometry.positions) for k in range(3)]
            acc_index=len(doc["accessors"])
            doc["accessors"].append(acc)
            accessors.append(acc_index)
        material=len(doc["materials"])
        doc["materials"].append({
            "name":name,"doubleSided":True,
            "pbrMetallicRoughness":pbr
        })
        primitives.append({
            "attributes":{"POSITION":accessors[0],"NORMAL":accessors[1]},
            "indices":accessors[2],"material":material,"mode":4
        })
    while len(data)%4:data.append(0)
    doc["buffers"][0]["byteLength"]=len(data)
    wheel_mesh_idx=len(doc["meshes"])
    doc["meshes"].append({"name":"ArtiSys_Palio_Wheel_Procedural","primitives":primitives})
    positions=[]
    if not doc.get("scenes"):
        raise ValueError("GLB sem cena")
    scene=doc["scenes"][doc.get("scene",0)]
    for axle,side,world in position_names(p):
        # Palio original já é visto Y-up pelo GLTFLoader; nós anexados na
        # cena são orientados em coordenadas mundiais e não herdam o FBX root.
        node={"name":"ArtiSys_Wheel_"+side+"_"+axle,
              "mesh":wheel_mesh_idx,
              "translation":[round(v,6) for v in world]}
        index=len(doc["nodes"])
        doc["nodes"].append(node)
        scene.setdefault("nodes",[]).append(index)
        positions.append({"name":side+"_"+axle,"world":list(world)})
    return {"wheels_created":4,"origin":"GEOMETRIA_PROCEDURAL_ARTISYS",
            "groups_cloned":0,"added_meshes":1,"positions_world":positions,
            "radius":radius,"width":width}, bytes(data)


def repair_file(source,dest,id,settings):
    if id not in VALID_IDS:
        raise ValueError("Modelo fora do escopo de reparo")
    src=Path(source)
    dst=Path(dest)
    if src.resolve()==dst.resolve():
        raise ValueError("É proibido sobrescrever o original")
    doc,tail=read_glb(src)
    orig_bin=read_bin_chunk(tail)
    mesh_before=len(doc.get("meshes",[]))
    node_before=len(doc.get("nodes",[]))
    if id=="fiat-palio":
        stats,new_bin=generate_palio_wheels(doc,orig_bin,settings)
        tail=struct.pack("<I4s",len(new_bin),b"BIN\0")+new_bin
    else:
        prefix="fmka" if id=="ford-ka" else "fmfox"
        stats=duplicate_original_wheels(doc,prefix,settings)
    write_glb(dst,doc,tail)
    check_doc,check_tail=read_glb(dst)
    expected_meshes=mesh_before+stats["added_meshes"]
    if len(check_doc.get("meshes",[]))!=expected_meshes:
        raise AssertionError("Contagem de malhas não corresponde ao reparo")
    if len(check_doc.get("nodes",[]))<=node_before:
        raise AssertionError("Rodas não foram adicionadas")
    if id!="fiat-palio" and read_bin_chunk(check_tail)!=orig_bin:
        raise AssertionError("Bloco binário original foi modificado")
    out={**stats,"asset_id":id,"original_file":src.name,
         "output_file":dst.name,"sha256_original":sha(src),
         "sha256_restaurado":sha(dst),"mesh_before":mesh_before,
         "mesh_after":expected_meshes,
         "review_status":"PENDENTE_REVISAO_VISUAL",
         "publication_status":"NAO_HOMOLOGADO",
         "note":"Aprovação comercial depende de inspeção visual real dos quatro pneus; usar somente no laboratório."}
    return out


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=ROOT/"curadoria/perfis-reparo-rodas.json")
    p.add_argument("--input",type=Path,default=ROOT/"curadoria/restaurados")
    p.add_argument("--output",type=Path,default=ROOT/"curadoria/reparos-rodas")
    a=p.parse_args()
    data=json.loads(a.config.read_text(encoding="utf-8"))
    catalog=json.loads((ROOT/"cars.json").read_text(encoding="utf-8"))
    credits={x["id"]:x["credit"] for x in catalog["cars"]}
    items=[]
    for id,settings in data["models"].items():
        source=a.input/(id+"-restaurado.glb")
        if not source.exists():
            raise FileNotFoundError("Versão restaurada de materiais ausente: "+str(source))
        output=a.output/(id+"-rodas-reparadas.glb")
        result=repair_file(source,output,id,settings)
        result["original_credit"]=credits[id]
        items.append(result)
        print("REPARO_OK",id,result["origin"],"rodas",result["wheels_created"])
    a.output.mkdir(parents=True,exist_ok=True)
    (a.output/"manifest.json").write_text(json.dumps({
        "schema_version":1,"status":"GEOMETRIA_REPARADA_PENDENTE_QA_VISUAL",
        "models":items,
        "notes":"Originais e versões restauradas permanecem imutáveis; as novas versões NÃO são autorizadas ao catálogo comercial."
    },ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
