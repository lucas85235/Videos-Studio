// Switch "Avatar neste vídeo: padrão do canal / ligado / desligado" (`ProjectDoc.presenter`), com o ajuste fino de
// thumb e vídeo quando ligado. Grava por PUT /api/projects/{id}/presenter (rotas do avatar, Fase 4).

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { UserRound } from 'lucide-react';
import React from 'react';
import type { Presenter, PresenterOverride } from '../../../api';
import { presenterApi } from '../../../apiPresenter';
import { ErrorBox, SegmentedControl, Spinner } from '../../../ui';

type Tri = 'channel' | 'on' | 'off';
const toTri = (v: boolean | null | undefined): Tri => (v == null ? 'channel' : v ? 'on' : 'off');
const fromTri = (t: Tri): boolean | null => (t === 'channel' ? null : t === 'on');

export const DEFAULT_OVERRIDE: PresenterOverride = { enabled: null, thumb: null, video: null, poses_off: [] };

/** Avatar efetivo na thumb do projeto (sem o estilo): canal com poses, ligado, e a thumb não desligada. */
export const avatarOnInThumb = (channel: Presenter | null | undefined, project: PresenterOverride | null | undefined, styleAllows: boolean) => {
  if (!styleAllows || !channel || !channel.poses.length) return false;
  const enabled = project?.enabled ?? channel.enabled;
  if (!enabled) return false;
  return project?.thumb ?? channel.use_in_thumb;
};

export const PresenterSwitch: React.FC<{
  projectId: string;
  channel: Presenter | null;
  channelLoading: boolean;
  value: PresenterOverride;
  isShort: boolean;
  disabled: boolean;
}> = ({ projectId, channel, channelLoading, value, isShort, disabled }) => {
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: (next: PresenterOverride) => presenterApi.saveProject(projectId, next),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', projectId] }),
  });
  // Enquanto grava, mostra o valor novo.
  const current = save.isPending && save.variables ? save.variables : value;
  const hasPoses = Boolean(channel?.poses.length);
  const channelOn = Boolean(channel?.enabled && hasPoses);
  const enabled = current.enabled ?? channelOn;
  const channelVideo = isShort ? channel?.use_in_shorts : channel?.use_in_video;
  const update = (patch: Partial<PresenterOverride>) => save.mutate({ ...DEFAULT_OVERRIDE, ...current, ...patch });

  const defaultLabel = channelLoading ? 'Padrão do canal' : `Padrão do canal (${channelOn ? 'ligado' : 'desligado'})`;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="inline-flex items-center gap-1.5 text-sm font-medium text-fg">
          <UserRound size={15} aria-hidden="true" className="text-muted" />
          Avatar neste vídeo
        </span>
        <SegmentedControl
          label="Avatar neste vídeo"
          size="sm"
          disabled={disabled || save.isPending}
          value={toTri(current.enabled)}
          onChange={(t) => update({ enabled: fromTri(t) })}
          options={[
            { id: 'channel', label: defaultLabel },
            { id: 'on', label: 'Ligado', disabled: !hasPoses && !channelLoading, title: hasPoses ? undefined : 'O canal ainda não tem poses do avatar.' },
            { id: 'off', label: 'Desligado' },
          ]}
        />
        {save.isPending ? <Spinner /> : null}
      </div>
      {enabled && hasPoses ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] text-muted">
          <span className="inline-flex items-center gap-2">
            Thumb
            <SegmentedControl
              label="Avatar na thumb"
              size="sm"
              disabled={disabled || save.isPending}
              value={toTri(current.thumb)}
              onChange={(t) => update({ thumb: fromTri(t) })}
              options={[
                { id: 'channel', label: `Canal (${channel?.use_in_thumb ? 'sim' : 'não'})` },
                { id: 'on', label: 'Sim' },
                { id: 'off', label: 'Não' },
              ]}
            />
          </span>
          <span className="inline-flex items-center gap-2">
            {isShort ? 'Short' : 'Vídeo'}
            <SegmentedControl
              label="Avatar nas cenas do vídeo"
              size="sm"
              disabled={disabled || save.isPending}
              value={toTri(current.video)}
              onChange={(t) => update({ video: fromTri(t) })}
              options={[
                { id: 'channel', label: `Canal (${channelVideo ? 'sim' : 'não'})` },
                { id: 'on', label: 'Sim' },
                { id: 'off', label: 'Não' },
              ]}
            />
          </span>
        </div>
      ) : !hasPoses && !channelLoading ? (
        <p className="text-[13px] text-subtle">O canal não tem avatar com poses. Importe as poses em Canais → Avatar.</p>
      ) : null}
      <ErrorBox error={save.error} title="Não foi possível salvar o avatar do projeto" onClose={() => save.reset()} />
    </div>
  );
};
