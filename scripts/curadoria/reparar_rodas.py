"""Reparo reversível de rodas GLB (Ka/Fox: peças originais, Palio: geometria marcada).

Compatível com GLB 2.0 comprimido com Meshopt: a transformação da cena mantém
sem mudança os bytes originais dos buffers. Para Palio acrescentamos dados BIN
não comprimidos com atributos válidos glTF 2.0. Nenhuma licença paga/API/CDN.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import struct
from pathlib import Path

from scripts.curadoria.restaurar_glb import read_glb, write_glb

ROOT=Path(__file__).resolve().parents[2]
AXES={"x":0,"y":1,"z":2}
MAX_FILE_BYTES=25*1024*1024


def plan_wheels(bounds, cfg):
    lo=bounds.get("min")
    hi=bounds.get("max")
    if (not isinstance(lo,list) or not isinstance(hi,list) or len(lo)!=3 or len(hi)!=3
        or not all(isinstance(x,(float,int)) and math.isfinite(x) for x in lo+hi)
        or any(b<=a for a,b in zip(lo,hi))):
        raise ValueError("Bounds 3D inconsistentes")
    names=[cfg.get(k) for k in ("width_axis","length_axis","up_axis")]
    if set(names)!={"x","y","z"}:
        raise ValueError("Eixos do modelo 3D devem ser distintos")
    offsets=cfg.get("axle_offsets",[])
    if (not isinstance(offsets,list) or len(offsets)!=2
        or not all(isinstance(x,(int,float)) and .05<=x<=.95 for x in offsets)
        or offsets[0]>=offsets[1]):
        raise ValueError("Posições dos eixos longitudinalmente inválidas")
    radius=cfg.get("wheel_radius")
    if not isinstance(radius,(int,float)) or not .1<=radius<=.8:
        raise ValueError("Raio de roda fora da escala esperada")
    w,l,u=[AXES[n] for n in names]
    width=hi[w]-lo[w]
    length=hi[l]-lo[l]
    if not 1<=width<=5 or not 2<=length<=12:
        raise ValueError("Dimensões do automóvel fora da escala esperada")
    inset=float(cfg.get("side_inset", .12))
    if not .06<=inset<width*.35:
        raise ValueError("Afastamento lateral inválido")
    ground=float(cfg.get("ground_offset", lo[u]))
    if not math.isfinite(ground):
        raise ValueError("Coordenada de solo inválida")
    centers=[]
    for f in offsets:
        for side in (-1,1):
            center=[(a+b)/2 for a,b in zip(lo,hi)]
            center[w]=(lo[w]+hi[w])/2+side*(width/2-inset)
            center[l]=lo[l]+f*length
            center[u]=ground+radius*1.02
            centers.append([round(x,6) for x in center])
    return centers


def source_group_reuse(doc, centers, cfg):
    names=cfg.get("reuse_groups",[])
    if not isinstance(names,list) or len(names)!=2 or len(set(names))!=2:
        raise ValueError("Grupo de aros e pneus deve ser identificado")
    nodes=doc.get("nodes",[])
    roots=[]
    for name in names:
        found=[i for i,n in enumerate(nodes) if n.get("name")==name]
        if len(found)!=1 or not nodes[found[0]].get("children"):
            raise ValueError("Grupo de roda original ausente ou ambíguo: "+name)
        roots.append(found[0])
    parents=[i for i,n in enumerate(nodes) if all(j in n.get("children",[]) for j in roots)]
    if len(parents)!=1:
        raise ValueError("Aros e pneus não compartilham o mesmo pai; revisão manual necessária")
    parent=nodes[parents[0]]
    # Nada é removido dos arrays; apenas reparentamos para quatro posições.
    parent["children"]=[i for i in parent["children"] if i not in roots]
    base_nodes=copy.deepcopy(nodes)
    def clone_tree(index):
        clone=copy.deepcopy(base_nodes[index])
        children=clone.get("children",[])
        clone["children"]=[clone_tree(child) for child in children]
        nodes.append(clone)
        return len(nodes)-1
    for i,center in enumerate(centers):
        group_ids=roots if i==0 else [clone_tree(index) for index in roots]
        # O aro original só tem a face externa em +x; para a roda esquerda
        # giramos todo o grupo 180° em y, preservando a montagem interna.
        side=-1 if i%2==0 else 1
        node={"name":f"ArtiSys_Wheel_{i+1:02d}_Reused",
              "translation":center,"children":group_ids,
              "extras":{"origin":"source_GLTF_original","curated":False,"part":"wheel"}}
        if side<0:
            node["rotation"]=[0,1,0,0]
        nodes.append(node)
        parent["children"].append(len(nodes)-1)
    return {"method":"REUSED_SOURCE_WHEELS","added_meshes":0,
            "wheel_assemblies":4,"source_groups":names}


def _append_geometry(doc, tail, cfg):
    if len(tail)<8:
        raise ValueError("GLB sem chunk BIN; não modificar")
    old_len,kind=struct.unpack_from("<I4s",tail,0)
    if kind!=b"BIN\0" or old_len!=len(tail)-8:
        raise ValueError("GLB com chunk BIN incompleto/múltiplo; intervenção manual")
    buffers=doc.get("buffers",[])
    # GLBs otimizados por Meshopt podem declarar um segundo buffer virtual
    # de fallback. Ele não corresponde a um segundo chunk BIN físico.
    fallback_only=all(b.get("extensions",{}).get("EXT_meshopt_compression",{}).get("fallback") is True
                      and not b.get("uri") for b in buffers[1:])
    if not buffers or buffers[0].get("uri") or not fallback_only:
        raise ValueError("Buffers externos ou múltiplos sem fallback Meshopt não suportados")
    original=tail[8:]
    packed=bytearray(original)
    radius=float(cfg["wheel_radius"])
    tire_width=min(.215,radius*.68)
    segments=40
    meshes=[]
    def geometry_pieces():
        # Uma roda em plano yz; eixo do veículo coincide com x.
        # Dois lados visíveis, pneu de perfil arredondado, aro em liga,
        # miolo e cinco raios por lado. Peça técnica aproximada.
        tire=[]
        ring=[(-tire_width*.52,.71),(-tire_width*.52,.90),
              (-tire_width*.36,1), (tire_width*.36,1),
              (tire_width*.52,.90),(tire_width*.52,.71)]
        for ra,rb in zip(ring,ring[1:]):
            positions=[];indices=[]
            for i in range(segments+1):
                t=2*math.pi*i/segments
                for axial,mul in (ra,rb):
                    positions.append((axial,radius*mul*math.sin(t),radius*mul*math.cos(t)))
            for i in range(segments):
                j=2*i
                indices.extend([j,j+1,j+2,j+1,j+3,j+2])
            tire.append((positions,indices,0))
        metal=[]
        for side in (-1,1):
            axial=side*(tire_width*.53)
            for ra,rb in ((.68,.44),(.25,.13)):
                positions=[];indices=[]
                for i in range(segments+1):
                    t=2*math.pi*i/segments
                    for mul in (ra,rb):
                        positions.append((axial,radius*mul*math.sin(t),radius*mul*math.cos(t)))
                for i in range(segments):
                    j=2*i
                    indices.extend([j,j+1,j+2,j+1,j+3,j+2])
                metal.append((positions,indices,1))
            for spoke in range(5):
                mid=2*math.pi*spoke/5
                inner,outer=.20,.62
                half=.105
                positions=[
                  (axial, radius*inner*math.sin(mid-half),radius*inner*math.cos(mid-half)),
                  (axial, radius*outer*math.sin(mid-half*.42),radius*outer*math.cos(mid-half*.42)),
                  (axial, radius*outer*math.sin(mid+half*.42),radius*outer*math.cos(mid+half*.42)),
                  (axial, radius*inner*math.sin(mid+half),radius*inner*math.cos(mid+half))
                ]
                metal.append((positions,[0,1,2,0,2,3],1))
        return tire+metal

    # Unificar vários componentes por material em uma primitive indexada.
    by_mat={0:{"vertices":[],"indices":[]},1:{"vertices":[],"indices":[]}}
    for vertices,indices,material in geometry_pieces():
        target=by_mat[material]
        start=len(target["vertices"])
        target["vertices"].extend(vertices)
        target["indices"].extend(start+i for i in indices)
    def add_view(data,target=34962):
        while len(packed)%4:packed.append(0)
        offset=len(packed)
        packed.extend(data)
        index=len(doc.setdefault("bufferViews",[]))
        doc["bufferViews"].append({"buffer":0,"byteOffset":offset,"byteLength":len(data),"target":target})
        return index
    def add_accessor(view,component_type,count,type_,minmax=None):
        index=len(doc.setdefault("accessors",[]))
        acc={"bufferView":view,"componentType":component_type,"count":count,"type":type_}
        if minmax:acc.update(minmax)
        doc["accessors"].append(acc)
        return index
    primitives=[]
    for material,src in by_mat.items():
        pts=src["vertices"]
        idx=src["indices"]
        if not pts or len(pts)>65535 or len(idx)>65535:
            raise ValueError("Roda com malha de tamanho inesperado")
        # Normais radiais para o pneu, laterais para o aro. doubleSided
        # garante a renderização da face interna de ambos os lados.
        normals=[]
        for x,y,z in pts:
            length=math.hypot(y,z)
            if material==1:
                nx=1 if x>=0 else -1
                normals.append((nx,0,0))
            else:
                normals.append((0,y/length if length else 0,z/length if length else 1))
        positions=b"".join(struct.pack("<fff",*p) for p in pts)
        directions=b"".join(struct.pack("<fff",*n) for n in normals)
        indices=b"".join(struct.pack("<H",i) for i in idx)
        pidx=add_accessor(add_view(positions),5126,len(pts),"VEC3",{
            "min":[min(p[k] for p in pts) for k in range(3)],
            "max":[max(p[k] for p in pts) for k in range(3)]})
        nidx=add_accessor(add_view(directions),5126,len(normals),"VEC3")
        iidx=add_accessor(add_view(indices,target=34963),5123,len(idx),"SCALAR")
        primitives.append({"attributes":{"POSITION":pidx,"NORMAL":nidx},
                           "indices":iidx,"material":material,"mode":4})
    # Índices de material devem ser absolutos, não relativos.
    materials=doc.setdefault("materials",[])
    start=len(materials)
    materials.extend([
      {"name":"ArtiSys_Procedural_Tire_Rubber","doubleSided":True,
       "pbrMetallicRoughness":{"baseColorFactor":[.048,.056,.061,1],
                              "roughnessFactor":.96,"metallicFactor":0}},
      {"name":"ArtiSys_Procedural_Rim_Aluminium","doubleSided":True,
       "pbrMetallicRoughness":{"baseColorFactor":[.57,.62,.65,1],
                              "roughnessFactor":.34,"metallicFactor":.58}}
    ])
    for primitive in primitives: primitive["material"]+=start
    wheel_mesh_index=len(doc.setdefault("meshes",[]))
    doc["meshes"].append({"name":"ArtiSys_5Spoke_Wheel_Approximate","primitives":primitives})
    doc["buffers"][0]["byteLength"]=len(packed)
    while len(packed)%4: packed.append(0)
    # glTF permite BIN chunk até 3 bytes maior que buffer.byteLength.
    doc["buffers"][0]["byteLength"]=len(packed)
    return wheel_mesh_index,struct.pack("<I4s",len(packed),b"BIN\0")+bytes(packed),len(original)


def repair_variant(source,target,bounds,cfg,asset_id):
    if asset_id not in {"ford-ka","vw-fox","fiat-palio"}:
        raise ValueError("Reparo permitido só para os três modelos auditados")
    src=Path(source);dst=Path(target)
    if src.resolve()==dst.resolve():raise ValueError("Não sobrescrever GLB original")
    if not src.is_file() or src.stat().st_size>MAX_FILE_BYTES:raise ValueError("GLB de origem não encontrado ou excessivo")
    doc,tail=read_glb(src)
    original_tail=tail
    original_meshes=len(doc.get("meshes",[]))
    original_nodes=len(doc.get("nodes",[]))
    centers=plan_wheels(bounds,cfg)
    if cfg.get("reuse_groups"):
        r=source_group_reuse(doc,centers,cfg)
        status="REUSED_ORIGINAL_WHEELS_PENDING_VISUAL_REVIEW"
    else:
        wheel_index,tail,bin_prefix_length=_append_geometry(doc,tail,cfg)
        for i,center in enumerate(centers):
            doc.setdefault("nodes",[]).append({
                "name":f"ArtiSys_Wheel_{i+1:02d}_Procedural",
                "mesh":wheel_index,"translation":center,
                "extras":{"origin":"ArtiSys_procedural_approximation","curated":False,"part":"wheel"}
            })
            doc["scenes"][doc.get("scene",0)].setdefault("nodes",[]).append(len(doc["nodes"])-1)
        r={"method":"PROCEDURAL_APPROXIMATION","added_meshes":1,"wheel_assemblies":4,
           "appended_bytes":len(tail)-len(original_tail)}
        status="PROCEDURAL_APPROXIMATION_PENDING_VISUAL_REVIEW"
    if len(doc.get("meshes",[]))!=original_meshes+r["added_meshes"] or len(doc.get("nodes",[]))<=original_nodes:
        raise AssertionError("Reparo alterou indevidamente quantidade de malhas")
    dst.parent.mkdir(parents=True,exist_ok=True)
    write_glb(dst,doc,tail)
    validated,newtail=read_glb(dst)
    if (len(validated.get("nodes",[]))!=len(doc["nodes"])
        or len(validated.get("meshes",[]))!=len(doc["meshes"])
        or newtail[8:8+len(original_tail)-8]!=original_tail[8:]):
        raise AssertionError("Geometria original corrompida no arquivo derivado")
    return {"asset_id":asset_id,"source":src.name,"output":dst.name,
      "sha256_source":hashlib.sha256(src.read_bytes()).hexdigest(),
      "sha256_repaired":hashlib.sha256(dst.read_bytes()).hexdigest(),
      "source_binary_prefix_preserved":True,
      "source_meshes":original_meshes,"repaired_meshes":len(validated.get("meshes",[])),
      "source_nodes":original_nodes,"repaired_nodes":len(validated.get("nodes",[])),
      "centers":centers,**r,
      "status":status,"approved_for_officina":False,
      "review_status":"PENDENTE_REVISAO_VISUAL"}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",type=Path,default=ROOT/"curadoria/reparo-rodas-config.json")
    parser.add_argument("--assets",type=Path,default=ROOT/"curadoria/restaurados")
    parser.add_argument("--output",type=Path,default=ROOT/"curadoria/rodas-reparadas")
    options=parser.parse_args()
    data=json.loads(options.config.read_text(encoding="utf-8"))
    catalog=json.loads((ROOT/"cars.json").read_text(encoding="utf-8"))
    original={m["id"]:m for m in catalog["cars"]}
    models=[]
    for car_id,cfg in data["cars"].items():
        source=options.assets/(car_id+"-restaurado.glb")
        target=options.output/(car_id+"-rodas.glb")
        result=repair_variant(source,target,cfg["bounds"],cfg,car_id)
        result["credit"]=original[car_id]["credit"]
        models.append(result)
        print("RODAS_OK",car_id,result["method"],result["centers"])
    (options.output/"manifest.json").write_text(json.dumps({
        "schema_version":1,"status":"EXPERIMENTAL_PENDENTE_REVISAO_VISUAL",
        "note":"Ka/Fox reutilizam as malhas originais; Palio recebe 4 rodas aproximadas não OEM.",
        "models":models},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":main()
