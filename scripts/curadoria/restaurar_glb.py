"""Restauração visual não destrutiva de GLB 2.0: corrige materiais, preserva malhas.

A saída é experimental, separada do acervo canônico. Não reconstrói geometria
nem certifica geração/modelo. Sem dependências Python externas ou APIs pagas.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import struct
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
VALID_ID=re.compile(r"[a-z0-9][a-z0-9-]*\Z")
HEX=re.compile(r"#[0-9a-fA-F]{6}\Z")


def parse_hex(value):
    if not isinstance(value,str) or HEX.fullmatch(value) is None:
        raise ValueError("Use somente cor hexadecimal #RRGGBB.")
    return [int(value[i:i+2],16)/255 for i in (1,3,5)]+[1]


def read_glb(path):
    b=Path(path).read_bytes()
    if len(b)<20: raise ValueError("GLB incompleto")
    magic,version,length=struct.unpack_from("<4sII",b,0)
    if magic!=b"glTF" or version!=2 or length!=len(b):
        raise ValueError("Cabeçalho ou comprimento GLB inválido")
    n,kind=struct.unpack_from("<I4s",b,12)
    if kind!=b"JSON" or n>30_000_000 or n+20>len(b) or n%4:
        raise ValueError("JSON chunk ausente/inválido")
    doc=json.loads(b[20:20+n].decode("utf-8").rstrip(" \0"))
    if doc.get("asset",{}).get("version")!="2.0":
        raise ValueError("Arquivo sem especificação glTF 2.0")
    tail=b[20+n:]
    # Opcional BIN e extensões intactas, incluindo dados de compressão Meshopt.
    return doc,tail


def parse_glb(path):
    return read_glb(path)[0]


def _is_neon_green(mat):
    rgb=mat.get("pbrMetallicRoughness",{}).get("baseColorFactor")
    return isinstance(rgb,list) and len(rgb)>=3 and rgb[0]<0.4 and rgb[1]>0.75 and rgb[2]<0.55


def _set_values(material,rule,paint):
    target=copy.deepcopy(material)
    pbr=target.setdefault("pbrMetallicRoughness",{})
    color=paint if rule.get("role")=="paint" else rule.get("color")
    if color is not None:pbr["baseColorFactor"]=parse_hex(color)
    if "roughness" in rule:
        rough=float(rule["roughness"])
        if not 0<=rough<=1:raise ValueError("Rugosidade fora do intervalo")
        pbr["roughnessFactor"]=rough
    if "metallic" in rule:
        metallic=float(rule["metallic"])
        if not 0<=metallic<=1:raise ValueError("Metallic fora do intervalo")
        pbr["metallicFactor"]=metallic
    return target


def apply_profile(doc,profile,paint_override=None):
    """Aplica somente os materiais declarados; nunca muda geometria dos meshes."""
    if not isinstance(profile,dict):raise ValueError("Perfil inválido")
    paint=paint_override or profile.get("paint")
    if paint:parse_hex(paint)
    changes=[]
    mats=doc.get("materials",[])
    for i,material in enumerate(mats):
        for rule in profile.get("rules",[]):
            if not re.fullmatch(rule["match"],str(material.get("name","")),flags=re.I):
                continue
            if rule.get("when")=="NEON_GREEN" and not _is_neon_green(material):
                continue
            new=_set_values(material,rule,paint)
            if new!=material:
                mats[i]=new
                changes.append({"type":"material","name":str(material.get("name","")),"index":i})
            break
        else:
            if "fallback" in profile:
                new=_set_values(material,profile["fallback"],paint)
                if new!=material:
                    mats[i]=new
                    changes.append({"type":"fallback","name":str(material.get("name","")),"index":i})
    groups=profile.get("mesh_groups",[])
    if groups:
        if not mats or not paint:raise ValueError("Material ou cor não disponível")
        paint_targets=set()
        nodes=doc.get("nodes",[])
        for node in nodes:
            if node.get("name") in groups:
                for ci in node.get("children",[]):
                    if 0<=ci<len(nodes) and "mesh" in nodes[ci]:
                        paint_targets.add(nodes[ci]["mesh"])
        if not paint_targets:
            raise ValueError("Nenhum grupo de carroceria encontrado no GLB")
        usages=Counter(n["mesh"] for n in nodes if "mesh" in n)
        for mesh_index in sorted(paint_targets):
            if mesh_index<0 or mesh_index>=len(doc.get("meshes",[])) or usages[mesh_index]!=1:
                raise ValueError("Mesh com instâncias incompatíveis, intervenção manual necessária")
            mesh=doc["meshes"][mesh_index]
            for primitive in mesh.get("primitives",[]):
                original_material=primitive.get("material",0)
                if original_material<0 or original_material>=len(mats):
                    raise ValueError("Material de malha ausente")
                new_mat=_set_values(mats[original_material],{
                    "role":"paint","roughness":0.47,"metallic":0.15},paint)
                new_mat["name"]="ArtiSys_Restauro_Pintura_"+str(mesh_index)
                mats.append(new_mat)
                primitive["material"]=len(mats)-1
                if profile.get("disable_vertex_color_for_mesh_groups"):
                    # Remove apenas o atributo de cor deste primitive; posições,
                    # índices, normais, UVs, dados binários e demais nós permanecem.
                    primitive.get("attributes",{}).pop("COLOR_0",None)
                changes.append({"type":"mesh_paint","mesh":mesh_index,"material":len(mats)-1})
    return changes


def write_glb(path,doc,tail):
    js=json.dumps(doc,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    js+=b" " * (-len(js)%4)
    new_header=struct.pack("<4sII",b"glTF",2,20+len(js)+len(tail))
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_bytes(new_header+struct.pack("<I4s",len(js),b"JSON")+js+tail)


def digest(path):
    sha=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""):sha.update(chunk)
    return sha.hexdigest()


def create_variant(source,target,profile,asset_id,paint_override=None):
    if VALID_ID.fullmatch(asset_id) is None:raise ValueError("ID de modelo inseguro")
    src=Path(source)
    dst=Path(target)
    if src.resolve()==dst.resolve():raise ValueError("É proibido sobrescrever o GLB original")
    doc,tail=read_glb(src)
    old_meshes=len(doc.get("meshes",[]))
    old_nodes=copy.deepcopy(doc.get("nodes",[]))
    old_bin_hash=hashlib.sha256(tail).hexdigest()
    changes=apply_profile(doc,profile,paint_override)
    if not changes:raise ValueError("Perfil não alterou materiais: intervenção manual necessária")
    if len(doc.get("meshes",[]))!=old_meshes or doc.get("nodes",[])!=old_nodes:
        raise ValueError("Restauro não pode excluir/reconstruir malhas ou nós")
    if hashlib.sha256(tail).hexdigest()!=old_bin_hash:raise AssertionError("Chunk BIN alterado")
    write_glb(dst,doc,tail)
    outdoc,outtail=read_glb(dst)
    if len(outdoc.get("meshes",[]))!=old_meshes or len(outdoc.get("nodes",[]))!=len(old_nodes):
        raise AssertionError("A estrutura do GLB restaurado divergiu")
    if outtail!=tail:raise AssertionError("Geometria BIN alterada no salvamento")
    return {"asset_id":asset_id,"source":src.name,"output":dst.name,
            "sha256_original":digest(src),"sha256_restaurado":digest(dst),
            "mesh_count_before":old_meshes,"mesh_count_after":len(outdoc.get("meshes",[])),
            "node_count":len(old_nodes),"changes":changes,
            "review_status":"PENDENTE_REVISAO_VISUAL",
            "geometry_status":"ORIGINAL_PRESERVADA",
            "license_status":"CREDITOS_ORIGINAIS_MANTIDOS"}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--ids",default="ford-ka,vw-fox,chevrolet-corsa,fiat-palio,fiat-uno")
    parser.add_argument("--paint",help="Cor opcional #RRGGBB para pintura")
    parser.add_argument("--assets",type=Path,default=ROOT/"assets")
    parser.add_argument("--output",type=Path,default=ROOT/"curadoria/restaurados")
    a=parser.parse_args()
    if a.paint:parse_hex(a.paint)
    config=json.loads((ROOT/"curadoria/restauro-perfis.json").read_text(encoding="utf-8"))
    catalog=json.loads((ROOT/"cars.json").read_text(encoding="utf-8"))
    entries={car["id"]:car for car in catalog["cars"]}
    outputs=[]
    for id in [x.strip() for x in a.ids.split(",")]:
        if VALID_ID.fullmatch(id) is None or id not in config["profiles"] or id not in entries:
            raise ValueError("ID desconhecido ou não permitido: "+id)
        car=entries[id]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.glb",car["file"]):
            raise ValueError("Arquivo de modelo inseguro")
        if not (a.assets/car["file"]).is_file():
            raise ValueError("Arquivo não encontrado no acervo original: "+car["file"])
        output=a.output/(id+"-restaurado.glb")
        row=create_variant(a.assets/car["file"],output,config["profiles"][id],id,a.paint)
        row["credit"]=car["credit"]
        outputs.append(row)
        print("RESTOUROU",id,"mudanças",len(row["changes"]))
    a.output.mkdir(parents=True,exist_ok=True)
    (a.output/"manifest.json").write_text(json.dumps({
        "schema_version":1,"models":outputs,"status":"EXPERIMENTAL_NAO_HOMOLOGADO",
        "notes":"GLBs derivados com restauração de materiais; geometria preservada, sem aprovação visual."
    },ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
