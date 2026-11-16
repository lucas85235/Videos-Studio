// No Short cortado de um longo: enquadramento vertical das imagens 16:9 do longo, cena a cena.
// Um clique na miniatura muda o ponto de foco (o recorte e a câmera seguem ele); cada cena pode usar o fundo desfocado
// (imagem inteira no meio) ou pedir a imagem de novo já em 9:16.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Crop, ImagePlus, RotateCcw } from 'lucide-react';
import React from 'react';
import { fileUrl } from '../../../../api';
import { type FrameEdit, type FrameMode, type FrameScene, type ShortFrames, shortsApi } from '../../../../apiShorts';
import { Badge, Button, ErrorBox, Loading, Section, Select } from '../../../../ui';

const MODE_LABELS: Record<FrameMode, string> = {
  crop: 'Recortar em volta do foco',
  blur: 'Imagem inteira com fundo desfocado',
};

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Janela vertical do vídeo sobre a imagem (frações da imagem), centrada no foco sem passar da borda. */
const cropWindow = (x: number, y: number, aspect: number, video: number) => {
  if (aspect > video) {
    const w = video / aspect;
    return { left: clamp(x - w / 2, 0, 1 - w), top: 0, width: w, height: 1 };
  }
  const h = aspect / video;
  return { left: 0, top: clamp(y - h / 2, 0, 1 - h), width: 1, height: h };
};

export const FrameEditor: React.FC<{ projectId: string }> = ({ projectId }) => {
  const client = useQueryClient();
  const key = ['short-frames', projectId];
  const q = useQuery({ queryKey: key, queryFn: () => shortsApi.frames(projectId) });
  // O documento mudou (nova versão): o projeto também é lido de novo.
  const onSaved = async (data: ShortFrames) => {
    client.setQueryData(key, data);
    await client.invalidateQueries({ queryKey: ['project', projectId] });
  };
  const save = useMutation({
    mutationFn: (body: { default_mode?: FrameMode; scenes?: FrameEdit[] }) => shortsApi.saveFrames(projectId, body),
    onSuccess: onSaved,
  });
  const vertical = useMutation({
    mutationFn: (sceneId: string) => shortsApi.verticalImage(projectId, sceneId),
    onSuccess: onSaved,
  });

  const data = q.data;
  if (q.isLoading) return <Loading />;
  if (q.isError) return <ErrorBox error={q.error} onRetry={() => void q.refetch()} />;
  if (!data || !data.scenes.length) return null;
  const video = data.width / data.height;
  const busy = save.isPending || vertical.isPending;

  return (
    <Section
      icon={Crop}
      title="Enquadramento vertical"
      description="As imagens vêm do vídeo longo (16:9). Clique na imagem para mudar o ponto de foco: o recorte e o movimento da câmera seguem ele. Vale na próxima prévia."
      actions={
        <label className="flex items-center gap-2 text-[13px] text-muted">
          Padrão do Short
          <Select
            value={data.default_mode}
            disabled={busy}
            onChange={(e) => save.mutate({ default_mode: e.target.value as FrameMode })}
            className="w-auto"
          >
            {(Object.keys(MODE_LABELS) as FrameMode[]).map((m) => (
              <option key={m} value={m}>
                {MODE_LABELS[m]}
              </option>
            ))}
          </Select>
        </label>
      }
    >
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {data.scenes.map((scene) => (
          <FrameCard
            key={scene.id}
            scene={scene}
            video={video}
            defaultMode={data.default_mode}
            busy={busy}
            onEdit={(edit) => save.mutate({ scenes: [{ id: scene.id, ...edit }] })}
            onVertical={() => vertical.mutate(scene.id)}
          />
        ))}
      </ul>
      {save.isError ? <ErrorBox error={save.error} title="Não deu para salvar o enquadramento" onClose={() => save.reset()} /> : null}
      {vertical.isError ? <ErrorBox error={vertical.error} title="Não deu para pedir a imagem vertical" onClose={() => vertical.reset()} /> : null}
    </Section>
  );
};

const FrameCard: React.FC<{
  scene: FrameScene;
  video: number;
  defaultMode: FrameMode;
  busy: boolean;
  onEdit: (edit: Omit<FrameEdit, 'id'>) => void;
  onVertical: () => void;
}> = ({ scene, video, defaultMode, busy, onEdit, onVertical }) => {
  const label = `Cena ${scene.index + 1}`;
  const frame = scene.frame;
  if (scene.vertical) {
    const done = Boolean(scene.src);
    const follows = scene.follows_index != null ? `Cena ${scene.follows_index + 1}` : null;
    return (
      <li className="flex flex-col gap-2 rounded-lg border border-line p-2 text-[13px]">
        <div className="flex items-center gap-2 px-1">
          <span className="font-medium text-fg">{label}</span>
          {done ? <Badge tone="info">imagem vertical gerada</Badge> : <Badge>imagem vertical pedida</Badge>}
        </div>
        {scene.src ? (
          <img
            src={fileUrl(scene.src)}
            alt=""
            loading="lazy"
            className="mx-auto max-h-56 rounded-md bg-black object-contain"
            style={{ aspectRatio: String(video) }}
          />
        ) : null}
        <span className="px-1 text-muted">
          {follows
            ? `Reaproveita a imagem da ${follows}, pedida em 9:16${done ? '' : ': sai na próxima prévia'}.`
            : done
              ? 'Imagem vertical (9:16) gerada pela cadeia de imagens do estilo.'
              : 'Imagem vertical (9:16) pedida: sai na próxima prévia, pela cadeia de imagens do estilo.'}
        </span>
        {!follows && frame.source ? (
          <div className="flex flex-wrap gap-1.5">
            <Button
              size="sm"
              variant="ghost"
              icon={RotateCcw}
              disabled={busy}
              onClick={() => onEdit({ restore: true })}
              title="Volta para a imagem 16:9 do longo, com o foco de antes"
            >
              Voltar à imagem 16:9
            </Button>
          </div>
        ) : null}
      </li>
    );
  }
  if (!scene.src) return null;
  const mode = frame.mode ?? defaultMode;
  const win = cropWindow(frame.x, frame.y, frame.aspect, video);
  const pick = (e: React.MouseEvent<HTMLButtonElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const x = clamp((e.clientX - box.left) / box.width, 0, 1);
    const y = clamp((e.clientY - box.top) / box.height, 0, 1);
    onEdit({ x: Math.round(x * 1000) / 1000, y: Math.round(y * 1000) / 1000 });
  };
  return (
    <li className="flex flex-col gap-2 rounded-lg border border-line p-2">
      <div className="flex items-center gap-2 px-1 text-[13px]">
        <span className="font-medium text-fg">{label}</span>
        {frame.auto ? <Badge tone="info">foco sugerido</Badge> : <Badge>foco seu</Badge>}
      </div>
      <button
        type="button"
        onClick={pick}
        disabled={busy}
        aria-label={`${label}: clique para escolher o ponto de foco`}
        className="relative block w-full cursor-crosshair overflow-hidden rounded-md bg-black disabled:cursor-wait"
        style={{ aspectRatio: String(frame.aspect) }}
      >
        <img src={fileUrl(scene.src)} alt="" loading="lazy" className="absolute inset-0 h-full w-full object-cover" />
        {mode === 'crop' ? (
          // O que fica fora do Short escurece; a janela vertical fica clara.
          <span
            aria-hidden="true"
            className="pointer-events-none absolute border-2 border-white/90"
            style={{
              left: `${win.left * 100}%`,
              top: `${win.top * 100}%`,
              width: `${win.width * 100}%`,
              height: `${win.height * 100}%`,
              boxShadow: '0 0 0 9999px rgba(0,0,0,0.55)',
            }}
          />
        ) : null}
        <span
          aria-hidden="true"
          className="pointer-events-none absolute h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-accent shadow"
          style={{ left: `${frame.x * 100}%`, top: `${frame.y * 100}%` }}
        />
      </button>
      <Select
        aria-label={`${label}: modo do enquadramento`}
        value={frame.mode ?? 'default'}
        disabled={busy}
        onChange={(e) => onEdit({ mode: e.target.value as FrameMode | 'default' })}
      >
        <option value="default">Padrão do Short ({MODE_LABELS[defaultMode].toLowerCase()})</option>
        {(Object.keys(MODE_LABELS) as FrameMode[]).map((m) => (
          <option key={m} value={m}>
            {MODE_LABELS[m]}
          </option>
        ))}
      </Select>
      <div className="flex flex-wrap gap-1.5">
        {!frame.auto ? (
          <Button size="sm" variant="ghost" icon={RotateCcw} disabled={busy} onClick={() => onEdit({ reset: true })}>
            Foco sugerido
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="ghost"
          icon={ImagePlus}
          disabled={busy}
          onClick={onVertical}
          title="Gera a imagem desta cena de novo, já em 9:16, na próxima prévia (usa a cota de imagens)"
        >
          Gerar imagem vertical
        </Button>
      </div>
    </li>
  );
};
