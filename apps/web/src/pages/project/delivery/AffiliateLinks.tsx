import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Copy, ShoppingBag, TriangleAlert } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import type { ProjectDetail } from '../../../api';
import { type AffiliateMode, businessApi, type AffiliateOffer, OFFER_STATUS_LABELS, type ProjectAffiliate, type ProjectAffiliateInput } from '../../../apiBusiness';
import { Badge, Button, Checkbox, ErrorBox, Field, Input, SegmentedControl, Section, Skeleton } from '../../../ui';
import { useCopy } from '../../ops/common';

type Mode = AffiliateMode;

const CopyBlock: React.FC<{ id: string; label: string; text: string; copied: string | null; onCopy: (key: string, text: string, title: string) => void }> = ({
  id,
  label,
  text,
  copied,
  onCopy,
}) => (
  <div className="flex flex-col gap-1.5">
    <div className="flex items-center justify-between gap-2">
      <span className="text-sm font-medium text-fg">{label}</span>
      <Button size="sm" variant="ghost" icon={copied === id ? Check : Copy} onClick={() => onCopy(id, text, `${label} copiado`)}>
        {copied === id ? 'Copiado' : 'Copiar'}
      </Button>
    </div>
    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-canvas p-2 font-mono text-xs text-muted">{text}</pre>
  </div>
);

/** Painel "Links de afiliado" na aba Vídeo final: liga os links no vídeo, escolhe ofertas ou temas e mostra o que entra. */
export const AffiliateLinks: React.FC<{ detail: ProjectDetail; lang: string }> = ({ detail }) => {
  const projectId = detail.project.id;
  const channelId = detail.project.channel_id;
  const qc = useQueryClient();
  const { copied, copy } = useCopy();
  const key = ['affiliate', projectId, channelId];
  const query = useQuery({ queryKey: key, queryFn: () => businessApi.projectAffiliate(projectId), enabled: Boolean(channelId) });
  const save = useMutation({
    mutationFn: (body: ProjectAffiliateInput) => businessApi.saveProjectAffiliate(projectId, body),
    onSuccess: (data) => {
      qc.setQueryData<ProjectAffiliate>(key, data);
      if (channelId) qc.invalidateQueries({ queryKey: ['money', channelId] });
    },
  });
  const data = query.data;
  const [topics, setTopics] = useState('');
  const [tracking, setTracking] = useState('');
  useEffect(() => {
    if (!data) return;
    setTopics(data.topics.join(', '));
    setTracking(data.tracking_id);
  }, [data]);

  if (!channelId) return null; // sem canal não há ofertas (a escolha do canal fica no projeto)
  if (query.isLoading) return <Skeleton className="mt-4 h-24" />;
  if (query.error || !data) return <ErrorBox className="mt-4" error={query.error} title="Links de afiliado" onRetry={() => query.refetch()} />;

  const mode: Mode = data.mode;
  const active = data.available.filter((o) => o.status === 'ativa');
  // Guarda o id da oferta (não muda se o apelido for trocado); projetos antigos podem ter o apelido.
  const isChosen = (o: AffiliateOffer) => data.offer_ids.includes(o.id) || data.offer_ids.includes(o.slug);
  const toggleOffer = (o: AffiliateOffer, on: boolean) => {
    const rest = data.offer_ids.filter((s) => s !== o.id && s !== o.slug);
    save.mutate({ mode: 'ofertas', offer_ids: on ? [...rest, o.id] : rest });
  };
  const saveTopics = () => save.mutate({ mode: 'temas', topics: topics.split(',').map((t) => t.trim()).filter(Boolean), offer_ids: [] });

  return (
    <Section
      icon={ShoppingBag}
      title="Links de afiliado"
      className="mt-4"
      description="Os links entram no topo da descrição e o aviso legal no fim, ao enviar ao YouTube e no pacote manual."
      actions={data.enabled ? <Badge tone="good">Ligado</Badge> : <Badge>Desligado</Badge>}
    >
      {!data.available.length ? (
        <p className="text-sm text-muted">
          O canal ainda não tem ofertas.{' '}
          <Link to="/dinheiro" className="text-fg underline">
            Cadastre em Dinheiro
          </Link>
          .
        </p>
      ) : (
        <>
          <Checkbox
            label="Pôr links de afiliado neste vídeo"
            description={active.length ? `${active.length} oferta(s) ativa(s) no canal.` : 'Nenhuma oferta ativa no canal: ative uma em Dinheiro.'}
            checked={data.enabled}
            disabled={save.isPending}
            onChange={(on) => save.mutate({ enabled: on })}
          />
          {data.enabled ? (
            <>
              <SegmentedControl
                label="Como escolher as ofertas"
                value={mode}
                onChange={(m) => save.mutate(m === 'temas' ? { mode: m, offer_ids: [] } : { mode: m })}
                options={[
                  { id: 'temas', label: 'Por tema' },
                  { id: 'ofertas', label: 'Escolher ofertas' },
                ]}
              />
              {mode === 'temas' ? (
                <form
                  className="flex flex-wrap items-end gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    saveTopics();
                  }}
                >
                  <Field label="Temas do vídeo" hint='Separados por vírgula. Entram as ofertas ativas com "sempre" ou com um destes temas.' className="min-w-[220px] flex-1">
                    <Input value={topics} onChange={(e) => setTopics(e.target.value)} placeholder="compra, console" />
                  </Field>
                  <Button type="submit" size="sm" busy={save.isPending}>
                    Aplicar temas
                  </Button>
                </form>
              ) : (
                <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0">
                  <legend className="mb-1 p-0 text-sm font-medium text-fg">Ofertas (na ordem marcada)</legend>
                  {data.available.map((o) => (
                    <Checkbox
                      key={o.id}
                      label={o.name}
                      description={`${o.slug} · ${OFFER_STATUS_LABELS[o.status]}${o.status !== 'ativa' ? ' (só oferta ativa entra no vídeo)' : ''}`}
                      checked={isChosen(o)}
                      disabled={save.isPending || (o.status !== 'ativa' && !isChosen(o))}
                      onChange={(on) => toggleOffer(o, on)}
                    />
                  ))}
                  {!data.offer_ids.length ? <p className="text-[13px] text-muted">Nenhuma oferta marcada: o vídeo sai sem links.</p> : null}
                </fieldset>
              )}
              <form
                className="flex flex-wrap items-end gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  save.mutate({ tracking_id: tracking.trim() });
                }}
              >
                <Field
                  label="ID de rastreamento deste vídeo"
                  hint={data.tracking_id ? 'Os links da Amazon usam este ID.' : 'Vazio usa o ID padrão do canal. Crie o ID em Associados › Gerenciar IDs de rastreamento.'}
                  className="min-w-[220px] flex-1"
                >
                  <Input value={tracking} placeholder={data.suggested_tracking_id || 'meucanal-20'} onChange={(e) => setTracking(e.target.value.replace(/\s/g, ''))} />
                </Field>
                <Button type="submit" size="sm" busy={save.isPending} disabled={tracking.trim() === data.tracking_id}>
                  Salvar ID
                </Button>
              </form>

              {data.errors.map((err) => (
                <div key={err} role="alert" className="flex gap-2 rounded-lg border border-amber-300/35 bg-amber-300/10 p-3 text-sm text-amber-100">
                  <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
                  <span>{err} Sem correção, a descrição sai sem links.</span>
                </div>
              ))}
              {data.top ? (
                <div className="flex flex-col gap-3">
                  <CopyBlock id="top" label="Topo da descrição" text={data.top} copied={copied} onCopy={copy} />
                  <CopyBlock id="pinned" label="Comentário fixado" text={data.pinned} copied={copied} onCopy={copy} />
                  {data.end ? <CopyBlock id="end" label="Última linha (aviso)" text={data.end} copied={copied} onCopy={copy} /> : null}
                </div>
              ) : null}
              {data.checklist.length ? (
                <div className="flex flex-col gap-1.5">
                  <span className="text-sm font-medium text-fg">Antes de publicar</span>
                  <ul className="flex flex-col gap-1 text-[13px] text-muted">
                    {data.checklist.map((item) => (
                      <li key={item} className={item.startsWith('CONFERIR') ? 'text-amber-200' : undefined}>
                        • {item}
                        {item.startsWith('Short:') && data.parent_title ? ` (vídeo longo: ${data.parent_title})` : ''}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </>
          ) : null}
        </>
      )}
      <ErrorBox error={save.error} onClose={() => save.reset()} />
    </Section>
  );
};
