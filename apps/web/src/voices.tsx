import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Play, Square } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { api, fileUrl, type VoiceCatalog, type VoiceChoice } from './api';
import { Badge, ErrorBox, Loading, Spinner } from './ui';

type Gender = 'todas' | 'feminina' | 'masculina';

const GENDER_OPTIONS: { id: Gender; label: string }[] = [
  { id: 'todas', label: 'Todas' },
  { id: 'feminina', label: 'Femininas' },
  { id: 'masculina', label: 'Masculinas' },
];

/**
 * Lista as vozes dos provedores com um botão para ouvir a amostra. A amostra é gerada pelo provedor na primeira
 * vez (um pedido da cota) e fica guardada. Com `onChange`, clicar no nome escolhe a voz.
 */
export const VoicePicker: React.FC<{
  lang: string;
  providers?: string[];
  value?: VoiceChoice | null;
  onChange?: (choice: VoiceChoice) => void;
  /** Voz padrão do estilo, marcada na lista. */
  defaultVoice?: string | null;
  disabled?: boolean;
}> = ({ lang, providers, value, onChange, defaultVoice, disabled }) => {
  const client = useQueryClient();
  const key = ['voices', lang, providers?.join(',') ?? ''];
  const catalog = useQuery({
    queryKey: key,
    queryFn: () => api.get<VoiceCatalog>(`/api/voices?lang=${encodeURIComponent(lang)}${providers?.length ? `&providers=${providers.join(',')}` : ''}`),
  });
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const [playError, setPlayError] = useState<string | null>(null);
  const [gender, setGender] = useState<Gender>('todas');

  useEffect(() => () => audio.current?.pause(), []);

  const playUrl = (id: string, url: string) => {
    audio.current?.pause();
    setPlayError(null);
    const el = new Audio(url);
    audio.current = el;
    el.onended = () => setPlaying((p) => (p === id ? null : p));
    el.onerror = () => {
      setPlaying((p) => (p === id ? null : p));
      setPlayError('Não foi possível tocar a amostra desta voz.');
    };
    setPlaying(id);
    el.play().catch(() => setPlaying((p) => (p === id ? null : p)));
  };

  const sample = useMutation({
    mutationFn: (v: VoiceChoice) => api.post<{ url: string }>('/api/voices/sample', { ...v, lang }),
    // Só a invalidação fica aqui (roda mesmo após desmontar); tocar a amostra fica no callback do mutate,
    // que o react-query não chama depois que o componente sai da tela.
    onSuccess: () => client.invalidateQueries({ queryKey: ['voices', lang] }),
  });

  const listen = (provider: string, voice: string, existing: string | null) => {
    const id = `${provider}:${voice}`;
    if (playing === id) {
      audio.current?.pause();
      setPlaying(null);
      return;
    }
    if (existing) playUrl(id, fileUrl(existing));
    else sample.mutate({ provider, voice }, { onSuccess: (data, v) => playUrl(`${v.provider}:${v.voice}`, data.url) });
  };

  if (catalog.isLoading) return <Loading label="Carregando vozes…" />;
  if (catalog.error) {
    return <ErrorBox error={catalog.error} title="Não foi possível carregar as vozes" onRetry={() => catalog.refetch()} />;
  }
  const groups = catalog.data?.providers ?? [];
  if (!groups.length) return <p className="text-[13px] text-muted">Nenhum provedor de voz deste estilo deixa escolher a voz.</p>;
  const hasGender = groups.some((g) => g.voices.some((v) => v.gender));
  const anyMissingSample = groups.some((g) => g.available && g.voices.some((v) => !v.sample));

  return (
    <div className="flex flex-col gap-3 text-[13px]">
      {hasGender ? (
        <div role="group" aria-label="Filtrar vozes por gênero" className="flex w-fit gap-0.5 rounded-lg border border-line bg-canvas p-0.5">
          {GENDER_OPTIONS.map((g) => (
            <button
              key={g.id}
              type="button"
              aria-pressed={gender === g.id}
              onClick={() => setGender(g.id)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                gender === g.id ? 'bg-accent/15 text-accent ring-1 ring-inset ring-accent/50' : 'text-muted hover:text-fg'
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>
      ) : null}
      {groups.map((g) => {
        const voices = g.voices.filter((v) => gender === 'todas' || v.gender === gender);
        return (
          <div key={g.id} className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium text-fg">{g.name}</span>
              {g.preview_only ? (
                <Badge tone="warn" title="Esta voz serve para testar; o vídeo publicado precisa de uma voz real.">
                  Só prévia
                </Badge>
              ) : null}
              {!g.available ? <span className="text-xs text-subtle">Indisponível: {g.reason}</span> : null}
            </div>
            {g.available ? (
              voices.length ? (
                <ul className="grid grid-cols-1 gap-1 sm:grid-cols-2 xl:grid-cols-3">
                  {voices.map((v) => {
                    const id = `${g.id}:${v.id}`;
                    const selected = value ? value.provider === g.id && value.voice === v.id : false;
                    const loading = sample.isPending && sample.variables?.provider === g.id && sample.variables?.voice === v.id;
                    const isPlaying = playing === id;
                    const details = [v.tone, v.gender].filter(Boolean).join(', ');
                    const listenLabel = isPlaying
                      ? `Parar ${v.label}`
                      : v.sample
                        ? `Ouvir ${v.label}`
                        : `Gerar amostra de ${v.label} (usa um pedido da cota)`;
                    const name = (
                      <>
                        <span className={`block truncate ${selected ? 'font-semibold text-fg' : 'text-fg'}`}>{v.label}</span>
                        {details || (defaultVoice && v.id === defaultVoice) ? (
                          <span className="block truncate text-xs text-subtle">
                            {details}
                            {defaultVoice && v.id === defaultVoice ? `${details ? ' · ' : ''}padrão do estilo` : ''}
                          </span>
                        ) : null}
                      </>
                    );
                    return (
                      <li
                        key={id}
                        className={`flex items-center gap-2 rounded-lg border px-2 py-1.5 transition-colors ${
                          selected ? 'border-accent/60 bg-accent/10' : 'border-line hover:border-line-strong'
                        }`}
                      >
                        <button
                          type="button"
                          aria-label={listenLabel}
                          title={listenLabel}
                          aria-disabled={loading || undefined}
                          aria-busy={loading || undefined}
                          onClick={() => {
                            // Gerando: continua focável (o foco do teclado não cai no <body>), mas ignora o clique.
                            if (!loading) listen(g.id, v.id, v.sample);
                          }}
                          className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line-strong bg-raised text-fg transition-colors hover:border-[#4a4a56] hover:bg-[#24242c] aria-busy:cursor-progress"
                        >
                          {loading ? (
                            <Spinner className="h-3 w-3" />
                          ) : isPlaying ? (
                            <Square size={12} fill="currentColor" aria-hidden="true" />
                          ) : (
                            <Play size={13} fill={v.sample ? 'currentColor' : 'none'} strokeWidth={2.25} aria-hidden="true" className="translate-x-px" />
                          )}
                          {!v.sample && !loading && !isPlaying ? (
                            <span aria-hidden="true" className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-accent ring-2 ring-card" />
                          ) : null}
                        </button>
                        {onChange ? (
                          <button
                            type="button"
                            aria-pressed={selected}
                            disabled={disabled}
                            onClick={() => onChange({ provider: g.id, voice: v.id })}
                            className="flex min-w-0 flex-1 items-center gap-2 rounded-md text-left disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            <span className="min-w-0 flex-1">{name}</span>
                            {selected ? <Check size={16} strokeWidth={2.5} aria-label="Escolhida" className="shrink-0 text-accent" /> : null}
                          </button>
                        ) : (
                          <span className="min-w-0 flex-1">{name}</span>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-xs text-subtle">Nenhuma voz com esse filtro.</p>
              )
            ) : null}
          </div>
        );
      })}
      {anyMissingSample ? (
        <p className="flex items-center gap-1.5 text-xs text-subtle">
          <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-accent" />
          Com o ponto, a amostra ainda não existe: ouvir gera a amostra e gasta um pedido da cota.
        </p>
      ) : null}
      <ErrorBox error={sample.error} title="Não foi possível gerar a amostra" onClose={() => sample.reset()} />
      <ErrorBox error={playError} title="Falha ao tocar" onClose={() => setPlayError(null)} />
    </div>
  );
};
