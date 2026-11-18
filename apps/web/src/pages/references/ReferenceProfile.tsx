// Perfil de edição medido da referência (ADR 0004 §3.2 e §3.11): mistura de material, ritmo, densidades por minuto,
// elementos recorrentes e a confiança de cada campo. Daqui saem o estilo ("Criar estilo") e o projeto novo
// ("Novo projeto a partir desta referência": usa um estilo já feito dela ou cria um, e abre o formulário de projeto
// novo com ele escolhido, onde o usuário diz o tema, o canal, a voz e o piloto automático). Os dois só com o perfil
// medido: sem ele o estilo sairia sem a gramática da referência. Embaixo, a aderência ao perfil dos projetos que já
// usam um estilo feito dela.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Clapperboard, Gauge, Microscope, Palette, RotateCw } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { fileUrl } from '../../api';
import type { NewProjectState } from '../NewProject';
import { type EditProfile, type ProfileFieldMeta, type ReferenceVideo, visualApi } from '../../apiVisual';
import { label, MATERIAL_LABELS } from '../../labels';
import { isActiveJob, useJobs } from '../../shell/data';
import { Badge, type BadgeTone, Button, Checkbox, ErrorBox, Field, Input, Select, Skeleton, useToast } from '../../ui';
import { defaultStyleName, ReferenceStyleButton } from './ReferenceStyle';

const STYLE_NAME_MAX = 120;
const NEW_STYLE = '';

const num = (v: number | null | undefined, digits = 1) =>
  (v ?? 0).toLocaleString('pt-BR', { maximumFractionDigits: digits, minimumFractionDigits: 0 });
const pct = (v: number | null | undefined) => `${Math.round((v ?? 0) * 100)}%`;

const ORIGIN_LABELS: Record<ProfileFieldMeta['origin'], string> = {
  medido: 'medido',
  visão: 'pela visão',
  llm: 'pela IA',
  padrão: 'padrão',
  usuário: 'do usuário',
};

/** Selo de confiança do campo (sem entrada no `meta`: padrão, confiança 0). */
const Confidence: React.FC<{ meta?: ProfileFieldMeta }> = ({ meta }) => {
  const c = meta?.confidence ?? 0;
  const tone: BadgeTone = c >= 0.7 ? 'good' : c >= 0.4 ? 'warn' : 'neutral';
  return (
    <Badge tone={tone} title={`Confiança ${pct(c)} (${ORIGIN_LABELS[meta?.origin ?? 'padrão']})`}>
      {c > 0 ? pct(c) : 'sem medida'}
    </Badge>
  );
};

/** A confiança do campo ("pace.median_s") ou, sem ela, a do bloco ("pace"). */
const metaOf = (meta: EditProfile['meta'], field: string): ProfileFieldMeta | undefined => meta[field] ?? meta[field.split('.')[0]];

type Row = { label: string; value: string; field: string };

const MetricRows: React.FC<{ title: string; rows: Row[]; meta: EditProfile['meta'] }> = ({ title, rows, meta }) => (
  <div className="flex min-w-0 flex-col gap-1.5">
    <h4 className="text-xs font-medium uppercase tracking-wide text-subtle">{title}</h4>
    <dl className="flex flex-col gap-1">
      {rows.map((r) => (
        <div key={r.field + r.label} className="flex items-center justify-between gap-2 text-[13px]">
          <dt className="min-w-0 truncate text-muted">{r.label}</dt>
          <dd className="flex shrink-0 items-center gap-1.5 tabular-nums text-fg">
            {r.value}
            <Confidence meta={metaOf(meta, r.field)} />
          </dd>
        </div>
      ))}
    </dl>
  </div>
);

const MixBars: React.FC<{ mix: Record<string, number>; labels: Record<string, string> }> = ({ mix, labels }) => {
  const entries = Object.entries(mix)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
  if (!entries.length) return <p className="text-[13px] text-subtle">Sem medida da mistura (precisa da análise a fundo).</p>;
  return (
    <ul className="flex flex-col gap-1.5" aria-label="Mistura de material">
      {entries.map(([key, share]) => (
        <li key={key} className="grid grid-cols-[8.5rem_minmax(0,1fr)_3rem] items-center gap-2 text-[13px]">
          <span className="truncate text-muted">{label(labels, key)}</span>
          <span className="h-2 overflow-hidden rounded-sm bg-canvas">
            <span className={`block h-full ${key === 'ai_illustration' ? 'bg-fuchsia-500' : 'bg-sky-400'}`} style={{ width: `${Math.max(2, share * 100)}%` }} />
          </span>
          <span className="text-right tabular-nums text-fg">{pct(share)}</span>
        </li>
      ))}
    </ul>
  );
};

/** O perfil medido, só leitura. */
export const ProfileView: React.FC<{ profile: EditProfile }> = ({ profile: p }) => {
  const mix = Object.keys(p.materials.mix).length ? p.materials.mix : p.materials.prov_mix;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-medium uppercase tracking-wide text-subtle">Mistura de material</h4>
          <Confidence meta={metaOf(p.meta, 'materials.mix')} />
        </div>
        <MixBars mix={mix} labels={MATERIAL_LABELS} />
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <MetricRows
          title="Ritmo"
          meta={p.meta}
          rows={[
            { label: 'Plano mediano', value: `${num(p.pace.median_s, 2)} s`, field: 'pace.median_s' },
            { label: 'Planos (p10–p90)', value: `${num(p.pace.p10_s, 1)}–${num(p.pace.p90_s, 1)} s`, field: 'pace.p10_s' },
            { label: 'Variação (CV)', value: num(p.pace.cv, 2), field: 'pace.cv' },
            { label: 'Cortes por minuto', value: num(p.pace.cuts_per_min), field: 'pace.cuts_per_min' },
            { label: 'Cortes/min no gancho', value: num(p.pace.hook_cuts_per_min), field: 'pace.hook_cuts_per_min' },
            { label: 'Micro-planos/min', value: num(p.pace.micro_per_min), field: 'pace.micro_per_min' },
            { label: 'Maior trecho parado', value: `${num(p.pace.max_still_s)} s`, field: 'pace.max_still_s' },
          ]}
        />
        <MetricRows
          title="Câmera e insertos (por minuto)"
          meta={p.meta}
          rows={[
            { label: 'Punch', value: num(p.camera.punch_per_min), field: 'camera.punch_per_min' },
            { label: 'Tremida', value: num(p.camera.shake_per_min), field: 'camera.shake_per_min' },
            { label: 'Zoom lento (tempo)', value: pct(p.camera.slow_zoom_share), field: 'camera.slow_zoom_share' },
            { label: 'Texto que pula', value: num(p.overlays.text_pop_per_min), field: 'overlays.text_pop_per_min' },
            { label: 'Memes', value: num(p.overlays.meme_per_min), field: 'overlays.meme_per_min' },
            { label: 'Setas e marca-texto', value: num(p.overlays.annot_per_min), field: 'overlays.annot_per_min' },
            { label: 'Eventos visuais', value: num(p.pace.events_per_min), field: 'pace.events_per_min' },
          ]}
        />
        <MetricRows
          title="Som e fala"
          meta={p.meta}
          rows={[
            { label: 'Efeitos sonoros/min', value: num(p.audio.sfx_per_min), field: 'audio.sfx_per_min' },
            { label: 'SFX no corte', value: pct(p.audio.sfx_on_cut_share), field: 'audio.sfx_on_cut_share' },
            { label: 'Quedas da trilha/min', value: num(p.audio.drops_per_min), field: 'audio.drops_per_min' },
            {
              label: 'Trilha nos respiros',
              value: p.audio.music_level_db == null ? '—' : `${num(p.audio.music_level_db)} dB`,
              field: 'audio.music_level_db',
            },
            { label: 'Palavras por minuto', value: p.speech.wpm ? num(p.speech.wpm, 0) : '—', field: 'speech.wpm' },
            { label: 'Trocas de material/min', value: num(p.materials.switch_per_min), field: 'materials.switch_per_min' },
          ]}
        />
      </div>
      {p.speech.tone_tags.length || p.humor.patterns.length ? (
        <div className="flex flex-col gap-1.5 text-[13px]">
          <h4 className="text-xs font-medium uppercase tracking-wide text-subtle">Tom e humor (padrões, nunca frases)</h4>
          {p.speech.tone_tags.length ? (
            <div className="flex flex-wrap gap-1.5">
              {p.speech.tone_tags.map((t) => (
                <Badge key={t}>{t}</Badge>
              ))}
            </div>
          ) : null}
          {p.humor.patterns.length ? (
            <ul className="list-disc pl-5 text-muted">
              {p.humor.patterns.map((h) => (
                <li key={h}>{h}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {p.recurring.length ? (
        <div className="flex flex-col gap-1.5">
          <h4 className="text-xs font-medium uppercase tracking-wide text-subtle">Elementos que voltam</h4>
          <ul className="flex flex-wrap gap-2">
            {p.recurring.map((r) => (
              <li key={r.id} className="flex items-center gap-2 rounded-md border border-line px-2 py-1 text-xs text-muted">
                {r.thumb ? <img src={fileUrl(r.thumb)} alt="" className="h-8 w-8 rounded object-cover" loading="lazy" /> : null}
                <span>
                  {r.kind === 'mascot' ? 'Mascote' : r.kind === 'logo' ? 'Logo' : r.kind === 'meme' ? 'Meme' : 'Elemento'} · {r.count}×
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
};

/** "Novo projeto a partir desta referência": escolhe um estilo já feito dela (sem duplicar) ou cria um, e abre o
 * formulário de projeto novo com ele escolhido. O tema, o canal, a voz e o piloto automático são os do fluxo normal: o
 * título da referência não vira o tema (o roteiro sairia sobre o vídeo do outro criador). */
const NewProjectFromReference: React.FC<{ reference: ReferenceVideo }> = ({ reference }) => {
  const navigate = useNavigate();
  const client = useQueryClient();
  const { toast } = useToast();
  const made = useQuery({ queryKey: ['reference-adherence', reference.id], queryFn: () => visualApi.adherence(reference.id), staleTime: 30_000 });
  const existing = made.data?.styles ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const choice = picked ?? existing[0]?.id ?? NEW_STYLE;
  const [styleName, setStyleName] = useState<string | null>(null);
  const styleNameValue = styleName ?? reference.suggested_style_name ?? defaultStyleName(reference.display_title ?? reference.title);
  const [useLlm, setUseLlm] = useState(true);
  const title = reference.display_title ?? reference.title;
  const open = (styleId: string, name: string) => {
    const state: NewProjectState = { fromReference: { styleId, styleName: name, title } };
    navigate('/novo', { state });
  };
  const create = useMutation({
    mutationFn: () => visualApi.styleFromReference(reference.id, { use_llm: useLlm, name: styleName === null ? undefined : styleName.trim() || undefined }),
    onSuccess: async (result) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['styles'] }),
        client.invalidateQueries({ queryKey: ['reference-adherence', reference.id] }),
      ]);
      toast({ tone: 'good', title: 'Estilo criado', description: `“${result.style.name}” está em Meus estilos.` });
      open(result.style.id, result.style.name);
    },
  });
  const go = () => {
    const style = existing.find((st) => st.id === choice);
    if (style) open(style.id, style.name);
    else create.mutate();
  };
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line p-3">
      <p className="text-[13px] text-muted">
        O projeto novo usa um estilo feito desta referência. Do vídeo dela vem só a fórmula (ritmo, mistura de fontes, densidades): o tema, o canal e a voz
        você escolhe no próximo passo.
      </p>
      {made.isPending ? <Skeleton className="h-9" /> : null}
      {existing.length ? (
        <Field label="Estilo" hint="Já há estilo feito desta referência: usar o mesmo não espalha a aderência por estilos repetidos.">
          <Select value={choice} onChange={(e) => setPicked(e.target.value)} className="max-w-md">
            {existing.map((st) => (
              <option key={st.id} value={st.id}>
                Usar “{st.name}”
              </option>
            ))}
            <option value={NEW_STYLE}>Criar outro estilo</option>
          </Select>
        </Field>
      ) : null}
      {choice === NEW_STYLE && !made.isPending ? (
        <>
          <Field label="Nome do estilo" hint="Dá para mudar depois no editor de estilos.">
            <Input value={styleNameValue} maxLength={STYLE_NAME_MAX} onChange={(e) => setStyleName(e.target.value)} className="max-w-md" />
          </Field>
          <Checkbox
            label="Deixar a IA completar o que as medidas não dizem"
            description="Arte, câmera e trilha. Os campos medidos não mudam."
            checked={useLlm}
            onChange={setUseLlm}
          />
        </>
      ) : null}
      <div>
        <Button variant="primary" icon={Clapperboard} busy={create.isPending} disabled={made.isPending} onClick={go}>
          {choice === NEW_STYLE ? 'Criar o estilo e escolher o tema' : 'Escolher o tema do projeto'}
        </Button>
      </div>
      <ErrorBox error={create.error} title="Não foi possível criar o estilo" />
    </div>
  );
};

const BAND_TONE: Record<string, BadgeTone> = { verde: 'good', amarelo: 'warn', vermelho: 'bad' };

/** Aderência ao perfil dos projetos que usam um estilo feito desta referência. */
const AdherenceList: React.FC<{ referenceId: string }> = ({ referenceId }) => {
  const query = useQuery({ queryKey: ['reference-adherence', referenceId], queryFn: () => visualApi.adherence(referenceId), staleTime: 30_000 });
  if (query.isPending) return <Skeleton className="h-10" />;
  if (query.isError) return <ErrorBox error={query.error} title="Não deu para medir os projetos" onRetry={() => query.refetch()} />;
  const { projects } = query.data;
  if (!projects.length) return <p className="text-[13px] text-subtle">Nenhum projeto usa ainda um estilo feito desta referência.</p>;
  return (
    <ul className="flex flex-col gap-1.5">
      {projects.map((p) => (
        <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 text-[13px]">
          <Link to={`/projetos/${p.id}`} className="min-w-0 truncate text-fg underline-offset-2 hover:underline">
            {p.title}
          </Link>
          <span className="flex items-center gap-1.5">
            {p.adherence != null ? <Badge tone={p.adherence >= 70 ? 'good' : 'warn'}>Aderência {p.adherence}</Badge> : <Badge>Sem nota</Badge>}
            {p.index != null ? (
              <Badge tone={BAND_TONE[p.band ?? ''] ?? 'neutral'} icon={Gauge}>
                Cara de IA {p.index}
              </Badge>
            ) : null}
          </span>
        </li>
      ))}
    </ul>
  );
};

type Panel = 'style' | 'project' | null;

const SKIPPED_LABELS: Record<string, string> = { speech: 'fala', humor: 'humor', vision: 'visão' };

/** Bloco do perfil na página Referências (dentro do detalhe da referência analisada). */
export const ReferenceProfile: React.FC<{ reference: ReferenceVideo }> = ({ reference }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const [panel, setPanel] = useState<Panel>(null);
  const jobs = useJobs();
  const running = (jobs.data ?? []).find((j) => j.kind === 'reference_profile' && isActiveJob(j) && j.payload.reference_id === reference.id);
  const key = ['reference-profile', reference.id];
  const query = useQuery({ queryKey: key, queryFn: () => visualApi.profile(reference.id), refetchInterval: running ? 4000 : false });
  // O job não tem projeto: quando ele some da lista de ativos, busca o perfil de novo.
  const wasRunning = useRef(Boolean(running));
  useEffect(() => {
    if (wasRunning.current && !running) void client.invalidateQueries({ queryKey: key });
    wasRunning.current = Boolean(running);
  });
  const measure = useMutation({
    mutationFn: (deep: boolean) => visualApi.measureProfile(reference.id, deep),
    onSuccess: async (_job, deep) => {
      await client.invalidateQueries({ queryKey: ['jobs'] });
      toast({ tone: 'info', title: deep ? 'Analisando a fundo (fala e visão)' : 'Medindo o perfil de edição' });
    },
  });
  const profile = query.data?.profile ?? null;
  const notes = Object.entries(query.data?.notes.skipped ?? {});
  const toggle = (next: Panel) => setPanel((cur) => (cur === next ? null : next));
  // Sem o perfil medido (referência analisada antes do ADR 0004) ou com a medida rodando, o estilo sairia sem a
  // gramática e as fontes da referência: os dois botões esperam.
  const blocked = Boolean(running) || !profile;
  const blockedWhy = running ? 'Espere a medida do perfil terminar.' : 'Meça o perfil primeiro.';
  return (
    <section aria-label="Perfil de edição" className="flex flex-col gap-3 rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-fg">Perfil de edição</h3>
          <p className="text-xs text-subtle">
            {profile
              ? `${profile.quick ? 'Modo rápido (cortes, movimento e som)' : 'Análise a fundo (com fala e visão)'}${profile.measured_at ? ` · medido em ${new Date(profile.measured_at).toLocaleDateString('pt-BR')}` : ''}`
              : 'Ainda não medido.'}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {!profile ? (
            <Button size="sm" icon={RotateCw} busy={measure.isPending && measure.variables === false} disabled={Boolean(running)} onClick={() => measure.mutate(false)}>
              Medir o perfil
            </Button>
          ) : null}
          <Button
            size="sm"
            icon={Microscope}
            busy={Boolean(running) || (measure.isPending && measure.variables === true)}
            disabled={Boolean(running)}
            title="Transcreve a fala e classifica os planos (mais demorado)."
            onClick={() => measure.mutate(true)}
          >
            Analisar a fundo
          </Button>
          <Button
            size="sm"
            icon={Palette}
            aria-expanded={panel === 'style' && !blocked}
            disabled={blocked}
            title={blocked ? blockedWhy : undefined}
            onClick={() => toggle('style')}
          >
            Criar estilo
          </Button>
          <Button
            size="sm"
            variant="primary"
            icon={Clapperboard}
            aria-expanded={panel === 'project' && !blocked}
            disabled={blocked}
            title={blocked ? blockedWhy : undefined}
            onClick={() => toggle('project')}
          >
            Novo projeto a partir desta referência
          </Button>
        </div>
      </div>
      {running ? <p className="text-[13px] text-muted">{running.message || 'Medindo…'}</p> : null}
      <ErrorBox error={measure.error} title="Não foi possível medir o perfil" onClose={() => measure.reset()} />
      {!profile && !running && query.isSuccess ? (
        <p className="text-[13px] text-muted">Meça o perfil primeiro: o estilo e o projeto copiam a edição da referência a partir dele.</p>
      ) : null}
      {panel === 'style' && !blocked ? <ReferenceStyleButton reference={reference} /> : null}
      {panel === 'project' && !blocked ? <NewProjectFromReference reference={reference} /> : null}
      {query.isPending ? <Skeleton className="h-24" /> : null}
      {query.isError ? <ErrorBox error={query.error} title="Não deu para ler o perfil" onRetry={() => query.refetch()} /> : null}
      {profile ? <ProfileView profile={profile} /> : null}
      {notes.length ? (
        <ul className="flex flex-col gap-0.5 text-xs text-subtle">
          {notes.map(([k, v]) => (
            <li key={k}>
              Ficou de fora ({SKIPPED_LABELS[k] ?? k}): {v}
            </li>
          ))}
        </ul>
      ) : null}
      {profile ? (
        <div className="flex flex-col gap-1.5">
          <h4 className="text-xs font-medium uppercase tracking-wide text-subtle">Projetos com estilo desta referência</h4>
          <AdherenceList referenceId={reference.id} />
        </div>
      ) : null}
    </section>
  );
};
