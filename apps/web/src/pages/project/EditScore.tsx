// Índice "cara de IA" do projeto (ADR 0004 §3.10 e §6): nota, detalhamento por componente, faixa da timeline colorida
// por nível de fonte (onde está a IA no tempo) e os itens `perfil.*`. Só o componente: a montagem na Prévia e na
// revisão do storyboard fica com o item `ui-edicao`, que liga os botões (Reeditar e Trocar IA por real).
import { useQuery } from "@tanstack/react-query";
import { Gauge, ImageOff, Wand2 } from "lucide-react";
import type React from "react";
import {
  type AiLookBand,
  checksApi,
  type EditMetrics,
  type EditMetricsSource,
  type Prov,
} from "../../apiChecks";
import { Badge, type BadgeTone, Button, ErrorBox, Loading } from "../../ui";
import { CheckList, clock } from "./delivery/preflight/CheckList";

const BAND_TONE: Record<AiLookBand, BadgeTone> = { verde: "good", amarelo: "warn", vermelho: "bad" };

export const PROV_LABEL: Record<Prov, string> = {
  own: "Próprio",
  official: "Oficial",
  capture: "Captura",
  free: "Foto livre",
  stock: "Banco",
  graphic: "Gráfico",
  meme: "Meme",
  ai: "IA",
};

export const PROV_COLOR: Record<Prov, string> = {
  own: "bg-emerald-400",
  official: "bg-sky-400",
  capture: "bg-cyan-300",
  free: "bg-teal-400",
  stock: "bg-indigo-300",
  graphic: "bg-zinc-400",
  meme: "bg-amber-300",
  ai: "bg-fuchsia-500",
};

const percent = (value: number) => `${Math.round(value * 100)}%`;

export const useEditMetrics = (
  projectId: string,
  lang: string,
  source: "auto" | EditMetricsSource = "auto",
  refreshKey: string | number = "",
) =>
  useQuery({
    queryKey: ["edit-metrics", projectId, lang, source, refreshKey],
    queryFn: () => checksApi.editMetrics(projectId, lang, source),
    staleTime: 15_000,
  });

/** Faixa horizontal da timeline, um trecho por sequência do mesmo nível de fonte. */
export const ProvStrip: React.FC<{
  data: EditMetrics;
  onSeek?: (seconds: number, sceneId: string) => void;
}> = ({ data, onSeek }) => {
  const total = data.duration || data.strip.at(-1)?.end || 1;
  const present = [...new Set(data.strip.map((s) => s.prov))];
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex h-3 w-full overflow-hidden rounded-sm bg-canvas" role="img" aria-label="Fonte do material ao longo do vídeo">
        {data.strip.map((span) => (
          <button
            key={`${span.start}-${span.prov}`}
            type="button"
            title={`${PROV_LABEL[span.prov]} · ${clock(span.start)}–${clock(span.end)}`}
            aria-label={`${PROV_LABEL[span.prov]} de ${clock(span.start)} a ${clock(span.end)}`}
            className={`h-full ${PROV_COLOR[span.prov]} ${onSeek ? "cursor-pointer hover:opacity-80" : "cursor-default"}`}
            style={{ width: `${((span.end - span.start) / total) * 100}%` }}
            onClick={onSeek ? () => onSeek(span.start, span.scene_id) : undefined}
            tabIndex={onSeek ? 0 : -1}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
        {present.map((prov) => (
          <span key={prov} className="inline-flex items-center gap-1">
            <span aria-hidden="true" className={`h-2 w-2 rounded-sm ${PROV_COLOR[prov]}`} />
            {PROV_LABEL[prov]}
          </span>
        ))}
      </div>
    </div>
  );
};

export const EditScoreView: React.FC<{
  data: EditMetrics;
  onReedit?: () => void;
  onReplaceAi?: (sceneIds: string[]) => void;
  onSeek?: (seconds: number, sceneId: string) => void;
  busy?: boolean;
}> = ({ data, onReedit, onReplaceAi, onSeek, busy }) => {
  const { index, metrics } = data;
  const aiScenes = [...new Set(data.strip.filter((s) => s.prov === "ai").map((s) => s.scene_id))];
  const issues = data.items.filter((i) => i.level !== "ok");
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-baseline gap-1.5">
          <span className="font-display text-3xl font-semibold tabular-nums text-fg">{index.score}</span>
          <span className="text-sm text-muted">de 100</span>
        </div>
        <Badge tone={BAND_TONE[index.band]} icon={Gauge}>
          Cara de IA: {index.band}
        </Badge>
        <span className="text-xs text-subtle">Medido na {data.source_label}</span>
        {data.adherence?.score != null ? (
          <Badge tone={data.adherence.score >= 70 ? "good" : "warn"}>Aderência à referência: {data.adherence.score}</Badge>
        ) : null}
      </div>

      <ProvStrip data={data} onSeek={onSeek} />

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-[13px] sm:grid-cols-4">
        <div>
          <dt className="text-subtle">IA na tela</dt>
          <dd className="tabular-nums text-fg">{percent(metrics.ai_screen_share)}</dd>
        </div>
        <div>
          <dt className="text-subtle">Material real</dt>
          <dd className="tabular-nums text-fg">{percent(metrics.real_share)}</dd>
        </div>
        <div>
          <dt className="text-subtle">Plano mediano</dt>
          <dd className="tabular-nums text-fg">{metrics.shot_median.toFixed(1)} s</dd>
        </div>
        <div>
          <dt className="text-subtle">Maior trecho parado</dt>
          <dd className="tabular-nums text-fg">{metrics.max_still_s.toFixed(1)} s</dd>
        </div>
      </dl>

      <ul className="flex flex-col gap-1.5" aria-label="Detalhamento do índice">
        {index.parts.map((part) => (
          <li key={part.key} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 text-[13px]">
            <div className="min-w-0">
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-fg">{part.label}</span>
                <span className="shrink-0 tabular-nums text-subtle">
                  {part.value.toFixed(2)} · alvo {part.target}
                </span>
              </div>
              <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-sm bg-canvas">
                <div
                  className={part.penalty > 0.5 ? "h-full bg-red-400" : part.penalty > 0 ? "h-full bg-amber-300" : "h-full bg-emerald-400"}
                  style={{ width: `${Math.max(2, part.penalty * 100)}%` }}
                />
              </div>
            </div>
            <span className="w-14 text-right tabular-nums text-muted">
              {part.points.toFixed(0)}/{part.weight}
            </span>
          </li>
        ))}
      </ul>

      {onReedit || (onReplaceAi && aiScenes.length) ? (
        <div className="flex flex-wrap gap-2">
          {onReedit ? (
            <Button size="sm" icon={Wand2} onClick={onReedit} busy={busy}>
              Reeditar
            </Button>
          ) : null}
          {onReplaceAi && aiScenes.length ? (
            <Button size="sm" icon={ImageOff} onClick={() => onReplaceAi(aiScenes)} busy={busy}>
              Trocar IA por real ({aiScenes.length} {aiScenes.length === 1 ? "cena" : "cenas"})
            </Button>
          ) : null}
        </div>
      ) : null}

      {issues.length ? <CheckList items={issues} levels={["erro", "aviso"]} /> : null}
    </div>
  );
};

/** Busca as métricas do projeto e mostra o índice. `refreshKey` muda quando a timeline muda (prévia, reedição). */
export const EditScore: React.FC<{
  projectId: string;
  lang: string;
  source?: "auto" | EditMetricsSource;
  refreshKey?: string | number;
  onReedit?: () => void;
  onReplaceAi?: (sceneIds: string[]) => void;
  onSeek?: (seconds: number, sceneId: string) => void;
  busy?: boolean;
}> = ({ projectId, lang, source = "auto", refreshKey = "", ...rest }) => {
  const query = useEditMetrics(projectId, lang, source, refreshKey);
  if (query.isPending) return <Loading label="Medindo a edição…" />;
  if (query.isError) return <ErrorBox error={query.error} title="Não deu para medir a edição" onRetry={() => query.refetch()} />;
  return <EditScoreView data={query.data} {...rest} />;
};

export default EditScore;
