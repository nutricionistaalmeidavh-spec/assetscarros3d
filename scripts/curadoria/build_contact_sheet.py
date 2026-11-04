"""Mosaico de revisão criado exclusivamente das miniaturas renderizadas dos GLBs."""
from pathlib import Path
import json
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[2]
FOLDER=ROOT/"curadoria"/"miniaturas"
data=json.loads((FOLDER/"index.json").read_text(encoding="utf-8"))
models=[model for model in data["models"] if model["status"]=="GERADO_PENDENTE_REVISAO"]
if not models:
 raise SystemExit("Nenhuma miniatura válida para montar mosaico")
w,h=400,325
columns=3
rows=(len(models)+columns-1)//columns
sheet=Image.new("RGB",(w*columns,h*rows),(239,244,240))
draw=ImageDraw.Draw(sheet)
try:
 font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",18)
 small=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",13)
except OSError:
 font=ImageFont.load_default()
 small=font
for idx,entry in enumerate(models):
 x=(idx%columns)*w;y=(idx//columns)*h
 img=FOLDER/(entry["asset_id"]+"-frente.png")
 with Image.open(img) as photo:
  photo=photo.convert("RGB")
  photo.thumbnail((w-16,235))
  px=x+(w-photo.width)//2;py=y+5+(235-photo.height)//2
  sheet.paste(photo,(px,py))
 draw.text((x+12,y+244),str(entry["rank"])+". "+entry["brand"]+" "+entry["model"],font=font,fill=(25,52,41))
 draw.text((x+12,y+275),"GLB real / Revisão visual pendente",font=small,fill=(85,112,99))
 draw.rectangle((x,y,x+w-1,y+h-1),outline=(210,222,214),width=1)
output=FOLDER/"MOSAICO_PRIMEIROS_MODELOS.jpg"
sheet.save(output,quality=88,optimize=True)
print("Mosaico:",output,len(models),"modelos")
