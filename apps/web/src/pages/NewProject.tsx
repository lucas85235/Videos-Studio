import { useMutation, useQuery } from '@tanstack/react-query';
import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, type Channel, LANGUAGES, type StyleItem, type VoiceChoice } from '../api';
import { Badge, Button, Card, ErrorBox, Field, Input, Select, Textarea } from '../ui';
import { VoicePicker } from '../voices';

export const NewProjectPage: React.FC = () => {
  const navigate = useNavigate();
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const [title, setTitle] = useState('');
  const [topic, setTopic] = useState('');
  const [brief, setBrief] = useState('');
  const [styleId, setStyleId] = useState('misterios-short');
  const [channelId, setChannelId] = useState('');
  const [langs, setLangs] = useState<string[]>(['pt-BR']);
  const [autopilot, setAutopilot] = useState(true);
  const [voice, setVoice] = useState<VoiceChoice | null>(null);

  const style = useMemo(() => styles.data?.find((s) => s.style.id === styleId), [styles.data, styleId]);

  const create = useMutation({
    mutationFn: () =>
      api.post<{ project: { id: string } }>('/api/projects', {
        title: title || topic || 'Novo vídeo',
        topic,
        brief,
        style_id: styleId,
        channel_id: channelId || null,
        languages: langs,
        autopilot,
        voice,
      }),
    onSuccess: (data) => navigate(`/projetos/${data.project.id}`),
  });

  const toggleLang = (code: string) =>
    setLangs((prev) => (prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]));

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h1 className="mb-1 text-xl font-bold">Novo projeto</h1>
      <p className="mb-6 text-sm text-zinc-500">Escolha o tema e o estilo. O roteiro é gerado e para para sua revisão antes de qualquer custo com voz ou imagens.</p>
      <div className="grid gap-4 md:grid-cols-3">
        <Card className="flex flex-col gap-4 md:col-span-2">
          <Field label="Tema" hint="Seja específico: um caso, um lugar, uma pergunta. Temas originais protegem a monetização.">
            <Input value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="ex.: o farol que ficou aceso por 40 anos sem ninguém" />
          </Field>
          <Field label="Título de trabalho (opcional)">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="o LLM sugere um título" />
          </Field>
          <Field label="Briefing (opcional)" hint="Ângulo, fatos que precisam aparecer, fontes, o que evitar.">
            <Textarea rows={4} value={brief} onChange={(e) => setBrief(e.target.value)} />
          </Field>
          <Field label="Idiomas" hint="O primeiro marcado é o idioma principal; os outros são traduzidos e narrados depois.">
            <div className="flex flex-wrap gap-1.5">
              {LANGUAGES.map((l) => (
                <button
                  key={l.code}
                  type="button"
                  onClick={() => toggleLang(l.code)}
                  className={`rounded-md border px-2 py-1 text-xs ${langs.includes(l.code) ? 'border-amber-400 bg-amber-400/10 text-amber-200' : 'border-zinc-700 text-zinc-400'}`}
                >
                  {langs[0] === l.code ? '★ ' : ''}
                  {l.label}
                </button>
              ))}
            </div>
          </Field>
          {style ? (
            <Field
              label="Voz do narrador"
              hint={`Clique em ▶ para ouvir e no nome para escolher. ${voice ? `Escolhida: ${voice.voice}.` : `Sem escolha, vale a do estilo${style.style.voice.voice ? ` (${style.style.voice.voice})` : ''}.`} A amostra é gerada na primeira vez que você ouve (um pedido da cota).`}
            >
              <div className="max-h-72 overflow-y-auto pr-1">
                <VoicePicker
                  lang={langs[0] ?? 'pt-BR'}
                  providers={style.style.voice.chain}
                  value={voice ?? (style.style.voice.voice ? { provider: style.style.voice.chain[0], voice: style.style.voice.voice } : null)}
                  defaultVoice={style.style.voice.voice}
                  onChange={setVoice}
                />
              </div>
            </Field>
          ) : null}
        </Card>
        <Card className="flex flex-col gap-4">
          <Field label="Estilo">
            <Select value={styleId} onChange={(e) => { setStyleId(e.target.value); setVoice(null); }}>
              {(styles.data ?? []).map((s) => (
                <option key={s.style.id} value={s.style.id}>
                  {s.style.name}
                  {s.source === 'user' ? ' (meu)' : ''}
                </option>
              ))}
            </Select>
          </Field>
          {style ? (
            <div className="flex flex-col gap-2 text-xs text-zinc-400">
              <div>{style.style.description}</div>
              <div className="flex flex-wrap gap-1">
                <Badge>{style.style.format.type === 'short' ? 'Short 9:16' : 'Longo 16:9'}</Badge>
                <Badge>~{Math.round(style.style.format.duration.target_s)} s</Badge>
                <Badge>{style.style.family}</Badge>
                {style.style.structure !== 'none' ? <Badge tone="info">{style.style.structure}</Badge> : null}
              </div>
            </div>
          ) : null}
          <Field label="Canal (opcional)">
            <Select value={channelId} onChange={(e) => setChannelId(e.target.value)}>
              <option value="">Sem canal</option>
              {(channels.data ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </Field>
          <label className="flex items-start gap-2 text-sm text-zinc-300">
            <input type="checkbox" className="mt-1" checked={autopilot} onChange={(e) => setAutopilot(e.target.checked)} />
            <span>
              Gerar o roteiro agora
              <span className="block text-xs text-zinc-500">Para no portão de aprovação do roteiro.</span>
            </span>
          </label>
          <Button variant="primary" busy={create.isPending} disabled={!topic.trim() || langs.length === 0} onClick={() => create.mutate()}>
            Criar projeto
          </Button>
          <ErrorBox error={create.error} />
        </Card>
      </div>
    </div>
  );
};
