"""Auditoria GLB local sem bibliotecas pagas. Não aprova modelos automaticamente."""
import argparse
import hashlib
import json
import re
import struct
import unicodedata
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENTS = {
 "portas": r"\b(?:door|doors|porta|portas)\b",
 "capo": r"\b(?:bonnet|hood|capo)\b",
 "porta_malas": r"\b(?:trunk|boot|porta malas)\b",
 "parachoques": r"\b(?:bumper|parachoque|para choque)\b",
 "rodas": r"\b(?:wheel|wheels|roda|rodas|pneu|pneus|tire|tyre)\b",
 "farois": r"\b(?:headlight|taillight|farol|farois|lanterna|lights?)\b",
 "vidros": r"\b(?:windshield|windscreen|window|vidro|parabrisa)\b",
 "motor": r"\b(?:engine|motor|radiator)\b",
 "suspensao": r"\b(?:suspension|shock|amortecedor)\b",
 "interior": r"\b(?:interior|cockpit|dashboard|volante|steering|seat|banco)\b",
}
ACCESSORIES = re.compile(
 r"\b(?:wheel pack|wheels|steering wheel|motor block|2jz|engine only|rim pack|"
 r"race steering|parts pack|excavator|tram|forklift|helicopter)\b", re.I)


def normalized(s):
 s=unicodedata.normalize("NFKD",str(s or "")).encode("ascii","ignore").decode().lower()
 return re.sub(r"[^a-z0-9]+"," ",s).strip()


def inspect_glb(path):
 result={"valid":False,"error":None,"bytes":0,"node_count":0,"mesh_count":0,
         "component_groups":[],"extensions_used":[]}
 if not path.is_file():
  result["error"]="ARQUIVO_AUSENTE"
  return result
 result["bytes"]=path.stat().st_size
 try:
  with path.open("rb") as f:
   header=f.read(12)
   if len(header)!=12: raise ValueError("Cabeçalho incompleto")
   magic,version,length=struct.unpack("<4sII",header)
   if (magic,version,length)!=(b"glTF",2,result["bytes"]):
    raise ValueError("Cabeçalho ou tamanho GLB inválido")
   chunk=f.read(8)
   if len(chunk)!=8: raise ValueError("JSON chunk ausente")
   n,t=struct.unpack("<I4s",chunk)
   if t!=b"JSON" or n>20_000_000 or 20+n>length: raise ValueError("JSON chunk inválido")
   doc=json.loads(f.read(n))
  if doc.get("asset",{}).get("version")!="2.0": raise ValueError("Asset glTF 2.0 ausente")
  names=[normalized(x.get("name")) for x in doc.get("nodes",[]) if isinstance(x,dict)]
  groups=[k for k,pattern in COMPONENTS.items() if any(re.search(pattern,name) for name in names)]
  result.update(valid=True,node_count=len(doc.get("nodes",[])),
                mesh_count=len(doc.get("meshes",[])),component_groups=groups,
                extensions_used=doc.get("extensionsUsed",[]))
 except (OSError,ValueError,UnicodeDecodeError,json.JSONDecodeError,struct.error) as error:
  result["error"]=str(error)[:180]
 return result


def tier(groups):
 critical={"portas","capo","porta_malas","parachoques","rodas","farois"}
 count=len(set(groups)&critical)
 return "POTENCIAL_ALTO" if count>=4 else "POTENCIAL_MEDIO" if count>=2 else "POTENCIAL_NAO_DEMONSTRADO"


def audit_catalog(catalog,assets_root,checksum=False):
 cars=catalog.get("cars",[])
 names=Counter((normalized(c.get("brand")),normalized(c.get("model"))) for c in cars)
 sources=Counter(c.get("credit",{}).get("sourceUrl","") for c in cars if c.get("credit",{}).get("sourceUrl"))
 entries=[]; stats=Counter()
 for car in cars:
  filename=str(car.get("file") or "")
  safe=bool(re.fullmatch(r"[a-zA-Z0-9_.-]+\.glb",filename))
  info=inspect_glb(assets_root/(filename if safe else "_INVALID_FILENAME_"))
  issues=[]
  if not safe: issues.append("CAMINHO_INVALIDO")
  if not info["valid"]:
   issues.append("ARQUIVO_AUSENTE" if info["error"]=="ARQUIVO_AUSENTE" else "GLB_INVALIDO")
  if info["valid"] and car.get("bytes") and car["bytes"]!=info["bytes"]: issues.append("TAMANHO_DIVERGENTE")
  if not car.get("model"): issues.append("MODELO_VAZIO")
  years=car.get("years") or []
  if years==[0,0]: issues.append("ANO_DESCONHECIDO")
  if years==[2000,2024]: issues.append("ANOS_GENERICOS")
  if names[(normalized(car.get("brand")),normalized(car.get("model")))]>1: issues.append("NOME_REPETIDO")
  if car.get("credit",{}).get("sourceUrl") and sources[car["credit"]["sourceUrl"]]>1:
   issues.append("FONTE_REPETIDA")
  title=car.get("credit",{}).get("sourceName","")
  if ACCESSORIES.search(str(car.get("model",""))+" "+title):
   issues.append("POSSIVEL_ACESSORIO_OU_OUTRO_TIPO")
  if not all(car.get("credit",{}).get(k) for k in ("author","license","sourceUrl")):
   issues.append("CREDITOS_INCOMPLETOS")
  if "ShareAlike" in str(car.get("credit",{}).get("license","")):
   issues.append("LICENCA_SHAREALIKE")
  origin=normalized(title).split()
  words=[w for w in normalized(car.get("model")).split() if len(w)>=3 and w not in ("low","poly","car")]
  if words and origin and not any(w in origin for w in words):
   issues.append("MODELO_DIFERE_TITULO_ORIGEM")
  sha=None
  if checksum and info["valid"]:
   h=hashlib.sha256()
   with (assets_root/filename).open("rb") as stream:
    for chunk in iter(lambda:stream.read(1024*1024),b""): h.update(chunk)
   sha=h.hexdigest()
  level=tier(info["component_groups"])
  entries.append({"id":car.get("id"),"brand":car.get("brand"),"model":car.get("model"),
   "category":car.get("category"),"file":filename,"years":years,
   "source_url":car.get("credit",{}).get("sourceUrl"),
   "license":car.get("credit",{}).get("license"),
   "author":car.get("credit",{}).get("author"),
   "technical":info,"sha256":sha,"component_potential":level,
   "issues":sorted(set(issues)),"review_status":"PENDENTE_REVISAO_VISUAL",
   "approved_for_officina":False})
  stats["models"]+=1
  stats["valid_glb" if info["valid"] else "invalid_glb"]+=1
  stats[level]+=1
  for issue in set(issues): stats["issue_"+issue]+=1
 return {"schema_version":1,"status":"AUDITORIA_AUTOMATICA_SEM_APROVACAO",
         "total_expected":len(cars),"summary":dict(sorted(stats.items())),"models":entries}


def write_report(data,output):
 output.mkdir(parents=True,exist_ok=True)
 (output/"auditoria.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 stats=data["summary"]
 issues=sorted(((k[6:],v) for k,v in stats.items() if k.startswith("issue_")),key=lambda x:-x[1])
 lines=["# Curadoria técnica dos modelos 3D","",
  "**Auditoria automática; não é aprovação visual/comercial.**",
  "Todos os arquivos seguem pendentes de revisão visual.", "",
  "## Indicadores","",
  "- Modelos: "+str(stats["models"]),
  "- GLB válidos: "+str(stats.get("valid_glb",0)),
  "- GLB ausentes ou inválidos: "+str(stats.get("invalid_glb",0)),
  "- Potencial alto: "+str(stats.get("POTENCIAL_ALTO",0)),
  "- Potencial médio: "+str(stats.get("POTENCIAL_MEDIO",0)),
  "- Potencial não demonstrado: "+str(stats.get("POTENCIAL_NAO_DEMONSTRADO",0)),
  "", "## Alertas", "", "| Tipo | Ocorrências |","|---|---:|"]
 lines += ["| "+k+" | "+str(v)+" |" for k,v in issues]
 lines += ["","## Primeiros 100 registros com alertas","",
  "| ID | Nome | Alertas | Nós | Grupos |","|---|---|---|---:|---|"]
 for m in [m for m in data["models"] if m["issues"]][:100]:
  lines.append("| "+" | ".join([str(m["id"]),str(m["brand"])+" "+str(m["model"]),
    ", ".join(m["issues"]),str(m["technical"]["node_count"]),
    ", ".join(m["technical"]["component_groups"])])+" |")
 lines+=["","## Etapa visual obrigatória","",
  "Renderizar imagens reais de frente, lados e traseira (e interior quando houver);",
  "validar modelo e versão, fidelidade, texturas e licença; conferir malhas clicáveis",
  "e desempenho antes de aprovar a inclusão no gerenciador do Oficina.",""]
 (output/"RELATORIO.md").write_text("\n".join(lines),encoding="utf-8")


def main():
 p=argparse.ArgumentParser()
 p.add_argument("--catalog",type=Path,default=ROOT/"cars.json")
 p.add_argument("--assets",type=Path,default=ROOT/"assets")
 p.add_argument("--out",type=Path,default=ROOT/"curadoria"/"relatorios")
 p.add_argument("--checksum",action="store_true")
 a=p.parse_args()
 data=audit_catalog(json.loads(a.catalog.read_text(encoding="utf-8")),a.assets,checksum=a.checksum)
 write_report(data,a.out)
 print(json.dumps(data["summary"],ensure_ascii=False,indent=2))
 if data["summary"].get("invalid_glb"): raise SystemExit("Falha: GLBs inválidos ou ausentes")


if __name__=="__main__":
 main()
