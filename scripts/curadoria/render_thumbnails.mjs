// Geração de miniaturas REAIS a partir dos GLBs locais (Three.js + Meshopt + Chromium).
// Nenhuma fotografia stock; não aprova correspondência do modelo sem revisão humana.
import {createServer} from 'node:http';
import {createReadStream,existsSync,mkdirSync,writeFileSync} from 'node:fs';
import {readFile} from 'node:fs/promises';
import {resolve,join,extname,dirname,sep} from 'node:path';
import {chromium} from 'playwright';
import {selectThumbnailCandidates,renderFiles} from './thumbnail-plan.mjs';

const root=resolve(process.cwd());
const priority=JSON.parse(await readFile(join(root,'curadoria/prioridades-frota-2026.json'),'utf8'));
const manifest=JSON.parse(await readFile(join(root,'cars.json'),'utf8'));
const count=Number(process.env.THUMBNAIL_LIMIT||'12');
if(!Number.isInteger(count)||count<1||count>50)throw Error('THUMBNAIL_LIMIT deve estar entre 1 e 50');
const ids=(process.env.RENDER_IDS||'').split(',').map(x=>x.trim()).filter(Boolean);
const selected=ids.length
 ? selectThumbnailCandidates(priority.models,{max:50}).filter(car=>ids.includes(car.asset_id))
 : selectThumbnailCandidates(priority.models,{max:count});
if(ids.length && selected.length!==ids.length)throw new Error('Seleção de renderizações incompleta');
if(!selected.length)throw Error('Nenhum modelo apto à geração da miniatura');
const catalogIds=new Map(manifest.cars.map(x=>[x.id,x]));
for(const car of selected){
 const original=catalogIds.get(car.asset_id);
 if(!original||original.file!==car.asset_file)throw Error('GLB divergente do catálogo: '+car.asset_id);
 if(!existsSync(join(root,'assets',car.asset_file)))throw Error('GLB ausente: '+car.asset_file);
}
const paths=new Set(selected.map(x=>'/assets/'+x.asset_file));
const html="<!doctype html><html lang=\"pt-BR\"><head><meta charset=\"UTF-8\"/><style>\nhtml,body{margin:0;padding:0;overflow:hidden;background:#f1f5f3;}\n#viewer{width:700px;height:480px;position:relative;overflow:hidden;background:radial-gradient(ellipse at 50% 65%,#f7f9f8 0%,#d7e0dc 90%);}\ncanvas{display:block}\n</style>\n<script type=\"importmap\">{\"imports\":{\"three\":\"/node_modules/three/build/three.module.js\",\"three/addons/\":\"/node_modules/three/examples/jsm/\"}}</script></head><body><div id=\"viewer\"></div><script type=\"module\">\nimport * as THREE from 'three';\nimport {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';\nimport {DRACOLoader} from 'three/addons/loaders/DRACOLoader.js';\nimport {MeshoptDecoder} from '/node_modules/meshoptimizer/meshopt_decoder.mjs';\n\nconst scene=new THREE.Scene();\nconst renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});\nrenderer.setPixelRatio(1);\nrenderer.setSize(700,480);\nrenderer.outputColorSpace=THREE.SRGBColorSpace;\nrenderer.toneMapping=THREE.ACESFilmicToneMapping;\nrenderer.toneMappingExposure=0.82;\ndocument.querySelector('#viewer').appendChild(renderer.domElement);\nscene.add(new THREE.HemisphereLight(0xffffff,0x9daaa1,1.25));\nconst sun=new THREE.DirectionalLight(0xffffff,1.85);sun.position.set(5,10,7);scene.add(sun);\nconst fill=new THREE.DirectionalLight(0xe6f9ff,0.6);fill.position.set(-6,4,-3);scene.add(fill);\nconst camera=new THREE.PerspectiveCamera(36,700/480,0.001,100000);\nconst loader=new GLTFLoader();\nloader.setMeshoptDecoder(MeshoptDecoder);\nconst draco=new DRACOLoader();\ndraco.setDecoderPath('/node_modules/three/examples/jsm/libs/draco/gltf/');\nloader.setDRACOLoader(draco);\nlet current=null;\nwindow.drawModel=async function(filename,view){\n if(!/^[a-z0-9][a-z0-9-]*\\.glb$/i.test(filename))throw Error('Arquivo GLB inválido');\n if(current){scene.remove(current);current=null;}\n const gltf=await loader.loadAsync('/assets/'+filename);\n current=gltf.scene;scene.add(current);\n const box=new THREE.Box3().setFromObject(current);\n if(box.isEmpty())throw Error('Cena GLB vazia');\n const size=box.getSize(new THREE.Vector3());\n const center=box.getCenter(new THREE.Vector3());\n if(!Number.isFinite(size.length())||size.length()<0.00001)throw Error('Geometria sem dimensão');\n const radius=Math.max(size.length()*0.5,0.01);\n const direction=view==='traseira'?new THREE.Vector3(-1.45,0.68,-1.55):new THREE.Vector3(1.45,0.68,1.55);\n direction.normalize();\n const distance=radius/Math.sin(THREE.MathUtils.degToRad(camera.fov/2))*1.24;\n camera.position.copy(center).addScaledVector(direction,distance);\n camera.near=Math.max(0.001,distance-radius*3);\n camera.far=Math.max(100, distance+radius*10);\n camera.updateProjectionMatrix();\n camera.lookAt(center);\n renderer.render(scene,camera);\n const rendered=renderer.info.render;\n if(rendered.triangles<1)throw Error('Nenhum triângulo renderizado');\n return {nodes:gltf.scene.children.length,triangles:rendered.triangles,width:size.x,height:size.y,depth:size.z};\n};\nwindow.ready=true;\n</script></body></html>";
const server=createServer((req,res)=>{
 try{
  const pathname=new URL(req.url,'http://127.0.0.1').pathname;
  if(pathname==='/viewer'){res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(html);return;}
  const deps=pathname.startsWith('/node_modules/three/')||pathname.startsWith('/node_modules/meshoptimizer/');
  if(!deps&&!paths.has(pathname)){res.writeHead(404);res.end('Não encontrado');return;}
  const filepath=resolve(root,'.'+pathname);
  const base=deps?resolve(root,'node_modules'):resolve(root,'assets');
  if(!filepath.startsWith(base+sep)||!existsSync(filepath)){res.writeHead(404);res.end('Não encontrado');return;}
  const mime={'.js':'application/javascript','.mjs':'application/javascript','.wasm':'application/wasm','.glb':'model/gltf-binary','.json':'application/json'}[extname(filepath)]||'application/octet-stream';
  res.writeHead(200,{'Content-Type':mime,'Cache-Control':'no-store'});
  createReadStream(filepath).pipe(res);
 }catch(error){res.writeHead(500);res.end('Falha de leitura local');}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const port=server.address().port;
const outputDir=process.env.RENDER_OUTPUT_DIR==='rodas-reparadas'?'miniaturas-rodas-reparadas':process.env.RENDER_OUTPUT_DIR==='restauradas'?'miniaturas-restauradas':'miniaturas';
const outdir=join(root,'curadoria',outputDir);
mkdirSync(outdir,{recursive:true});
const records=[];
let browser=null;
try{
 browser=await chromium.launch({headless:true,args:[
  '--use-gl=angle','--use-angle=swiftshader','--enable-webgl','--enable-unsafe-swiftshader',
  '--disable-gpu-sandbox','--disable-dev-shm-usage'
 ]});
 const page=await browser.newPage({viewport:{width:700,height:480},deviceScaleFactor:1});
 const errors=[];
 page.on('pageerror',err=>{const msg=String(err?.message||err);errors.push(msg);console.error('PAGE_ERROR',msg);});
 page.on('response',response=>{if(response.status()>=400)console.error('HTTP_ERROR',response.status(),response.url());});
 page.on('console',msg=>{if(msg.type()==='error')console.error('BROWSER_CONSOLE_ERROR',msg.text());});
 await page.goto('http://127.0.0.1:'+port+'/viewer',{waitUntil:'load'});
 await page.waitForFunction(()=>window.ready===true,{timeout:30000});
 for(const car of selected){
  const files=renderFiles(car);
  const output={rank:car.rank,brand:car.brand,model:car.model,asset_id:car.asset_id,
   asset_file:car.asset_file,source_title:car.source_title,source_url:car.source_url,
   author:catalogIds.get(car.asset_id)?.credit?.author,license:car.license,
   status:'FALHOU_NA_RENDERIZACAO',review_status:'PENDENTE_REVISAO_VISUAL',views:[]};
  try{
   output.variant=process.env.RENDER_VARIANT||'ORIGINAL';
   for(let i=0;i<2;i++){
    const view=i===0?'frente':'traseira';
    const stats=await page.evaluate(args=>window.drawModel(args.file,args.view),{file:car.asset_file,view});
    const filename=files[i].split('/').at(-1);
    const full=join(outdir,filename);
    await page.locator('#viewer').screenshot({path:full,animations:'disabled',timeout:45000});
    output.views.push({view,file:'curadoria/'+outputDir+'/'+filename,stats});
   }
   output.status='GERADO_PENDENTE_REVISAO';
   console.log('RENDER_OK',car.rank,car.brand,car.model);
  }catch(error){
   output.error=String(error.message||error).slice(0,700);
   console.error('RENDER_ERROR',car.asset_id,output.error);
  }
  records.push(output);
 }
 if(errors.length)console.error('PAGE_ERRORS',errors.slice(0,20));
}finally{
 await browser?.close();
 await new Promise(resolve=>server.close(resolve));
}
const summary={selected:selected.length,generated:records.filter(x=>x.status==='GERADO_PENDENTE_REVISAO').length,
 failed:records.filter(x=>x.status!=='GERADO_PENDENTE_REVISAO').length};
writeFileSync(join(outdir,'index.json'),JSON.stringify({
 schema_version:1,generator:'GLB real / Three.js + Meshopt / render offline', variant:process.env.RENDER_VARIANT||'ORIGINAL',
 note:'Imagens reais dos GLB sem aprovação visual; não usar para associar geração/versão automaticamente.',
 ...summary,models:records},null,2)+'\n');
const rows=['# Miniaturas 3D para revisão visual','',
 '**Geradas a partir de GLB reais. Não são miniaturas aprovadas.**','',
 'Resultado: '+summary.generated+' / '+summary.selected+' modelos; '+summary.failed+' falhas.',''];
for(const car of records){
 rows.push('## '+car.rank+'. '+car.brand+' '+car.model);
 rows.push('Fonte: '+(car.source_title||'—')+' · licença '+(car.license||'—')+' · '+(car.author||'—'));
 rows.push('Status: '+car.status+' · revisão humana pendente');
 if(car.error)rows.push('Erro: '+car.error);
 for(const view of car.views)rows.push('!['+car.brand+' '+car.model+' '+view.view+']('+view.file.split('/').at(-1)+')');
 rows.push('');
}
writeFileSync(join(outdir,'GALERIA.md'),rows.join('\n'));
console.log('SUMMARY',JSON.stringify(summary));
if(summary.failed>0)process.exitCode=1;
