// Gera os mapas embutidos do cartão de mapa (renderer/src/geo/*.json) e a tabela de nomes do backend
// (backend/studio/core/geo_regions.json). Não roda no build: os arquivos gerados ficam no repositório.
//
// Fontes (domínio público, redistribuídas em pacotes npm de licença ISC/MIT):
// - países: Natural Earth 1:110m Admin 0 (pacote world-atlas@2.0.2, countries-110m.json);
// - estados dos EUA: Census Bureau cartographic boundary 1:10m (pacote us-atlas@3.0.1, states-10m.json), simplificado;
// - nomes em pt/en/es e códigos ISO: pacote i18n-iso-countries@7.14.0 (MIT).
//
// Para gerar de novo (numa pasta temporária, fora do projeto):
//   npm install --no-save world-atlas@2.0.2 us-atlas@3.0.1 i18n-iso-countries@7.14.0
//   node <projeto>/renderer/scripts/build-geo.mjs <pasta com o node_modules> <projeto>

import fs from 'node:fs';
import path from 'node:path';

const [, , modulesDir = '.', projectDir = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/, '$1')), '..', '..')] = process.argv;
const nm = (p) => path.join(modulesDir, 'node_modules', p);
const readJson = (p) => JSON.parse(fs.readFileSync(p, 'utf8'));

const countries = readJson(nm('world-atlas/countries-110m.json'));
const states = readJson(nm('us-atlas/states-10m.json'));
const iso = readJson(nm('i18n-iso-countries/codes.json'));
const LANGS = ['pt', 'en', 'es'];
const names = Object.fromEntries(LANGS.map((l) => [l, readJson(nm(`i18n-iso-countries/langs/${l}.json`)).countries]));

// --- TopoJSON → anéis [lon, lat] -----------------------------------------------------------------

const decodeArcs = (topo) => {
  const [sx, sy] = topo.transform.scale;
  const [tx, ty] = topo.transform.translate;
  return topo.arcs.map((arc) => {
    let x = 0;
    let y = 0;
    return arc.map(([dx, dy]) => {
      x += dx;
      y += dy;
      return [x * sx + tx, y * sy + ty];
    });
  });
};

const ringOf = (arcs, indexes) => {
  const out = [];
  for (const i of indexes) {
    const arc = i >= 0 ? arcs[i] : [...arcs[~i]].reverse();
    out.push(...(out.length ? arc.slice(1) : arc));
  }
  return out;
};

const polygonsOf = (arcs, geometry) => {
  if (geometry.type === 'Polygon') return [geometry.arcs.map((r) => ringOf(arcs, r))];
  if (geometry.type === 'MultiPolygon') return geometry.arcs.map((p) => p.map((r) => ringOf(arcs, r)));
  return [];
};

/** Tira pontos a menos de `step` graus do anterior e arredonda (2 casas = ~1 km): o mapa é desenhado em vídeo. */
const simplify = (ring, step) => {
  const out = [];
  for (const [x, y] of ring) {
    const last = out[out.length - 1];
    if (!last || Math.abs(x - last[0]) + Math.abs(y - last[1]) >= step) out.push([x, y]);
  }
  const first = ring[0];
  out.push([first[0], first[1]]);
  return out.length >= 4 ? out.map(([x, y]) => [Math.round(x * 100) / 100, Math.round(y * 100) / 100]) : null;
};

/** Região: anéis achatados (lon, lat, lon, lat...). O primeiro anel de cada polígono é o contorno; o resto, buracos. */
const regionOf = (id, arcs, geometry, step) => {
  const polygons = polygonsOf(arcs, geometry)
    .map((rings) => rings.map((r) => simplify(r, step)).filter(Boolean))
    .filter((rings) => rings.length > 0)
    .map((rings) => rings.map((r) => r.flat()));
  return { id, p: polygons };
};

// --- países ---------------------------------------------------------------------------------------

const alpha3ByNumeric = Object.fromEntries(iso.map(([a2, a3, num]) => [num, a3]));
const alpha2ByAlpha3 = Object.fromEntries(iso.map(([a2, a3]) => [a3, a2]));
// Sem código numérico no Natural Earth: Kosovo tem código de uso comum (XKX); os outros ficam desenhados, sem destaque.
const EXTRA = { Kosovo: 'XKX' };
const EXTRA_NAMES = { XKX: { pt: 'Kosovo', en: 'Kosovo', es: 'Kosovo' } };
// Nomes curtos e em português do Brasil (o pacote traz o português de Portugal e nomes oficiais longos).
const OVERRIDE = {
  pt: {
    ARM: 'Armênia', BEN: 'Benin', COD: 'República Democrática do Congo', COG: 'República do Congo', CZE: 'Tchéquia',
    EST: 'Estônia', GRL: 'Groenlândia', IRN: 'Irã', KEN: 'Quênia', KWT: 'Kuwait', LVA: 'Letônia', MKD: 'Macedônia do Norte',
    MMR: 'Mianmar', POL: 'Polônia', PSE: 'Palestina', QAT: 'Catar', ROU: 'Romênia', SVN: 'Eslovênia', VNM: 'Vietnã',
    YEM: 'Iêmen', TKM: 'Turcomenistão', TJK: 'Tadjiquistão', MWI: 'Malawi',
  },
  en: {
    BRN: 'Brunei', CHN: 'China', CIV: 'Ivory Coast', COD: 'DR Congo', COG: 'Republic of the Congo', CZE: 'Czechia',
    FLK: 'Falkland Islands', GMB: 'The Gambia', IRN: 'Iran', LAO: 'Laos', MDA: 'Moldova', MKD: 'North Macedonia',
    PSE: 'Palestine', RUS: 'Russia', SYR: 'Syria', TUR: 'Turkey', TWN: 'Taiwan', TZA: 'Tanzania', USA: 'United States',
  },
  es: { COD: 'República Democrática del Congo', KOR: 'Corea del Sur', PRK: 'Corea del Norte' },
};
// Apelidos comuns que o pacote não traz.
const EXTRA_ALIAS = {
  USA: ['EUA', 'E.U.A.'],
  GBR: ['Grã-Bretanha', 'Inglaterra', 'Escócia', 'País de Gales', 'England', 'Scotland', 'Wales', 'Escocia', 'Gales'],
  NLD: ['Holanda'],
  KOR: ['Coreia'],
  CZE: ['República Tcheca', 'República Checa'],
  BIH: ['Bósnia', 'Bosnia'],
  ARE: ['Emirados Árabes', 'Emirados'],
  MMR: ['Birmânia', 'Burma'],
  COD: ['RD Congo', 'RDC', 'Congo-Kinshasa'],
};
// Nomes errados do pacote (Congo trocado): não viram apelido.
const BAD_ALIAS = new Set(['República Popular do Congo', 'República Democrática do Congo:COG']);

const worldArcs = decodeArcs(countries);
const world = [];
const regionNames = {};
for (const g of countries.objects.countries.geometries) {
  const a3 = (g.id && alpha3ByNumeric[g.id]) || EXTRA[g.properties?.name] || '';
  world.push(regionOf(a3, worldArcs, g, 0));
  if (!a3) continue;
  const a2 = alpha2ByAlpha3[a3];
  const pick = (l) => {
    const n = a2 ? names[l][a2] : undefined;
    return OVERRIDE[l][a3] || (Array.isArray(n) ? n[0] : n) || EXTRA_NAMES[a3]?.[l] || g.properties.name;
  };
  const aliases = a2
    ? [...new Set(LANGS.flatMap((l) => [names[l][a2]].flat().filter(Boolean)))].filter(
        (n) => !LANGS.some((l) => pick(l) === n) && !BAD_ALIAS.has(n) && !BAD_ALIAS.has(`${n}:${a3}`),
      )
    : [];
  aliases.push(...(EXTRA_ALIAS[a3] ?? []));
  regionNames[a3] = { pt: pick('pt'), en: pick('en'), es: pick('es'), ...(a2 ? { a2 } : {}), ...(aliases.length ? { alias: aliases } : {}) };
}

// --- estados dos EUA ------------------------------------------------------------------------------

// FIPS → sigla postal (US-XX, ISO 3166-2) e o nome em português quando é diferente do inglês.
const FIPS = {
  '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA', '08': 'CO', '09': 'CT', '10': 'DE', '11': 'DC', '12': 'FL',
  '13': 'GA', '15': 'HI', '16': 'ID', '17': 'IL', '18': 'IN', '19': 'IA', '20': 'KS', '21': 'KY', '22': 'LA', '23': 'ME',
  '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN', '28': 'MS', '29': 'MO', '30': 'MT', '31': 'NE', '32': 'NV', '33': 'NH',
  '34': 'NJ', '35': 'NM', '36': 'NY', '37': 'NC', '38': 'ND', '39': 'OH', '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI',
  '45': 'SC', '46': 'SD', '47': 'TN', '48': 'TX', '49': 'UT', '50': 'VT', '51': 'VA', '53': 'WA', '54': 'WV', '55': 'WI',
  '56': 'WY', '60': 'AS', '66': 'GU', '69': 'MP', '72': 'PR', '78': 'VI',
};
const STATE_PT = {
  AK: 'Alasca', CA: 'Califórnia', FL: 'Flórida', HI: 'Havaí', LA: 'Luisiana', NY: 'Nova York', NJ: 'Nova Jersey', NM: 'Novo México',
  NH: 'Nova Hampshire', NC: 'Carolina do Norte', SC: 'Carolina do Sul', ND: 'Dakota do Norte', SD: 'Dakota do Sul',
  PA: 'Pensilvânia', VA: 'Virgínia', WV: 'Virgínia Ocidental', DC: 'Distrito de Colúmbia', MP: 'Ilhas Marianas do Norte',
  VI: 'Ilhas Virgens Americanas', AS: 'Samoa Americana', OR: 'Oregon', TN: 'Tennessee', MS: 'Mississippi',
};
// Outros nomes dos estados (o nome em inglês já vale).
const STATE_ALIAS = { NY: ['Nova Iorque'], DC: ['Washington DC', 'Washington D.C.', 'Washington, D.C.'] };
const STATE_ES = {
  AK: 'Alaska', CA: 'California', NY: 'Nueva York', NJ: 'Nueva Jersey', NM: 'Nuevo México', NH: 'Nuevo Hampshire',
  NC: 'Carolina del Norte', SC: 'Carolina del Sur', ND: 'Dakota del Norte', SD: 'Dakota del Sur', PA: 'Pensilvania',
  VA: 'Virginia', WV: 'Virginia Occidental', HI: 'Hawái', LA: 'Luisiana', FL: 'Florida',
};

const stateArcs = decodeArcs(states);
const usStates = [];
for (const g of states.objects.states.geometries) {
  const code = FIPS[g.id];
  if (!code) continue;
  const id = `US-${code}`;
  usStates.push(regionOf(id, stateArcs, g, 0.08));
  const en = g.properties.name;
  const alias = STATE_ALIAS[code];
  regionNames[id] = { pt: STATE_PT[code] ?? en, en, es: STATE_ES[code] ?? STATE_PT[code] ?? en, ...(alias ? { alias } : {}) };
}

// --- saída ----------------------------------------------------------------------------------------

const outDir = path.join(projectDir, 'renderer', 'src', 'geo');
fs.mkdirSync(outDir, { recursive: true });
const write = (file, data) => {
  fs.writeFileSync(file, `${JSON.stringify(data)}\n`);
  console.log(`${path.relative(projectDir, file)}: ${(fs.statSync(file).size / 1024).toFixed(0)} KB`);
};
write(path.join(outDir, 'world.json'), { source: 'Natural Earth 1:110m (domínio público), via world-atlas 2.0.2', regions: world });
write(path.join(outDir, 'us-states.json'), { source: 'US Census Bureau 1:10m (domínio público), via us-atlas 3.0.1', regions: usStates });
const sorted = Object.fromEntries(Object.entries(regionNames).sort(([a], [b]) => a.localeCompare(b)));
fs.writeFileSync(path.join(projectDir, 'backend', 'studio', 'core', 'geo_regions.json'), `${JSON.stringify(sorted, null, 0).replace(/\},"/g, '},\n"')}\n`);
console.log(`backend/studio/core/geo_regions.json: ${Object.keys(sorted).length} regiões`);
