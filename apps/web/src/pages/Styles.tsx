import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, type StyleItem, type StyleSpec } from '../api';
import { Badge, Button, Card, ErrorBox, Field, Input, Select, Textarea } from '../ui';

type Meta = { families: { id: string; label: string; implemented: boolean }[] };

type FromText = { style: StyleSpec; summary: string; applied: { path: string; value: unknown; reason: string }[]; rejected: { path: string; error: string }[]; provider: string };

export const StylesPage: React.FC = () => {
  const navigate = useNavigate();
  const client = useQueryClient();
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const meta = useQuery({ queryKey: ['styles-meta'], queryFn: () => api.get<Meta>('/api/styles/meta') });
  const familyLabel = (id: string) => meta.data?.families.find((f) => f.id === id)?.label ?? id;
  const [description, setDescription] = useState('');
  const [base, setBase] = useState('documentario-sombrio');
  const [name, setName] = useState('');
  const [result, setResult] = useState<FromText | null>(null);
  const generate = useMutation({
    mutationFn: () => api.post<FromText>('/api/styles/from-text', { description, base_style_id: base, name: name || undefined }),
    onSuccess: setResult,
  });
  const save = useMutation({
    mutationFn: (style: StyleSpec) => api.post<{ style: StyleSpec }>('/api/styles', { style, parent_id: result?.style ? base : null }),
    onSuccess: (data) => {
      client.invalidateQueries({ queryKey: ['styles'] });
      navigate(`/estilos/${data.style.id}`);
    },
  });

  return (
    <div className="mx-auto max-w-6xl p-6">
      <h1 className="mb-1 text-xl font-bold">Estilos</h1>
      <p className="mb-6 text-sm text-zinc-500">
        Um estilo junta formato, roteiro, voz, arte, edição, áudio e thumbnail. Duplique um preset e ajuste, ou descreva o estilo em texto.
      </p>

      <Card className="mb-6 grid gap-3 md:grid-cols-[1fr_220px_220px_auto] md:items-end">
        <Field label="Criar estilo a partir de uma descrição" hint="ex.: documentário de jornal antigo, lento, sépia, texto de máquina de escrever, legendas pequenas">
          <Textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <Field label="Partir de">
          <Select value={base} onChange={(e) => setBase(e.target.value)}>
            {(styles.data ?? []).map((s) => (
              <option key={s.style.id} value={s.style.id}>{s.style.name}</option>
            ))}
          </Select>
        </Field>
        <Field label="Nome (opcional)">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Button variant="primary" busy={generate.isPending} disabled={description.trim().length < 3} onClick={() => generate.mutate()}>
          Gerar
        </Button>
        <div className="md:col-span-4">
          <ErrorBox error={generate.error ?? save.error} />
          {result ? (
            <div className="mt-2 flex flex-col gap-2 rounded-lg border border-zinc-700 bg-zinc-950 p-3 text-sm">
              <div>{result.summary}</div>
              {result.applied.map((a) => (
                <div key={a.path} className="text-xs text-zinc-400">
                  <Badge tone="good">{a.path}</Badge> {JSON.stringify(a.value).slice(0, 160)} {a.reason ? <span className="text-zinc-500">— {a.reason}</span> : null}
                </div>
              ))}
              {result.rejected.map((r) => (
                <div key={r.path} className="text-xs text-red-300">
                  <Badge tone="bad">{r.path}</Badge> {r.error}
                </div>
              ))}
              <div className="flex gap-2">
                <Button variant="primary" busy={save.isPending} onClick={() => save.mutate(result.style)}>
                  Salvar como “{result.style.name}”
                </Button>
                <span className="self-center text-xs text-zinc-500">id: {result.style.id} · via {result.provider}</span>
              </div>
            </div>
          ) : null}
        </div>
      </Card>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {(styles.data ?? []).map((item) => {
          const s = item.style;
          return (
            <Link key={s.id} to={`/estilos/${s.id}`}>
              <Card className="h-full transition hover:border-zinc-600">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="font-semibold">{s.name}</span>
                  <Badge tone={item.source === 'preset' ? 'neutral' : 'info'}>{item.source === 'preset' ? 'preset' : `meu · v${item.version}`}</Badge>
                </div>
                <div className="mb-2 line-clamp-2 text-xs text-zinc-400">{s.description}</div>
                <div className="flex flex-wrap gap-1 text-xs">
                  <Badge>{s.format.type === 'short' ? `Short ${s.format.aspect}` : `Longo ${s.format.aspect}`}</Badge>
                  {(s.families_mix.length ? s.families_mix : [s.family]).map((f) => (
                    <Badge key={f} tone="info">{familyLabel(f)}</Badge>
                  ))}
                  {s.structure !== 'none' ? <Badge tone="warn">{s.structure}</Badge> : null}
                </div>
                {item.warnings.length ? <div className="mt-2 text-[11px] text-amber-300/80">⚠ {item.warnings[0]}</div> : null}
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
};
