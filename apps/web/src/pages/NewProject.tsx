import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AudioLines, Check, ExternalLink, FileText, History, Languages, Palette, Plus, TriangleAlert, Tv, Wand2 } from 'lucide-react';
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { api, type Channel, type ChannelYouTube, DEFAULT_STYLE_ID, LANGUAGES, type StyleItem, type VoiceChoice } from '../api';
import { langLabel } from '../labels';
import { isBoolean, isStringArray, readLocal, writeLocal } from '../shell/data';
import {
  Button,
  Checkbox,
  ErrorBox,
  Field,
  FieldGroup,
  formatRelative,
  Input,
  Page,
  PageHeader,
  Section,
  Select,
  Skeleton,
  Textarea,
  useConfirm,
  useToast,
} from '../ui';
import { useVoiceCatalog, VoicePicker } from '../voices';
import { durationLabel, formatLabel } from './projects/meta';
import { FormatGlyph, StepNumber } from './projects/parts';
import { StylePicker } from './projects/StylePicker';

const TOPIC_ID = 'novo-projeto-tema';
const BLOCKER_ID = 'novo-projeto-bloqueio';
const DEFAULT_STYLE = DEFAULT_STYLE_ID;
const DEFAULT_PRIMARY = 'pt-BR';
const TITLE_MAX = 200; // limite do backend (CreateProject.title)

/**
 * Nome curto do provedor de voz para o resumo: "Gemini 3.8 Flash TTS (Google AI Studio)" → "Gemini",
 * "Google Cloud TTS" → "Google Cloud". Corta o que está entre parênteses e para na versão ou em "TTS".
 */
const providerShortName = (name: string) => {
  const words = name.replace(/\(.*?\)/g, ' ').trim().split(/\s+/);
  const end = words.findIndex((w, i) => i > 0 && (/\d/.test(w) || /^tts$/i.test(w)));
  return (end === -1 ? words : words.slice(0, end)).join(' ') || name;
};

/* ------------------------------------------------------------------ */
/* Rascunho                                                            */
/* ------------------------------------------------------------------ */

// Sem useBlocker (BrowserRouter): qualquer navegação dentro do app desmontaria o formulário.
// O rascunho fica no localStorage e volta quando a página abre de novo.
const DRAFT_KEY = 'vulto.rascunhoNovoProjeto';

type Touched = { style: boolean; langs: boolean };

type Draft = {
  title: string;
  topic: string;
  brief: string;
  styleId: string;
  channelId: string;
  primary: string;
  extras: string[];
  autopilot: boolean;
  voice: VoiceChoice | null;
  /** A voz do rascunho veio do padrão do canal (rascunhos antigos não têm o campo). */
  voiceFromChannel?: boolean;
  touched: Touched;
  savedAt: string;
};

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);
const isStr = (v: unknown): v is string => typeof v === 'string';

const isDraft = (v: unknown): v is Draft => {
  if (!isObj(v)) return false;
  const voiceOk = v.voice === null || (isObj(v.voice) && isStr(v.voice.provider) && isStr(v.voice.voice));
  const touchedOk = isObj(v.touched) && isBoolean(v.touched.style) && isBoolean(v.touched.langs);
  return (
    isStr(v.title) &&
    isStr(v.topic) &&
    isStr(v.brief) &&
    isStr(v.styleId) &&
    isStr(v.channelId) &&
    isStr(v.primary) &&
    v.primary.length > 0 &&
    isStringArray(v.extras) &&
    isBoolean(v.autopilot) &&
    voiceOk &&
    (v.voiceFromChannel === undefined || isBoolean(v.voiceFromChannel)) &&
    touchedOk &&
    isStr(v.savedAt)
  );
};

const removeLocal = (key: string) => {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* sem armazenamento: nada a apagar */
  }
};

const savedWhen = (iso: string) => {
  const rel = formatRelative(iso);
  if (!rel || /^(em |amanhã|agora)/.test(rel)) return 'agora há pouco';
  return /^(há|ontem)/.test(rel) ? rel : `em ${rel}`;
};

/* ------------------------------------------------------------------ */
/* Canal: o que ele preencheu, para poder desfazer                     */
/* ------------------------------------------------------------------ */

type ChannelApplied = {
  channelName: string;
  /** Estilo que estava antes de o canal trocar (só se o canal trocou e o usuário não mexeu depois). */
  prevStyle?: string;
  /** Idiomas que estavam antes de o canal trocar. */
  prevLangs?: { primary: string; extras: string[] };
};

const appliedParts = (a: ChannelApplied) =>
  [a.prevStyle !== undefined ? 'o estilo' : null, a.prevLangs ? 'os idiomas' : null].filter(Boolean).join(' e ');

/** Link para outra página do app que abre em outra aba, para o formulário não se perder. */
/** Para onde vão os envios ao YouTube dos projetos do canal escolhido. */
const youtubeNote = (y: ChannelYouTube) => {
  const name = y.handle || y.title;
  if (y.token === 'ok' || y.token === 'global') return `Os envios ao YouTube vão para o canal ${name}.`;
  if (y.token === 'public') return `O canal ${name} foi ligado só pelo link: para publicar nele, vincule com o login do Google em Canais.`;
  return `O canal ${name} está sem acesso ao YouTube: vincule de novo em Canais para publicar nele.`;
};

const NewTabLink: React.FC<{ to: string; children: React.ReactNode }> = ({ to, children }) => (
  <Link
    to={to}
    target="_blank"
    rel="noopener"
    className="inline-flex items-center gap-1 text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg"
  >
    {children}
    <ExternalLink size={14} aria-hidden="true" className="shrink-0" />
    <span className="sr-only"> (abre em outra aba)</span>
  </Link>
);

/** Estado da navegação vindo de "Novo projeto a partir desta referência": o estilo feito dela já escolhido. */
export type NewProjectState = { fromReference?: { styleId: string; styleName: string; title: string } };

const isRefState = (v: unknown): v is Required<NewProjectState> => {
  if (!v || typeof v !== 'object') return false;
  const ref = (v as NewProjectState).fromReference;
  return Boolean(ref) && typeof ref?.styleId === 'string' && typeof ref.styleName === 'string' && typeof ref.title === 'string';
};

export const NewProjectPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [fromReference] = useState(() => (isRefState(location.state) ? location.state.fromReference : null));
  const client = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });

  const [draft] = useState(() => readLocal<Draft | null>(DRAFT_KEY, null, (v): v is Draft | null => isDraft(v)));
  const [restoredAt, setRestoredAt] = useState<string | null>(draft?.savedAt ?? null);

  const [title, setTitle] = useState(draft?.title ?? '');
  const [topic, setTopic] = useState(draft?.topic ?? '');
  const [topicTouched, setTopicTouched] = useState(false);
  const [brief, setBrief] = useState(draft?.brief ?? '');
  const [styleId, setStyleId] = useState(fromReference?.styleId ?? draft?.styleId ?? DEFAULT_STYLE);
  const [channelId, setChannelId] = useState(draft?.channelId ?? '');
  const [primary, setPrimary] = useState(draft?.primary ?? DEFAULT_PRIMARY);
  const [extras, setExtras] = useState<string[]>(draft?.extras ?? []);
  const [autopilot, setAutopilot] = useState(draft?.autopilot ?? true);
  const [voice, setVoice] = useState<VoiceChoice | null>(fromReference && fromReference.styleId !== draft?.styleId ? null : (draft?.voice ?? null));
  // A voz atual veio da voz final padrão do canal (não foi escolhida à mão): trocar de canal pode trocá-la.
  const [voiceFromChannel, setVoiceFromChannel] = useState(draft?.voiceFromChannel ?? false);
  const pickVoice = (v: VoiceChoice | null) => {
    setVoice(v);
    setVoiceFromChannel(false);
  };
  // O que o usuário já escolheu à mão não é sobrescrito pelo canal.
  const [touched, setTouched] = useState<Touched>(() => {
    const base = draft?.touched ?? { style: false, langs: false };
    // O estilo feito da referência foi escolhido de propósito: o padrão do canal não o troca.
    return fromReference ? { ...base, style: true } : base;
  });
  const [applied, setApplied] = useState<ChannelApplied | null>(null);
  const [channelNote, setChannelNote] = useState<string | null>(null);

  const langs = useMemo(() => [primary, ...extras.filter((c) => c !== primary)], [primary, extras]);
  const styleList = styles.data ?? [];
  // Se o estilo guardado não existe (padrão ausente nesta instalação, ou apagado em outra aba), vale o primeiro da lista.
  const effectiveStyleId = styles.data?.some((s) => s.style.id === styleId) ? styleId : (styles.data?.[0]?.style.id ?? styleId);
  const style = useMemo(() => styles.data?.find((s) => s.style.id === effectiveStyleId), [styles.data, effectiveStyleId]);
  // A voz escolhida vale só para o estilo em que foi escolhida (cada estilo tem sua cadeia de provedores).
  const chosenVoice = effectiveStyleId === styleId ? voice : null;
  const channel = channels.data?.find((c) => c.id === channelId);

  // Mesma consulta do VoicePicker (mesma chave): só lê o cache para mostrar o nome da voz no resumo.
  const voiceCatalog = useVoiceCatalog(primary, { purpose: 'final' });
  const defaultVoice = style?.style.voice.voice ?? null;
  const effectiveVoice: VoiceChoice | null =
    chosenVoice ?? (style && defaultVoice ? { provider: style.style.voice.chain[0], voice: defaultVoice } : null);
  /** Nome humano da voz; null enquanto o catálogo não tem a voz (nunca o id técnico do provedor). */
  const voiceName = (choice: VoiceChoice | null) => {
    if (!choice) return null;
    const group = voiceCatalog.data?.providers.find((g) => g.id === choice.provider);
    const found = group?.voices.find((v) => v.id === choice.voice);
    return found ? `${found.label}${group ? ` · ${providerShortName(group.name)}` : ''}` : null;
  };
  const effectiveVoiceName = voiceName(effectiveVoice);

  const create = useMutation({
    mutationFn: () => {
      const base = title.trim() || topic.trim();
      const finalTitle = base.length > TITLE_MAX ? `${base.slice(0, TITLE_MAX - 1).trimEnd()}…` : base || 'Novo vídeo';
      return api.post<{ project: { id: string } }>('/api/projects', {
        title: finalTitle,
        topic,
        brief,
        style_id: effectiveStyleId,
        channel_id: channelId || null,
        languages: langs,
        autopilot,
        voice: chosenVoice,
        // O título digitado vira o 1º candidato do pacote (o tema sozinho, não).
        seed_candidate: Boolean(title.trim()),
      });
    },
    onSuccess: (data) => {
      removeLocal(DRAFT_KEY);
      client.invalidateQueries({ queryKey: ['projects'] });
      toast({
        tone: 'good',
        title: 'Projeto criado',
        description: autopilot ? 'O roteiro está sendo escrito. Ele para e espera sua aprovação.' : undefined,
      });
      navigate(`/projetos/${data.project.id}`);
    },
  });

  const dirty = Boolean(topic.trim() || brief.trim() || title.trim());

  // Rascunho: grava a cada mudança enquanto há texto; some quando o formulário fica vazio ou o projeto é criado.
  useEffect(() => {
    if (create.isSuccess) return;
    if (!dirty) {
      removeLocal(DRAFT_KEY);
      return;
    }
    const value: Draft = {
      title,
      topic,
      brief,
      styleId,
      channelId,
      primary,
      extras,
      autopilot,
      voice,
      voiceFromChannel,
      touched,
      savedAt: new Date().toISOString(),
    };
    writeLocal(DRAFT_KEY, value);
  }, [dirty, create.isSuccess, title, topic, brief, styleId, channelId, primary, extras, autopilot, voice, voiceFromChannel, touched]);

  // Fechar ou recarregar a aba: o rascunho já está salvo, mas o aviso do navegador evita a surpresa.
  useEffect(() => {
    if (!dirty || create.isSuccess) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [dirty, create.isSuccess]);

  // O erro de criação fica no fluxo da página, logo acima do rodapé: rola até ele para ficar à vista.
  const createErrorRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (create.error) createErrorRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [create.error]);

  const discardDraft = async () => {
    const ok = await confirm({
      title: 'Descartar o rascunho?',
      description: 'O tema, o título, o briefing e as escolhas deste formulário voltam ao padrão. Não dá para desfazer.',
      confirmLabel: 'Descartar rascunho',
      tone: 'danger',
    });
    if (!ok) return;
    removeLocal(DRAFT_KEY);
    setTitle('');
    setTopic('');
    setTopicTouched(false);
    setBrief('');
    setStyleId(DEFAULT_STYLE);
    setChannelId('');
    setPrimary(DEFAULT_PRIMARY);
    setExtras([]);
    setAutopilot(true);
    pickVoice(null);
    setTouched({ style: false, langs: false });
    setApplied(null);
    setChannelNote(null);
    setRestoredAt(null);
    document.getElementById(TOPIC_ID)?.focus();
  };

  const pickStyle = (id: string) => {
    if (id === effectiveStyleId) return;
    setStyleId(id);
    pickVoice(null);
    setTouched((t) => ({ ...t, style: true }));
    setApplied((a) => (a && a.prevStyle !== undefined ? { ...a, prevStyle: undefined } : a));
    setChannelNote(null);
  };

  const pickPrimary = (code: string) => {
    setPrimary(code);
    setExtras((list) => list.filter((c) => c !== code));
    pickVoice(null);
    setTouched((t) => ({ ...t, langs: true }));
    setApplied((a) => (a?.prevLangs ? { ...a, prevLangs: undefined } : a));
    setChannelNote(null);
  };

  const toggleExtra = (code: string) => {
    setExtras((list) => (list.includes(code) ? list.filter((c) => c !== code) : [...list, code]));
    setTouched((t) => ({ ...t, langs: true }));
    setApplied((a) => (a?.prevLangs ? { ...a, prevLangs: undefined } : a));
    setChannelNote(null);
  };

  const pickChannel = (id: string) => {
    setChannelId(id);
    // 1. Desfaz o que o canal anterior preencheu (e que o usuário não mexeu depois).
    let nextStyle = effectiveStyleId;
    let nextPrimary = primary;
    let nextExtras = extras;
    if (applied?.prevStyle !== undefined) nextStyle = applied.prevStyle;
    if (applied?.prevLangs) {
      nextPrimary = applied.prevLangs.primary;
      nextExtras = applied.prevLangs.extras;
    }
    const undone = applied ? appliedParts(applied) : '';

    // 2. Aplica o padrão do novo canal, guardando o que estava antes.
    const c = channels.data?.find((x) => x.id === id);
    const next: ChannelApplied | null = c ? { channelName: c.name } : null;
    if (c && next) {
      if (!touched.style && c.default_style_id && c.default_style_id !== nextStyle && styles.data?.some((s) => s.style.id === c.default_style_id)) {
        next.prevStyle = nextStyle;
        nextStyle = c.default_style_id;
      }
      if (!touched.langs && c.languages.length) {
        next.prevLangs = { primary: nextPrimary, extras: nextExtras };
        nextPrimary = c.languages[0];
        nextExtras = c.languages.slice(1).filter((x) => x !== nextPrimary);
      }
    }
    const nextApplied = next && (next.prevStyle !== undefined || next.prevLangs) ? next : null;

    // 3. Voz final: a padrão do canal entra se você não escolheu outra à mão (estilo e idioma iguais mantêm a sua).
    // Só vale no idioma em que foi escolhida (o primeiro do canal, em Canais): projeto em outro idioma não a recebe.
    const manualVoice = voice !== null && !voiceFromChannel && nextStyle === effectiveStyleId && nextPrimary === primary;
    const channelVoice = c?.voice_defaults?.final;
    if (!manualVoice) {
      if (channelVoice?.provider && channelVoice.voice && nextPrimary === c?.languages[0]) {
        setVoice({ provider: channelVoice.provider, voice: channelVoice.voice });
        setVoiceFromChannel(true);
      } else pickVoice(null);
    }
    setStyleId(nextStyle);
    setPrimary(nextPrimary);
    setExtras(nextExtras);
    setApplied(nextApplied);
    setChannelNote(
      !nextApplied && undone && applied
        ? `Voltamos ${undone} ao que estava antes de escolher o canal ${applied.channelName}.`
        : null,
    );
  };

  const channelHint = applied
    ? `Preenchemos ${appliedParts(applied)} com o padrão do canal ${applied.channelName}. Você pode mudar abaixo.`
    : (channelNote ?? undefined);

  const missingTopic = !topic.trim();
  const blocker = styles.isLoading
    ? 'Carregando os estilos…'
    : styles.error
      ? 'Os estilos não carregaram. Use "Tentar de novo" na seção 2.'
      : !style
        ? 'Nenhum estilo disponível. Crie um em Estilos.'
        : missingTopic
          ? 'Escreva o tema para continuar.'
          : null;
  const topicError = topicTouched && !topic.trim() ? 'Escreva o tema do vídeo. É o único campo obrigatório.' : null;

  const extraCount = langs.length - 1;
  const primaryOptions = LANGUAGES.some((l) => l.code === primary) ? LANGUAGES : [{ code: primary, label: langLabel(primary) }, ...LANGUAGES];
  // Idiomas que vieram do canal (ou do rascunho) e não estão na lista padrão também viram chips, para poder tirá-los.
  const extraOptions = [
    ...LANGUAGES,
    ...extras.filter((c) => !LANGUAGES.some((l) => l.code === c)).map((c) => ({ code: c, label: langLabel(c) })),
  ].filter((l) => l.code !== primary);
  const langsSentence = extraCount
    ? `Serão ${langs.length} vídeos: 1 original e ${extraCount} ${extraCount === 1 ? 'versão traduzida' : 'versões traduzidas'}.`
    : 'Só um vídeo, no idioma principal.';

  const voiceSummary = effectiveVoice ? (effectiveVoiceName ? `voz ${effectiveVoiceName}` : chosenVoice ? 'voz escolhida' : 'voz padrão do estilo') : null;
  const summary = [
    style ? formatLabel(style.style.format) : null,
    style ? durationLabel(style.style.format.duration.target_s) : null,
    style?.style.name,
    `${langLabel(primary)}${extraCount ? ` + ${extraCount} ${extraCount === 1 ? 'idioma' : 'idiomas'}` : ''}`,
    voiceSummary,
    channel ? `canal ${channel.name}` : null,
  ].filter(Boolean) as string[];
  const shortSummary = [
    style ? formatLabel(style.style.format) : null,
    `${langLabel(primary)}${extraCount ? ` +${extraCount}` : ''}`,
    style?.style.name,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <Page width="narrow">
      <PageHeader
        title="Novo projeto"
        breadcrumb={[{ label: 'Projetos', to: '/' }, { label: 'Novo projeto' }]}
        description="Escolha o formato, o tema e os idiomas. O roteiro para e espera sua revisão antes de qualquer gasto com voz ou imagens."
      />

      {fromReference ? (
        <div className="mb-4 flex items-start gap-2 rounded-lg border border-line bg-raised px-3 py-2 text-[13px] text-muted">
          <Palette size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span>
            Estilo “{fromReference.styleName}”, feito da referência “{fromReference.title}”: dela vem só a fórmula (ritmo, mistura de fontes, densidades). Diga
            o tema do seu vídeo.
          </span>
        </div>
      ) : null}
      {restoredAt ? (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-sky-400/35 bg-sky-400/10 px-3 py-2 text-[13px] text-sky-200">
          <span className="flex min-w-0 items-center gap-2">
            <History size={16} aria-hidden="true" className="shrink-0" />
            Rascunho restaurado: você deixou este formulário {savedWhen(restoredAt)}.
          </span>
          <span className="flex shrink-0 items-center gap-1">
            <Button size="sm" variant="ghost" onClick={discardDraft}>
              Descartar rascunho
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setRestoredAt(null)}>
              Continuar
            </Button>
          </span>
        </div>
      ) : null}

      <div className="flex flex-col gap-4">
        <Section
          icon={Tv}
          title={
            <>
              <StepNumber n={1} />
              Canal
            </>
          }
          description="Opcional. O canal já sabe o estilo e os idiomas que costuma usar e preenche essas escolhas."
        >
          {channels.error ? (
            <ErrorBox error={channels.error} title="Não foi possível carregar os canais" onRetry={() => channels.refetch()} />
          ) : channels.isLoading ? (
            <Skeleton className="h-8 w-full sm:w-80" />
          ) : channels.data?.length ? (
            <>
              <Field label="Para qual canal é este vídeo?" hint={channelHint}>
                <Select value={channelId} onChange={(e) => pickChannel(e.target.value)} className="w-full sm:w-80">
                  <option value="">Sem canal</option>
                  {channels.data.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                      {c.youtube?.handle ? ` (${c.youtube.handle})` : ''}
                    </option>
                  ))}
                </Select>
              </Field>
              {channel?.youtube ? <p className="text-[13px] text-subtle">{youtubeNote(channel.youtube)}</p> : null}
            </>
          ) : (
            <p className="text-sm text-muted">
              Você ainda não tem canais. Sem problema: o vídeo pode ficar sem canal.{' '}
              <NewTabLink to="/canais">Vincular do YouTube ou cadastrar um canal</NewTabLink>
            </p>
          )}
        </Section>

        <Section
          icon={Palette}
          title={
            <>
              <StepNumber n={2} />
              Formato e estilo
            </>
          }
          description="O estilo define o formato, a duração, o visual e a voz padrão."
        >
          {styles.error ? (
            <ErrorBox error={styles.error} title="Não foi possível carregar os estilos" onRetry={() => styles.refetch()} />
          ) : styles.isLoading ? (
            <div role="status" className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <span className="sr-only">Carregando estilos…</span>
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} className="h-24" />
              ))}
            </div>
          ) : styleList.length ? (
            <FieldGroup legend="Estilo do vídeo">
              <StylePicker styles={styleList} value={effectiveStyleId} onChange={pickStyle} />
            </FieldGroup>
          ) : (
            <p className="text-sm text-muted">
              Nenhum estilo disponível. <NewTabLink to="/estilos">Ver estilos</NewTabLink>
            </p>
          )}
          {style?.warnings.length ? (
            <div className="flex items-start gap-2 rounded-lg border border-amber-300/35 bg-amber-300/10 p-3 text-[13px] text-amber-200">
              <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
              <ul className="flex flex-col gap-1">
                {style.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </Section>

        <Section
          icon={FileText}
          title={
            <>
              <StepNumber n={3} />
              Tema e briefing
            </>
          }
          description="Quanto mais específico o tema, melhor o roteiro. Temas originais protegem a monetização."
        >
          <Field
            label={
              <>
                Tema <span className="font-normal text-subtle">(obrigatório)</span>
              </>
            }
            hint="Um caso, um lugar, uma pergunta."
            error={topicError}
          >
            <Input
              id={TOPIC_ID}
              value={topic}
              required
              onChange={(e) => setTopic(e.target.value)}
              onBlur={() => setTopicTouched(true)}
              placeholder="ex.: o farol que ficou aceso por 40 anos sem ninguém"
            />
          </Field>
          <Field
            label="Título de trabalho (vai virar candidato)"
            hint={`Até ${TITLE_MAX} caracteres. Opcional: se ficar vazio, o projeto usa o tema. No vídeo longo ele entra como 1º candidato na etapa Título e thumb, ao lado dos títulos que o Claude propõe.`}
          >
            <Input value={title} maxLength={TITLE_MAX} onChange={(e) => setTitle(e.target.value)} placeholder="ex.: O farol sem faroleiro" />
          </Field>
          <Field label="Briefing (opcional)" hint="Ângulo, fatos que precisam aparecer, fontes, o que evitar.">
            <Textarea rows={4} value={brief} onChange={(e) => setBrief(e.target.value)} />
          </Field>
        </Section>

        <Section
          icon={Languages}
          title={
            <>
              <StepNumber n={4} />
              Idiomas
            </>
          }
          description="O roteiro é escrito e narrado no idioma principal. Os outros são traduções do mesmo vídeo."
        >
          <Field label="Idioma principal (roteiro e narração)">
            <Select value={primary} onChange={(e) => pickPrimary(e.target.value)} className="w-full sm:w-80">
              {primaryOptions.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </Select>
          </Field>
          <FieldGroup
            legend={
              <>
                Também gerar em <span className="font-normal text-subtle">(opcional)</span>
              </>
            }
            hint={
              extraCount
                ? `${langsSentence} Cada idioma extra é traduzido pelo Claude e narrado de novo, então gasta mais cota.`
                : `${langsSentence} Cada idioma extra vira uma versão traduzida e narrada (gasta mais cota).`
            }
          >
            <div className="flex flex-wrap gap-1.5">
              {extraOptions.map((l) => {
                const on = extras.includes(l.code);
                return (
                  <button
                    key={l.code}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggleExtra(l.code)}
                    className={`inline-flex min-h-8 items-center gap-1.5 rounded-md border px-2.5 py-1 text-[13px] transition-colors ${
                      on ? 'border-accent/60 bg-accent/10 text-fg' : 'border-line text-muted hover:border-line-strong hover:text-fg'
                    }`}
                  >
                    {on ? <Check size={14} strokeWidth={2.5} aria-hidden="true" className="text-accent" /> : null}
                    {l.label}
                  </button>
                );
              })}
            </div>
          </FieldGroup>
          {/* A dica do grupo só é lida ao entrar nele: aqui a contagem é anunciada quando muda. */}
          <p aria-live="polite" className="sr-only">
            {langsSentence}
          </p>
        </Section>

        <Section
          icon={AudioLines}
          title={
            <>
              <StepNumber n={5} />
              Voz do vídeo final
            </>
          }
          description={
            effectiveVoice ? (
              effectiveVoiceName ? (
                <>
                  {chosenVoice ? (voiceFromChannel ? 'Padrão do canal: ' : 'Escolhida: ') : 'Padrão do estilo: '}
                  <span className="font-medium text-fg">{effectiveVoiceName}</span>
                  {chosenVoice ? null : '. Escolha outra abaixo, se quiser.'}
                </>
              ) : chosenVoice ? (
                'Voz escolhida na lista abaixo.'
              ) : (
                'Vale a voz padrão do estilo. Escolha outra abaixo, se quiser.'
              )
            ) : (
              'Sem escolha, vale a voz padrão do estilo.'
            )
          }
        >
          {style ? (
            <FieldGroup
              legend={`Vozes em ${langLabel(primary)}`}
              hint="A prévia sai com uma voz local grátis, para você conferir o conteúdo; esta voz só é gravada quando você renderiza o vídeo final (dá para trocar até lá). Clique em Ouvir (▶) para escutar uma amostra e no nome para escolher."
            >
              <div className="max-h-80 overflow-y-auto rounded-lg border border-line bg-canvas p-3">
                <VoicePicker lang={primary} purpose="final" value={effectiveVoice} defaultVoice={defaultVoice} onChange={pickVoice} />
              </div>
            </FieldGroup>
          ) : styles.isLoading ? (
            <Skeleton className="h-32" />
          ) : (
            <p className="text-sm text-muted">Escolha um estilo para ver as vozes.</p>
          )}
        </Section>
      </div>

      {/* Fora do rodapé fixo: um erro longo não toma a tela. */}
      <div ref={createErrorRef} className={create.error ? 'mt-6 scroll-mb-40' : ''}>
        {create.error ? <ErrorBox error={create.error} title="Não foi possível criar o projeto" onClose={() => create.reset()} /> : null}
      </div>

      {/* No celular o rodapé fica no fim da página (fixo, tomaria boa parte da tela com o teclado aberto).
          Fixo, ele se alinha à coluna dos cartões (mesma largura, cantos de cima arredondados), não à caixa da página. */}
      <footer className="bottom-0 z-10 -mx-4 -mb-10 mt-6 border-t border-line bg-panel/95 px-4 py-3 backdrop-blur-md sm:sticky sm:mx-0 sm:rounded-t-xl sm:border-x sm:px-5 sm:shadow-[0_-12px_24px_-12px_rgb(0_0_0/0.5)]">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex min-w-0 flex-col gap-2">
            <div role="group" aria-label="Resumo do projeto" className="min-w-0 text-[13px] text-muted">
              <p className="truncate sm:hidden">
                <span className="font-medium text-fg">{shortSummary}</span>
              </p>
              <div className="hidden min-w-0 flex-wrap items-center gap-x-2 gap-y-1 sm:flex">
                {style ? <FormatGlyph aspect={style.style.format.aspect} className="text-fg" /> : null}
                {/* O "·" vai dentro do item seguinte: a quebra de linha nunca deixa um ponto sozinho no fim. */}
                {summary.map((item, i) => (
                  <span key={`${item}-${i}`} title={item} className={`max-w-full truncate whitespace-nowrap ${i === 0 ? 'font-medium text-fg' : ''}`}>
                    {i > 0 ? (
                      <span aria-hidden="true" className="mr-2 text-subtle">
                        ·
                      </span>
                    ) : null}
                    {item}
                  </span>
                ))}
              </div>
            </div>
            <Checkbox
              checked={autopilot}
              onChange={setAutopilot}
              label="Gerar o roteiro agora"
              description={
                autopilot
                  ? 'Usa o limite do seu plano do Claude.'
                  : 'Nada é gerado agora: você gera o roteiro dentro do projeto quando quiser.'
              }
            />
          </div>
          <div className="flex shrink-0 flex-col items-stretch gap-1.5 lg:items-end">
            <Button
              variant="primary"
              icon={autopilot ? Wand2 : Plus}
              busy={create.isPending}
              disabled={Boolean(blocker)}
              aria-describedby={blocker ? BLOCKER_ID : undefined}
              onClick={() => create.mutate()}
            >
              {autopilot ? 'Criar e gerar roteiro' : 'Criar projeto'}
            </Button>
            {/* Sempre no DOM e anunciado: o botão desabilitado não recebe foco, então o motivo precisa ser lido sozinho. */}
            <p id={BLOCKER_ID} role="status" className={blocker ? 'text-[13px] text-subtle lg:text-right' : 'sr-only'}>
              {blocker ? (
                style && missingTopic ? (
                  <button
                    type="button"
                    onClick={() => {
                      setTopicTouched(true);
                      document.getElementById(TOPIC_ID)?.focus();
                    }}
                    className="rounded-sm underline decoration-line-strong underline-offset-2 hover:text-fg hover:decoration-fg"
                  >
                    {blocker}
                  </button>
                ) : (
                  blocker
                )
              ) : null}
            </p>
          </div>
        </div>
      </footer>
    </Page>
  );
};
