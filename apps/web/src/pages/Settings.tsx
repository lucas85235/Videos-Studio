import { useIsFetching, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AudioLines,
  Captions,
  Check,
  CircleCheck,
  CircleDollarSign,
  CircleX,
  Copy,
  Cpu,
  Eraser,
  FileText,
  FolderOpen,
  HardDrive,
  Image,
  Info,
  KeyRound,
  Music,
  RotateCw,
  Trash2,
  TriangleAlert,
  Upload,
  type LucideIcon,
} from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { api, LANGUAGES, type LibraryMusic, MOOD_LABELS, type Provider } from '../api';
import { CREDENTIAL_FIELD_LABELS, label, PROVIDER_KIND_LABELS } from '../labels';
import { useProviders } from '../shell/data';
import {
  Badge,
  Button,
  Checkbox,
  ErrorBox,
  Field,
  formatBytes,
  formatMoney,
  IconButton,
  Input,
  Page,
  PageHeader,
  Section,
  Select,
  Skeleton,
  useConfirm,
  useToast,
} from '../ui';
import { VoicePicker } from '../voices';
import { AnchorLink, jumpTo, useCopy } from './ops/common';

type Status = { data_dir: string; offline: boolean; renderer_version: string; video_encoder: string; nvenc: boolean; providers_available: Record<string, string[]> };
type Costs = { by_provider: { provider: string; kind: string; units: number; cost_usd: number; calls: number }[]; total_usd: number };

const KINDS = ['llm', 'tts', 'image', 'align', 'publish'] as const;
type Kind = (typeof KINDS)[number];

const KIND_ICON: Record<Kind, LucideIcon> = { llm: FileText, tts: AudioLines, image: Image, align: Captions, publish: Upload };

const KIND_DESCRIPTION: Record<Kind, string> = {
  llm: 'Escreve roteiros, traduções, títulos e as edições pedidas ao agente. Usa o Claude da sua conta (Claude Code logado).',
  tts: 'Gera a narração. A voz grátis vem da chave do Gemini (Google AI Studio). Sem um provedor real, a voz sai em modo prévia (voz do Windows).',
  image: 'Gera as imagens das cenas. As imagens vêm do Codex (ChatGPT Plus). Sem um provedor real, as imagens saem em modo prévia (procedurais).',
  align: 'Sincroniza cada palavra da legenda com a narração.',
  publish: 'Envia os vídeos e agenda as publicações.',
};

const providerSection = (kind: string) => `provedores-${kind}`;

const kindChipLabel = (k: Kind) => (k === 'llm' ? 'Roteiro' : k === 'align' ? 'Sincronia' : label(PROVIDER_KIND_LABELS, k));

// "Ouvir vozes" vem logo depois de Voz (mesma ordem da página).
const SECTIONS: { id: string; label: string }[] = [
  ...KINDS.flatMap((k) => [
    { id: providerSection(k), label: kindChipLabel(k) },
    ...(k === 'tts' ? [{ id: 'ouvir-vozes', label: 'Ouvir vozes' }] : []),
  ]),
  { id: 'musicas', label: 'Músicas' },
  { id: 'armazenamento', label: 'Armazenamento' },
  { id: 'sistema', label: 'Sistema e custos' },
];

/* ------------------------------------------------------------------ */
/* Prontidão                                                           */
/* ------------------------------------------------------------------ */

/*
 * Cadeia padrão dos estilos, na ordem de uso (espelha DEFAULT_CHAINS em backend/studio/providers/registry.py e
 * VoiceSpec.chain / ArtSpec.image_chain em backend/studio/core/styles/schema.py). Provedores fora dela só entram
 * se um estilo pedir; os de último recurso (DEV_FALLBACKS) geram prévia, que a publicação bloqueia.
 */
const DEFAULT_CHAIN: Record<Kind, string[]> = {
  llm: ['claude_code'],
  tts: ['gemini_tts', 'google_chirp3hd', 'qwen3_tts_local'],
  image: ['codex_cli'],
  align: ['faster_whisper'],
  publish: ['youtube'],
};
const LAST_RESORT = new Set(['windows_sapi', 'mock_tts', 'placeholder', 'estimate']);
const OFFLINE_ONLY = new Set(['mock_llm']);

/** Campos que não são segredo (aparecem enquanto digita). */
const PLAIN_FIELDS = new Set(['contact']);

const isReal = (p: Provider) => p.available && p.commercial_use && !p.quota_exhausted_today;

/** Provedores do tipo, na ordem: cadeia padrão, depois os que só entram se o estilo pedir, depois os de último recurso. */
const orderProviders = (list: Provider[], kind: Kind) => {
  const chain = DEFAULT_CHAIN[kind];
  const rank = (p: Provider) => {
    const i = chain.indexOf(p.id);
    if (i >= 0) return i;
    if (LAST_RESORT.has(p.id) || OFFLINE_ONLY.has(p.id)) return 2000;
    return 1000;
  };
  return [...list].sort((a, b) => rank(a) - rank(b));
};

/** Texto do front para o YouTube (o motivo do backend cita ids técnicos). O passo a passo fica no cartão da seção. */
const publishHint = (p: Provider | undefined) => {
  if (!p) return 'Nenhum provedor de envio';
  if (p.available) return 'Envio direto ativo';
  return p.credentials?.configured ? 'Conecte o canal na aba Vídeo e publicação de um projeto' : 'Falta conectar o YouTube';
};

/** Nome curto do provedor, para as dicas da faixa de prontidão caberem em duas linhas. */
const SHORT_NAMES: Record<string, string> = {
  claude_code: 'Claude',
  gemini_tts: 'Gemini',
  google_chirp3hd: 'Chirp 3 HD',
  elevenlabs: 'ElevenLabs',
  qwen3_tts_local: 'Qwen3',
  codex_cli: 'Codex',
};
const shortName = (p: Provider) => SHORT_NAMES[p.id] ?? p.name.replace(/\s*\(.*\)\s*$/, '');
const names = (list: Provider[], short = true) => list.map((p) => (short ? shortName(p) : p.name)).join(', ');

type ReadyTone = 'good' | 'warn' | 'bad' | 'idle';
/** `full`: a dica sem abreviar (vai no title do bloco). */
type ReadyState = { tone: ReadyTone; value: string; hint: string; full?: string; quota?: boolean };

const readyState = (providers: Provider[] | undefined, kind: Kind, loading: boolean, failed: boolean): ReadyState => {
  if (!providers)
    return failed && !loading ? { tone: 'bad', value: 'Sem resposta', hint: 'Não deu para verificar' } : { tone: 'idle', value: 'Verificando…', hint: '' };
  const list = providers.filter((p) => p.kind === kind);
  const chain = DEFAULT_CHAIN[kind].map((id) => list.find((p) => p.id === id)).filter((p): p is Provider => Boolean(p));
  if (kind === 'publish') {
    const yt = chain[0];
    return yt?.available ? { tone: 'good', value: yt.name, hint: 'Envio direto ativo' } : { tone: 'warn', value: 'Só postagem manual', hint: publishHint(yt) };
  }
  const active = chain.find(isReal);
  const exhausted = chain.filter((p) => p.available && p.commercial_use && p.quota_exhausted_today);
  if (active) {
    const skipped = exhausted.filter((p) => chain.indexOf(p) < chain.indexOf(active));
    return {
      tone: 'good',
      value: active.name,
      hint: skipped.length ? `${names(skipped)} sem cota hoje; usando o próximo da lista` : 'Conteúdo real',
      full: skipped.length ? `${names(skipped, false)} sem cota hoje; usando o próximo da lista do estilo` : undefined,
    };
  }
  if (exhausted.length) {
    const back = exhausted.length > 1 ? 'voltam' : 'volta';
    return {
      tone: 'warn',
      value: 'Cota esgotada hoje',
      hint: `${names(exhausted)} ${back} amanhã; o app tenta o próximo da lista.`,
      full: `${names(exhausted, false)} ${back} amanhã; até lá, o app tenta os outros provedores da lista do estilo.`,
      quota: true,
    };
  }
  const outside = list.find((p) => !DEFAULT_CHAIN[kind].includes(p.id) && isReal(p));
  const first = chain[0];
  const configure = first ? `Configure o ${first.name}` : 'Configure um provedor real';
  const hint = outside ? `${configure}. ${outside.name} está pronto, mas só entra se o estilo pedir.` : configure;
  if (list.some((p) => p.available)) return { tone: 'warn', value: 'Só prévia', hint };
  return { tone: 'bad', value: 'Indisponível', hint: first?.reason || hint };
};

const READY_STYLE: Record<ReadyTone, { box: string; icon: LucideIcon; iconClass: string; value: string }> = {
  good: { box: 'border-emerald-400/30 bg-emerald-400/5 hover:border-emerald-400/50', icon: CircleCheck, iconClass: 'text-emerald-300', value: 'text-fg' },
  warn: { box: 'border-amber-300/40 bg-amber-300/10 hover:border-amber-300/60', icon: TriangleAlert, iconClass: 'text-amber-200', value: 'text-amber-200' },
  bad: { box: 'border-red-400/40 bg-red-400/10 hover:border-red-400/60', icon: CircleX, iconClass: 'text-red-300', value: 'text-red-200' },
  idle: { box: 'border-line bg-card', icon: Info, iconClass: 'text-subtle', value: 'text-muted' },
};

const READY_KINDS: { kind: Kind; title: string }[] = [
  { kind: 'llm', title: 'Roteiro' },
  { kind: 'tts', title: 'Voz' },
  { kind: 'image', title: 'Imagens' },
  { kind: 'publish', title: 'Publicação' },
];

const ReadinessStrip: React.FC<{ providers: Provider[] | undefined; loading: boolean; failed: boolean; refreshing: boolean; onRefresh: () => void }> = ({
  providers,
  loading,
  failed,
  refreshing,
  onRefresh,
}) => {
  const states = READY_KINDS.map((r) => ({ ...r, state: readyState(providers, r.kind, loading, failed) }));
  const missing = states.filter((s) => s.state.tone === 'warn' || s.state.tone === 'bad');
  const onlyQuota = missing.length > 0 && missing.every((s) => s.state.quota);
  const unknown = !providers && failed && !loading;
  const title = unknown
    ? 'Não foi possível verificar os provedores'
    : !providers
      ? 'Verificando os provedores…'
      : onlyQuota
        ? `Cota esgotada hoje em: ${missing.map((s) => s.title.toLowerCase()).join(', ')}`
        : missing.length
          ? 'Falta configurar para publicar conteúdo real'
          : 'Tudo pronto para publicar conteúdo real';
  return (
    <section aria-labelledby="prontidao-titulo" aria-busy={refreshing || undefined} className="mb-6 flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 id="prontidao-titulo" className="font-display text-[15px] font-semibold text-fg">
            {title}
          </h2>
          {providers && missing.length && !onlyQuota ? (
            <p className="text-[13px] text-muted">Clique num bloco para ir à seção e ver o que falta: {missing.map((s) => s.title.toLowerCase()).join(', ')}.</p>
          ) : null}
          {providers && onlyQuota ? <p className="text-[13px] text-muted">Não há nada a configurar: a cota volta amanhã.</p> : null}
        </div>
        <Button size="sm" variant="ghost" icon={RotateCw} busy={refreshing} onClick={onRefresh}>
          Verificar de novo
        </Button>
      </div>
      <ul className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {states.map(({ kind, title, state }) => {
          const s = READY_STYLE[state.tone];
          const Icon = s.icon;
          const KindIcon = KIND_ICON[kind];
          return (
            <li key={kind} className="min-w-0">
              <AnchorLink
                target={providerSection(kind)}
                aria-label={`${title}: ${state.value}. ${state.full ?? state.hint}`}
                className={`flex h-full min-w-0 flex-col gap-1 rounded-xl border px-3.5 py-3 transition-colors ${s.box}`}
              >
                <span className="flex items-center gap-1.5 text-[13px] text-muted">
                  <KindIcon size={16} aria-hidden="true" className="shrink-0" />
                  {title}
                  <Icon size={16} aria-hidden="true" className={`ml-auto shrink-0 ${s.iconClass}`} />
                </span>
                <span className={`truncate text-sm font-semibold ${s.value}`} title={state.value}>
                  {state.value}
                </span>
                {state.hint ? (
                  <span className="line-clamp-2 text-[13px] leading-snug text-subtle" title={state.full ?? state.hint}>
                    {state.hint}
                  </span>
                ) : null}
              </AnchorLink>
            </li>
          );
        })}
      </ul>
    </section>
  );
};

/* ------------------------------------------------------------------ */
/* Provedor                                                            */
/* ------------------------------------------------------------------ */

const ProviderStatus: React.FC<{ p: Provider }> = ({ p }) => {
  if (p.quota_exhausted_today) return <Badge tone="bad" icon={TriangleAlert}>Cota esgotada hoje</Badge>;
  if (p.available && p.commercial_use) return <Badge tone="good" icon={CircleCheck}>Pronto</Badge>;
  if (p.available) return <Badge tone="warn">Só prévia</Badge>;
  return <Badge>Indisponível</Badge>;
};

/** Lugar do provedor na cadeia padrão (ou por que ele fica fora dela). */
const chainBadge = (p: Provider, kind: Kind): { text: string; title: string; position?: number } | null => {
  const chain = DEFAULT_CHAIN[kind];
  const i = chain.indexOf(p.id);
  if (i >= 0) return chain.length > 1 ? { text: `${i + 1}º`, title: `${i + 1}º na ordem padrão dos estilos`, position: i + 1 } : null;
  if (kind === 'publish') return null;
  if (OFFLINE_ONLY.has(p.id)) return { text: 'Só no modo offline', title: 'Usado só quando o app roda sem internet' };
  if (LAST_RESORT.has(p.id))
    return { text: 'Último recurso', title: kind === 'align' ? 'Usado quando os outros falham' : 'Usado quando os outros falham; gera prévia, que não pode ser publicada' };
  if (p.id === 'pexels') return { text: 'Só para cenas com foto de banco', title: 'Entra apenas nas cenas que pedem foto de banco de imagens' };
  if (p.id === 'wikimedia_commons') return { text: 'Só para mídia real', title: 'Busca de fotos de licença livre sugeridas nas cenas; não gera imagem' };
  return { text: 'Só se o estilo pedir', title: 'Fora da ordem padrão: entra só se um estilo pedir' };
};

/** Trechos entre crases viram <code> (nome de comando, pasta etc.), sem as crases literais. */
const withCode = (text: string) =>
  text.split(/(`[^`]+`)/g).map((part, i) =>
    part.length > 2 && part.startsWith('`') && part.endsWith('`') ? (
      <code key={i} className="rounded bg-raised px-1 py-0.5 font-mono text-xs text-fg">
        {part.slice(1, -1)}
      </code>
    ) : (
      <React.Fragment key={i}>{part}</React.Fragment>
    ),
  );

/** Frase que cita variável de ambiente (ex.: VS_CLAUDE_MODEL): fica em "Detalhes técnicos", fora do texto principal. */
const TECH_SENTENCE = /\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b/;

const ProviderNotes: React.FC<{ text: string }> = ({ text }) => {
  const sentences = text.split(/(?<=[.!?])\s+/).filter(Boolean);
  const plain = sentences.filter((t) => !TECH_SENTENCE.test(t)).join(' ');
  const tech = sentences.filter((t) => TECH_SENTENCE.test(t));
  return (
    <>
      {plain ? <p className="text-[13px] leading-snug text-muted">{withCode(plain)}</p> : null}
      {tech.length ? (
        <details className="text-[13px] leading-snug text-subtle">
          <summary className="w-fit cursor-pointer rounded-sm hover:text-fg">Detalhes técnicos</summary>
          <p className="mt-1.5 [overflow-wrap:anywhere]">
            {tech.map((t, i) => (
              <React.Fragment key={i}>
                {i ? ' ' : ''}
                {withCode(t.replace(new RegExp(TECH_SENTENCE.source, 'g'), (m) => `\`${m}\``).replace(/``/g, '`'))}
              </React.Fragment>
            ))}
          </p>
        </details>
      ) : null}
    </>
  );
};

const ProviderCard: React.FC<{ p: Provider; kind: Kind }> = ({ p, kind }) => {
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: () => api.put<{ available: boolean; reason: string }>(`/api/providers/${p.id}/credentials`, { fields: values }),
    onSuccess: (result) => {
      setValues({});
      toast(
        result.available
          ? { tone: 'good', title: `Chave do ${p.name} salva`, description: 'O provedor está funcionando.' }
          : { tone: 'warn', title: `Chave do ${p.name} salva`, description: `Ainda indisponível: ${result.reason}` },
      );
      client.invalidateQueries({ queryKey: ['providers'] });
      client.invalidateQueries({ queryKey: ['status'] });
    },
  });
  const clear = useMutation({
    mutationFn: () => api.del(`/api/providers/${p.id}/credentials`),
    onSuccess: () => {
      save.reset();
      toast({ tone: 'good', title: `Chave do ${p.name} removida` });
      client.invalidateQueries({ queryKey: ['providers'] });
    },
  });
  const creds = p.credentials;
  const dirty = Object.values(values).some(Boolean);
  const masked = p.credential_fields.filter((f) => creds?.fields[f]);
  /** Provedor que guarda um contato, não um segredo (Wikimedia Commons): outras palavras na tela. */
  const contact = p.credential_fields.length > 0 && p.credential_fields.every((f) => PLAIN_FIELDS.has(f));
  const noun = contact ? 'contato' : 'chave';
  const partial = !creds?.configured && masked.length > 0;
  const removable = (creds?.configured || masked.length > 0) && !creds?.from_env;
  const badge = chainBadge(p, kind);
  const reason =
    kind === 'publish' ? (!p.credentials?.configured && p.credential_fields.length ? 'Falta conectar o YouTube: cole abaixo o ID e o segredo do cliente.' : publishHint(p)) : p.reason;

  const askClear = async () => {
    const realKind = contact ? null : p.kind === 'tts' ? 'a voz volta' : p.kind === 'image' ? 'as imagens voltam' : null;
    const ok = await confirm({
      title: `Remover ${contact ? 'o contato' : 'a chave'} do ${p.name}?`,
      description: contact
        ? 'A busca de fotos deixa de usar o Wikimedia Commons até você informar o contato de novo.'
        : `O app deixa de usar este provedor até você colar a chave de novo.${
            realKind ? ` Se não houver outro provedor real, ${realKind} ao modo prévia.` : ''
          }`,
      confirmLabel: `Remover ${noun}`,
      tone: 'danger',
    });
    if (ok) clear.mutate();
  };

  return (
    <article aria-label={p.name} className="flex min-w-0 flex-col gap-3 rounded-xl border border-line bg-card p-4">
      <div className="flex items-start gap-2.5">
        {badge?.position ? (
          <span
            title={badge.title}
            className="relative mt-0.5 flex h-6 min-w-6 shrink-0 items-center justify-center rounded-md border border-line-strong bg-raised px-1 text-xs font-semibold tabular-nums text-muted"
          >
            {badge.text}
            <span className="sr-only"> na ordem padrão dos estilos</span>
          </span>
        ) : null}
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <h3 className="text-sm font-semibold text-fg [overflow-wrap:anywhere]">{p.name}</h3>
          <div className="flex flex-wrap gap-1.5">
            <ProviderStatus p={p} />
            {p.free ? <Badge tone="info">Grátis</Badge> : null}
            {p.local ? <Badge>Roda neste computador</Badge> : null}
            {badge && !badge.position ? <Badge title={badge.title}>{badge.text}</Badge> : null}
            {!p.commercial_use && !p.available ? <Badge tone="warn">Só prévia</Badge> : null}
          </div>
        </div>
      </div>
      {!p.available && reason ? (
        <p className="flex gap-1.5 text-[13px] leading-snug text-muted">
          <Info size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-subtle" />
          <span className="min-w-0">{withCode(reason)}</span>
        </p>
      ) : null}
      {p.notes ? <ProviderNotes text={p.notes} /> : null}
      {p.cost_note ? (
        <p className="flex gap-1.5 text-[13px] leading-snug text-subtle">
          <CircleDollarSign size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span className="min-w-0">{p.cost_note}</span>
        </p>
      ) : null}
      {p.credential_fields.length ? (
        <form
          className="mt-auto flex flex-col gap-3 border-t border-line pt-3"
          autoComplete="off"
          onSubmit={(e) => {
            e.preventDefault();
            if (dirty && !save.isPending) save.mutate();
          }}
        >
          <div className="flex flex-wrap items-center gap-2">
            {creds?.from_env ? (
              <Badge tone="good" icon={KeyRound}>
                Usando a variável de ambiente {creds.from_env}
              </Badge>
            ) : creds?.configured ? (
              <Badge tone="good" icon={KeyRound}>
                {contact ? 'Contato salvo' : 'Chave salva'}
              </Badge>
            ) : partial ? (
              <Badge tone="warn" icon={KeyRound} title="Falta preencher um dos campos abaixo">
                Chave incompleta
              </Badge>
            ) : (
              <Badge icon={KeyRound}>{contact ? 'Sem contato' : 'Sem chave'}</Badge>
            )}
            {masked.length && !creds?.from_env ? (
              <span className="text-[13px] text-subtle">
                {masked.map((f) => (
                  <span key={f} className="mr-2 inline-block">
                    {label(CREDENTIAL_FIELD_LABELS, f)}: <span className="font-mono text-muted">{creds?.fields[f]}</span>
                  </span>
                ))}
              </span>
            ) : null}
          </div>
          {p.credential_fields.map((f, i) => (
            <Field
              key={f}
              label={label(CREDENTIAL_FIELD_LABELS, f)}
              hint={i === p.credential_fields.length - 1 && p.env_vars.length && !creds?.from_env ? `Ou defina ${p.env_vars.join(' ou ')} antes de abrir o app.` : undefined}
            >
              <Input
                type={PLAIN_FIELDS.has(f) ? 'text' : 'password'}
                autoComplete="off"
                spellCheck={false}
                placeholder={contact ? 'voce@exemplo.com ou https://seusite.com' : creds?.fields[f] ? 'Cole uma nova para trocar' : 'Cole aqui'}
                value={values[f] ?? ''}
                onChange={(e) => setValues({ ...values, [f]: e.target.value })}
              />
            </Field>
          ))}
          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" size="sm" variant={dirty ? 'primary' : 'secondary'} icon={Check} disabled={!dirty} busy={save.isPending}>
              {creds?.configured && !creds.from_env ? `Trocar ${noun}` : `Salvar ${noun}`}
            </Button>
            {removable ? (
              <Button size="sm" variant="ghost" icon={Trash2} busy={clear.isPending} onClick={() => void askClear()} className="hover:text-red-300">
                Remover {noun}
              </Button>
            ) : null}
          </div>
          {save.data && !dirty ? (
            save.data.available ? (
              <p role="status" className="flex items-start gap-1.5 text-[13px] text-emerald-300">
                <CircleCheck size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
                {contact ? 'Contato salvo: a busca de fotos já usa o Commons.' : 'Chave salva e funcionando.'}
              </p>
            ) : (
              <p role="status" className="flex items-start gap-1.5 text-[13px] text-amber-200">
                <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
                <span>Chave salva, mas o provedor ainda não está disponível: {save.data.reason}</span>
              </p>
            )
          ) : null}
          <ErrorBox error={save.error} title="Não foi possível salvar a chave" onClose={() => save.reset()} />
          <ErrorBox error={clear.error} title="Não foi possível remover a chave" onClose={() => clear.reset()} />
        </form>
      ) : null}
    </article>
  );
};

const ProviderSection: React.FC<{ kind: Kind; providers: Provider[] }> = ({ kind, providers }) => (
  <div id={providerSection(kind)}>
    <Section
      title={label(PROVIDER_KIND_LABELS, kind)}
      icon={KIND_ICON[kind]}
      description={
        <>
          {KIND_DESCRIPTION[kind]}
          {DEFAULT_CHAIN[kind].length > 1
            ? ' Os números são a ordem padrão (cada estilo pode mudá-la em Voz e Arte). Se um provedor falhar ou ficar sem cota, o app tenta o próximo da lista do estilo.'
            : kind !== 'publish' && providers.length > 1
              ? ' Cada estilo escolhe, em Voz e Arte, quais provedores usa.'
              : ''}
        </>
      }
    >
      {providers.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {orderProviders(providers, kind).map((p) => (
            <ProviderCard key={p.id} p={p} kind={kind} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted">Nenhum provedor deste tipo.</p>
      )}
    </Section>
  </div>
);

/* ------------------------------------------------------------------ */
/* Ouvir vozes                                                         */
/* ------------------------------------------------------------------ */

const VoiceSamplesSection: React.FC = () => {
  const [lang, setLang] = useState('pt-BR');
  return (
    <div id="ouvir-vozes">
      <Section
        title="Ouvir vozes"
        icon={AudioLines}
        description="A amostra é gerada na primeira vez que você ouve (gasta um pedido da cota do provedor de voz) e fica guardada. A voz de cada vídeo é escolhida no novo projeto ou na aba Prévia."
        actions={
          <label className="flex items-center gap-2 text-[13px] text-muted">
            Idioma
            <Select value={lang} onChange={(e) => setLang(e.target.value)}>
              {LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </Select>
          </label>
        }
      >
        <VoicePicker lang={lang} />
      </Section>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Músicas                                                             */
/* ------------------------------------------------------------------ */

// Nomes de pasta que `mood_key` (backend/studio/media/audio.py) reconhece; o resto vale como neutro.
const MOOD_FOLDERS = ['animado', 'inspirador', 'lofi', 'corporativo', 'divertido', 'calmo', 'epico', 'pulso', 'misterio', 'tensao', 'terror', 'sombrio'];

const MusicLibrarySection: React.FC = () => {
  const library = useQuery({ queryKey: ['library-music'], queryFn: () => api.get<LibraryMusic>('/api/library/music') });
  const open = useMutation({ mutationFn: () => api.post('/api/library/music/open') });
  const { copied, copy } = useCopy();
  const data = library.data;
  return (
    <div id="musicas">
      <Section
        title="Biblioteca de músicas"
        icon={Music}
        description="Suas próprias faixas para a trilha dos vídeos."
        actions={
          <>
            <Button size="sm" variant="ghost" icon={RotateCw} onClick={() => library.refetch()} busy={library.isFetching}>
              Atualizar
            </Button>
            <Button size="sm" icon={FolderOpen} onClick={() => open.mutate()} busy={open.isPending}>
              Abrir pasta
            </Button>
          </>
        }
      >
        {library.isLoading ? <Skeleton className="h-16" /> : null}
        {data ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={data.tracks.length ? 'good' : 'neutral'} icon={Music}>
                {data.tracks.length} {data.tracks.length === 1 ? 'faixa' : 'faixas'}
              </Badge>
              <span className="min-w-0 break-all font-mono text-[13px] text-muted">{data.dir}</span>
              <IconButton size="sm" label="Copiar o caminho da pasta" icon={copied === 'pasta' ? Check : Copy} onClick={() => void copy('pasta', data.dir, 'Caminho copiado')} />
            </div>
            <ul aria-label="Faixas por clima" className="flex flex-wrap gap-1.5">
              {data.moods.map((m) => {
                const n = data.counts[m] ?? 0;
                return (
                  <li
                    key={m}
                    title={n ? undefined : 'Sem faixas deste clima'}
                    className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs ${
                      n ? 'border-line-strong bg-raised text-fg' : 'border-dashed border-line-strong text-subtle'
                    }`}
                  >
                    {MOOD_LABELS[m] ?? m}
                    <span className="tabular-nums">{n}</span>
                  </li>
                );
              })}
            </ul>
          </>
        ) : null}
        <ol className="flex list-decimal flex-col gap-1 pl-5 text-[13px] leading-snug text-muted marker:text-subtle">
          <li>Abra a pasta e copie arquivos de áudio (mp3, wav, m4a, ogg ou flac).</li>
          <li>
            Separe em subpastas pelo clima (nomes de pasta, sem acento):{' '}
            {MOOD_FOLDERS.map((m, i) => (
              <React.Fragment key={m}>
                <code className="font-mono text-fg">{m}</code>
                {i < MOOD_FOLDERS.length - 2 ? ', ' : i === MOOD_FOLDERS.length - 2 ? ' ou ' : ''}
              </React.Fragment>
            ))}
            . O resto conta como neutro.
          </li>
          <li>No estilo, escolha a trilha “Suas faixas”. A faixa é repetida ou cortada para caber no vídeo, com fade e volume ajustados.</li>
        </ol>
        <p className="text-[13px] leading-snug text-subtle">
          Fonte grátis: a Biblioteca de áudio do YouTube Studio (filtre por “Atribuição não necessária”). Use só faixas com licença para o seu uso: música de terceiros
          sem licença gera reivindicação de Content ID.
        </p>
        <ErrorBox error={library.error ?? open.error} />
      </Section>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Armazenamento                                                       */
/* ------------------------------------------------------------------ */

type CacheReport = {
  dry_run: boolean;
  sizes: Record<string, number>;
  total: number;
  removed: Record<string, number>;
  freed: Record<string, number>;
  freed_total: number;
  kept_in_use: number;
  labels: Record<string, string>;
  old_versions: boolean;
  limit: number | null;
  total_after: number;
  over_limit: boolean;
  /** Categorias que apagam narrações e imagens (refazer gasta cota). */
  costly_categories: string[];
  /** Projetos cuja versão atual não deu para ler: nenhuma narração ou imagem sai. */
  unreadable_projects: string[];
  /** Publicações de antes do registro do que cada vídeo usa: o que foi criado até `legacy_until` fica. */
  legacy_publications: string[];
  legacy_until: string | null;
  aborted: boolean;
  policy: CachePolicy;
};

type CachePolicy = { limit_gb: number; old_versions: boolean; auto: boolean };

const KIND_SIZE_LABEL: Record<string, string> = {
  tts: 'Narrações',
  image: 'Imagens',
  align: 'Alinhamentos',
  music: 'Trilhas',
  sfx: 'Efeitos sonoros',
  mix: 'Mixagens',
  chunk: 'Blocos de render',
  final: 'Vídeos finais',
  thumbnail: 'Thumbnails',
  bundles: 'Renderer',
  tmp: 'Temporários',
};

// Rampa neutra/informativa: sem verde nem vermelho, que no guia significam sucesso e erro.
const BAR_COLORS = ['bg-sky-400', 'bg-sky-300/80', 'bg-indigo-300', 'bg-violet-300', 'bg-slate-400', 'bg-slate-500'];

const percent = (part: number, total: number) => (total ? Math.round((part / total) * 100) : 0);

const StorageSection: React.FC = () => {
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  // Prévia com ou sem narrações e imagens de versões antigas (custam cota para refazer: só com a caixa marcada).
  const [oldVersions, setOldVersions] = useState(false);
  const report = useQuery({
    queryKey: ['cache-report', oldVersions],
    queryFn: () => api.get<CacheReport>(`/api/maintenance/cache?old_versions=${oldVersions}`),
    staleTime: 60_000,
  });
  const clean = useMutation({
    mutationFn: () => api.post<CacheReport>('/api/maintenance/cache/clean', { old_versions: oldVersions }),
    onSuccess: (result) => {
      toast({ tone: 'good', title: `Cache limpo: ${formatBytes(result.freed_total)} liberados` });
      client.invalidateQueries({ queryKey: ['cache-report'] });
    },
  });
  const data = report.data;
  const sizes = Object.entries(data?.sizes ?? {})
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
  const shown = sizes.slice(0, BAR_COLORS.length);
  const others = sizes.slice(BAR_COLORS.length).reduce((n, [, v]) => n + v, 0);
  const segments = [
    ...shown.map(([k, v], i) => ({ key: k, label: KIND_SIZE_LABEL[k] ?? k, value: v, color: BAR_COLORS[i] })),
    ...(others ? [{ key: 'outros', label: 'Outros', value: others, color: 'bg-line-strong' }] : []),
  ];
  const total = data?.total ?? 0;
  // Lista pelo que é removido (há categorias sem bytes, como registros sem arquivo), para bater com a contagem.
  const freedEntries = Object.entries(data?.removed ?? {}).filter(([, n]) => n > 0);

  const askClean = async () => {
    // Números frescos antes de confirmar: o relatório em cache pode ter até um minuto.
    const fresh = (await report.refetch()).data ?? data;
    if (!fresh?.freed_total) {
      toast({ tone: 'info', title: 'Nada para limpar agora' });
      return;
    }
    const items = Object.values(fresh.removed).reduce((n, v) => n + v, 0);
    // Narrações e imagens que saem (pela opção de versões antigas ou pelo limite de espaço): refazer gasta cota.
    const costly = fresh.costly_categories.reduce((n, k) => n + (fresh.removed[k] ?? 0), 0);
    const costlyByLimit = fresh.removed.lru_costly ?? 0;
    const ok = await confirm({
      title: `Liberar ${formatBytes(fresh.freed_total)} do cache?`,
      description: (
        <div className="flex flex-col gap-2">
          <p>
            Apaga {items} {items === 1 ? 'item' : 'itens'} que nenhum projeto usa mais. Não dá para desfazer.
          </p>
          <ul className="flex flex-col gap-0.5 text-[13px]">
            {Object.entries(fresh.removed)
              .filter(([, n]) => n > 0)
              .map(([k, n]) => {
                const bytes = fresh.freed[k] ?? 0;
                return (
                  <li key={k} className="flex justify-between gap-3">
                    <span>{fresh.labels[k] ?? KIND_SIZE_LABEL[k] ?? k}</span>
                    <span className="tabular-nums text-fg">
                      {n}
                      {bytes > 0 ? ` · ${formatBytes(bytes)}` : ''}
                    </span>
                  </li>
                );
              })}
          </ul>
          {costly ? (
            <p className="flex items-start gap-1.5 text-[13px] text-amber-200">
              <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
              <span>
                {costly === 1 ? 'Sai 1 narração ou imagem' : `Saem ${costly} narrações e imagens`} que nenhuma versão atual nem publicação usa
                {costlyByLimit ? ` (${costlyByLimit} pelo limite de espaço${fresh.old_versions ? '' : ', mesmo com a caixa de versões antigas desmarcada'})` : ''}. Se
                precisar delas de novo, são geradas outra vez e gastam cota.
              </span>
            </p>
          ) : null}
          <p className="text-[13px] text-subtle">
            {fresh.old_versions || fresh.limit != null
              ? 'Narrações e imagens que as versões atuais e as publicações usam ficam, e os vídeos prontos em “outputs” não são tocados.'
              : 'Narrações, imagens e os vídeos prontos em “outputs” não são tocados.'}
          </p>
        </div>
      ),
      confirmLabel: 'Limpar cache',
      tone: 'danger',
    });
    if (ok) clean.mutate();
  };

  return (
    <div id="armazenamento">
      <Section
        title="Armazenamento"
        icon={HardDrive}
        description="O que as versões atuais e as publicações usam nunca sai, nem nada usado nas últimas 24 h. Narrações e imagens (custam cota para refazer) só saem com a opção de versões antigas ou pelo limite de espaço. Os vídeos prontos em “outputs” ficam."
        actions={
          <>
            <Button size="sm" variant="ghost" icon={RotateCw} onClick={() => report.refetch()} busy={report.isFetching && !clean.isPending}>
              Recalcular
            </Button>
            <Button size="sm" icon={Eraser} disabled={!data?.freed_total} busy={clean.isPending} onClick={() => void askClean()}>
              Limpar cache
            </Button>
          </>
        }
      >
        <Checkbox
          label="Incluir narrações e imagens de versões antigas"
          description="Apaga as que nenhuma versão atual nem publicação usa. Se um dia voltar a uma versão antiga, elas são geradas de novo (gasta cota)."
          checked={oldVersions}
          onChange={setOldVersions}
        />
        {report.isLoading ? <Skeleton className="h-20" /> : null}
        {data ? (
          <>
            <div className="flex flex-wrap gap-x-8 gap-y-2">
              <div>
                <div className="text-[13px] text-muted">Ocupado pelo cache</div>
                <div className="font-display text-xl font-bold tabular-nums text-fg">{formatBytes(total)}</div>
              </div>
              <div>
                <div className="text-[13px] text-muted">A limpeza libera</div>
                <div className={`font-display text-xl font-bold tabular-nums ${data.freed_total ? 'text-fg' : 'text-subtle'}`}>
                  {data.freed_total ? formatBytes(data.freed_total) : 'nada agora'}
                </div>
              </div>
            </div>
            {segments.length ? (
              <>
                <div aria-hidden="true" className="flex h-2 w-full overflow-hidden rounded-full bg-line">
                  {segments.map((s) => (
                    <div key={s.key} className={`${s.color} h-full`} style={{ width: `${(s.value / total) * 100}%` }} />
                  ))}
                </div>
                <ul aria-label="Espaço por tipo" className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
                  {segments.map((s) => (
                    <li key={s.key} className="flex items-center gap-2 text-[13px]">
                      <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-sm ${s.color}`} />
                      <span className="min-w-0 flex-1 truncate text-muted">{s.label}</span>
                      <span className="tabular-nums text-fg">{formatBytes(s.value)}</span>
                      <span className="w-10 text-right tabular-nums text-subtle">{percent(s.value, total)}%</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {freedEntries.length ? (
              <div className="rounded-lg border border-line bg-canvas p-3">
                <div className="mb-1.5 text-[13px] font-medium text-fg">O que a limpeza apaga</div>
                <ul className="flex flex-col gap-0.5 text-[13px] text-muted">
                  {freedEntries.map(([k, n]) => {
                    const bytes = data.freed[k] ?? 0;
                    return (
                      <li key={k} className="flex justify-between gap-3">
                        <span>{data.labels[k] ?? KIND_SIZE_LABEL[k] ?? k}</span>
                        <span className="tabular-nums">
                          {n} {n === 1 ? 'item' : 'itens'}
                          {bytes > 0 ? ` · ${formatBytes(bytes)}` : ''}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : null}
          </>
        ) : null}
        {data?.unreadable_projects.length && (data.old_versions || data.limit != null) ? (
          <p className="flex items-start gap-1.5 text-[13px] text-amber-200">
            <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
            <span>
              Nenhuma narração ou imagem sai enquanto não der para ler a versão atual destes projetos (abra cada um para conferir):{' '}
              {data.unreadable_projects.join(', ')}.
            </span>
          </p>
        ) : null}
        {data?.legacy_publications.length && data.legacy_until && (data.old_versions || data.limit != null) ? (
          <p className="flex items-start gap-1.5 text-[13px] text-subtle">
            <Info size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
            <span>
              Narrações e imagens criadas até {new Date(data.legacy_until).toLocaleDateString('pt-BR')} ficam: estas publicações são de antes do
              registro do que cada vídeo usa: {data.legacy_publications.join(', ')}.
            </span>
          </p>
        ) : null}
        {data?.over_limit && data.limit != null ? (
          <p className="flex items-start gap-1.5 text-[13px] text-amber-200">
            <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
            <span>
              Mesmo depois da limpeza o cache fica com {formatBytes(data.total_after)}, acima do limite de {formatBytes(data.limit)}: o resto está em uso
              pelas versões atuais e publicações ou foi usado nas últimas 24 h.
            </span>
          </p>
        ) : null}
        {data ? <CachePolicyForm policy={data.policy} /> : null}
        {clean.data ? (
          <p role="status" className="flex items-center gap-1.5 text-[13px] text-emerald-300">
            <CircleCheck size={14} aria-hidden="true" />
            Liberados {formatBytes(clean.data.freed_total)}.
          </p>
        ) : null}
        <ErrorBox error={report.error ?? clean.error} onClose={clean.error ? () => clean.reset() : undefined} />
      </Section>
    </div>
  );
};

/** Limite de espaço e limpeza automática (roda depois de cada build e ao abrir o app). */
const CachePolicyForm: React.FC<{ policy: CachePolicy }> = ({ policy }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const [limit, setLimit] = useState(policy.limit_gb ? String(policy.limit_gb) : '');
  const [auto, setAuto] = useState(policy.auto);
  const [oldVersions, setOldVersions] = useState(policy.old_versions);
  useEffect(() => {
    setLimit(policy.limit_gb ? String(policy.limit_gb) : '');
    setAuto(policy.auto);
    setOldVersions(policy.old_versions);
  }, [policy.limit_gb, policy.auto, policy.old_versions]);
  const parsed = limit.trim() === '' ? 0 : Number(limit.replace(',', '.'));
  // O servidor grava com duas casas: um valor positivo abaixo de 0,01 viraria 0 (sem limite) sem aviso.
  const invalid = !Number.isFinite(parsed) || parsed < 0 || (parsed > 0 && parsed < 0.01);
  const limitGb = invalid ? 0 : parsed;
  const autoOn = auto && limitGb > 0;
  const changed = limitGb !== policy.limit_gb || autoOn !== policy.auto || oldVersions !== policy.old_versions;
  const save = useMutation({
    mutationFn: () => api.put<CachePolicy>('/api/maintenance/cache/policy', { limit_gb: limitGb, auto: autoOn, old_versions: oldVersions }),
    onSuccess: (saved) => {
      toast({ tone: 'good', title: saved.limit_gb ? `Limite do cache: ${saved.limit_gb} GB` : 'Cache sem limite de espaço' });
      client.invalidateQueries({ queryKey: ['cache-report'] });
    },
  });
  return (
    <form
      className="flex flex-col gap-3 rounded-lg border border-line bg-canvas p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!invalid) save.mutate();
      }}
    >
      <div className="text-[13px] font-medium text-fg">Limite de espaço</div>
      <Field
        label="Tamanho máximo do cache (GB)"
        hint="Passou disso, a limpeza apaga pelo último uso o que não está protegido: primeiro renders e mixagens, e só depois narrações e imagens. Vazio = sem limite."
        error={invalid ? 'Use 0 (sem limite) ou um número a partir de 0,01.' : undefined}
      >
        <Input inputMode="decimal" placeholder="sem limite" value={limit} invalid={invalid} onChange={(e) => setLimit(e.target.value)} className="max-w-40" />
      </Field>
      <Checkbox
        label="Limpar sozinho quando passar do limite"
        description="Roda depois de cada prévia ou render e ao abrir o app, só quando não há outras tarefas na fila. Para agendar no Windows: videos-studio clean --auto."
        checked={autoOn}
        disabled={limitGb <= 0}
        onChange={setAuto}
      />
      <Checkbox
        label="Na limpeza sozinha, incluir narrações e imagens de versões antigas"
        checked={oldVersions}
        disabled={!autoOn}
        onChange={setOldVersions}
      />
      <div>
        <Button size="sm" type="submit" disabled={!changed || invalid} busy={save.isPending}>
          Salvar limite
        </Button>
      </div>
      <ErrorBox error={save.error} onClose={save.error ? () => save.reset() : undefined} />
    </form>
  );
};

/* ------------------------------------------------------------------ */
/* Sistema e custos                                                    */
/* ------------------------------------------------------------------ */

const ENCODER_LABELS: Record<string, string> = { h264_nvenc: 'Placa NVIDIA (H.264)', libx264: 'Processador (H.264)' };

const SystemSection: React.FC<{ providers: Provider[] | undefined }> = ({ providers }) => {
  const status = useQuery({ queryKey: ['status'], queryFn: () => api.get<Status>('/api/status') });
  const costs = useQuery({ queryKey: ['costs'], queryFn: () => api.get<Costs>('/api/costs') });
  const { copied, copy } = useCopy();
  const nameOf = (id: string) => providers?.find((p) => p.id === id)?.name ?? id;
  const rows = [...(costs.data?.by_provider ?? [])].sort((a, b) => b.cost_usd - a.cost_usd || b.calls - a.calls);
  return (
    <div id="sistema">
      <Section title="Sistema e custos" icon={Cpu}>
        {status.isLoading ? <Skeleton className="h-14" /> : null}
        {status.data ? (
          <dl className="grid gap-4 text-sm md:grid-cols-[minmax(0,2fr)_1fr_1fr_1fr]">
            <div className="min-w-0">
              <dt className="text-[13px] text-muted">Pasta de dados</dt>
              <dd className="mt-0.5 flex items-start gap-1">
                <span className="min-w-0 break-all font-mono text-[13px] text-fg">{status.data.data_dir}</span>
                <IconButton
                  size="sm"
                  label="Copiar o caminho da pasta de dados"
                  icon={copied === 'dados' ? Check : Copy}
                  onClick={() => void copy('dados', status.data!.data_dir, 'Caminho copiado')}
                  className="-mt-1"
                />
              </dd>
            </div>
            <div>
              <dt className="text-[13px] text-muted">Codificador de vídeo</dt>
              <dd className="mt-0.5 flex flex-wrap items-center gap-1.5 text-fg">
                <span title={status.data.video_encoder}>{ENCODER_LABELS[status.data.video_encoder] ?? 'Outro codificador'}</span>
                {status.data.nvenc ? (
                  <Badge tone="good" title="O vídeo é codificado pela placa de vídeo NVIDIA (mais rápido)">
                    Placa NVIDIA
                  </Badge>
                ) : null}
              </dd>
            </div>
            <div>
              <dt className="text-[13px] text-muted">Versão do renderer</dt>
              <dd className="mt-0.5 font-mono text-[13px] text-fg">{status.data.renderer_version}</dd>
            </div>
            <div>
              <dt className="text-[13px] text-muted">Gasto registrado</dt>
              <dd className="mt-0.5 font-semibold tabular-nums text-fg">{costs.data ? formatMoney(costs.data.total_usd) : '…'}</dd>
            </div>
          </dl>
        ) : null}
        <ErrorBox error={status.error ?? costs.error} />
        {rows.length ? (
          <div className="relative overflow-x-auto rounded-lg border border-line">
            <table className="w-full min-w-[560px] text-left text-[13px]">
              <caption className="sr-only">Uso por provedor</caption>
              <thead className="bg-raised text-muted">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">Provedor</th>
                  <th scope="col" className="px-3 py-2 font-medium">Tipo</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Chamadas</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Unidades</th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">Custo</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((c) => (
                  <tr key={`${c.provider}-${c.kind}`}>
                    <th scope="row" className="px-3 py-2 font-normal text-fg">{nameOf(c.provider)}</th>
                    <td className="px-3 py-2 text-muted">{label(PROVIDER_KIND_LABELS, c.kind)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-muted">{c.calls.toLocaleString('pt-BR')}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-muted">{c.units.toLocaleString('pt-BR')}</td>
                    <td className={`px-3 py-2 text-right tabular-nums ${c.cost_usd ? 'text-fg' : 'text-subtle'}`}>
                      {c.cost_usd ? formatMoney(c.cost_usd) : 'Sem custo registrado'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : costs.data ? (
          <p className="text-[13px] text-subtle">Nenhum uso registrado ainda.</p>
        ) : null}
      </Section>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Página                                                              */
/* ------------------------------------------------------------------ */

/** Acha o contêiner que rola (o <main>), como o jumpTo. */
const scrollParent = (el: HTMLElement) => {
  let node = el.parentElement;
  while (node && !/(auto|scroll)/.test(getComputedStyle(node).overflowY)) node = node.parentElement;
  return node;
};

/**
 * Seção atual da página (scrollspy): a primeira seção que cruza a faixa logo abaixo do cabeçalho fixo.
 * No fim da rolagem vale a última. Depois de um clique na navegação, a seção clicada vale até o usuário rolar.
 */
const useCurrentSection = (ids: string[], layoutKey: string) => {
  const [current, setCurrent] = useState(ids[0]);
  const locked = useRef<string | null>(null);
  const key = ids.join(',');
  useEffect(() => {
    const els = key
      .split(',')
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => Boolean(el));
    if (!els.length || typeof IntersectionObserver === 'undefined') return;
    const scroller = scrollParent(els[0]);
    const header = scroller?.querySelector<HTMLElement>('header.sticky');
    const top = Math.max(0, Math.round(header?.getBoundingClientRect().bottom ?? 0) + 8);
    const visible = new Set<string>();
    const atEnd = () =>
      scroller
        ? scroller.scrollTop > 0 && scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4
        : window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
    const pick = () => {
      if (locked.current) return setCurrent(locked.current);
      if (atEnd()) return setCurrent(els[els.length - 1].id);
      const first = els.find((el) => visible.has(el.id));
      if (first) setCurrent(first.id);
    };
    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) visible.add(e.target.id);
          else visible.delete(e.target.id);
        }
        pick();
      },
      { rootMargin: `-${top}px 0px -55% 0px` },
    );
    els.forEach((el) => observer.observe(el));
    // Rolagem feita pelo usuário (roda, toque, teclado) solta a seção travada pelo clique.
    const release = () => {
      locked.current = null;
    };
    const target: HTMLElement | Window = scroller ?? window;
    const onScroll = () => pick();
    target.addEventListener('scroll', onScroll, { passive: true });
    for (const ev of ['wheel', 'touchstart', 'keydown'] as const) target.addEventListener(ev, release, { passive: true });
    return () => {
      observer.disconnect();
      target.removeEventListener('scroll', onScroll);
      for (const ev of ['wheel', 'touchstart', 'keydown'] as const) target.removeEventListener(ev, release);
    };
  }, [key, layoutKey]);
  const choose = (id: string) => {
    locked.current = id;
    setCurrent(id);
  };
  return [current, choose] as const;
};

const SECTION_IDS = SECTIONS.map((s) => s.id);

/** Navegação de âncoras no visual de sublinhado das abas; marca a seção atual e rola até ela na horizontal. */
const SectionNav: React.FC<{ layoutKey: string }> = ({ layoutKey }) => {
  const [current, choose] = useCurrentSection(SECTION_IDS, layoutKey);
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const nav = navRef.current;
    const link = nav?.querySelector<HTMLElement>('[aria-current="location"]');
    if (!nav || !link) return;
    const left = link.offsetLeft;
    const right = left + link.offsetWidth;
    if (left < nav.scrollLeft || right > nav.scrollLeft + nav.clientWidth) nav.scrollTo({ left: Math.max(0, left - 16) });
  }, [current]);
  return (
    <nav ref={navRef} aria-label="Seções das configurações" className="relative -mx-1 -mb-3 flex w-full gap-1 overflow-x-auto overflow-y-hidden px-1">
      {SECTIONS.map((s) => {
        const on = s.id === current;
        return (
          <AnchorLink
            key={s.id}
            target={s.id}
            aria-current={on ? 'location' : undefined}
            onClick={() => choose(s.id)}
            className={`inline-flex min-h-8 shrink-0 items-center whitespace-nowrap rounded-t-md px-3 py-2 text-sm font-medium transition-[color,box-shadow] focus-visible:outline-offset-[-2px] ${
              on ? 'text-fg shadow-[inset_0_-2px_0_var(--color-accent)]' : 'text-muted hover:text-fg hover:shadow-[inset_0_-2px_0_var(--color-line-strong)]'
            }`}
          >
            {s.label}
          </AnchorLink>
        );
      })}
    </nav>
  );
};

// Consultas das seções abaixo: o salto por #hash espera todas assentarem para o alvo não sair do lugar.
const JUMP_KEYS = ['providers', 'library-music', 'cache-report', 'status'];

export const SettingsPage: React.FC = () => {
  const providers = useProviders();
  const client = useQueryClient();
  const { hash } = useLocation();
  const jumped = useRef('');
  const pending = useIsFetching({ predicate: (q) => JUMP_KEYS.includes(String(q.queryKey[0])) });
  const { refetch } = providers;

  // A barra lateral usa a mesma chave com staleTime de 60 s: ao abrir a página, confere de novo (login no Claude, Codex, YouTube…).
  useEffect(() => {
    void refetch();
  }, [refetch]);

  // Link direto para uma seção (ex.: /configuracoes#musicas), depois que as seções carregam (ou falham).
  const settled =
    !pending &&
    JUMP_KEYS.every((k) => {
      const st = client.getQueryState([k]);
      return Boolean(st) && st?.status !== 'pending';
    });
  useEffect(() => {
    if (!hash || jumped.current === hash) return;
    let id = '';
    try {
      id = decodeURIComponent(hash.slice(1));
    } catch {
      return;
    }
    const go = () => {
      jumped.current = hash;
      jumpTo(id);
    };
    // Com tudo carregado salta já; senão, espera (plano B: salta assim mesmo se alguma seção demorar demais).
    const t = window.setTimeout(go, settled ? 0 : 4000);
    return () => window.clearTimeout(t);
  }, [hash, settled]);

  return (
    <Page>
      <PageHeader
        title="Configurações"
        sticky
        description="As chaves ficam criptografadas neste computador. Configure os provedores que faltam para sair do modo prévia."
        meta={<SectionNav layoutKey={providers.data ? 'pronto' : providers.error ? 'erro' : 'carregando'} />}
      />
      <ReadinessStrip
        providers={providers.data}
        loading={providers.isLoading}
        failed={Boolean(providers.error)}
        refreshing={providers.isFetching}
        onRefresh={() => void providers.refetch()}
      />
      <ErrorBox error={providers.error} title="Não foi possível carregar os provedores" onRetry={() => providers.refetch()} className="mb-6" />
      <div className="flex flex-col gap-6">
        {providers.isLoading ? (
          <>
            <Skeleton className="h-48" />
            <Skeleton className="h-48" />
          </>
        ) : null}
        {providers.data ? (
          KINDS.map((kind) => (
            <React.Fragment key={kind}>
              <ProviderSection kind={kind} providers={providers.data.filter((p) => p.kind === kind)} />
              {kind === 'tts' ? <VoiceSamplesSection /> : null}
            </React.Fragment>
          ))
        ) : providers.error ? (
          <VoiceSamplesSection />
        ) : null}
        <MusicLibrarySection />
        <StorageSection />
        <SystemSection providers={providers.data} />
      </div>
    </Page>
  );
};
