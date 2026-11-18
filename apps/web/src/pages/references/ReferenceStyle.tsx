// "Criar estilo a partir desta referência": as medidas (ritmo, paleta, proporção) viram um estilo em Meus estilos,
// que abre no editor. O editor mostra de onde veio cada valor só logo depois de criar: vem no estado da navegação,
// que é lido uma vez e limpo (não volta ao recarregar nem ao usar Voltar; ver ReferenceOrigins).
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Palette, Wand2 } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { visualApi, type RefStyleOrigin, type RefStyleResult, type ReferenceVideo } from '../../apiVisual';
import { CAMERA_LABELS, label } from '../../labels';
import { Button, Checkbox, ErrorBox, Field, Input, useToast } from '../../ui';

/** O que vai no estado da navegação para o editor de estilos. */
type OriginsState = { fromReference?: { title: string; result: RefStyleResult } };

const SOURCE_LABELS: Record<RefStyleOrigin['source'], string> = {
  preset: 'Ponto de partida',
  proporcao: 'Da proporção do vídeo',
  ritmo: 'Do ritmo dos cortes',
  paleta: 'Da paleta de cores',
  texto: 'Do texto na tela',
  perfil: 'Do perfil de edição medido',
  llm: 'Da IA (o que as medidas não dizem)',
};
const SOURCE_ORDER: RefStyleOrigin['source'][] = ['preset', 'proporcao', 'ritmo', 'paleta', 'texto', 'perfil', 'llm'];

const FIELD_LABELS: Record<string, string> = {
  base: 'Preset base',
  'format.aspect': 'Proporção',
  'format.resolution': 'Resolução',
  'format.type': 'Formato',
  'format.duration': 'Duração',
  'format.fps': 'Quadros por segundo',
  'edit.shot_seconds': 'Duração de cada cena',
  'edit.transitions': 'Transições',
  'edit.transition_seconds': 'Duração das transições',
  'edit.effects': 'Efeitos e gradação',
  'edit.text_cards.background': 'Fundo dos cartões de texto',
  'edit.text_cards.accent': 'Destaque dos cartões de texto',
  'edit.captions.highlight': 'Destaque da legenda',
  'edit.captions.style': 'Tipo de legenda',
  'edit.progress_color': 'Cor da barra de progresso',
  'thumbnail.accent': 'Destaque da miniatura',
  'edit.camera': 'Câmera',
  'art.bible': 'Direção de arte',
  'edit.grammar': 'Gramática de edição',
  'art.sources': 'Fontes da mídia',
  'edit.captions.font': 'Fonte da legenda',
  'edit.captions.weight': 'Peso da legenda',
  'edit.captions.uppercase': 'Legenda em caixa alta',
  'edit.captions.position': 'Posição da legenda',
  'edit.captions.max_words_per_line': 'Palavras por linha',
  'audio.sfx.auto': 'Efeitos sonoros automáticos',
  'edit.text_cards.font': 'Fonte dos cartões de texto',
  'edit.new_image_ratio': 'Cenas com imagem nova de IA',
  'voice.speed': 'Velocidade da voz',
};

/** Humor da gramática em português (os ids são internos; os níveis de fonte já vêm traduzidos no detalhe). */
const HUMOR_LABELS: Record<string, string> = { none: 'nenhum', light: 'leve', meme: 'memes' };

/** Número com vírgula decimal (1,43). */
const num = (v: number) => v.toLocaleString('pt-BR', { maximumFractionDigits: 2 });

/** Limite do nome do estilo (o mesmo do campo Nome no editor e da API). */
const STYLE_NAME_MAX = 120;
const REF_SUFFIX = ' (referência)';

/** Nome sugerido: "<título> (referência)", cortando o título (nunca o sufixo) para caber no limite. O corte é por
 * caractere (code point), não por unidade UTF-16: um emoji no limite não fica partido ao meio. Só serve de reserva
 * quando a API não manda `suggested_style_name` (que já vem do título limpo). */
export const defaultStyleName = (title: string) =>
  `${Array.from(title).slice(0, STYLE_NAME_MAX - REF_SUFFIX.length).join('').trimEnd()}${REF_SUFFIX}`;

const isColor = (v: unknown): v is string => typeof v === 'string' && /^#[0-9a-f]{6}$/i.test(v);

function showValue(path: string, value: unknown): React.ReactNode {
  if (isColor(value))
    return (
      <span className="inline-flex items-center gap-1">
        <span aria-hidden="true" className="inline-block h-3.5 w-3.5 rounded border border-line" style={{ background: value }} />
        {value}
      </span>
    );
  if (path === 'edit.shot_seconds' && value && typeof value === 'object') {
    const r = value as { min: number; max: number };
    return `${r.min} a ${r.max} s`;
  }
  if (path === 'edit.effects' && Array.isArray(value)) {
    const grade = value.find((e) => e?.type === 'grade');
    return grade ? `gradação ${grade.preset}` : value.map((e) => e?.type).join(', ');
  }
  if (path === 'edit.transitions' && value && typeof value === 'object') {
    const w = value as Record<string, number>;
    const total = Object.values(w).reduce((a, b) => a + b, 0) || 1;
    return Object.entries(w)
      .map(([k, v]) => `${k} ${Math.round((v / total) * 100)}%`)
      .join(', ');
  }
  if (path === 'edit.grammar' && value && typeof value === 'object') {
    const g = value as { shot_median_s: number; punch_per_min: number; sfx_per_min: number; humor: string };
    return `plano mediano de ${num(g.shot_median_s)} s, ${num(g.punch_per_min)} punch-ins/min, ${num(g.sfx_per_min)} SFX/min, humor ${HUMOR_LABELS[g.humor] ?? g.humor}`;
  }
  if (path === 'art.sources' && value && typeof value === 'object') {
    const s = value as { ai_max_share: number };
    return `IA em até ${Math.round(s.ai_max_share * 100)}% do tempo`;
  }
  if (path === 'edit.camera' && value && typeof value === 'object') {
    const w = value as Record<string, number>;
    const total = Object.values(w).reduce((a, b) => a + b, 0) || 1;
    return Object.entries(w)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `${label(CAMERA_LABELS, k).toLowerCase()} ${Math.round((v / total) * 100)}%`)
      .join(', ');
  }
  if ((path === 'edit.new_image_ratio' || path === 'voice.speed') && typeof value === 'number') {
    return path === 'voice.speed' ? `${num(value)}×` : `${Math.round(value * 100)}%`;
  }
  if (typeof value === 'boolean') return value ? 'sim' : 'não';
  if (typeof value === 'number') return num(value);
  if (typeof value === 'string') return value;
  const text = JSON.stringify(value);
  return text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

/** Lista "de onde veio cada valor", agrupada pela medida. */
export const RefStyleOriginsList: React.FC<{ result: RefStyleResult }> = ({ result }) => {
  const groups = SOURCE_ORDER.map((source) => ({ source, items: result.origins.filter((o) => o.source === source) })).filter((g) => g.items.length);
  return (
    <div className="flex flex-col gap-3">
      {groups.map((g) => (
        <div key={g.source}>
          <p className="text-xs font-medium text-subtle">{SOURCE_LABELS[g.source]}</p>
          <ul className="mt-1 flex flex-col gap-1">
            {g.items.map((o) => (
              <li key={`${o.source}-${o.path}`} className="text-[13px] text-fg">
                <span className="text-muted">{FIELD_LABELS[o.path] ?? o.path}:</span> {o.path === 'base' ? o.detail : showValue(o.path, o.value)}
                {o.path !== 'base' && o.detail ? <span className="text-subtle"> ({o.detail})</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ))}
      {result.suggestions?.map((s) => (
        <p key={s} className="text-[13px] text-muted">
          Sugestão: {s}
        </p>
      ))}
      {result.llm.message ? <p className="text-[13px] text-muted">{result.llm.message}</p> : null}
      {result.llm.used && result.llm.summary && result.origins.some((o) => o.source === 'llm') ? <p className="text-[13px] text-muted">IA: {result.llm.summary}</p> : null}
      {result.rejected.length ? (
        <p className="text-xs text-subtle">
          {result.rejected.length} sugestão(ões) da IA ficaram de fora (campo medido ou valor inválido): {result.rejected.map((r) => r.path).join(', ')}.
        </p>
      ) : null}
    </div>
  );
};

/** Botão na página Referências. */
export const ReferenceStyleButton: React.FC<{ reference: ReferenceVideo }> = ({ reference }) => {
  const client = useQueryClient();
  const navigate = useNavigate();
  const { toast } = useToast();
  const [useLlm, setUseLlm] = useState(true);
  // null = ainda não mexeu: mostra o nome que a API vai dar (do título limpo, que segue um renomear enquanto a tela
  // está aberta) e não manda `name`, para o backend calcular o padrão a partir do título limpo.
  const [name, setName] = useState<string | null>(null);
  const nameValue = name ?? reference.suggested_style_name ?? defaultStyleName(reference.title);
  const create = useMutation({
    mutationFn: () =>
      visualApi.styleFromReference(reference.id, { use_llm: useLlm, name: name === null ? undefined : name.trim() || undefined }),
    onSuccess: (result) => {
      void client.invalidateQueries({ queryKey: ['styles'] });
      toast({ tone: 'good', title: 'Estilo criado', description: `“${result.style.name}” está em Meus estilos.` });
      const state: OriginsState = { fromReference: { title: reference.title, result } };
      navigate(`/estilos/${result.style.id}`, { state });
    },
  });
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line p-3">
      <p className="text-[13px] text-muted">
        Vira um estilo novo em Meus estilos: parte do preset mais parecido e usa o ritmo dos cortes, a paleta e a proporção medidos. O material do vídeo
        não é usado.
      </p>
      <Field label="Nome do estilo" hint="Dá para mudar depois no editor de estilos.">
        <Input value={nameValue} maxLength={STYLE_NAME_MAX} onChange={(e) => setName(e.target.value)} className="max-w-md" />
      </Field>
      <Checkbox
        label="Deixar a IA completar o que as medidas não dizem"
        description="Arte, câmera e trilha. As medidas não mudam. Sem IA disponível, o estilo sai só com as medidas e o preset."
        checked={useLlm}
        onChange={setUseLlm}
      />
      <div>
        <Button icon={Wand2} variant="primary" busy={create.isPending} onClick={() => create.mutate()}>
          Criar estilo a partir desta referência
        </Button>
      </div>
      <ErrorBox error={create.error} title="Não foi possível criar o estilo" />
    </div>
  );
};

/** No editor de estilos: aviso "criado a partir da referência" com a origem de cada valor (só logo depois de criar).
 * O estado da navegação fica guardado aqui e é limpo do histórico, senão o aviso voltaria (com origens velhas)
 * ao recarregar ou ao usar Voltar. */
export const ReferenceOrigins: React.FC<{ styleId: string }> = ({ styleId }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const [data] = useState(() => (location.state as OriginsState | null)?.fromReference);
  const [open, setOpen] = useState(true);
  useEffect(() => {
    if ((location.state as OriginsState | null)?.fromReference)
      navigate(`${location.pathname}${location.search}${location.hash}`, { replace: true, state: null });
  }, [location, navigate]);
  if (!data || data.result.style.id !== styleId || !open) return null;
  return (
    <div role="note" className="mb-6 flex flex-col gap-3 rounded-xl border border-line bg-card p-4">
      <div className="flex items-start gap-3">
        <Palette size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-fg">Criado a partir da referência “{data.title}”</p>
          <p className="mt-0.5 text-sm text-muted">De onde veio cada valor. Ajuste à vontade: o estilo já está salvo em Meus estilos.</p>
        </div>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
          Fechar
        </Button>
      </div>
      <RefStyleOriginsList result={data.result} />
    </div>
  );
};
