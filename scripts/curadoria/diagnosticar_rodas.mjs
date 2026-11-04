// Inspeciona os GLBs reais sem alterar origem, para localizar pneus/aros e vãos de roda.
// Three.js + Playwright locais no runner. Relatório JSON + PNGs apenas para curadoria.
import {createServer} from 'node:http';
import {createReadStream,mkdirSync,writeFileSync,existsSync} from 'node:fs';
import {resolve,join,extname,sep} from 'node:path';
import {chromium} from 'playwright';

const root=resolve(process.cwd());
const ids=['ford-ka','vw-fox','fiat-palio'];
const html="<!DOCTYPE html><html><head><meta charset=\"utf-8\"><script type=\"importmap\">{\"imports\":{\"three\":\"/node_modules/three/build/three.module.js\",\"three/addons/\":\"/node_modules/three/examples/jsm/\"}}</script><style>html,body{margin:0;background:#e3eae6}canvas{display:block}</style></head><body><script type=\"module\">\nimport * as THREE from 'three';\nimport {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';\nimport {MeshoptDecoder} from '/node_modules/meshoptimizer/meshopt_decoder.mjs';\nconst renderer=new THREE.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});\nrenderer.setSize(900,630);renderer.setPixelRatio(1);renderer.setClearColor('#e3eae6');\nrenderer.outputColorSpace=THREE.SRGBColorSpace;\nrenderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=.82;\ndocument.body.appendChild(renderer.domElement);\nconst scene=new THREE.Scene();scene.add(new THREE.HemisphereLight(0xffffff,0x8c938d,1.4));\nconst key=new THREE.DirectionalLight(0xffffff,2.2);key.position.set(4,8,6);scene.add(key);\nconst cam=new THREE.PerspectiveCamera(36,900/630,.001,10000);\nconst loader=new GLTFLoader();loader.setMeshoptDecoder(MeshoptDecoder);await MeshoptDecoder.ready;\nlet object, center, dist;\nfunction bjson(box){\n return {min:box.min.toArray().map(n=>+n.toFixed(4)),max:box.max.toArray().map(n=>+n.toFixed(4)),\n center:box.getCenter(new THREE.Vector3()).toArray().map(n=>+n.toFixed(4)),\n size:box.getSize(new THREE.Vector3()).toArray().map(n=>+n.toFixed(4))};\n}\nwindow.setAngle=function(kind){\n const dir=kind==='lateral'?new THREE.Vector3(1,.18,.1):new THREE.Vector3(1.25,.67,1.5);\n cam.position.copy(center).addScaledVector(dir.normalize(),dist);\n cam.lookAt(center);renderer.render(scene,cam);\n}\nwindow.inspect=async function(id){\n if(object)scene.remove(object);\n const doc=await loader.loadAsync('/assets/'+id+'.glb');object=doc.scene;scene.add(object);\n object.updateWorldMatrix(true,true);\n const allBox=new THREE.Box3().setFromObject(object);\n center=allBox.getCenter(new THREE.Vector3());\n const size=allBox.getSize(new THREE.Vector3());\n dist=Math.max(size.length()*.5,.05)/Math.sin(THREE.MathUtils.degToRad(cam.fov/2))*1.25;\n cam.near=Math.max(.001,dist-size.length()*1.5);cam.far=dist+size.length()*7;cam.updateProjectionMatrix();\n const wheels=[],wheelGroups=[],majorMeshes=[];\n object.traverse(n=>{\n  const name=String(n.name||'');\n  const isWheel=/\\b(wheel|wheels|tire|tyre|pneu|aro|jante|rodas?)\\b|fmka_wheels|fmka_tires|fmfox_wheels|fmfox_tires/i.test(name.replace(/[_-]/g,' '));\n  if(isWheel&&!n.isMesh){\n    let box=new THREE.Box3().setFromObject(n);\n    wheelGroups.push({name,bounds:bjson(box),type:n.type,childCount:n.children.length});\n  }\n  if(n.isMesh){\n    const box=new THREE.Box3().setFromObject(n);\n    const item={name:n.name||'',material:n.material?.name||'',bounds:bjson(box),triangles:n.geometry?.index?.count/3||n.geometry?.attributes?.position?.count/3||0,parent:n.parent?.name||''};\n    if(isWheel||/wheel|tire|tyre|pneu|rim|raceparts|grey/i.test(n.parent?.name||'')||/wheel|tire|tyre|pneu/i.test(item.material))wheels.push(item);\n    if(item.triangles>70)majorMeshes.push(item);\n  }\n });\n majorMeshes.sort((a,b)=>b.triangles-a.triangles);\n window.setAngle('diagonal');\n return {id,dimensions:bjson(allBox).size,box:bjson(allBox),wheels,wheelGroups,largestMeshes:majorMeshes.slice(0,15),sceneRoot:object.name};\n};\nwindow.diagnosticReady=true;\n</script></body></html>";
const allowed=new Set(ids.map(id=>'/assets/'+id+'.glb'));
const server=createServer((req,res)=>{
 const pathname=new URL(req.url,'http://127.0.0.1').pathname;
 if(pathname==='/diag'){res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(html);return;}
 if(!pathname.startsWith('/node_modules/three/')&&!pathname.startsWith('/node_modules/meshoptimizer/')&&!allowed.has(pathname)){
   res.writeHead(404);res.end('Não encontrado');return;
 }
 const full=resolve(root,'.'+pathname);
 if(!full.startsWith(resolve(root,pathname.startsWith('/node_modules')?'node_modules':'assets')+sep)||!existsSync(full)){
   res.writeHead(404);res.end('Arquivo ausente');return;
 }
 const mime={'.js':'text/javascript','.mjs':'text/javascript','.glb':'model/gltf-binary','.wasm':'application/wasm'}[extname(full)]||'application/octet-stream';
 res.writeHead(200,{'Content-Type':mime});
 createReadStream(full).pipe(res);
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const port=server.address().port;
const out=join(root,'curadoria',process.env.WHEEL_DIAG_OUTPUT==='repaired'?'diagnostico-rodas-reparadas':'diagnostico-rodas');
mkdirSync(out,{recursive:true});
let browser;
try{
 browser=await chromium.launch({headless:true,args:['--use-gl=angle','--use-angle=swiftshader','--enable-webgl','--enable-unsafe-swiftshader','--disable-gpu-sandbox']});
 const page=await browser.newPage({viewport:{width:900,height:630},deviceScaleFactor:1});
 const errors=[];
 page.on('pageerror',e=>errors.push(String(e.message||e)));
 await page.goto('http://127.0.0.1:'+port+'/diag',{waitUntil:'load'});
 await page.waitForFunction(()=>window.diagnosticReady===true,{timeout:20000});
 const results=[];
 for(const id of ids){
   const model=await page.evaluate(id=>window.inspect(id),id);
   for(const angle of ['diagonal','lateral']){
     await page.evaluate(angle=>window.setAngle(angle),angle);
     await page.screenshot({path:join(out,id+'-'+angle+'.png')});
   }
   results.push(model);
   console.log('DIAGNOSTICO',id,JSON.stringify({dimensions:model.dimensions,box:model.box,wheels:model.wheels.length,wheelGroups:model.wheelGroups.length}));
 }
 writeFileSync(join(out,'geometry-report.json'),JSON.stringify({schema_version:1,types:'MEDICOES_REAIS_GLTF',models:results},null,2)+'\n');
 if(errors.length)throw new Error('Erros WebGL '+errors.join('; ').slice(0,600));
}finally{
 await browser?.close();
 await new Promise(resolve=>server.close(resolve));
}
