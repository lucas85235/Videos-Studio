// Trilha de edição (ADR 0004 §3.11), no painel "Cortes" da Prévia: o índice "cara de IA" (EditScore), o botão
// "Reeditar" com intensidade e, por cena, os subplanos e eventos que o editor automático pôs, com travar, apagar e
// mover o gatilho para outra palavra. Tudo grava pelas rotas de `routes_edit.py`; a prévia precisa ser gerada de novo
// para mostrar a mudança.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, Lock, LockOpen, Trash2, Wand2 } from 'lucide-react';
import React, { useId, useMemo, useState } from 'react';
import type { FxEvent, ProjectDetail, SubShot } from '../../api';
import { EDIT_INTENSITY_LABELS, type EditIntensity, editApi, plainFx, plainShot, type TrackScene } from '../../apiEdit';
import { PROV_LABELS } from '../../apiPool';
import { BEAT_ROLE_LABELS, FX_LABELS, label, SHOT_MOTION_LABELS, SHOT_SOURCE_LABELS } from '../../labels';
import { Badge, Button, ErrorBox, IconButton, Select, Skeleton, useToast } from '../../ui';
import { EditScore } from './EditScore';
import { poolTargets } from './PoolPanel';
import type { ProjectTab } from './types';

export const editTrackKey = (projectId: string) => ['project', projectId, 'edit-track'] as const;

const secs = (t: number) => `${t.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} s`;

/** Seletor do gatilho: as palavras faladas da cena (o "@ms" atual continua na lista). */
const TriggerSelect: React.FC<{
  scene: TrackScene;
  value: string;
  disabled?: boolean;
  label: string;
  onChange: (at: string) => void;
}> = ({ scene, value, disabled, label: text, onChange }) => {
  const id = useId();
  const known = scene.words.some((w) => w.at === value);
  return (
    <>
      <label htmlFor={id} className="sr-only">
        {text}
      </label>
      <Select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} className="min-w-0 flex-1 py-0.5 text-xs">
        {known ? null : <option value={value}>{value}</option>}
        {scene.words.map((w) => (
          <option key={w.at} value={w.at}>
            {w.text} · {secs(w.t)}
          </option>
        ))}
      </Select>
    </>
  );
};

const LockButton: React.FC<{ locked: boolean; what: string; disabled?: boolean; onToggle: () => void }> = ({ locked, what, disabled, onToggle }) => (
  <IconButton
    icon={locked ? Lock : LockOpen}
    label={locked ? `Destravar ${what} (o Reeditar pode mudar)` : `Travar ${what} (o Reeditar não mexe)`}
    disabled={disabled}
    onClick={onToggle}
    className={locked ? 'text-accent' : undefined}
  />
);

/** Os subplanos e eventos de uma cena. */
export const SceneTrack: React.FC<{ projectId: string; scene: TrackScene; number: number; disabled?: boolean }> = ({
  projectId,
  scene,
  number,
  disabled,
}) => {
  const client = useQueryClient();
  const done = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: editTrackKey(projectId) }),
      client.invalidateQueries({ queryKey: ['project', projectId] }),
      client.invalidateQueries({ queryKey: ['edit-metrics', projectId] }),
    ]);
  };
  const saveFx = useMutation({ mutationFn: (fx: FxEvent[]) => editApi.setFx(projectId, scene.scene_id, fx), onSuccess: done });
  const saveShots = useMutation({ mutationFn: (shots: SubShot[]) => editApi.setShots(projectId, scene.scene_id, shots), onSuccess: done });
  const busy = disabled || scene.locked || saveFx.isPending || saveShots.isPending;
  const fx = scene.fx.map(plainFx);
  const shots = scene.shots.map(plainShot);
  // Mexer à mão faz o evento ser do usuário: o Reeditar preserva.
  const editFx = (i: number, change: Partial<FxEvent> | null) =>
    saveFx.mutate(change === null ? fx.filter((_, k) => k !== i) : fx.map((e, k) => (k === i ? { ...e, ...change, origin: 'user' } : e)));
  const editShot = (i: number, change: Partial<SubShot> | null) =>
    saveShots.mutate(change === null ? shots.filter((_, k) => k !== i) : shots.map((s, k) => (k === i ? { ...s, ...change, origin: 'user' } : s)));
  const emph = new Set(scene.mark.emphasis.map((e) => e.toLowerCase()));
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-1.5 text-[13px]">
        <span className="font-medium text-fg">Cena {number}</span>
        <Badge>{label(BEAT_ROLE_LABELS, scene.mark.role)}</Badge>
        <Badge tone={scene.prov === 'ai' ? 'bad' : 'good'}>{PROV_LABELS[scene.prov]}</Badge>
        {scene.mark.serious ? <Badge tone="warn">Séria (sem humor)</Badge> : null}
        {scene.locked ? <Badge tone="warn" icon={Lock}>Cena travada</Badge> : null}
        <span className="text-xs text-subtle">~{secs(scene.dur_s)}</span>
      </div>
      <p className="text-[13px] leading-relaxed text-muted">
        {scene.words.map((w) => (
          <span key={w.at} className={emph.has(w.text.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '')) ? 'font-semibold text-fg' : undefined}>
            {w.text}{' '}
          </span>
        ))}
      </p>

      <div className="flex flex-col gap-1.5">
        <h4 className="text-xs font-medium uppercase tracking-wide text-subtle">Planos ({shots.length || 1})</h4>
        {scene.shots.length ? (
          <ul className="flex flex-col gap-1">
            {scene.shots.map((s, i) => (
              <li key={`${i}-${s.at ?? 'inicio'}`} className="flex flex-col gap-1 rounded-md border border-line px-2 py-1 text-xs">
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className="tabular-nums text-subtle">{secs(s.t)}</span>
                  <span className="min-w-0 flex-1 truncate text-fg" title={s.desc || undefined}>
                    {label(SHOT_SOURCE_LABELS, s.source)}
                    {s.pool_id ? ` ${s.pool_id}` : ''}
                    {s.motion ? ` · ${label(SHOT_MOTION_LABELS, s.motion)}` : ''}
                    {s.framing && s.framing.scale > 1.01 ? ` · ${s.framing.scale.toFixed(2)}×` : ''}
                  </span>
                  <Badge tone={s.prov === 'ai' ? 'bad' : 'neutral'}>{PROV_LABELS[s.prov]}</Badge>
                </div>
                <div className="flex min-w-0 items-center gap-1.5">
                  {i > 0 ? (
                    <TriggerSelect
                      scene={scene}
                      value={s.at ?? ''}
                      disabled={busy}
                      label={`Palavra do corte do plano ${i + 1}`}
                      onChange={(at) => editShot(i, { at })}
                    />
                  ) : (
                    <span className="flex-1 text-subtle">Abre a cena</span>
                  )}
                  <LockButton locked={s.locked} what={`o plano ${i + 1}`} disabled={busy} onToggle={() => editShot(i, { locked: !s.locked })} />
                  <IconButton icon={Trash2} label={`Apagar o plano ${i + 1}`} disabled={busy || scene.shots.length < 2} onClick={() => editShot(i, null)} />
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-subtle">Um plano só (o visual da cena).</p>
        )}
      </div>

      <div className="flex flex-col gap-1.5">
        <h4 className="text-xs font-medium uppercase tracking-wide text-subtle">Eventos ({scene.fx.length})</h4>
        {scene.fx.length ? (
          <ul className="flex flex-col gap-1">
            {scene.fx.map((e, i) => {
              const text = typeof e.params.text === 'string' ? e.params.text : typeof e.params.kind === 'string' ? e.params.kind : '';
              return (
                <li key={`${i}-${e.type}-${e.at}`} className="flex flex-col gap-1 rounded-md border border-line px-2 py-1 text-xs">
                  <div className="flex min-w-0 items-center gap-1.5">
                    <span className="tabular-nums text-subtle">{secs(e.t)}</span>
                    <span className="min-w-0 flex-1 truncate text-fg">
                      {label(FX_LABELS, e.type)}
                      {text ? <span className="text-muted"> · {text}</span> : null}
                      {e.origin === 'user' ? <span className="text-subtle"> · seu</span> : null}
                    </span>
                  </div>
                  <div className="flex min-w-0 items-center gap-1.5">
                    <TriggerSelect scene={scene} value={e.at} disabled={busy} label={`Palavra do evento ${label(FX_LABELS, e.type)}`} onChange={(at) => editFx(i, { at })} />
                    <LockButton locked={e.locked} what={`o evento ${label(FX_LABELS, e.type)}`} disabled={busy} onToggle={() => editFx(i, { locked: !e.locked })} />
                    <IconButton icon={Trash2} label={`Apagar o evento ${label(FX_LABELS, e.type)}`} disabled={busy} onClick={() => editFx(i, null)} />
                  </div>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-xs text-subtle">Nenhum evento nesta cena.</p>
        )}
      </div>
      <ErrorBox
        error={saveFx.error ?? saveShots.error}
        title="Não foi possível mudar a edição"
        onClose={() => {
          saveFx.reset();
          saveShots.reset();
        }}
      />
    </div>
  );
};

/** "Reeditar" com intensidade: refaz subplanos e eventos sem LLM nem cota; o travado e o que é seu ficam. */
export const ReeditControl: React.FC<{ projectId: string; current: EditIntensity; disabled?: boolean }> = ({ projectId, current, disabled }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const id = useId();
  const [intensity, setIntensity] = useState<EditIntensity>(current);
  const reedit = useMutation({
    mutationFn: () => editApi.reedit(projectId, intensity),
    onSuccess: async (r) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['project', projectId] }),
        client.invalidateQueries({ queryKey: ['edit-metrics', projectId] }),
      ]);
      toast({
        tone: r.editor === 'off' ? 'warn' : 'good',
        title: r.editor === 'off' ? 'O editor automático está desligado no estilo' : r.changed ? 'Edição refeita' : 'Nada mudou',
        description:
          r.editor === 'off'
            ? 'Ligue em Estilo › Gramática de edição.'
            : `${r.summary.shots} planos e ${r.summary.fx_total} eventos. Gere a prévia de novo para ver.`,
      });
    },
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <label htmlFor={id} className="text-[13px] text-muted">
        Intensidade
      </label>
      <Select id={id} value={intensity} onChange={(e) => setIntensity(Number(e.target.value) as EditIntensity)} className="w-auto py-1 text-[13px]">
        {([0, 1, 2, 3] as EditIntensity[]).map((n) => (
          <option key={n} value={n}>
            {EDIT_INTENSITY_LABELS[n]}
          </option>
        ))}
      </Select>
      <Button size="sm" icon={Wand2} busy={reedit.isPending} disabled={disabled} title="Refaz planos e eventos pela gramática do estilo. Não usa o Claude nem cota de imagem." onClick={() => reedit.mutate()}>
        Reeditar
      </Button>
      <ErrorBox error={reedit.error} title="Não foi possível reeditar" onClose={() => reedit.reset()} className="basis-full" />
    </div>
  );
};

/** O painel "Cortes" da Prévia. */
export const EditTrackPanel: React.FC<{
  detail: ProjectDetail;
  lang: string;
  currentSceneId: string | null;
  disabled?: boolean;
  goTo?: (tab: ProjectTab) => void;
  onSeekScene: (sceneId: string) => void;
  onSeekSeconds: (seconds: number) => void;
}> = ({ detail, lang, currentSceneId, disabled, goTo, onSeekScene, onSeekSeconds }) => {
  const { doc } = detail;
  const track = useQuery({ queryKey: [...editTrackKey(doc.id), detail.project.current_version], queryFn: () => editApi.track(doc.id), placeholderData: (p) => p });
  const [picked, setPicked] = useState<string | null>(null);
  const scenes = track.data?.scenes ?? [];
  const followId = picked ?? currentSceneId;
  const index = useMemo(() => {
    const i = scenes.findIndex((s) => s.scene_id === followId);
    return i >= 0 ? i : 0;
  }, [scenes, followId]);
  const scene = scenes[index];
  const go = (delta: number) => {
    const next = scenes[Math.max(0, Math.min(scenes.length - 1, index + delta))];
    if (!next) return;
    setPicked(next.scene_id);
    onSeekScene(next.scene_id);
  };
  return (
    <div className="flex flex-col gap-5">
      <EditScore
        projectId={doc.id}
        lang={lang}
        refreshKey={detail.project.current_version}
        onSeek={(seconds) => onSeekSeconds(seconds)}
        onReplaceAi={
          goTo
            ? (ids) => {
                poolTargets.set(doc.id, 'cenas', ids);
                goTo('scenes');
              }
            : undefined
        }
      />
      {track.data ? <ReeditControl key={track.data.intensity} projectId={doc.id} current={track.data.intensity} disabled={disabled} /> : null}
      <div className="flex flex-col gap-2 border-t border-line pt-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-medium text-fg">Planos e eventos da cena</h3>
          {scenes.length ? (
            <div className="flex items-center gap-1">
              <IconButton icon={ChevronLeft} label="Cena anterior" disabled={index <= 0} onClick={() => go(-1)} />
              <span className="text-xs tabular-nums text-subtle">
                {index + 1}/{scenes.length}
              </span>
              <IconButton icon={ChevronRight} label="Próxima cena" disabled={index >= scenes.length - 1} onClick={() => go(1)} />
            </div>
          ) : null}
        </div>
        {picked && currentSceneId && picked !== currentSceneId ? (
          <button type="button" className="w-fit text-xs text-muted underline underline-offset-2 hover:text-fg" onClick={() => setPicked(null)}>
            Seguir a cena do player
          </button>
        ) : null}
        {track.isPending ? <Skeleton className="h-32" /> : null}
        {track.isError ? <ErrorBox error={track.error} title="Não foi possível ler a edição" onRetry={() => track.refetch()} /> : null}
        {track.data?.editor === 'off' ? <p className="text-[13px] text-amber-200">O editor automático está desligado neste estilo.</p> : null}
        {scene ? <SceneTrack projectId={doc.id} scene={scene} number={index + 1} disabled={disabled} /> : track.data ? <p className="text-[13px] text-subtle">Nenhuma cena com fala.</p> : null}
        <p className="text-xs text-subtle">Os tempos são estimados pelo texto; a prévia nova usa a narração de verdade.</p>
      </div>
    </div>
  );
};
