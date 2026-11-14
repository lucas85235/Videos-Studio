import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Play, Square } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { api, fileUrl, type VoiceCatalog, type VoiceChoice } from './api';
import { useProviders } from './shell/data';
import { Badge, ErrorBox, Loading, SegmentedControl, Spinner } from './ui';

type Gender = 'todas' | 'feminina' | 'masculina';

const GENDER_OPTIONS: { id: Gender; label: string }[] = [
  { id: 'todas', label: 'Todas' },
  { id: 'feminina', label: 'Femininas' },
  { id: 'masculina', label: 'Masculinas' },
];

/** Para que serve a lista: a voz local da prévia ou as vozes que podem ir para o vídeo final. */
export type VoicePurpose = 'preview' | 'final';

/** Catálogo de vozes (mesma chave em todos os lugares: o painel e o seletor dividem o cache). */
export const useVoiceCatalog = (lang: string, opts: { providers?: string[]; purpose?: VoicePurpose } = {}) => {
  const { providers, purpose } = opts;
  const query = providers?.length ? `&providers=${providers.join(',')}` : purpose ? `&purpose=${purpose}` : '';
  return useQuery({
    queryKey: ['voices', lang, query],
    queryFn: () => api.get<VoiceCatalog>(`/api/voices?lang=${encodeURIComponent(lang)}${query}`),
  });
};

/**
 * Lista as vozes dos provedores com um botão para ouvir a amostra. A amostra é gerada pelo provedor na primeira
 * vez (um pedido da cota, exceto na voz local) e fica guardada. Com `onChange`, clicar no nome escolhe a voz.
 */
export const VoicePicker: React.FC<{
  lang: string;
  providers?: string[];
  purpose?: VoicePurpose;
  value?: VoiceChoice | null;
  onChange?: (choice: VoiceChoice) => void;
  /** Voz padrão do estilo, marcada na lista. */
  defaultVoice?: string | null;
  disabled?: boolean;
}> = ({ lang, providers, purpose, value, onChange, defaultVoice, disabled }) => {
  const client = useQueryClient();
  const catalog = useVoiceCatalog(lang, { providers, purpose });
  const audio = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);
  const [playError, setPlayError] = useState<string | null>(null);
  const [gender, setGender] = useState<Gender>('todas');
  // Mesma consulta da barra lateral (cache compartilhado): avisa antes de "Ouvir" falhar por falta de cota.
  const providerList = useProviders();

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
  // A voz local gera a amostra no seu computador: não gasta cota.
  const anyMissingSample = groups.some((g) => g.available && !g.local && g.voices.some((v) => !v.sample));
  const quotaOut = (id: string) => Boolean(providerList.data?.some((p) => p.id === id && p.quota_exhausted_today));

  return (
    // @container: as colunas da grade seguem a largura do seletor (painel da prévia, Novo projeto, Configurações), não a da janela.
    <div className="@container flex flex-col gap-3 text-[13px]">
      {hasGender ? <SegmentedControl label="Filtrar vozes por gênero" size="sm" options={GENDER_OPTIONS} value={gender} onChange={setGender} /> : null}
      {anyMissingSample ? (
        <p className="text-xs text-subtle">
          Play vazado: a amostra ainda não existe. Ouvir pela primeira vez gera a amostra e gasta 1 pedido da cota.
        </p>
      ) : null}
      {groups.map((g) => {
        const voices = g.voices.filter((v) => gender === 'todas' || v.gender === gender);
        return (
          <div key={g.id} className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium text-fg">{g.name}</span>
              {g.local && !g.preview_only ? (
                <Badge tone="good" title="Roda no seu computador: sem cota e sem custo.">
                  Grátis
                </Badge>
              ) : null}
              {g.preview_only ? (
                <Badge tone="warn" title="Esta voz serve para testar; o vídeo publicado precisa de uma voz real.">
                  Voz de teste
                </Badge>
              ) : null}
              {g.available && quotaOut(g.id) ? (
                <Badge tone="warn" title="A cota grátis deste provedor acabou hoje: gerar amostras novas vai falhar até amanhã.">
                  Cota esgotada hoje
                </Badge>
              ) : null}
              {!g.available ? <span className="text-xs text-subtle">Indisponível: {g.reason}</span> : null}
            </div>
            {g.available ? (
              voices.length ? (
                <ul className="grid grid-cols-1 gap-1 @md:grid-cols-2 @3xl:grid-cols-3">
                  {voices.map((v) => {
                    const id = `${g.id}:${v.id}`;
                    const selected = value ? value.provider === g.id && value.voice === v.id : false;
                    const loading = sample.isPending && sample.variables?.provider === g.id && sample.variables?.voice === v.id;
                    const isPlaying = playing === id;
                    const details = [v.tone, v.gender].filter(Boolean).join(', ');
                    const listenLabel = isPlaying
                      ? `Parar ${v.label}`
                      : v.sample || g.local
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
                          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-line-strong bg-raised text-fg transition-colors hover:border-[#4a4a56] hover:bg-[#24242c] aria-busy:cursor-progress pointer-coarse:h-9 pointer-coarse:w-9"
                        >
                          {loading ? (
                            <Spinner className="h-3 w-3" />
                          ) : isPlaying ? (
                            <Square size={12} fill="currentColor" aria-hidden="true" />
                          ) : (
                            <Play size={13} fill={v.sample || g.local ? 'currentColor' : 'none'} strokeWidth={2.25} aria-hidden="true" className="translate-x-px" />
                          )}
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
      <ErrorBox error={sample.error} title="Não foi possível gerar a amostra" onClose={() => sample.reset()} />
      <ErrorBox error={playError} title="Falha ao tocar" onClose={() => setPlayError(null)} />
    </div>
  );
};
