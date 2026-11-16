// Peças compartilhadas pelas abas Prévia e Vídeo e publicação.
import { useQuery } from "@tanstack/react-query";
import {
  Check,
  Copy,
  Info,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import React, { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  api,
  type BuildResult,
  type Job,
  JOB_LABELS,
  type ProjectDetail,
  type ProjectDoc,
  toLocalInput,
  type Version,
} from "../../../api";
import { langLabel } from "../../../labels";
import { Button, formatMoney, ProgressBar } from "../../../ui";

/* ------------------------------------------------------------------ */
/* Idioma compartilhado (?idioma=)                                      */
/* ------------------------------------------------------------------ */

/** Idioma das abas Prévia e Vídeo: fica na URL (?idioma=), preservando as outras chaves (?aba=…). */
export const useDeliveryLang = (
  doc: ProjectDoc,
): [string, (lang: string) => void] => {
  const [params, setParams] = useSearchParams();
  const raw = params.get("idioma");
  const lang = raw && doc.languages.includes(raw) ? raw : doc.primary_language;
  const setLang = (next: string) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (next === doc.primary_language) p.delete("idioma");
        else p.set("idioma", next);
        return p;
      },
      { replace: true },
    );
  return [lang, setLang];
};

/** Seletor segmentado de idioma. Some quando o projeto tem um idioma só. */
export const LangSwitcher: React.FC<{
  languages: string[];
  value: string;
  onChange: (lang: string) => void;
  /** Marca com um ponto verde os idiomas que já têm resultado (ex.: vídeo final pronto). */
  ready?: (lang: string) => boolean;
  readyLabel?: string;
}> = ({ languages, value, onChange, ready, readyLabel = "pronto" }) => {
  if (languages.length <= 1) return null;
  return (
    <div
      role="group"
      aria-label="Idioma"
      className="flex flex-wrap items-center gap-0.5 rounded-lg border border-line bg-canvas p-0.5"
    >
      {languages.map((l) => {
        const selected = l === value;
        const isReady = ready?.(l) ?? false;
        return (
          <button
            key={l}
            type="button"
            aria-pressed={selected}
            onClick={() => onChange(l)}
            className={`inline-flex min-h-7 items-center gap-1.5 rounded-md px-2.5 py-1 text-[13px] transition-colors ${
              selected
                ? "bg-accent/15 font-medium text-accent ring-1 ring-inset ring-accent/50"
                : "text-muted hover:text-fg"
            }`}
          >
            {ready ? (
              <span
                aria-hidden="true"
                className={`h-1.5 w-1.5 rounded-full ${isReady ? "bg-emerald-400" : "bg-line-strong"}`}
              />
            ) : null}
            {langLabel(l)}
            {ready && isReady ? (
              <span className="sr-only">({readyLabel})</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Impressão digital do build (cache das consultas e dos arquivos)     */
/* ------------------------------------------------------------------ */

const hashString = (s: string) => {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
};

/**
 * Token que muda a cada build: manifesto de artefatos do último resultado + fim do último job que gera esse
 * resultado. Os arquivos de saída têm caminho fixo (timeline.json, <titulo>.mp4, thumbnail.jpg), então o
 * token entra na chave das consultas e como ?b= nas URLs para o navegador não mostrar a versão antiga.
 */
/** O job mexeu na principal? Um `ab_build` só dos ramos (`branches` sem "main") não regrava a prévia dela. */
export const buildsMain = (job: Job) => {
  if (job.kind !== "ab_build") return true;
  const branches = job.payload?.branches;
  return !Array.isArray(branches) || branches.includes("main");
};

export const buildToken = (
  build: BuildResult | null | undefined,
  jobs: Job[],
  kinds: string[],
): string => {
  const lastDone = jobs
    .filter(
      (j) =>
        kinds.includes(j.kind) &&
        j.status === "done" &&
        j.finished_at &&
        buildsMain(j),
    )
    .map((j) => j.finished_at as string)
    .sort()
    .pop();
  const art = build?.artifacts?.join("|") ?? "";
  if (!art && !lastDone)
    return build ? hashString(JSON.stringify(build.langs)) : "nenhum";
  return hashString(`${art}#${lastDone ?? ""}`);
};

/** Acrescenta o token do build à URL de um arquivo. */
export const withBust = (url: string, token: string) =>
  url ? `${url}${url.includes("?") ? "&" : "?"}b=${token}` : url;

/* ------------------------------------------------------------------ */
/* Jobs                                                                 */
/* ------------------------------------------------------------------ */

export const isActive = (job: Job) =>
  job.status === "queued" || job.status === "running";

/** Primeiro job ativo de um dos tipos (opcionalmente filtrado). */
export const findActiveJob = (
  detail: ProjectDetail,
  kinds: string[],
  filter?: (job: Job) => boolean,
) =>
  detail.jobs.find(
    (j) => isActive(j) && kinds.includes(j.kind) && (!filter || filter(j)),
  );

/** Estado ao vivo de um job (mesma chave ['job', id] que os eventos do servidor atualizam). */
export const useLiveJob = (job: Job | undefined) => {
  const live = useQuery({
    queryKey: ["job", job?.id ?? "nenhum"],
    queryFn: () => api.get<Job>(`/api/jobs/${job!.id}`),
    initialData: job,
    enabled: Boolean(job),
    refetchInterval: 5_000,
  });
  return job ? (live.data ?? job) : undefined;
};

/** Progresso de um job: rótulo, mensagem e porcentagem. */
export const JobProgress: React.FC<{
  job: Job;
  title?: string;
  className?: string;
}> = ({ job, title, className = "" }) => {
  const live = useLiveJob(job) ?? job;
  const queued = live.status === "queued";
  const pct = Math.round((live.progress ?? 0) * 100);
  const name = title ?? JOB_LABELS[live.kind] ?? "Tarefa";
  // Leitores de tela ouvem só a troca de estado (na fila → rodando → concluído), não cada tique.
  const statusText =
    live.status === "queued"
      ? `${name}: na fila`
      : live.status === "running"
        ? `${name}: em andamento`
        : live.status === "done"
          ? `${name}: concluído`
          : live.status === "failed"
            ? `${name}: falhou`
            : `${name}: cancelado`;
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <span className="sr-only" role="status">
        {statusText}
      </span>
      <div className="flex items-baseline justify-between gap-3 text-[13px]">
        <span className="min-w-0 truncate font-medium text-fg">
          {name}
          {live.message && !queued ? (
            <span className="font-normal text-muted"> · {live.message}</span>
          ) : null}
        </span>
        <span className="shrink-0 tabular-nums text-muted">
          {queued ? "Na fila" : `${pct}%`}
        </span>
      </div>
      <ProgressBar
        value={live.progress ?? 0}
        indeterminate={queued}
        label={name}
        valueText={queued ? "Na fila" : undefined}
      />
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Avisos em linha                                                      */
/* ------------------------------------------------------------------ */

const NOTICE_TONES = {
  warn: {
    box: "border-amber-300/35 bg-amber-300/10",
    icon: "text-amber-200",
    Icon: TriangleAlert,
  },
  info: {
    box: "border-sky-400/35 bg-sky-400/10",
    icon: "text-sky-300",
    Icon: Info,
  },
} as const;

/** Aviso que não é erro (âmbar ou azul), com ações opcionais. */
export const Notice: React.FC<{
  tone?: keyof typeof NOTICE_TONES;
  title?: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode;
  icon?: LucideIcon;
  className?: string;
}> = ({ tone = "warn", title, children, actions, icon, className = "" }) => {
  const t = NOTICE_TONES[tone];
  const Icon = icon ?? t.Icon;
  return (
    <div
      className={`flex items-start gap-3 rounded-lg border p-3 text-sm ${t.box} ${className}`}
    >
      <Icon
        size={18}
        strokeWidth={2}
        aria-hidden="true"
        className={`mt-0.5 shrink-0 ${t.icon}`}
      />
      <div className="flex min-w-0 flex-1 flex-wrap items-start gap-x-3 gap-y-2">
        <div className="flex min-w-0 grow basis-56 flex-col gap-0.5">
          {title ? <div className="font-medium text-fg">{title}</div> : null}
          {children ? (
            <div className="text-[13px] text-muted">{children}</div>
          ) : null}
        </div>
        {actions ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
          </div>
        ) : null}
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Avisos do build (narração com problema etc.)                         */
/* ------------------------------------------------------------------ */

export type BuildWarning =
  | {
      kind: "voice";
      raw: string;
      segmentId: string;
      lang: string;
      title: string;
      detail: string;
    }
  | { kind: "other"; raw: string; title: string; detail: string };

/** "do segmento 3" / "no segmento 3", ou "de um segmento" / "em um segmento" quando o id sumiu. */
export const segRef = (doc: ProjectDoc, id: unknown) => {
  const i = doc.segments.findIndex((s) => s.id === id);
  return i >= 0
    ? { n: i + 1, nome: `segmento ${i + 1}`, de: `do segmento ${i + 1}`, em: `no segmento ${i + 1}` }
    : { n: 0, nome: "um segmento", de: "de um segmento", em: "em um segmento" };
};

/** Nome legível de uma faixa da biblioteca a partir do caminho ("terror/dark_ambience_03.mp3" → "dark ambience 03"). */
export const trackName = (path: string) =>
  (path.split(/[\\/]/).pop() ?? path)
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/[_-]+/g, " ")
    .trim();

const PINNED_MISSING = /^A faixa fixada '(.+)' não está mais na biblioteca/;
const LIBRARY_EMPTY = /^A biblioteca de músicas está vazia/;

const VOICE_WARNING =
  /^Narração de (\S+) \(([^)]+)\) com problema(?: depois de (\d+) tentativa\(s\))?: ([\s\S]*?)\.?\s*Ouça esse trecho antes de publicar\.?$/;

/** Traduz um aviso do build para texto humano ("Segmento 4" no lugar do id). */
export const humanizeWarning = (raw: string, doc: ProjectDoc): BuildWarning => {
  const m = VOICE_WARNING.exec(raw.trim());
  if (m) {
    const [, segmentId, lang, attempts, problems] = m;
    const ref = segRef(doc, segmentId);
    const langPart = doc.languages.length > 1 ? ` (${langLabel(lang)})` : "";
    return {
      kind: "voice",
      raw,
      segmentId,
      lang,
      title: `Narração ${ref.de}${langPart} com problema`,
      detail: `${problems.trim()}${attempts ? ` (depois de ${attempts} tentativa${attempts === "1" ? "" : "s"})` : ""}. Ouça o trecho antes de publicar.`,
    };
  }
  const pinned = PINNED_MISSING.exec(raw.trim());
  if (pinned) {
    return {
      kind: "other",
      raw,
      title: `A faixa fixada (${trackName(pinned[1])}) não está mais na biblioteca`,
      detail: "O app escolheu outra faixa. Fixe outra na aba Trilha.",
    };
  }
  if (LIBRARY_EMPTY.test(raw.trim())) {
    return {
      kind: "other",
      raw,
      title: "A biblioteca de músicas está vazia",
      detail:
        "O app usou uma trilha gerada. Para usar suas músicas, coloque faixas na biblioteca (veja em Configurações).",
    };
  }
  // Troca ids de segmento soltos por "segmento N".
  let text = raw;
  doc.segments.forEach((s, i) => {
    if (s.id && text.includes(s.id))
      text = text.split(s.id).join(`segmento ${i + 1}`);
  });
  return { kind: "other", raw, title: text, detail: "" };
};

export const isMusicWarning = (raw: string) => /faixa|música|trilha/i.test(raw);

/* ------------------------------------------------------------------ */
/* Resumo do build                                                      */
/* ------------------------------------------------------------------ */

const ARTIFACT_KIND_LABELS: Record<string, string> = {
  tts: "narrações",
  align: "sincronias de legenda",
  image: "imagens",
  music: "trilhas",
  mix: "mixagens de áudio",
  chunk: "blocos de vídeo",
  final: "vídeo final",
  thumbnail: "capas",
  sfx: "efeitos sonoros",
};

const sumValues = (rec: Record<string, number> | undefined) =>
  Object.values(rec ?? {}).reduce((a, b) => a + (Number(b) || 0), 0);

/** "3 itens refeitos · 52 reaproveitados · sem custo", com o detalhamento num <details>. */
export const BuildSummary: React.FC<{
  build: BuildResult;
  className?: string;
}> = ({ build, className = "" }) => {
  const made = sumValues(build.generated);
  const reused = sumValues(build.reused);
  const cost =
    build.cost_usd > 0 ? `custo ${formatMoney(build.cost_usd)}` : "sem custo";
  const rows = (rec: Record<string, number>) =>
    Object.entries(rec)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${v} ${ARTIFACT_KIND_LABELS[k] ?? "outros itens"}`)
      .join(", ");
  return (
    <details className={`text-[13px] text-subtle ${className}`}>
      <summary className="w-fit cursor-pointer rounded-sm tabular-nums hover:text-fg">
        {made
          ? `${made} ${made === 1 ? "item gerado" : "itens gerados"}`
          : "Nada novo gerado"}{" "}
        · {reused} reaproveitado{reused === 1 ? "" : "s"} do cache · {cost}
      </summary>
      <div className="mt-1.5 flex flex-col gap-0.5 pl-3">
        <div>Gerado agora: {rows(build.generated) || "nada"}</div>
        <div>Reaproveitado: {rows(build.reused) || "nada"}</div>
      </div>
    </details>
  );
};

/* ------------------------------------------------------------------ */
/* Utilidades                                                           */
/* ------------------------------------------------------------------ */

/** Botão "Copiar" que confirma por 2 s. */
export const CopyButton: React.FC<{
  text: string;
  label?: string;
  /** Nome acessível com contexto (ex.: "Copiar legenda (TikTok)"). */
  ariaLabel?: string;
  className?: string;
}> = ({ text, label = "Copiar", ariaLabel, className = "" }) => {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  return (
    <Button
      size="sm"
      variant="ghost"
      icon={copied ? Check : Copy}
      className={className}
      aria-label={copied ? undefined : ariaLabel}
      disabled={!text}
      onClick={() => {
        navigator.clipboard?.writeText(text).then(
          () => {
            setCopied(true);
            window.clearTimeout(timer.current);
            timer.current = window.setTimeout(() => setCopied(false), 2000);
          },
          () => undefined,
        );
      }}
    >
      <span aria-live="polite">{copied ? "Copiado" : label}</span>
    </Button>
  );
};

/** Mensagem da versão criada ao salvar título/descrição à mão. */
export const METADATA_EDIT_MESSAGE = "Título e descrição editados";

/** Versões que não mudam a imagem nem o som (não deixam a prévia desatualizada). */
export const NON_VISUAL_VERSION_MESSAGES = new Set([
  "Prévia aprovada",
  "Roteiro aprovado",
  "Cenas aprovadas",
  "Metadados (título, descrição, tags)",
  METADATA_EDIT_MESSAGE,
]);

/**
 * Um único aviso do navegador ao recarregar ou fechar a janela quando há rascunho destas abas (título e
 * descrição, instrução ou proposta da prévia): os rascunhos sobrevivem à navegação dentro do app.
 */
const unloadChecks = new Set<() => boolean>();
export const guardUnload = (check: () => boolean) => {
  if (typeof window === "undefined") return;
  if (!unloadChecks.size) {
    window.addEventListener("beforeunload", (event) => {
      if (![...unloadChecks].some((c) => c())) return;
      event.preventDefault();
      // Navegadores antigos exigem returnValue para mostrar o aviso.
      event.returnValue = "";
    });
  }
  unloadChecks.add(check);
};

/** Valor mínimo para <input type="datetime-local"> (agora). */
export const nowLocalInput = () => toLocalInput(new Date().toISOString());

export const isFutureLocal = (value: string) => {
  const t = new Date(value).getTime();
  return Number.isFinite(t) && t > Date.now();
};

/* ------------------------------------------------------------------ */
/* Estado da prévia e do vídeo final (mesma regra nas duas abas)        */
/* ------------------------------------------------------------------ */

/** Dispensar ou editar a sugestão de mídia real (routes_media.py) não muda o vídeo: só a mídia anexada muda. */
const REAL_HINT_VERSION = /: sugestão de mídia real (dispensada|editada)$/;

const isVisualVersion = (v: Version) =>
  !NON_VISUAL_VERSION_MESSAGES.has(v.message) && !REAL_HINT_VERSION.test(v.message);
const parseTime = (iso: string | null | undefined) => (iso ? Date.parse(iso) : NaN);

/** Jobs que regravam o timeline.json da prévia. */
export const TIMELINE_JOB_KINDS = ["preview", "autopilot", "render", "ab_build"];

/** Versão da linha principal (A)? As versões dos ramos A/B ("b", "c"...) não mudam a prévia nem o vídeo da principal. */
export const isMainVersion = (v: Version) => !v.branch || v.branch === "main";

/** Só as versões da linha principal. */
export const mainVersions = (versions: Version[]) => versions.filter(isMainVersion);

/** Job concluído mais recente de um dos tipos. */
export const lastDoneJob = (
  detail: ProjectDetail,
  kinds: string[],
  filter?: (job: Job) => boolean,
) =>
  detail.jobs
    .filter(
      (j) =>
        kinds.includes(j.kind) &&
        j.status === "done" &&
        j.finished_at &&
        buildsMain(j) &&
        (!filter || filter(j)),
    )
    .sort((a, b) => parseTime(b.finished_at) - parseTime(a.finished_at))[0];

/** Há versão que muda imagem ou som criada depois deste momento? */
const visualChangeSince = (detail: ProjectDetail, since: number) =>
  Number.isFinite(since) &&
  detail.versions.some(
    (v) => isMainVersion(v) && isVisualVersion(v) && parseTime(v.created_at) > since,
  );

/**
 * Prévia desatualizada e aprovada.
 * - Desatualizada: há versão que muda imagem ou som depois da prévia (pela versão do timeline, quando
 *   carregado; senão pelo início do último job que montou a prévia).
 * - Aprovada: `preview_approved`, sem mudanças pendentes e a última "Prévia aprovada" vem depois da última
 *   versão visual (salvar o título durante o render não desfaz a aprovação).
 */
export const previewApproval = (
  detail: ProjectDetail,
  timelineVersion?: number,
): { stale: boolean; approved: boolean } => {
  // Versões dos ramos A/B não mexem na prévia da principal (a numeração é a mesma, mas a linha é outra).
  const versions = mainVersions(detail.versions);
  const hasPreview = Boolean(detail.outputs.preview);
  let stale = false;
  if (timelineVersion != null) {
    const newer = versions.filter((v) => v.number > timelineVersion);
    // A lista vem cortada (últimas versões): se ela não alcança a versão da prévia, pode faltar mudança no meio.
    const oldest = detail.versions.reduce((m, v) => Math.min(m, v.number), Infinity);
    const unlisted =
      detail.project.current_version > timelineVersion && oldest > timelineVersion + 1;
    stale = newer.some(isVisualVersion) || unlisted;
  } else if (hasPreview) {
    const last = lastDoneJob(detail, TIMELINE_JOB_KINDS);
    stale = Boolean(
      last && visualChangeSince(detail, parseTime(last.started_at ?? last.finished_at)),
    );
  }
  const numbers = (list: Version[]) =>
    list.reduce((m, v) => Math.max(m, v.number), -1);
  const lastVisual = numbers(versions.filter(isVisualVersion));
  const lastApproval = numbers(
    versions.filter((v) => v.message === "Prévia aprovada"),
  );
  // A lista vem cortada (últimas versões): sem aprovação nem mudança visual nela, vale o campo do projeto.
  const approvalInWindow =
    lastApproval > lastVisual || (lastApproval < 0 && lastVisual < 0);
  const approved =
    hasPreview && detail.doc.preview_approved && !stale && approvalInWindow;
  return { stale, approved };
};

/** Último job que gerou o vídeo final (render; o piloto automático só conta quando renderiza direto). */
export const finalJobKinds = (detail: ProjectDetail) =>
  detail.project.settings.gates?.preview === false
    ? ["render", "autopilot"]
    : ["render"];

/** O vídeo final ficou para trás: há mudança de imagem ou som depois do início do último render. */
export const isFinalStale = (detail: ProjectDetail) => {
  if (!detail.outputs.final) return false;
  const last = lastDoneJob(detail, finalJobKinds(detail));
  return Boolean(
    last && visualChangeSince(detail, parseTime(last.started_at ?? last.finished_at)),
  );
};

/** Token só do manifesto de artefatos (muda quando o conteúdo muda); o fim do job é a reserva. */
export const artifactsToken = (
  build: BuildResult | null | undefined,
  jobs: Job[],
  kinds: string[],
) =>
  build?.artifacts?.length
    ? hashString(build.artifacts.join("|"))
    : buildToken(build, jobs, kinds);

/* ------------------------------------------------------------------ */
/* Provedores de teste                                                 */
/* ------------------------------------------------------------------ */

/** Provedores de teste conhecidos, para quando a lista de provedores ainda não chegou. */
const TEST_TTS = new Set(["windows_sapi", "mock_tts"]);
const TEST_IMAGE = new Set(["placeholder"]);

/** O que saiu de teste num build: voz, imagens ou os dois (pelos ids de `preview_only_providers`). */
export const testProviderKinds = (
  ids: string[],
  providers?: { id: string; kind: string }[],
) => {
  const kindOf = (id: string) =>
    providers?.find((p) => p.id === id)?.kind ??
    (TEST_TTS.has(id) ? "tts" : TEST_IMAGE.has(id) ? "image" : "other");
  const kinds = ids.map(kindOf);
  return { voice: kinds.includes("tts"), images: kinds.includes("image") };
};

/** Rótulo do selo de teste: "Voz de teste", "Imagens de teste" ou "Voz e imagens de teste". */
export const testProvidersLabel = (k: { voice: boolean; images: boolean }) =>
  k.voice && k.images
    ? "Voz e imagens de teste"
    : k.voice
      ? "Voz de teste"
      : k.images
        ? "Imagens de teste"
        : "Provedores de teste";

/** Nome curto do provedor ("Gemini 3.8 Flash TTS (Google AI Studio)" → "Gemini"). */
export const shortProviderName = (name: string) =>
  name.replace(/\s*\(.*\)\s*$/, "").split(/\s+/)[0] || name;
