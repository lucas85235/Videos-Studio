// Estado da conferência antes de publicar (ADR 0003, bloco H): consulta, rótulos e o selo usado perto do passo Publicar.
import { useQuery } from "@tanstack/react-query";
import { CircleCheck, OctagonAlert, TriangleAlert } from "lucide-react";
import type React from "react";
import type { ProjectDetail } from "../../../../api";
import { type CheckItem, type CheckLevel, checksApi } from "../../../../apiChecks";
import { Badge, type BadgeTone } from "../../../../ui";
import { finalJobKinds, lastDoneJob, TIMELINE_JOB_KINDS } from "../shared";

export const LEVEL_ORDER: Record<CheckLevel, number> = { erro: 0, aviso: 1, ok: 2 };

export const LEVEL_TONE: Record<CheckLevel, BadgeTone> = {
  ok: "good",
  aviso: "warn",
  erro: "bad",
};

export const LEVEL_ICON = {
  ok: CircleCheck,
  aviso: TriangleAlert,
  erro: OctagonAlert,
} as const;

/** Último job `final_check` do idioma (ativo ou não), para refazer a consulta quando ele anda ou termina. */
export const lastFinalCheckJob = (detail: ProjectDetail, lang: string) =>
  detail.jobs.find(
    (j) =>
      j.kind === "final_check" &&
      String(j.payload?.lang ?? detail.doc.primary_language) === lang,
  );

/**
 * Último job concluído que muda o resultado da conferência: render (vídeo novo no mesmo caminho), prévia (regrava a
 * timeline e as legendas), checagem de fatos, conferência de áudio e miniaturas.
 */
export const lastInputJob = (detail: ProjectDetail) =>
  lastDoneJob(detail, [
    ...new Set([
      ...finalJobKinds(detail),
      ...TIMELINE_JOB_KINDS,
      "factcheck",
      "audio_check",
      "thumbs_variants",
    ]),
  ]);

/** Último render do vídeo final concluído (para disparar a conferência sozinha uma vez por render). */
export const lastFinalRender = (detail: ProjectDetail) =>
  lastDoneJob(detail, finalJobKinds(detail));

export const usePreflight = (detail: ProjectDetail, lang: string) => {
  const projectId = detail.doc.id;
  const job = lastFinalCheckJob(detail, lang);
  const video = detail.outputs.final?.langs?.[lang]?.video ?? null;
  return useQuery({
    queryKey: [
      "preflight",
      projectId,
      lang,
      video,
      job ? `${job.id}:${job.status}` : "sem-job",
      detail.project.current_version,
      lastInputJob(detail)?.id ?? "sem-entrada",
    ],
    queryFn: () => checksApi.preflight(projectId, lang),
    staleTime: 15_000,
  });
};

export const countBy = (items: CheckItem[], level: CheckLevel) =>
  items.filter((i) => i.level === level).length;

/** "Pronto para publicar" / "N avisos" / "N problemas". */
export const statusTitle = (items: CheckItem[]) => {
  const errors = countBy(items, "erro");
  const warns = countBy(items, "aviso");
  if (errors) return `${errors} ${errors === 1 ? "problema" : "problemas"}`;
  if (warns) return `${warns} ${warns === 1 ? "aviso" : "avisos"}`;
  return "Pronto para publicar";
};

/** Selo do estado da conferência, para perto do passo Publicar. */
export const PreflightBadge: React.FC<{ detail: ProjectDetail; lang: string }> = ({
  detail,
  lang,
}) => {
  const state = usePreflight(detail, lang);
  const data = state.data;
  if (!data?.video) return null;
  const items = data.items ?? data.quick;
  const label =
    data.status === "ok" && !data.fresh
      ? "Conferência pendente"
      : `Conferência: ${statusTitle(items).toLowerCase()}`;
  const tone: BadgeTone = data.status === "ok" && !data.fresh ? "neutral" : LEVEL_TONE[data.status];
  return (
    <a href="#antes-de-publicar" className="rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40">
      <Badge tone={tone} icon={LEVEL_ICON[data.status]} title="Ver a conferência antes de publicar">
        {label}
      </Badge>
    </a>
  );
};
