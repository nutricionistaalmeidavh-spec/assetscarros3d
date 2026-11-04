import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {selectThumbnailCandidates, safeModelFile, renderFiles} from '../scripts/curadoria/thumbnail-plan.mjs';
const priority = JSON.parse(await readFile(new URL('../curadoria/prioridades-frota-2026.json', import.meta.url)));

test('ranking prioriza 50 modelos da frota e não finge que ausentes existem', () => {
  assert.equal(priority.models.length, 50);
  assert.deepEqual(priority.models.map(m=>m.rank), Array.from({length:50},(_,i)=>i+1));
  assert.equal(new Set(priority.models.map(m=>m.brand+' '+m.model)).size,50);
  const missing=priority.models.filter(m=>m.technical_availability==='SEM_GLB_COMPATIVEL');
  assert.equal(missing.length,14);
  assert.ok(missing.some(m=>m.brand==='Fiat'&&m.model==='Argo'));
  assert.ok(priority.models.every(m=>m.review_status==='PENDENTE_REVISAO_VISUAL'));
});

test('não renderizar a foto de Golf como Gol ou outro falso correspondente', () => {
  const candidates=selectThumbnailCandidates(priority.models,{max:30});
  assert.ok(!candidates.some(c=>c.asset_id==='volkswagen-gol'));
  assert.ok(!candidates.some(c=>!c.asset_id));
  assert.equal(candidates[0].asset_id,'fiat-uno');
  assert.ok(candidates.some(c=>c.asset_id==='fiat-palio'));
});

test('caminhos de GLB para miniaturas são estritamente confinados à pasta assets',()=>{
  assert.equal(safeModelFile('fiat-uno.glb'),true);
  assert.equal(safeModelFile('../a.glb'),false);
  assert.equal(safeModelFile('anything.glb/../../secret'),false);
  assert.equal(safeModelFile('fiat-uno.png'),false);
  assert.deepEqual(renderFiles({asset_id:'fiat-uno',asset_file:'fiat-uno.glb'}),[
    'curadoria/miniaturas/fiat-uno-frente.png',
    'curadoria/miniaturas/fiat-uno-traseira.png'
  ]);
});

test('seleção prioriza frota e permite limitar lote de renderização',()=>{
  const v=selectThumbnailCandidates(priority.models,{max:4});
  assert.equal(v.length,4);
  assert.deepEqual(v.map(x=>x.asset_id), ['fiat-uno','fiat-palio','fiat-strada','chevrolet-corsa']);
});
