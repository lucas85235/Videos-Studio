// Painel "Mídia real" do cartão da cena (aba Cenas): a sugestão do storyboard, fotos livres (Commons/Openverse),
// link colado, arquivo enviado e a mídia anexada (crédito, enquadramento, notícia). Nada é anexado sozinho.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Camera, Copyright, ExternalLink, ImagePlus, Link2, Newspaper, Replace, RotateCw, Search, Trash2, TriangleAlert, Upload, X } from 'lucide-react';
import React, { useEffect, useId, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  fileUrl,
  type MediaCandidate,
  type MediaCandidates,
  type MediaPatch,
  type MediaRef,
  type ProjectDoc,
  type RealHint,
  type RealKind,
  type Scene,
} from '../../api';
import { FOCUS_LABELS, label, MEDIA_FIT_LABELS, MEDIA_SOURCE_LABELS, REAL_KIND_LABELS } from '../../labels';
import { Badge, Button, Checkbox, ErrorBox, IconButton, Input, Select, Skeleton, useConfirm } from '../../ui';
import { sceneChanges } from './core/drafts';
import { ProofCapture } from './proof/ProofCapture';
import {
  attachCandidate,
  attachLink,
  creditLine,
  EDITORIAL_NOTICE,
  linkedSources,
  mediaCandidates,
  mediaCandidatesKey,
  removeMedia,
  setRealHint,
  updateMedia,
  UPLOAD_MAX_BYTES,
  uploadMedia,
} from './core/media';

const KIND_ICONS: Record<RealKind, typeof Camera> = { photo: Camera, news: Newspaper, official: Copyright };
const KINDS: RealKind[] = ['photo', 'news', 'official'];
const FITS = ['auto', 'cover', 'contain'];
const URL_MAX = 2000;
const NEWS_OUTSIDE_IMAGE = 'O cartão de notícia só aparece em cena do tipo Imagem.';

/** Mensagem de cada fonte que não respondeu. */
const SOURCE_DOWN: Record<string, string> = {
  commons: 'O Wikimedia Commons não respondeu agora.',
  openverse: 'O Openverse não respondeu agora.',
  pexels: 'O Pexels não respondeu agora.',
};

type AttachVars =
  | { via: 'candidate'; id: string }
  | { via: 'link'; url: string; kind: RealKind | null; from: 'link' | 'source' | 'hint' }
  | { via: 'upload'; file: File; kind: RealKind };

/** Marca `true` quando o elemento chega perto da tela (uma vez só): a busca da sugestão só roda para cartões vistos. */
const useSeen = (ref: React.RefObject<HTMLElement | null>, active: boolean) => {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (!active || seen) return;
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined') {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          io.disconnect();
        }
      },
      { rootMargin: '200px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [ref, active, seen]);
  return seen;
};

/** Select pequeno com rótulo visível (o mesmo visual dos seletores do cartão da cena). */
const SmallSelect: React.FC<{ label: string; value: string; disabled: boolean; onChange: (v: string) => void; children: React.ReactNode }> = ({
  label: text,
  value,
  disabled,
  onChange,
  children,
}) => {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-subtle">
        {text}
      </label>
      <Select id={id} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} className="w-full min-w-0 text-[13px]">
        {children}
      </Select>
    </div>
  );
};

/** Campo de texto que salva no Enter ou ao sair (Esc volta ao salvo). */
const SavedInput: React.FC<{
  label: string;
  value: string;
  placeholder?: string;
  maxLength?: number;
  disabled: boolean;
  busy: boolean;
  /** Ajuda abaixo do campo, calculada com o texto digitado. */
  hint?: (draft: string) => React.ReactNode;
  onSave: (value: string) => void;
}> = ({ label: text, value, placeholder, maxLength, disabled, busy, hint, onSave }) => {
  const id = useId();
  const [draft, setDraft] = useState(value);
  // Valor novo do servidor (salvo, normalizado ou mudado em outra aba): o campo acompanha.
  useEffect(() => setDraft(value), [value]);
  const commit = () => {
    // Enter e depois sair do campo: a primeira gravação ainda está em andamento (não manda de novo).
    if (!disabled && !busy && draft.trim() !== value.trim()) onSave(draft.trim());
  };
  const help = hint?.(draft);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-subtle">
        {text}
      </label>
      <Input
        id={id}
        value={draft}
        placeholder={placeholder}
        maxLength={maxLength}
        disabled={disabled}
        aria-busy={busy || undefined}
        aria-describedby={help ? `${id}-ajuda` : undefined}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            commit();
          } else if (e.key === 'Escape') {
            setDraft(value);
          }
        }}
        className="w-full min-w-0 text-[13px]"
      />
      {help ? (
        <p id={`${id}-ajuda`} className="text-xs text-subtle">
          {help}
        </p>
      ) : null}
    </div>
  );
};

/** Uma foto livre achada na busca. */
const CandidateItem: React.FC<{ candidate: MediaCandidate; disabled: boolean; busy: boolean; onUse: () => void }> = ({
  candidate: c,
  disabled,
  busy,
  onUse,
}) => (
  <li className="flex min-w-0 flex-col gap-1">
    <a
      href={c.page_url || undefined}
      target="_blank"
      rel="noreferrer"
      title={`${c.title || 'Foto'} (${label(MEDIA_SOURCE_LABELS, c.source)}): abrir a página da foto`}
      className="block aspect-square overflow-hidden rounded-md border border-line bg-canvas"
    >
      {/* Miniatura direto do servidor da fonte (sem chave nem dado pessoal). */}
      <img src={c.thumb} alt={c.title || 'Foto livre'} loading="lazy" referrerPolicy="no-referrer" className="h-full w-full object-cover" />
    </a>
    <div className="flex min-w-0 flex-wrap items-center gap-1">
      <Badge tone="good" title={c.attribution_required ? 'Exige o crédito do autor (vai na tela e na descrição)' : 'Livre, sem exigência de crédito'}>
        {c.license || 'Livre'}
      </Badge>
    </div>
    {c.author ? (
      <span className="truncate text-xs text-subtle" title={c.author}>
        por {c.author}
      </span>
    ) : null}
    {c.warning ? <span className="text-xs text-amber-200">{c.warning}</span> : null}
    <Button size="sm" disabled={disabled} busy={busy} onClick={onUse} className="mt-auto">
      Usar esta
    </Button>
  </li>
);

type OptionsProps = {
  scene: Scene;
  doc: ProjectDoc;
  projectId: string;
  hint: RealHint | null;
  /** Tudo desabilitado (cena travada, job gravando o documento, outra ação em andamento). */
  locked: boolean;
  pending: AttachVars | null;
  onAttach: (vars: AttachVars) => void;
  /** Erro local (antes de enviar): link inválido, arquivo grande demais. */
  onLocalError: (message: string) => void;
};

/** Opções para anexar: busca de fotos livres, fonte da notícia, link, fonte do roteiro e arquivo. */
const MediaOptions: React.FC<OptionsProps> = ({ scene, doc, projectId, hint, locked, pending, onAttach, onLocalError }) => {
  const rootRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const searchId = useId();
  const linkId = useId();
  const sourceId = useId();
  const [typed, setTyped] = useState(hint?.query ?? '');
  // null: nenhuma busca pedida pelo usuário (a da sugestão de foto roda sozinha com `q` vazio).
  const [q, setQ] = useState<string | null>(null);
  const [link, setLink] = useState('');
  const [source, setSource] = useState('');
  const refreshNext = useRef(false);

  const sources = linkedSources(doc);
  const hintSource = hint?.kind === 'news' && hint.source_ref ? sources.find((s) => s.n === hint.source_ref) : undefined;
  const showSearch = !hint || hint.kind === 'photo';
  const autoQuery = hint?.kind === 'photo' && hint.query.trim() ? '' : null;
  const activeQ = q ?? autoQuery;
  const seen = useSeen(rootRef, activeQ !== null && q === null);

  const search = useQuery({
    queryKey: mediaCandidatesKey(projectId, scene.id, activeQ ?? ''),
    queryFn: () => {
      const refresh = refreshNext.current;
      refreshNext.current = false;
      return mediaCandidates(projectId, scene.id, activeQ || undefined, refresh);
    },
    enabled: activeQ !== null && (q !== null || seen),
    staleTime: 10 * 60_000,
    retry: false,
  });

  const submitSearch = () => {
    const text = typed.trim().slice(0, 120);
    // Caixa vazia volta para a busca da sugestão (quando há uma).
    const next = text || null;
    if (next === null && autoQuery === null) return;
    if (next === q) void search.refetch();
    else setQ(next);
  };
  const retry = () => {
    refreshNext.current = true;
    void search.refetch();
  };

  const submitLink = () => {
    const url = link.trim();
    if (!/^https?:\/\/\S+$/i.test(url)) {
      onLocalError('Cole um endereço que começa com http:// ou https://.');
      return;
    }
    if (url.length > URL_MAX) {
      onLocalError('O link é longo demais.');
      return;
    }
    onAttach({ via: 'link', url, kind: null, from: 'link' });
  };

  const pickFile = (file: File | undefined) => {
    if (!file) return;
    if (file.size > UPLOAD_MAX_BYTES) {
      onLocalError('O arquivo passa de 25 MB.');
      return;
    }
    onAttach({ via: 'upload', file, kind: hint?.kind ?? scene.visual.media?.kind ?? 'photo' });
  };

  const busy = pending !== null;
  const data = search.data;
  const statuses: MediaCandidates['sources'] = data?.sources ?? {};
  const offline = Object.values(statuses).includes('offline');
  const down = (['commons', 'openverse', 'pexels'] as const).filter((s) => statuses[s] === 'erro');
  const noContact = statuses.commons === 'sem_contato';
  const candidates = data?.candidates ?? [];

  return (
    <div ref={rootRef} className="flex flex-col gap-3">
      {showSearch ? (
        <div className="flex flex-col gap-2">
          <form
            role="search"
            className="flex flex-col gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              submitSearch();
            }}
          >
            <label htmlFor={searchId} className="text-xs font-medium text-subtle">
              Buscar fotos livres
            </label>
            <div className="flex gap-1.5">
              <Input
                id={searchId}
                type="search"
                lang="en"
                value={typed}
                maxLength={120}
                placeholder="Buscar em inglês (ex.: Miami Beach)"
                disabled={locked}
                onChange={(e) => setTyped(e.target.value)}
                className="min-w-0 flex-1 text-[13px]"
              />
              <Button size="sm" type="submit" icon={Search} disabled={locked || (!typed.trim() && autoQuery === null)} busy={search.isFetching}>
                Buscar
              </Button>
            </div>
          </form>
          {search.isLoading ? (
            <ul aria-label="Carregando fotos" className="m-0 grid list-none grid-cols-3 gap-2 p-0">
              {[0, 1, 2].map((i) => (
                <li key={i}>
                  <Skeleton className="aspect-square rounded-md" />
                </li>
              ))}
            </ul>
          ) : null}
          <ErrorBox error={search.error} title="Não foi possível buscar as fotos" onRetry={retry} />
          {data ? (
            <div className="flex flex-col gap-1.5">
              {candidates.length ? (
                <ul aria-label={`Fotos livres para “${data.query}”`} className="m-0 grid list-none grid-cols-2 gap-2 p-0 @sm:grid-cols-3">
                  {candidates.map((c) => (
                    <CandidateItem
                      key={c.id}
                      candidate={c}
                      disabled={locked || (busy && !(pending?.via === 'candidate' && pending.id === c.id))}
                      busy={pending?.via === 'candidate' && pending.id === c.id}
                      onUse={() => onAttach({ via: 'candidate', id: c.id })}
                    />
                  ))}
                </ul>
              ) : null}
              <div role="status" className="flex flex-col gap-1.5 empty:hidden">
                {!candidates.length && !offline && !down.length && data.query ? (
                  <p className="text-[13px] text-muted">Nenhuma foto livre achada para “{data.query}”.</p>
                ) : null}
                {offline ? <p className="text-[13px] text-amber-200">Sem internet: cole um link ou envie o arquivo.</p> : null}
                {noContact && !offline ? (
                  <p className="text-[13px] text-muted">
                    Fotos do Wikimedia Commons: informe um contato seu (site ou e-mail) em{' '}
                    <Link to="/configuracoes#provedores-image" className="text-accent underline-offset-2 hover:underline">
                      Configurações
                    </Link>
                    . A política de robôs deles exige; até lá, a busca usa só o Openverse.
                  </p>
                ) : null}
                {down.length ? (
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-amber-200">
                    <span>{down.map((s) => SOURCE_DOWN[s]).join(' ')}</span>
                    <Button size="sm" variant="ghost" icon={RotateCw} disabled={locked} busy={search.isFetching} onClick={retry}>
                      Tentar de novo
                    </Button>
                  </div>
                ) : null}
              </div>
              {candidates.length ? <p className="text-xs text-subtle">O crédito do autor e a licença vão na tela e na descrição.</p> : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {hintSource ? (
        <div className="flex flex-col items-start gap-1">
          <Button
            size="sm"
            icon={Newspaper}
            disabled={locked || (busy && pending?.via !== 'link')}
            busy={pending?.via === 'link' && pending.from === 'hint'}
            onClick={() => onAttach({ via: 'link', url: hintSource.url.trim(), kind: 'news', from: 'hint' })}
            className="max-w-full text-left"
          >
            <span className="min-w-0 truncate">Usar a fonte: {hintSource.title || hintSource.url}</span>
          </Button>
          <span className="text-xs text-subtle">Vira um cartão de notícia com o veículo, a data e a manchete.</span>
        </div>
      ) : null}

      {hint?.kind === 'official' ? (
        <p className="flex items-start gap-1.5 text-[13px] text-amber-100">
          <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
          <span>{EDITORIAL_NOTICE} Só você pode anexar: cole o link ou envie o arquivo.</span>
        </p>
      ) : null}

      <form
        noValidate
        className="flex flex-col gap-1"
        onSubmit={(e) => {
          e.preventDefault();
          submitLink();
        }}
      >
        <label htmlFor={linkId} className="text-xs font-medium text-subtle">
          Colar link (página ou imagem)
        </label>
        <div className="flex gap-1.5">
          <Input
            id={linkId}
            type="url"
            inputMode="url"
            value={link}
            placeholder="https://…"
            disabled={locked}
            aria-describedby={`${linkId}-ajuda`}
            onChange={(e) => setLink(e.target.value)}
            className="min-w-0 flex-1 text-[13px]"
          />
          <Button
            size="sm"
            type="submit"
            icon={Link2}
            disabled={locked || !link.trim() || (busy && !(pending?.via === 'link' && pending.from === 'link'))}
            busy={pending?.via === 'link' && pending.from === 'link'}
          >
            Usar link
          </Button>
        </div>
        <p id={`${linkId}-ajuda`} className="text-xs text-subtle">
          O app lê só o título, a data e a imagem da página.
        </p>
      </form>

      {sources.length ? (
        <div className="flex flex-col gap-1">
          <label htmlFor={sourceId} className="text-xs font-medium text-subtle">
            Usar uma fonte do roteiro
          </label>
          <div className="flex gap-1.5">
            <Select id={sourceId} value={source} disabled={locked} onChange={(e) => setSource(e.target.value)} className="min-w-0 flex-1 text-[13px]">
              <option value="">Escolha a fonte…</option>
              {sources.map((s) => (
                <option key={s.n} value={s.url.trim()}>
                  {s.title || s.url}
                </option>
              ))}
            </Select>
            <Button
              size="sm"
              disabled={locked || !source || (busy && !(pending?.via === 'link' && pending.from === 'source'))}
              busy={pending?.via === 'link' && pending.from === 'source'}
              onClick={() => onAttach({ via: 'link', url: source, kind: null, from: 'source' })}
            >
              Usar fonte
            </Button>
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className="hidden"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            pickFile(e.target.files?.[0]);
            // Limpa a escolha: o mesmo arquivo pode ser escolhido de novo.
            e.target.value = '';
          }}
        />
        <Button
          size="sm"
          icon={Upload}
          disabled={locked || (busy && pending?.via !== 'upload')}
          busy={pending?.via === 'upload'}
          onClick={() => fileRef.current?.click()}
        >
          Enviar arquivo
        </Button>
        <span className="text-xs text-subtle">JPG, PNG ou WEBP, até 25 MB.</span>
      </div>

      <ProofCapture projectId={projectId} sceneId={scene.id} locked={locked} />
    </div>
  );
};

/** Origem e licença da mídia anexada ("CC BY-SA 4.0 · por Fulano"). */
const LicenseLine: React.FC<{ media: MediaRef }> = ({ media }) => {
  const lic = media.license.trim();
  if (!lic && !media.author.trim()) return null;
  return (
    <p className="text-xs text-muted">
      {lic ? (
        media.license_url ? (
          <a href={media.license_url} target="_blank" rel="noreferrer" className="underline underline-offset-2 hover:text-fg">
            {lic}
          </a>
        ) : (
          lic
        )
      ) : null}
      {lic && media.author.trim() ? ' · ' : null}
      {media.author.trim() ? `por ${media.author.trim()}` : null}
    </p>
  );
};

type Props = {
  scene: Scene;
  /** Número de exibição ("Cena 3"). */
  index: number;
  doc: ProjectDoc;
  projectId: string;
  /** Cena travada ou outra edição da cena em andamento. */
  disabled: boolean;
  /** Motivo visível para não editar agora (job gravando o documento, estilo removido). */
  blockedReason: string | null;
};

export const RealMediaPanel: React.FC<Props> = ({ scene, index, doc, projectId, disabled, blockedReason }) => {
  const client = useQueryClient();
  const confirm = useConfirm();
  const media = scene.visual.media ?? null;
  const hint = scene.real ?? null;
  const lang = doc.primary_language;
  const name = `Cena ${index}`;
  // Sem sugestão: abre pelo botão "Mídia real". Com mídia: "Trocar" abre as opções.
  const [open, setOpen] = useState(false);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [localError, setLocalError] = useState<string | null>(null);

  const refresh = async (mark: boolean) => {
    if (mark) sceneChanges.set(projectId, scene.id, Date.now());
    await client.invalidateQueries({ queryKey: ['project', projectId] });
  };

  const attach = useMutation({
    mutationFn: (v: AttachVars) =>
      v.via === 'candidate'
        ? attachCandidate(projectId, scene.id, v.id)
        : v.via === 'link'
          ? attachLink(projectId, scene.id, v.url, v.kind)
          : uploadMedia(projectId, scene.id, v.file, { kind: v.kind }),
    onSuccess: async (r) => {
      await refresh(true);
      setWarnings(r.warnings ?? []);
      setOpen(false);
    },
  });
  const edit = useMutation({
    mutationFn: (patch: MediaPatch) => updateMedia(projectId, scene.id, patch),
    onSuccess: () => refresh(true),
  });
  const remove = useMutation({
    mutationFn: () => removeMedia(projectId, scene.id),
    onSuccess: async () => {
      await refresh(true);
      setWarnings([]);
      setOpen(false);
    },
  });
  // Dispensar a sugestão não muda o vídeo: não marca a cena como alterada.
  const dismiss = useMutation({
    mutationFn: () => setRealHint(projectId, scene.id, null),
    onSuccess: () => refresh(false),
  });

  const busy = attach.isPending || edit.isPending || remove.isPending || dismiss.isPending;
  const locked = disabled || Boolean(blockedReason);
  const error = localError ?? attach.error ?? edit.error ?? remove.error ?? dismiss.error;
  const errorTitle = localError || attach.error
    ? 'Não foi possível anexar a mídia real'
    : edit.error
      ? 'Não foi possível salvar a mídia real'
      : remove.error
        ? 'Não foi possível tirar a mídia real'
        : 'Não foi possível dispensar a sugestão';
  const clearErrors = () => {
    setLocalError(null);
    attach.reset();
    edit.reset();
    remove.reset();
    dismiss.reset();
  };
  const runAttach = (vars: AttachVars) => {
    clearErrors();
    attach.mutate(vars);
  };
  const runEdit = (patch: MediaPatch) => {
    clearErrors();
    edit.mutate(patch);
  };
  const editing = (key: keyof MediaPatch) => edit.isPending && edit.variables?.[key] !== undefined;

  const errorBox = <ErrorBox error={error} title={errorTitle} onClose={clearErrors} />;
  const options = (
    <MediaOptions
      scene={scene}
      doc={doc}
      projectId={projectId}
      hint={hint}
      locked={locked || (busy && !attach.isPending)}
      pending={attach.isPending ? (attach.variables ?? null) : null}
      onAttach={runAttach}
      onLocalError={setLocalError}
    />
  );

  /* 1. Sem sugestão e sem mídia: só o botão que abre as opções. */
  if (!media && !hint) {
    if (!open) {
      return (
        <div className="flex">
          <Button size="sm" variant="ghost" icon={ImagePlus} disabled={locked} aria-expanded={false} onClick={() => setOpen(true)}>
            Mídia real
          </Button>
        </div>
      );
    }
    return (
      <section aria-label={`Mídia real da ${name.toLowerCase()}`} className="flex flex-col gap-2.5 rounded-lg border border-line bg-canvas p-2.5">
        <div className="flex items-start gap-1.5">
          <ImagePlus size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-subtle" />
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="text-xs font-medium text-fg">Mídia real</span>
            <span className="text-xs text-subtle">Foto livre, link ou arquivo no lugar da imagem de IA.</span>
          </div>
          <IconButton size="sm" className="-mr-1 -mt-1" label="Fechar a mídia real" icon={X} onClick={() => setOpen(false)} aria-expanded={true} />
        </div>
        {options}
        {errorBox}
      </section>
    );
  }

  /* 2. Sugestão do storyboard, ainda sem mídia. */
  if (!media && hint) {
    const KindIcon = KIND_ICONS[hint.kind] ?? Camera;
    return (
      <section aria-label={`Mídia real sugerida para a ${name.toLowerCase()}`} className="flex flex-col gap-2.5 rounded-lg border border-sky-400/30 bg-sky-400/5 p-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone="info" icon={KindIcon}>
            {label(REAL_KIND_LABELS, hint.kind)}
          </Badge>
          <span className="text-xs font-medium text-subtle">Mídia real sugerida</span>
          <Button
            size="sm"
            variant="ghost"
            icon={X}
            className="ml-auto"
            disabled={locked || (busy && !dismiss.isPending)}
            busy={dismiss.isPending}
            onClick={() => {
              clearErrors();
              dismiss.mutate();
            }}
          >
            Dispensar sugestão
          </Button>
        </div>
        {hint.note.trim() ? <p className="text-[13px] text-fg">Sugestão: {hint.note.trim()}</p> : null}
        {options}
        {errorBox}
      </section>
    );
  }

  /* 3. Mídia anexada. */
  const m = media!;
  const KindIcon = KIND_ICONS[m.kind] ?? Camera;
  const fit = m.fit ?? 'auto';
  const origin = m.page_url || m.url;
  const isNews = m.kind === 'news';

  const askRemove = async () => {
    const ok = await confirm({
      title: 'Tirar a mídia real?',
      description: 'A cena volta a usar a imagem de IA de reserva. O arquivo fica guardado.',
      confirmLabel: 'Tirar',
      tone: 'danger',
    });
    if (ok) {
      clearErrors();
      remove.mutate();
    }
  };

  return (
    <section aria-label={`Mídia real da ${name.toLowerCase()}`} className="flex flex-col gap-2.5 rounded-lg border border-line bg-canvas p-2.5">
      <div className="flex gap-2.5">
        {m.asset ? (
          <img src={fileUrl(m.asset)} alt="" loading="lazy" className="h-16 w-16 shrink-0 rounded-md border border-line object-cover" />
        ) : (
          <div
            role="img"
            aria-label="Notícia sem imagem: o cartão usa o fundo animado"
            title="Notícia sem imagem: o cartão usa o fundo animado"
            className="flex h-16 w-16 shrink-0 items-center justify-center rounded-md border border-line"
          >
            <Newspaper size={18} aria-hidden="true" className="text-subtle" />
          </div>
        )}
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-1">
            <Badge tone="info" icon={KindIcon}>
              {label(REAL_KIND_LABELS, m.kind)}
            </Badge>
            <Badge>{label(MEDIA_SOURCE_LABELS, m.source)}</Badge>
            {m.editorial ? <Badge tone="warn">Material de terceiros</Badge> : null}
          </div>
          {m.title.trim() ? (
            <p className="line-clamp-2 text-[13px] text-fg" title={m.title}>
              {m.title}
            </p>
          ) : null}
          <LicenseLine media={m} />
          {origin ? (
            <a href={origin} target="_blank" rel="noreferrer" className="inline-flex w-fit items-center gap-1 rounded-sm text-xs text-sky-300 hover:underline">
              <ExternalLink size={12} aria-hidden="true" className="shrink-0" />
              Ver origem
            </a>
          ) : null}
        </div>
      </div>

      {warnings.length ? (
        <div className="flex items-start gap-1.5 text-[13px] text-amber-100">
          <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
          <ul className="m-0 flex min-w-0 flex-1 list-none flex-col gap-0.5 p-0">
            {warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
          <IconButton size="sm" label="Dispensar os avisos" icon={X} className="-mr-1 -mt-1" onClick={() => setWarnings([])} />
        </div>
      ) : null}
      {m.editorial ? (
        <p className="flex items-start gap-1.5 text-[13px] text-amber-100">
          <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
          {EDITORIAL_NOTICE}
        </p>
      ) : null}
      {isNews && scene.kind !== 'image' ? (
        <p className="flex items-start gap-1.5 text-[13px] text-amber-100">
          <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" />
          {NEWS_OUTSIDE_IMAGE}
        </p>
      ) : null}

      <SavedInput
        label="Crédito na tela"
        value={m.credit}
        maxLength={120}
        placeholder="Ex.: Rockstar Games"
        disabled={locked || (busy && !editing('credit'))}
        busy={editing('credit')}
        hint={(draft) => {
          const line = creditLine({ kind: m.kind, credit: draft }, lang);
          return line ? `Na tela: ${line}` : 'Sem crédito na tela.';
        }}
        onSave={(credit) => runEdit({ credit })}
      />

      <div className="grid grid-cols-1 gap-2 @md:grid-cols-3">
        <SmallSelect label="Tipo" value={m.kind} disabled={locked || busy} onChange={(v) => runEdit({ kind: v as RealKind })}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {label(REAL_KIND_LABELS, k)}
            </option>
          ))}
        </SmallSelect>
        <SmallSelect label="Enquadramento" value={fit} disabled={locked || busy} onChange={(v) => runEdit({ fit: v as MediaPatch['fit'] })}>
          {FITS.map((f) => (
            <option key={f} value={f}>
              {label(MEDIA_FIT_LABELS, f)}
            </option>
          ))}
        </SmallSelect>
        {fit !== 'contain' ? (
          <SmallSelect label="Sujeito na foto" value={m.focus ?? 'center'} disabled={locked || busy} onChange={(v) => runEdit({ focus: v })}>
            {Object.keys(FOCUS_LABELS).map((f) => (
              <option key={f} value={f}>
                {label(FOCUS_LABELS, f)}
              </option>
            ))}
          </SmallSelect>
        ) : null}
      </div>

      <Checkbox
        label="Material de terceiros (oficial ou imprensa)"
        checked={m.editorial}
        disabled={locked || busy}
        onChange={(editorial) => runEdit({ editorial })}
      />

      {isNews ? (
        <div className="grid grid-cols-1 gap-2 @md:grid-cols-2">
          <SavedInput
            label="Veículo"
            value={m.outlet}
            maxLength={60}
            disabled={locked || (busy && !editing('outlet'))}
            busy={editing('outlet')}
            onSave={(outlet) => runEdit({ outlet })}
          />
          <SavedInput
            label="Data (AAAA-MM-DD)"
            value={m.published}
            placeholder="2026-11-14"
            maxLength={10}
            disabled={locked || (busy && !editing('published'))}
            busy={editing('published')}
            onSave={(published) => runEdit({ published })}
          />
          <div className="@md:col-span-2">
            <SavedInput
              label="Manchete (até 14 palavras)"
              value={m.headline?.[lang] ?? ''}
              placeholder={m.title || undefined}
              maxLength={200}
              disabled={locked || (busy && !editing('headline'))}
              busy={editing('headline')}
              onSave={(headline) => runEdit({ headline, lang })}
            />
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" variant="ghost" icon={Replace} disabled={locked || busy} aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? 'Cancelar a troca' : 'Trocar'}
        </Button>
        <Button size="sm" variant="ghost" icon={Trash2} disabled={locked || (busy && !remove.isPending)} busy={remove.isPending} onClick={() => void askRemove()}>
          Tirar a mídia real
        </Button>
      </div>

      {open ? <div className="border-t border-line pt-2.5">{options}</div> : null}
      {errorBox}
    </section>
  );
};
