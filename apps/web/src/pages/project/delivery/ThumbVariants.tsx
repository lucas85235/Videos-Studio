// Painel "Miniaturas (teste A/B)" da aba Vídeo final (ADR 0003, bloco I): as 3 opções, a escolhida para o envio,
// a folha "do tamanho do YouTube" e a regra de quando usar o teste do YouTube Studio. Com pacote (título e thumb
// primeiro), as opções são o candidato escolhido e os aprovados: aparecem antes do render, a edição fica na etapa
// "Título e thumb" e aqui só dá para trocar o fundo por um frame do próprio vídeo.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Images, RefreshCw } from 'lucide-react';
import React, { useMemo, useState } from 'react';
import { fileUrl, type PackageCandidate, type ProjectDetail } from '../../../api';
import { packageApi, packageQueryKey } from '../../../apiPackage';
import { visualApi, type ThumbSpec } from '../../../apiVisual';
import { isActiveJob } from '../../../shell/data';
import { Button, ErrorBox, formatRelative, Section, Skeleton, useToast } from '../../../ui';
import { useJobDone } from '../proof/useJobDone';
import { ThumbCard, type SceneOption } from './thumbs/ThumbCard';

const thumbsKey = (projectId: string, lang: string) => ['thumbs', projectId, lang] as const;

/** Trocar o fundo do candidato por um frame do próprio vídeo (a imagem de uma cena): `media:<id da cena>`. */
const BackgroundFromScene: React.FC<{
  projectId: string;
  candidate: PackageCandidate;
  scenes: SceneOption[];
  disabled: boolean;
  onSaved: () => void;
}> = ({ projectId, candidate, scenes, disabled, onSaved }) => {
  const { toast } = useToast();
  const bg = candidate.layers.background;
  const current = bg.kind === 'media' && bg.ref?.startsWith('media:') ? bg.ref.slice('media:'.length) : '';
  const save = useMutation({
    mutationFn: (sceneId: string) =>
      packageApi.putCandidate(projectId, candidate.id, {
        layers: { ...candidate.layers, background: { ...bg, kind: 'media', ref: `media:${sceneId}`, t: null } },
      }),
    onSuccess: () => {
      toast({ tone: 'good', title: 'Fundo trocado', description: 'A prévia do candidato está sendo refeita. Depois, gere as miniaturas de novo.' });
      onSaved();
    },
    onError: (e) => toast({ tone: 'bad', title: 'Não foi possível trocar o fundo', description: e instanceof Error ? e.message : undefined }),
  });
  if (!scenes.length) return null;
  return (
    <label className="flex flex-col gap-1 text-xs text-subtle">
      Fundo: frame do vídeo
      <select
        className="rounded-md border border-line bg-canvas px-2 py-1 text-[13px] text-fg"
        value={scenes.some((s) => s.id === current) ? current : ''}
        disabled={disabled || save.isPending}
        onChange={(e) => {
          if (e.target.value) save.mutate(e.target.value);
        }}
      >
        <option value="">{current ? 'Outro fundo' : 'O do pacote'}</option>
        {scenes.map((s) => (
          <option key={s.id} value={s.id}>
            {s.label}
          </option>
        ))}
      </select>
    </label>
  );
};

export const ThumbVariants: React.FC<{ detail: ProjectDetail; lang: string; onEditPackage?: () => void }> = ({ detail, lang, onEditPackage }) => {
  const projectId = detail.doc.id;
  const client = useQueryClient();
  const { toast } = useToast();
  const [jobId, setJobId] = useState<string | null>(null);
  const state = useQuery({ queryKey: thumbsKey(projectId, lang), queryFn: () => visualApi.thumbs(projectId, lang) });

  const scenes: SceneOption[] = useMemo(() => {
    const order = new Map(detail.doc.segments.map((s, i) => [s.id, i]));
    return [...detail.doc.scenes]
      .sort((a, b) => (order.get(a.segment_id) ?? 1e9) - (order.get(b.segment_id) ?? 1e9) || a.part - b.part)
      .map((s, i) => ({ id: s.id, label: `Cena ${i + 1}` }));
  }, [detail.doc.scenes, detail.doc.segments]);

  const refresh = () => client.invalidateQueries({ queryKey: thumbsKey(projectId, lang) });
  const job = useJobDone(jobId, (j) => {
    setJobId(null);
    void refresh();
    if (j.status === 'failed') toast({ tone: 'bad', title: 'As miniaturas não saíram', description: j.error ?? undefined });
    else if (j.status === 'done') toast({ tone: 'good', title: 'Miniaturas prontas' });
  });
  const running = Boolean(job && isActiveJob(job)) || Boolean(jobId && !job);

  const make = useMutation({
    mutationFn: () => visualApi.makeThumbs(projectId, lang),
    onSuccess: (j) => setJobId(j.id),
  });
  const choose = useMutation({
    mutationFn: (n: number) => visualApi.chooseThumb(projectId, lang, n),
    onSuccess: (data) => client.setQueryData(thumbsKey(projectId, lang), data),
  });
  const save = useMutation({
    mutationFn: (spec: ThumbSpec[]) => visualApi.setThumbSpec(projectId, lang, spec),
    onSuccess: (data) => client.setQueryData(thumbsKey(projectId, lang), data),
  });

  const data = state.data;
  const busy = running || make.isPending || choose.isPending || save.isPending;
  const pkg = data?.package ?? null;
  const candidates = detail.doc.package?.candidates ?? [];
  const afterPackageEdit = () => {
    void client.invalidateQueries({ queryKey: ['project', projectId] });
    void client.invalidateQueries({ queryKey: packageQueryKey(projectId) });
    void refresh();
  };

  return (
    <Section
      icon={Images}
      title="Miniaturas (teste A/B)"
      description={
        pkg
          ? `O candidato escolhido no pacote e os aprovados para o A/B, em ${data?.size ?? ''}, até 2 MB.`
          : data
            ? `${data.max} opções em ${data.size}, até 2 MB. A 1ª é a recomendada.`
            : 'Três opções para o teste A/B.'
      }
      actions={
        <Button size="sm" icon={RefreshCw} busy={running || make.isPending} disabled={busy} onClick={() => make.mutate()}>
          {data?.variants.length ? 'Gerar de novo' : `Gerar as ${data?.max ?? 3}`}
        </Button>
      }
    >
      {state.isLoading ? <Skeleton className="h-40 rounded-lg" /> : null}
      <ErrorBox error={state.error} title="Não foi possível ler as miniaturas" onRetry={() => void state.refetch()} />
      <ErrorBox error={make.error ?? choose.error ?? save.error} title="Não foi possível salvar" />
      {running && job ? <p className="text-xs text-muted">{job.message || 'Gerando…'}</p> : null}
      {data ? (
        <>
          <p className="rounded-lg border border-line bg-canvas px-3 py-2 text-[13px] text-fg">
            {data.rule}
            {data.ab.median_impressions !== null ? (
              <span className="text-subtle">
                {' '}
                (mediana de {Math.round(data.ab.median_impressions).toLocaleString('pt-BR')} impressões em 7 dias, {data.ab.n}{' '}
                {data.ab.n === 1 ? 'vídeo longo' : 'vídeos longos'})
              </span>
            ) : (
              <span className="text-subtle"> (ainda sem impressões de 7 dias dos vídeos longos do canal)</span>
            )}
          </p>
          {data.ctr ? (
            <p
              role="status"
              className={`rounded-lg border px-3 py-2 text-[13px] ${
                data.ctr.verdict === 'trocar' ? 'border-amber-500/40 bg-amber-500/10 text-amber-200' : 'border-line bg-canvas text-muted'
              }`}
            >
              {data.ctr.text}
            </p>
          ) : null}
          {data.stale ? (
            <p role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[13px] text-amber-200">
              As miniaturas são de antes da última mudança na capa ou nas imagens das cenas. Clique em “Gerar de novo”. Até lá, o envio usa a capa do vídeo final.
            </p>
          ) : null}
          {pkg ? (
            <p className="text-xs text-subtle">
              Vêm do pacote (etapa “Título e thumb”).{' '}
              {onEditPackage ? (
                <button type="button" className="text-accent underline-offset-2 hover:underline" onClick={onEditPackage}>
                  Editar no pacote
                </button>
              ) : null}
            </p>
          ) : null}
          <ul className="m-0 grid list-none grid-cols-1 gap-3 p-0 sm:grid-cols-3">
            {data.spec.map((spec, i) => {
              const variant = data.variants.find((v) => v.n === i + 1) ?? null;
              const candidate = spec.candidate_id ? candidates.find((c) => c.id === spec.candidate_id) ?? null : null;
              const packageMode = spec.candidate_id
                ? {
                    previewImage: pkg?.candidates.find((c) => c.id === spec.candidate_id)?.image ?? candidate?.image ?? null,
                    onEdit: onEditPackage,
                    extra: candidate ? (
                      <BackgroundFromScene projectId={projectId} candidate={candidate} scenes={scenes} disabled={busy} onSaved={afterPackageEdit} />
                    ) : null,
                  }
                : undefined;
              return (
                <ThumbCard
                  key={i}
                  n={i + 1}
                  label={spec.label ?? variant?.label ?? `Opção ${i + 1}`}
                  spec={spec}
                  variant={variant}
                  chosen={Boolean(variant) && data.chosen === i + 1}
                  goesOut={!data.stale && !(variant?.errors.length ?? 0)}
                  version={data.made_at ?? ''}
                  isLong={data.is_long}
                  scenes={scenes}
                  templates={data.templates}
                  disabled={busy}
                  choosing={choose.isPending && choose.variables === i + 1}
                  saving={save.isPending}
                  packageMode={packageMode}
                  onChoose={() => choose.mutate(i + 1)}
                  onSave={(edited) => save.mutate(data.spec.map((s, k) => (k === i ? edited : { scene_id: s.scene_id, text: s.text, template: s.template })))}
                />
              );
            })}
          </ul>
          {data.variants.length && data.made_at ? (
            <p className="text-xs text-subtle">Geradas {formatRelative(data.made_at)}. Editou? Clique em “Gerar de novo”.</p>
          ) : null}
          {data.sheet_small ? (
            <figure className="m-0 flex flex-col gap-1">
              <img
                src={`${fileUrl(data.sheet_small)}?v=${encodeURIComponent(data.made_at ?? '')}`}
                alt="As miniaturas do tamanho que aparecem no YouTube"
                className="max-w-full self-start rounded-md border border-line"
              />
              <figcaption className="text-xs text-subtle">
                Do tamanho do YouTube (168, 246 e 360 px), com a duração no canto. Dá para ler?{' '}
                {data.sheet_big ? (
                  <a href={fileUrl(data.sheet_big)} target="_blank" rel="noreferrer" className="text-accent underline-offset-2 hover:underline">
                    Ver grandes
                  </a>
                ) : null}
              </figcaption>
            </figure>
          ) : null}
        </>
      ) : null}
    </Section>
  );
};
