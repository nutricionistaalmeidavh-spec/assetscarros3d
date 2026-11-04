// Curadoria de previews reais: nunca baixar / mostrar modelo inexistente como veículo correto.
export function safeModelFile(file) {
  return typeof file === 'string' && /^[a-z0-9][a-z0-9-]*\.glb$/i.test(file);
}

export function selectThumbnailCandidates(entries, { max = 14 } = {}) {
  const candidates = entries
    .filter(x => x.asset_id && safeModelFile(x.asset_file)
      && x.technical_availability === 'GLB_EXISTE'
      && !x.issues.some(issue => /NÃO Gol brasileiro|NÃO Gol/i.test(issue)))
    .sort((a,b) => a.rank - b.rank);
  return candidates.slice(0, Math.max(0,Math.floor(Number(max) || 0)));
}

export function renderFiles(car) {
  if(!car?.asset_id || !/^[a-z0-9][a-z0-9-]*$/i.test(car.asset_id)
    || !safeModelFile(car.asset_file)) throw new Error('Modelo de arquivo inseguro');
  return ['frente','traseira'].map(v=> 'curadoria/miniaturas/' + car.asset_id + '-' + v + '.png');
}
