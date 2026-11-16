// Mapas embutidos e projeção do cartão de mapa, sem biblioteca de mapas.
// world.json: países do Natural Earth 1:110m; us-states.json: estados dos EUA (Census 1:10m, simplificado). Ambos
// de domínio público, gerados por renderer/scripts/build-geo.mjs. Cada região: polígonos → anéis achatados
// [lon, lat, lon, lat, ...]; o primeiro anel de cada polígono é o contorno e os outros são buracos.
import usStates from './us-states.json';
import world from './world.json';

type RawRegion = { id: string; p: number[][][] };
type RawMap = { source: string; regions: RawRegion[] };

/** Ponto na tela do mapa (unidades da projeção, y para baixo). */
export type Pt = readonly [number, number];
export type Ring = Pt[];
/** Região projetada: polígonos (anéis) e, à parte, o maior polígono (para o rótulo e o enquadramento). */
export type GeoShape = { id: string; polygons: Ring[][] };

const WORLD = (world as unknown as RawMap).regions;
const US_STATES = (usStates as unknown as RawMap).regions;
const BY_ID = new Map<string, RawRegion>([...WORLD, ...US_STATES].filter((r) => r.id).map((r) => [r.id, r]));

export const hasRegion = (id: string | undefined): id is string => Boolean(id && BY_ID.has(id));
export const isUsState = (id: string) => id.startsWith('US-');

// --- projeção Equal Earth (Šavrič, Patterson e Jenny, 2018), com meridiano central `lon0` ---------------------------

const A1 = 1.340264;
const A2 = -0.081106;
const A3 = 0.000893;
const A4 = 0.003796;
const M = Math.sqrt(3) / 2;
const RAD = Math.PI / 180;

/** `dLon` já é a longitude relativa ao meridiano central (graus, pode passar um pouco de ±180). */
export const equalEarth = (dLon: number, lat: number): Pt => {
  const theta = Math.asin(M * Math.sin(lat * RAD));
  const t2 = theta * theta;
  const t6 = t2 * t2 * t2;
  const x = (2 * Math.sqrt(3) * dLon * RAD * Math.cos(theta)) / (3 * (9 * A4 * t6 * t2 + 7 * A3 * t6 + 3 * A2 * t2 + A1));
  const y = theta * (A1 + A2 * t2 + t6 * (A3 + A4 * t2));
  return [x, -y];
};

const wrap = (d: number) => ((((d + 180) % 360) + 360) % 360) - 180;

/** Anel projetado sem "rasgar" no antimeridiano: a longitude anda contínua e o anel inteiro fica do lado mais perto
 *  do meridiano central (a Rússia e Fiji não viram uma faixa atravessando o mapa). */
const projectRing = (flat: number[], lon0: number): Ring => {
  const lons: number[] = [];
  let prev = 0;
  for (let i = 0; i < flat.length; i += 2) {
    const d = i === 0 ? wrap(flat[0] - lon0) : prev + wrap(flat[i] - flat[i - 2]);
    lons.push(d);
    prev = d;
  }
  const mean = lons.reduce((a, b) => a + b, 0) / Math.max(1, lons.length);
  const shift = -360 * Math.round(mean / 360);
  return lons.map((d, j) => equalEarth(d + shift, flat[j * 2 + 1]));
};

const shapeOf = (raw: RawRegion, lon0: number): GeoShape => ({
  id: raw.id,
  polygons: raw.p.map((poly) => poly.map((ring) => projectRing(ring, lon0))),
});

const cache = new Map<string, GeoShape[]>();

/** Camadas projetadas com o meridiano central `lon0` (arredondado, para o cache servir a cena inteira). */
export const projectedLayer = (layer: 'world' | 'us', lon0: number): GeoShape[] => {
  const key = `${layer}|${Math.round(lon0)}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const shapes = (layer === 'world' ? WORLD : US_STATES).map((r) => shapeOf(r, Math.round(lon0)));
  cache.set(key, shapes);
  return shapes;
};

export const ringArea = (ring: Ring) => {
  let a = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) a += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
  return Math.abs(a / 2);
};

export const ringCentroid = (ring: Ring): Pt => {
  let a = 0;
  let cx = 0;
  let cy = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const f = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
    a += f;
    cx += (ring[j][0] + ring[i][0]) * f;
    cy += (ring[j][1] + ring[i][1]) * f;
  }
  if (Math.abs(a) < 1e-12) return ring[0];
  return [cx / (3 * a), cy / (3 * a)];
};

/** Polígonos principais da região: o maior e os que têm pelo menos 25% da área dele (a Guiana Francesa não puxa o
 *  enquadramento da França; o Alasca e o Havaí não puxam o dos EUA). */
export const mainPolygons = (shape: GeoShape): Ring[][] => {
  const areas = shape.polygons.map((p) => ringArea(p[0]));
  const biggest = Math.max(0, ...areas);
  return shape.polygons.filter((_, i) => areas[i] >= biggest * 0.25);
};

/** Ponto do rótulo: o centro do maior polígono. */
export const labelPoint = (shape: GeoShape): Pt => {
  const areas = shape.polygons.map((p) => ringArea(p[0]));
  const i = areas.indexOf(Math.max(...areas));
  return ringCentroid(shape.polygons[Math.max(0, i)][0]);
};

/** Longitude do centro do maior polígono da região (para o meridiano central do mapa). */
export const centerLon = (id: string): number | null => {
  const raw = BY_ID.get(id);
  if (!raw) return null;
  let best: number[] | null = null;
  let bestArea = -1;
  for (const poly of raw.p) {
    const ring = poly[0];
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (let i = 0; i < ring.length; i += 2) {
      minX = Math.min(minX, ring[i]);
      maxX = Math.max(maxX, ring[i]);
      minY = Math.min(minY, ring[i + 1]);
      maxY = Math.max(maxY, ring[i + 1]);
    }
    const area = (maxX - minX) * (maxY - minY);
    if (maxX - minX < 300 && area > bestArea) {
      bestArea = area;
      best = [minX, maxX];
    }
  }
  return best ? (best[0] + best[1]) / 2 : null;
};

/** Média circular das longitudes (o meridiano central de várias regiões). */
export const meanLon = (lons: number[]) => {
  if (!lons.length) return 0;
  const x = lons.reduce((a, l) => a + Math.cos(l * RAD), 0);
  const y = lons.reduce((a, l) => a + Math.sin(l * RAD), 0);
  return Math.atan2(y, x) / RAD;
};

export type Box = { x0: number; y0: number; x1: number; y1: number };

export const boxOf = (rings: Ring[]): Box => {
  const box = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
  for (const ring of rings) {
    for (const [x, y] of ring) {
      box.x0 = Math.min(box.x0, x);
      box.y0 = Math.min(box.y0, y);
      box.x1 = Math.max(box.x1, x);
      box.y1 = Math.max(box.y1, y);
    }
  }
  return box;
};

/** Caminho SVG da região (anéis fechados; use fill-rule evenodd para os buracos). */
export const pathOf = (shape: GeoShape) =>
  shape.polygons
    .flat()
    .map((ring) => `M${ring.map(([x, y]) => `${x.toFixed(4)},${y.toFixed(4)}`).join('L')}Z`)
    .join('');
