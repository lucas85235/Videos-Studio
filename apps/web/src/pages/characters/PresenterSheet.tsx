import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FolderInput, ImagePlus, Star, Trash2, UserRound } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { api, type Character, fileUrl, type Presenter, type PresenterPose } from '../../api';
import {
  CANAIS_PRESENTER_DIR,
  normalizePresenter,
  parseWords,
  POSE_ID_RE,
  poseIdFromFile,
  presenterApi,
  presenterQueryKey,
} from '../../apiPresenter';
import { Badge, Button, Checkbox, ErrorBox, errorMessage, Field, IconButton, Input, Select, Skeleton, useConfirm, useToast } from '../../ui';
import { Sheet } from '../ops/common';
import { Notice } from '../project/delivery/shared';

export type PresenterTarget = { id: string; name: string };

const FACING_LABELS: Record<PresenterPose['facing'], string> = { center: 'De frente', left: 'Para a esquerda', right: 'Para a direita' };

/** Fundo xadrez para ver o recorte (alfa) da pose. */
const CHECKER = {
  backgroundImage:
    'linear-gradient(45deg, rgba(255,255,255,.06) 25%, transparent 25%), linear-gradient(-45deg, rgba(255,255,255,.06) 25%, transparent 25%), linear-gradient(45deg, transparent 75%, rgba(255,255,255,.06) 75%), linear-gradient(-45deg, transparent 75%, rgba(255,255,255,.06) 75%)',
  backgroundSize: '16px 16px',
  backgroundPosition: '0 0, 0 8px, 8px -8px, -8px 0',
} as const;

const poseSrc = (p: PresenterPose) => fileUrl(p.render_file ?? p.file);

/** Limpa as listas digitadas (emoções e apelidos) antes de salvar ou comparar. */
const cleaned = (p: Presenter): Presenter => ({
  ...p,
  poses: p.poses.map((pose) => ({ ...pose, emotions: parseWords(pose.emotions.join(',')), aliases: parseWords(pose.aliases.join(',')) })),
});

const same = (a: Presenter, b: Presenter) => JSON.stringify(cleaned(a)) === JSON.stringify(cleaned(b));

/** Prévia num quadro 16:9 fictício: a pose entra por baixo, no canto, na altura da reação. */
const PresenterPreview: React.FC<{ presenter: Presenter; pose: PresenterPose | null }> = ({ presenter, pose }) => {
  const right = pose?.facing !== 'right';
  const height = `${Math.round(presenter.video.height_reacao * 100)}%`;
  return (
    <figure className="flex flex-col gap-1.5">
      <div
        className="relative aspect-video w-full overflow-hidden rounded-lg border border-line"
        style={{ background: 'linear-gradient(135deg, #1b1035 0%, #3a1450 45%, #ff4fa3 100%)' }}
      >
        <div className="absolute left-[6%] top-[10%] max-w-[55%] font-display text-[clamp(14px,3.2cqw,28px)] font-bold uppercase leading-tight text-white drop-shadow">
          Cena de exemplo
        </div>
        <div className="absolute bottom-[7%] left-1/2 -translate-x-1/2 rounded bg-black/60 px-2 py-0.5 text-[11px] text-white">legenda da narração</div>
        {pose ? (
          <img
            src={poseSrc(pose)}
            alt={`Pose ${pose.id} na prévia`}
            className={`absolute bottom-0 ${right ? 'right-[-3%]' : 'left-[-3%]'} max-w-none`}
            style={{
              height,
              filter: `drop-shadow(${presenter.video.outline_px}px 0 0 #fff) drop-shadow(-${presenter.video.outline_px}px 0 0 #fff) drop-shadow(0 ${presenter.video.outline_px}px 0 #fff) drop-shadow(0 -${presenter.video.outline_px}px 0 #fff) drop-shadow(0 8px 14px rgba(0,0,0,.45))`,
              transform: 'rotate(1.5deg)',
            }}
          />
        ) : null}
      </div>
      <figcaption className="text-xs text-subtle">
        {pose
          ? `Prévia aproximada: pose “${pose.id}” em ${height} da altura, no lado ${right ? 'direito' : 'esquerdo'}. O vídeo escolhe o lado oposto ao foco da cena.`
          : 'Escolha uma pose para ver a prévia.'}
      </figcaption>
    </figure>
  );
};

const PoseEditor: React.FC<{
  pose: PresenterPose;
  isDefault: boolean;
  previewing: boolean;
  onChange: (p: PresenterPose) => void;
  onRemove: () => void;
  onDefault: () => void;
  onPreview: () => void;
}> = ({ pose, isDefault, previewing, onChange, onRemove, onDefault, onPreview }) => (
  <li className={`flex flex-col gap-2 rounded-xl border bg-card p-3 ${previewing ? 'border-accent/60' : 'border-line'}`}>
    <div className="flex items-start gap-3">
      <button
        type="button"
        onClick={onPreview}
        aria-label={`Ver a pose ${pose.id} na prévia`}
        aria-pressed={previewing}
        className="h-28 w-20 shrink-0 overflow-hidden rounded-lg border border-line"
        style={CHECKER}
      >
        <img src={poseSrc(pose)} alt="" loading="lazy" className="h-full w-full object-contain object-bottom" />
      </button>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="truncate font-mono text-sm text-fg">{pose.id}</span>
          {isDefault ? (
            <Badge tone="accent" icon={Star}>
              Padrão
            </Badge>
          ) : null}
          {pose.thumb_ok ? <Badge>Thumb</Badge> : null}
        </div>
        <Checkbox label="Serve para thumb" checked={pose.thumb_ok} onChange={(thumb_ok) => onChange({ ...pose, thumb_ok })} />
        <Field label="Olha para">
          <Select value={pose.facing} onChange={(e) => onChange({ ...pose, facing: e.target.value as PresenterPose['facing'] })}>
            {(Object.keys(FACING_LABELS) as PresenterPose['facing'][]).map((f) => (
              <option key={f} value={f}>
                {FACING_LABELS[f]}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <div className="flex flex-col gap-1">
        <IconButton label={`Remover a pose ${pose.id}`} icon={Trash2} size="sm" onClick={onRemove} className="hover:text-red-300" />
        {!isDefault ? <IconButton label={`Usar ${pose.id} como pose padrão`} icon={Star} size="sm" onClick={onDefault} /> : null}
      </div>
    </div>
    <Field label="Emoções" hint="Separe por vírgula. O storyboard e o pacote escolhem a pose por elas (ex.: choque, surpresa).">
      <Input
        value={pose.emotions.join(', ')}
        onChange={(e) => onChange({ ...pose, emotions: e.target.value.split(',').map((w) => w.trimStart()) })}
        onBlur={() => onChange({ ...pose, emotions: parseWords(pose.emotions.join(',')) })}
        placeholder="ex.: choque, surpresa"
      />
    </Field>
    <Field label="Outros nomes" hint="Separe por vírgula (ex.: espantado).">
      <Input
        value={pose.aliases.join(', ')}
        onChange={(e) => onChange({ ...pose, aliases: e.target.value.split(',').map((w) => w.trimStart()) })}
        onBlur={() => onChange({ ...pose, aliases: parseWords(pose.aliases.join(',')) })}
        placeholder="ex.: espantado"
      />
    </Field>
  </li>
);

/** Painel do avatar do apresentador de um canal: ligar/desligar, importar as poses do canais, emoções e prévia. */
export const PresenterSheet: React.FC<{ target: PresenterTarget; onClose: () => void }> = ({ target, onClose }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const query = useQuery({ queryKey: presenterQueryKey(target.id), queryFn: () => presenterApi.get(target.id) });
  const characters = useQuery({ queryKey: ['characters', 'channel', target.id], queryFn: () => api.get<Character[]>(`/api/channels/${target.id}/characters`) });

  const [draft, setDraft] = useState<Presenter | null>(null);
  const [initial, setInitial] = useState<Presenter | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [folder, setFolder] = useState(CANAIS_PRESENTER_DIR);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const reset = (p: Presenter) => {
    setDraft(p);
    setInitial(p);
  };
  useEffect(() => {
    if (query.data && draft === null) {
      reset(query.data.presenter);
      setWarnings(query.data.warnings);
    }
  }, [query.data, draft]);

  const p = draft;
  const dirty = p !== null && initial !== null && !same(p, initial);
  const set = (patch: Partial<Presenter>) => p && setDraft({ ...p, ...patch });
  const setPose = (id: string, next: PresenterPose) => p && setDraft({ ...p, poses: p.poses.map((o) => (o.id === id ? next : o)) });
  const preview = p?.poses.find((o) => o.id === previewId) ?? p?.poses.find((o) => o.id === p.default_pose) ?? p?.poses[0] ?? null;
  const defaultMissing = Boolean(p && p.poses.length && !p.poses.some((o) => o.id === p.default_pose));

  const afterServer = async (presenter: Presenter) => {
    await Promise.all([client.invalidateQueries({ queryKey: ['presenter'] }), client.invalidateQueries({ queryKey: ['channels'] })]);
    client.setQueryData(presenterQueryKey(target.id), { presenter, warnings: [] });
  };

  const save = useMutation({
    mutationFn: () => presenterApi.save(target.id, cleaned(p as Presenter)),
    onSuccess: async (res) => {
      await afterServer(res.presenter);
      toast({
        tone: 'good',
        title: res.presenter.enabled ? 'Avatar salvo e ligado' : 'Avatar salvo (desligado)',
        description: res.presenter.enabled ? 'Vale para as próximas cenas, prévias e thumbs do canal.' : undefined,
      });
      onClose();
    },
  });

  const importFolder = useMutation({
    mutationFn: () => presenterApi.importFolder(target.id, folder.trim()),
    onSuccess: async (res) => {
      reset(res.presenter);
      setWarnings(res.warnings);
      setPreviewId(null);
      await afterServer(res.presenter);
      const n = res.imported.length || res.presenter.poses.length;
      toast({ tone: 'good', title: `${n} ${n === 1 ? 'pose importada' : 'poses importadas'}`, description: 'O avatar continua desligado até você ligar.' });
    },
  });

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      // O mesmo nome de uma pose que já existe troca o arquivo dela (mantém emoções e "serve para thumb"); só dois
      // arquivos com o mesmo nome no mesmo envio ganham sufixo.
      const existing = new Set((p?.poses ?? []).map((o) => o.id));
      const batch = new Set<string>();
      const added: PresenterPose[] = [];
      const notes: string[] = [];
      let serverDefault: string | null = null;
      for (const file of files) {
        const id = poseIdFromFile(file.name, batch);
        batch.add(id);
        const res = await presenterApi.uploadPose(target.id, file, id);
        const pose = res.pose ?? res.presenter?.poses.find((o) => o.id === id) ?? null;
        if (pose) added.push(pose);
        if (res.presenter?.default_pose) serverDefault = res.presenter.default_pose;
        if (existing.has(id)) notes.push(`${id}: arquivo trocado (a pose manteve as emoções e o uso em thumb).`);
        notes.push(...res.warnings.map((w) => `${id}: ${w}`));
      }
      return { added, notes, serverDefault };
    },
    onSuccess: ({ added, notes, serverDefault }) => {
      if (p) {
        const poses = [...p.poses.filter((o) => !added.some((a) => a.id === o.id)), ...added];
        // O servidor troca a pose padrão quando a atual não existe (canal novo sem "base"): a tela segue.
        const keep = poses.some((o) => o.id === p.default_pose);
        setDraft({ ...p, poses, default_pose: keep ? p.default_pose : serverDefault ?? poses[0]?.id ?? p.default_pose });
      }
      setWarnings(notes);
      if (added[0]) setPreviewId(added[0].id);
    },
    onError: (err) => toast({ tone: 'bad', title: 'Não foi possível enviar a pose', description: errorMessage(err) }),
  });

  const askImport = async () => {
    if (
      dirty &&
      !(await confirm({
        title: 'Importar e descartar as alterações?',
        description: 'A importação grava as poses da pasta no canal; o que você mudou aqui e não salvou se perde.',
        confirmLabel: 'Importar',
        tone: 'danger',
      }))
    )
      return;
    importFolder.mutate();
  };

  const close = async () => {
    if (dirty && !(await confirm({ title: 'Descartar as alterações?', description: 'O avatar que você mudou não foi salvo.', confirmLabel: 'Descartar', tone: 'danger' })))
      return;
    onClose();
  };

  const badIds = (p?.poses ?? []).filter((o) => !POSE_ID_RE.test(o.id));
  const busy = save.isPending || importFolder.isPending || upload.isPending;

  return (
    <Sheet
      title={`Avatar do apresentador de “${target.name}”`}
      description="Um personagem recortado (PNG com fundo transparente) que reage nas cenas do vídeo e aparece grande nas thumbs."
      onRequestClose={() => void close()}
      onSubmit={() => {
        if (!busy && dirty && !badIds.length && !defaultMissing) save.mutate();
      }}
      footer={
        <>
          <ErrorBox error={save.error} title="Não foi possível salvar o avatar" onClose={() => save.reset()} className="w-full" />
          <Button onClick={() => void close()}>Cancelar</Button>
          <Button type="submit" variant="primary" busy={save.isPending} disabled={!dirty || busy || Boolean(badIds.length) || defaultMissing}>
            Salvar avatar
          </Button>
        </>
      }
    >
      <ErrorBox error={query.error} title="Não foi possível carregar o avatar" onRetry={() => query.refetch()} />
      {query.isLoading ? <Skeleton className="h-40" /> : null}
      {p ? (
        <>
          <div className="flex flex-col gap-2 rounded-xl border border-line bg-canvas p-3">
            <Checkbox
              label="Usar avatar do apresentador"
              description={p.poses.length ? 'Desligado, nenhuma cena nem thumb do canal usa o avatar.' : 'Importe ou envie as poses antes de ligar.'}
              checked={p.enabled}
              disabled={!p.poses.length && !p.enabled}
              onChange={(enabled) => set({ enabled })}
            />
            <div className={`ml-6 flex flex-col gap-1.5 ${p.enabled ? '' : 'opacity-60'}`}>
              <Checkbox label="Nas thumbs" description="Busto grande nos moldes com avatar." checked={p.use_in_thumb} disabled={!p.enabled} onChange={(use_in_thumb) => set({ use_in_thumb })} />
              <Checkbox
                label="Nas cenas do vídeo longo"
                description={`Reações curtas a cada ${p.video.every_s[0]}–${p.video.every_s[1]} s.`}
                checked={p.use_in_video}
                disabled={!p.enabled}
                onChange={(use_in_video) => set({ use_in_video })}
              />
              <Checkbox label="Nos Shorts" description="Só na capa do Short (o vídeo vertical não mostra o avatar). Padrão: não." checked={p.use_in_shorts} disabled={!p.enabled} onChange={(use_in_shorts) => set({ use_in_shorts })} />
            </div>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Personagem ligado" hint="Liga o avatar à descrição e às âncoras de IA do personagem.">
              <Select value={p.character_id ?? ''} onChange={(e) => set({ character_id: e.target.value || null })}>
                <option value="">Nenhum</option>
                {(characters.data ?? []).map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name || c.id}
                  </option>
                ))}
                {p.character_id && !(characters.data ?? []).some((c) => c.id === p.character_id) ? <option value={p.character_id}>{p.character_id}</option> : null}
              </Select>
            </Field>
            <Field label="Pose padrão" hint="Vale quando a cena não pede pose ou pede uma que não existe." error={defaultMissing ? `A pose “${p.default_pose}” não existe.` : undefined}>
              <Select value={p.default_pose} onChange={(e) => set({ default_pose: e.target.value })} invalid={defaultMissing}>
                {defaultMissing ? <option value={p.default_pose}>{p.default_pose} (não existe)</option> : null}
                {p.poses.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.id}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          <div className="flex flex-col gap-2 rounded-xl border border-line p-3">
            <div className="text-sm text-fg">Importar pasta do canais</div>
            <p className="text-[13px] text-muted">
              Copia os PNG da pasta, confere o fundo transparente, recorta e cria a versão menor para o vídeo. Emoções, apelidos e “serve para thumb” vêm
              preenchidos. Só aceita pastas dentro de <code>canais</code> ou da biblioteca.
            </p>
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Pasta" className="min-w-56 flex-1">
                <Input value={folder} onChange={(e) => setFolder(e.target.value)} spellCheck={false} />
              </Field>
              <Button icon={FolderInput} busy={importFolder.isPending} disabled={!folder.trim() || busy} onClick={() => void askImport()}>
                Importar
              </Button>
            </div>
            <ErrorBox error={importFolder.error} title="Não foi possível importar" onClose={() => importFolder.reset()} />
          </div>

          {warnings.length ? (
            <Notice tone="warn" title="Avisos das poses">
              <ul className="list-disc pl-4">
                {warnings.map((w, i) => (
                  <li key={`${i}-${w}`}>{w}</li>
                ))}
              </ul>
            </Notice>
          ) : null}

          {p.poses.length ? <PresenterPreview presenter={p} pose={preview} /> : null}

          {badIds.length ? (
            <p role="alert" className="text-[13px] text-red-300">
              Ids de pose inválidos: {badIds.map((o) => o.id).join(', ')} (use letras minúsculas, números, “-” ou “_”).
            </p>
          ) : null}

          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-[13px] text-muted">
                Poses ({p.poses.length}, {p.poses.filter((o) => o.thumb_ok).length} para thumb)
              </div>
              <Button size="sm" icon={ImagePlus} busy={upload.isPending} disabled={busy} onClick={() => fileInput.current?.click()}>
                Enviar PNG
              </Button>
              <input
                ref={fileInput}
                type="file"
                accept="image/png"
                multiple
                hidden
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? []);
                  e.target.value = '';
                  if (files.length) upload.mutate(files);
                }}
              />
            </div>
            {p.poses.length ? (
              <ul className="grid gap-3 md:grid-cols-2">
                {p.poses.map((o) => (
                  <PoseEditor
                    key={o.id}
                    pose={o}
                    isDefault={o.id === p.default_pose}
                    previewing={preview?.id === o.id}
                    onChange={(next) => setPose(o.id, next)}
                    onRemove={() => set({ poses: p.poses.filter((x) => x.id !== o.id), enabled: p.poses.length > 1 ? p.enabled : false })}
                    onDefault={() => set({ default_pose: o.id })}
                    onPreview={() => setPreviewId(o.id)}
                  />
                ))}
              </ul>
            ) : (
              <Notice tone="info" icon={UserRound} title="Nenhuma pose ainda">
                Importe a pasta do canais ou envie PNG com fundo transparente (um por expressão: surpreso, apontando, rindo…).
              </Notice>
            )}
          </div>
        </>
      ) : null}
    </Sheet>
  );
};

/** Pequeno helper para quem só precisa ler o avatar do canal (o chip da cena e o pacote). */
export const usePresenter = (channelId: string | null | undefined) =>
  useQuery({
    queryKey: presenterQueryKey(channelId ?? ''),
    queryFn: () => presenterApi.get(channelId as string),
    enabled: Boolean(channelId),
    staleTime: 60_000,
    select: (r) => normalizePresenter(r.presenter),
  });
