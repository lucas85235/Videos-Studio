/* Cliente do avatar do apresentador (seção C da especificação "título e thumb primeiro"): o avatar do canal
 * (`style_bible.presenter`), a importação das poses do canais, o ajuste do projeto e o avatar de cada cena.
 * As rotas ficam em `backend/studio/api/routes_characters.py`. */
import { api, type Presenter, type PresenterOverride, type PresenterPose, type PresenterVideo, type ScenePresenter } from './api';

/** Pasta das poses do Lucas em Vice no canais (a importação só aceita pastas dentro de `canais` ou da biblioteca). */
export const CANAIS_PRESENTER_DIR = 'C:/Users/lucas/canais/plantao-vice/video/public/apresentador';

/** Mesmo padrão do backend (`PRESENTER_POSE_ID_RE`). */
export const POSE_ID_RE = /^[a-z0-9][a-z0-9_-]{0,39}$/;
/** Gatilho do avatar na cena (`PRESENTER_AT_RE`): "palavra", "palavra#2" ou "@1200" (ms desde o início da cena). */
export const PRESENTER_AT_RE = /^(@\d{1,7}|[^\s#@][^\s#]{0,59}(#\d{1,3})?)$/;

export const DEFAULT_PRESENTER_VIDEO: PresenterVideo = {
  every_s: [60, 90],
  min_gap_scenes: 2,
  height_reacao: 0.55,
  height_explica: 0.48,
  outline_px: 5,
  hide_on_kinds: ['document', 'quote', 'chart', 'map', 'news'],
  hide_on_vertical: true,
  skip_first_scene: true,
};

/** Avatar padrão (desligado), igual ao `Presenter()` do backend. */
export const DEFAULT_PRESENTER: Presenter = {
  enabled: false,
  character_id: null,
  poses: [],
  default_pose: 'base',
  video: DEFAULT_PRESENTER_VIDEO,
  use_in_thumb: true,
  use_in_video: true,
  use_in_shorts: false,
  thumb_height: 1.5,
  thumb_crop: 'busto',
  thumb_outline_px: 10,
};

export const DEFAULT_SCENE_PRESENTER: ScenePresenter = { pose: null, at: null, mode: 'reacao', side: 'auto', source: 'manual', off: false };

/** Resposta das rotas do avatar do canal: o avatar salvo e os avisos (ex.: "a figura encosta na lateral"). */
export type PresenterResult = { presenter: Presenter; warnings: string[] };
export type PoseUploadResult = { pose: PresenterPose | null; presenter: Presenter | null; warnings: string[] };
export type PresenterImportResult = PresenterResult & { imported: string[] };
export type AssignResult = { warnings: string[] };

type Loose = Record<string, unknown> | null | undefined;

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);

/** Completa um avatar vindo do servidor (aceita `Presenter` puro ou `{presenter}`), sem quebrar com campo ausente. */
export const normalizePresenter = (raw: unknown): Presenter => {
  const obj = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const p = (obj.presenter && typeof obj.presenter === 'object' ? obj.presenter : obj) as Partial<Presenter>;
  return {
    ...DEFAULT_PRESENTER,
    ...p,
    poses: Array.isArray(p.poses)
      ? p.poses.map((pose: Partial<PresenterPose>) => ({ id: '', file: '', render_file: null, emotions: [], aliases: [], thumb_ok: false, facing: 'center' as const, ...pose }))
      : [],
    video: { ...DEFAULT_PRESENTER_VIDEO, ...(p.video ?? {}) },
  };
};

const resultOf = (raw: Loose): PresenterResult => ({ presenter: normalizePresenter(raw), warnings: strings(raw?.warnings) });

export const presenterApi = {
  /** GET /api/channels/{id}/presenter: o avatar do canal (padrão desligado quando o canal não tem). */
  get: async (channelId: string): Promise<PresenterResult> => resultOf(await api.get<Loose>(`/api/channels/${channelId}/presenter`)),

  /** PUT /api/channels/{id}/presenter com o `Presenter` no corpo (sem embrulho): grava o avatar inteiro (as outras
   * chaves do `style_bible` ficam). */
  save: async (channelId: string, presenter: Presenter): Promise<PresenterResult> =>
    resultOf(await api.put<Loose>(`/api/channels/${channelId}/presenter`, presenter)),

  /** POST /api/channels/{id}/presenter/poses (multipart `file` + `pose_id`): envia um PNG RGBA; o backend valida o alfa. */
  uploadPose: async (channelId: string, file: File, poseId: string): Promise<PoseUploadResult> => {
    const form = new FormData();
    form.append('file', file);
    form.append('pose_id', poseId);
    const raw = await api.upload<Loose>(`/api/channels/${channelId}/presenter/poses`, form);
    const pose = (raw?.pose && typeof raw.pose === 'object' ? raw.pose : null) as PresenterPose | null;
    return { pose, presenter: raw?.presenter ? normalizePresenter(raw.presenter) : null, warnings: strings(raw?.warnings) };
  },

  /** POST /api/channels/{id}/presenter/import com `{path}`: copia e valida as poses da pasta (ex.: a do canais). A
   * resposta traz `report` ({imported, updated, rejected, warnings}) e o avatar salvo. */
  importFolder: async (channelId: string, path: string): Promise<PresenterImportResult> => {
    const raw = await api.post<Loose>(`/api/channels/${channelId}/presenter/import`, { path });
    const report = (raw?.report && typeof raw.report === 'object' ? raw.report : {}) as Record<string, unknown>;
    const rejected = report.rejected && typeof report.rejected === 'object' ? (report.rejected as Record<string, unknown>) : {};
    const warnings = [
      ...strings(raw?.warnings),
      ...strings(report.warnings),
      ...Object.entries(rejected).map(([file, why]) => `${file}: ${String(why)}`),
    ];
    return {
      presenter: normalizePresenter(raw),
      warnings,
      imported: [...strings(report.imported), ...strings(report.updated), ...strings(raw?.imported)],
    };
  },

  /** PUT /api/projects/{id}/presenter com o `PresenterOverride` no corpo (sem embrulho): o ajuste do projeto (null
   * segue o canal). */
  saveProject: (projectId: string, presenter: PresenterOverride) => api.put<unknown>(`/api/projects/${projectId}/presenter`, presenter),

  /** PUT /api/projects/{id}/scenes/{sid}/presenter com `{presenter}` (null: volta para a distribuição automática). */
  saveScene: (projectId: string, sceneId: string, presenter: ScenePresenter | null) =>
    api.put<unknown>(`/api/projects/${projectId}/scenes/${sceneId}/presenter`, { presenter }),

  /** POST /api/projects/{id}/presenter/assign: redistribui o avatar nas cenas (as manuais ficam). */
  assign: async (projectId: string): Promise<AssignResult> => {
    const raw = await api.post<Loose>(`/api/projects/${projectId}/presenter/assign`, {});
    // O backend devolve `issues` no formato da checagem do storyboard ({code, severity, message}).
    const issues = Array.isArray(raw?.issues) ? (raw.issues as Array<Record<string, unknown>>) : [];
    const fromIssues = issues
      .map((i) => (typeof i?.message === 'string' ? `${typeof i.severity === 'string' ? `${i.severity}: ` : ''}${i.message}` : ''))
      .filter(Boolean);
    return { warnings: [...strings(raw?.warnings), ...fromIssues] };
  },
};

export const presenterQueryKey = (channelId: string) => ['presenter', channelId] as const;

/** Avatar efetivo no projeto (espelho de `core/project/presenter.effective` sem o estilo): ligado e valendo para as cenas. */
export const presenterOnInScenes = (channel: Presenter | null | undefined, project: PresenterOverride | null | undefined, short: boolean): boolean => {
  if (!channel || !channel.poses.length) return false;
  const enabled = project?.enabled ?? channel.enabled;
  if (!enabled) return false;
  // O vídeo vertical não mostra o avatar (`hide_on_vertical`, padrão ligado: timeline e renderer o tiram).
  if (short && (channel.video?.hide_on_vertical ?? true)) return false;
  return project?.video ?? (short ? channel.use_in_shorts : channel.use_in_video);
};

/** Lê uma lista separada por vírgula (emoções, apelidos): minúsculas, sem repetir, como o `_clean_words` do backend. */
export const parseWords = (text: string): string[] =>
  Array.from(
    new Set(
      text
        .split(',')
        .map((w) => w.split(/\s+/).filter(Boolean).join(' ').toLowerCase().slice(0, 40))
        .filter(Boolean),
    ),
  ).slice(0, 12);

/** Id de pose a partir do nome do arquivo ("Mão na testa.png" → "mao-na-testa"). */
export const poseIdFromFile = (name: string, taken: Set<string>): string => {
  const base =
    name
      .replace(/\.[^.]+$/, '')
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9_-]+/g, '-')
      .replace(/^[-_]+|[-_]+$/g, '')
      .slice(0, 34) || 'pose';
  let id = base;
  for (let n = 2; taken.has(id); n++) id = `${base}-${n}`;
  return id;
};
