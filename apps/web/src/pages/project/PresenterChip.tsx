import { useMutation, useQueryClient } from '@tanstack/react-query';
import { EyeOff, RotateCcw, Shuffle, UserRound } from 'lucide-react';
import React, { useId, useState } from 'react';
import type { PresenterMode, PresenterSide, ProjectDoc, Scene, ScenePresenter } from '../../api';
import { DEFAULT_SCENE_PRESENTER, PRESENTER_AT_RE, presenterApi, presenterOnInScenes } from '../../apiPresenter';
import { Button, ErrorBox, Field, Input, SegmentedControl, Select, useToast } from '../../ui';
import { usePresenter } from '../characters/PresenterSheet';

const MODES: { id: PresenterMode; label: string; title: string }[] = [
  { id: 'reacao', label: 'Reação', title: 'Aparece por ~2 s na palavra e sai.' },
  { id: 'explica', label: 'Explica', title: 'Fica do gatilho até o fim da cena (até 8 s).' },
];
const SIDES: { id: PresenterSide; label: string }[] = [
  { id: 'auto', label: 'Automático' },
  { id: 'left', label: 'Esquerda' },
  { id: 'right', label: 'Direita' },
];

/** Texto do chip: "Avatar: surpreso @'chocante'", "Sem avatar" ou "Avatar: automático". */
const describe = (sp: ScenePresenter | null | undefined, defaultPose: string) => {
  if (!sp) return 'Avatar: automático (nenhum nesta cena)';
  if (sp.off) return 'Sem avatar';
  const at = sp.at ? (sp.at.startsWith('@') ? ` ${sp.at.slice(1)} ms` : ` @“${sp.at}”`) : '';
  return `Avatar: ${sp.pose ?? defaultPose}${at}${sp.mode === 'explica' ? ' · explica' : ''}${sp.source === 'manual' ? ' · manual' : ''}`;
};

/**
 * Avatar do apresentador numa cena: o chip mostra a pose e a palavra; aberto, escolhe pose, gatilho, modo e lado,
 * tira o avatar da cena ou redistribui o projeto inteiro. Some quando o canal não tem avatar ligado.
 */
export const PresenterChip: React.FC<{ projectId: string; doc: ProjectDoc; scene: Scene; short: boolean; disabled?: boolean }> = ({
  projectId,
  doc,
  scene,
  short,
  disabled,
}) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const panelId = useId();
  const presenter = usePresenter(doc.channel_id);
  const [open, setOpen] = useState(false);
  const current = scene.presenter ?? null;
  const [form, setForm] = useState<ScenePresenter>(current ?? DEFAULT_SCENE_PRESENTER);

  const refresh = () => client.invalidateQueries({ queryKey: ['project', projectId] });
  const saveScene = useMutation({
    mutationFn: (value: ScenePresenter | null) => presenterApi.saveScene(projectId, scene.id, value),
    onSuccess: async () => {
      await refresh();
      setOpen(false);
    },
  });
  const assign = useMutation({
    mutationFn: () => presenterApi.assign(projectId),
    onSuccess: async ({ warnings }) => {
      await refresh();
      toast({
        tone: 'good',
        title: 'Avatar redistribuído',
        description: warnings.length ? warnings.slice(0, 4).join(' · ') : 'As cenas marcadas à mão ficaram como estavam.',
      });
    },
  });

  const p = presenter.data;
  const on = presenterOnInScenes(p, doc.presenter, short);
  if (!p || (!on && !current)) return null;

  const at = (form.at ?? '').trim();
  const atInvalid = Boolean(at) && !PRESENTER_AT_RE.test(at);
  const busy = saveScene.isPending || assign.isPending;
  const poseMissing = Boolean(current?.pose && !p.poses.some((o) => o.id === current.pose));

  const toggle = () => {
    if (!open) setForm(current ? { ...current } : { ...DEFAULT_SCENE_PRESENTER });
    setOpen(!open);
  };

  return (
    <div className="flex flex-col gap-2 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={open}
          aria-controls={panelId}
          disabled={disabled}
          title={on ? 'Editar o avatar desta cena' : 'O avatar está desligado neste projeto ou canal'}
          className={`inline-flex min-h-6 items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
            current && !current.off ? 'border-accent/50 text-fg' : 'border-line text-muted'
          } ${on ? '' : 'line-through decoration-subtle'}`}
        >
          {current?.off ? <EyeOff size={12} aria-hidden="true" /> : <UserRound size={12} aria-hidden="true" />}
          {describe(current, p.default_pose)}
        </button>
        {poseMissing ? <span className="text-xs text-amber-300">pose “{current?.pose}” não existe: vale a mais próxima</span> : null}
      </div>
      {open ? (
        <div id={panelId} className="flex flex-col gap-2 rounded-lg border border-line bg-canvas p-2.5">
          <div className="grid grid-cols-1 gap-2 @md:grid-cols-2">
            <Field label="Pose">
              <Select value={form.pose ?? ''} onChange={(e) => setForm({ ...form, pose: e.target.value || null })}>
                <option value="">Padrão do canal ({p.default_pose})</option>
                {p.poses.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.id}
                    {o.emotions.length ? ` · ${o.emotions.slice(0, 3).join(', ')}` : ''}
                  </option>
                ))}
                {form.pose && !p.poses.some((o) => o.id === form.pose) ? <option value={form.pose}>{form.pose} (não existe)</option> : null}
              </Select>
            </Field>
            <Field label="Palavra" hint="Da narração: “palavra”, “palavra#2” (2ª vez) ou “@1200” (ms na cena). Vazio: o começo da cena." error={atInvalid ? 'Gatilho inválido.' : undefined}>
              <Input value={form.at ?? ''} invalid={atInvalid} onChange={(e) => setForm({ ...form, at: e.target.value })} placeholder="ex.: chocante" spellCheck={false} />
            </Field>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <SegmentedControl label="Modo do avatar" size="sm" options={MODES} value={form.mode} onChange={(mode) => setForm({ ...form, mode })} />
            <SegmentedControl label="Lado do avatar" size="sm" options={SIDES} value={form.side} onChange={(side) => setForm({ ...form, side })} />
          </div>
          <div className="flex flex-wrap gap-1.5">
            <Button
              size="sm"
              variant="primary"
              busy={saveScene.isPending && saveScene.variables !== null && !saveScene.variables?.off}
              disabled={busy || atInvalid}
              onClick={() => saveScene.mutate({ ...form, at: at || null, off: false, source: 'manual' })}
            >
              Usar nesta cena
            </Button>
            <Button
              size="sm"
              icon={EyeOff}
              busy={saveScene.isPending && Boolean(saveScene.variables?.off)}
              disabled={busy}
              onClick={() => saveScene.mutate({ ...DEFAULT_SCENE_PRESENTER, off: true, source: 'manual' })}
            >
              Sem avatar
            </Button>
            {current ? (
              <Button
                size="sm"
                variant="ghost"
                icon={RotateCcw}
                busy={saveScene.isPending && saveScene.variables === null}
                disabled={busy}
                onClick={() => saveScene.mutate(null)}
                title="Tira o ajuste manual: a distribuição automática decide esta cena."
              >
                Voltar ao automático
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              icon={Shuffle}
              busy={assign.isPending}
              disabled={busy || !on}
              onClick={() => assign.mutate()}
              title="Redistribui o avatar em todas as cenas do projeto (as marcadas à mão ficam)."
              className="ml-auto"
            >
              Redistribuir automático
            </Button>
          </div>
          <ErrorBox error={saveScene.error} title="Não foi possível salvar o avatar da cena" onClose={() => saveScene.reset()} />
          <ErrorBox error={assign.error} title="Não foi possível redistribuir o avatar" onClose={() => assign.reset()} />
        </div>
      ) : null}
    </div>
  );
};
