import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useEffect, useRef, useState } from 'react';
import { api, fileUrl, type VoiceCatalog, type VoiceChoice } from './api';
import { Badge, ErrorBox, Spinner } from './ui';

type Gender = 'todas' | 'feminina' | 'masculina';

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
  const [gender, setGender] = useState<Gender>('todas');

  useEffect(() => () => audio.current?.pause(), []);

  const playUrl = (id: string, url: string) => {
    audio.current?.pause();
    const el = new Audio(url);
    audio.current = el;
    el.onended = () => setPlaying((p) => (p === id ? null : p));
    setPlaying(id);
    el.play().catch(() => setPlaying(null));
  };

  const sample = useMutation({
    mutationFn: (v: VoiceChoice) => api.post<{ url: string }>('/api/voices/sample', { ...v, lang }),
    onSuccess: (data, v) => {
      playUrl(`${v.provider}:${v.voice}`, data.url);
      client.invalidateQueries({ queryKey: ['voices', lang] });
    },
  });

  const listen = (provider: string, voice: string, existing: string | null) => {
    const id = `${provider}:${voice}`;
    if (playing === id) {
      audio.current?.pause();
      setPlaying(null);
      return;
    }
    if (existing) playUrl(id, fileUrl(existing));
    else sample.mutate({ provider, voice });
  };

  if (catalog.isLoading) return <div className="flex items-center gap-2 text-xs text-zinc-500"><Spinner /> carregando vozes…</div>;
  const groups = catalog.data?.providers ?? [];
  if (!groups.length) return <div className="text-xs text-zinc-500">Nenhum provedor de voz deste estilo deixa escolher a voz.</div>;
  const hasGender = groups.some((g) => g.voices.some((v) => v.gender));

  return (
    <div className="flex flex-col gap-3 text-xs">
      {hasGender ? (
        <div className="flex gap-1">
          {(['todas', 'feminina', 'masculina'] as Gender[]).map((g) => (
            <button
              key={g}
              type="button"
              onClick={() => setGender(g)}
              className={`rounded-md border px-2 py-0.5 ${gender === g ? 'border-zinc-500 bg-zinc-800 text-zinc-100' : 'border-zinc-800 text-zinc-400 hover:text-zinc-200'}`}
            >
              {g === 'todas' ? 'Todas' : g === 'feminina' ? 'Femininas' : 'Masculinas'}
            </button>
          ))}
        </div>
      ) : null}
      {groups.map((g) => (
        <div key={g.id} className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-semibold text-zinc-300">{g.name}</span>
            {g.preview_only ? <Badge tone="warn">só prévia</Badge> : null}
            {!g.available ? <span className="text-zinc-500">indisponível: {g.reason}</span> : null}
          </div>
          {g.available ? (
            <div className="grid grid-cols-1 gap-1 sm:grid-cols-2 xl:grid-cols-3">
              {g.voices
                .filter((v) => gender === 'todas' || v.gender === gender)
                .map((v) => {
                  const id = `${g.id}:${v.id}`;
                  const selected = value ? value.provider === g.id && value.voice === v.id : false;
                  const loading = sample.isPending && sample.variables?.provider === g.id && sample.variables?.voice === v.id;
                  return (
                    <div
                      key={id}
                      className={`flex items-center gap-2 rounded-md border px-2 py-1 ${selected ? 'border-amber-400 bg-amber-400/10' : 'border-zinc-800'}`}
                    >
                      <button
                        type="button"
                        aria-label={playing === id ? `Parar ${v.label}` : `Ouvir ${v.label}`}
                        title={v.sample ? 'Ouvir' : 'Ouvir (gera a amostra na primeira vez)'}
                        disabled={loading}
                        onClick={() => listen(g.id, v.id, v.sample)}
                        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-zinc-200 hover:bg-zinc-700"
                      >
                        {loading ? <Spinner className="h-3 w-3" /> : playing === id ? '■' : '▶'}
                      </button>
                      <button
                        type="button"
                        disabled={!onChange || disabled}
                        onClick={() => onChange?.({ provider: g.id, voice: v.id })}
                        className={`min-w-0 flex-1 text-left ${onChange ? 'cursor-pointer' : 'cursor-default'}`}
                      >
                        <span className={selected ? 'font-semibold text-amber-200' : 'text-zinc-200'}>{v.label}</span>
                        <span className="text-zinc-500">
                          {[v.tone, v.gender].filter(Boolean).length ? ` · ${[v.tone, v.gender].filter(Boolean).join(', ')}` : ''}
                        </span>
                        {defaultVoice && v.id === defaultVoice ? <span className="text-zinc-500"> · padrão do estilo</span> : null}
                        {selected ? <span className="text-amber-300"> ✓</span> : null}
                      </button>
                    </div>
                  );
                })}
            </div>
          ) : null}
        </div>
      ))}
      <ErrorBox error={sample.error ?? catalog.error} />
    </div>
  );
};
