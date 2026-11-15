// Painel "Antes de publicar" da aba Vídeo final (ADR 0003, bloco H): conferência do vídeo final pronto.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ClipboardCheck, RefreshCw } from "lucide-react";
import type React from "react";
import { useEffect } from "react";
import { fileUrl, type ProjectDetail } from "../../../api";
import { checksApi, type FinalCheckData } from "../../../apiChecks";
import { Badge, Button, ErrorBox, formatRelative, Section } from "../../../ui";
import { CheckList, clock, sortItems } from "./preflight/CheckList";
import {
  LEVEL_ICON,
  LEVEL_TONE,
  lastFinalCheckJob,
  lastFinalRender,
  statusTitle,
  usePreflight,
} from "./preflight/state";
import { isActive, JobProgress, Notice } from "./shared";

/** Conferências já disparadas sozinhas nesta sessão (uma vez por vídeo, render e versão). */
const autoRan = new Set<string>();

const num = (v: number | null | undefined, digits = 1) =>
  v == null || !Number.isFinite(v) ? "—" : v.toFixed(digits).replace(".", ",");

export const PreflightPanel: React.FC<{ detail: ProjectDetail; lang: string }> = ({ detail, lang }) => {
  const projectId = detail.doc.id;
  const client = useQueryClient();
  const state = usePreflight(detail, lang);
  const job = lastFinalCheckJob(detail, lang);
  const running = job && isActive(job) ? job : undefined;
  const run = useMutation({
    mutationFn: () => checksApi.runPreflight(projectId, lang),
    onSuccess: () => client.invalidateQueries({ queryKey: ["project", projectId] }),
  });
  const data = state.data;
  const video = data?.video ?? null;
  const autoKey = `${projectId}|${lang}|${video}|${lastFinalRender(detail)?.id ?? ""}|${detail.project.current_version}`;

  // Roda sozinho uma vez quando há vídeo final sem relatório fresco (e nada rodando).
  useEffect(() => {
    if (!data || !video || data.fresh || running || run.isPending || autoRan.has(autoKey)) return;
    if (job?.status === "failed") return; // falhou: o usuário vê o erro e decide
    autoRan.add(autoKey);
    run.mutate();
  }, [data, video, running, run, autoKey, job?.status]);

  if (!data) {
    return state.error ? <ErrorBox error={state.error} onRetry={() => state.refetch()} /> : null;
  }
  if (!video) return null;

  const items = sortItems(data.items ?? data.quick);
  const report = data.report;
  const measures = (report?.data ?? {}) as FinalCheckData;
  const StatusIcon = LEVEL_ICON[data.status];
  const failed = !running && job?.status === "failed" ? job : undefined;

  return (
    <div id="antes-de-publicar" tabIndex={-1} className="focus:outline-none">
      <Section
        icon={ClipboardCheck}
        title="Antes de publicar"
        description="Volume, voz e música, tela preta, textos perto do corte, checagem de fatos e o que vai no envio."
        actions={
          <Button
            size="sm"
            icon={RefreshCw}
            busy={run.isPending}
            disabled={Boolean(running)}
            onClick={() => run.mutate()}
          >
            Conferir agora
          </Button>
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={LEVEL_TONE[data.status]} icon={StatusIcon}>
            {statusTitle(items)}
          </Badge>
          {report ? (
            <span className="text-xs text-subtle">
              Conferido {report.created_at ? formatRelative(report.created_at) : ""}
            </span>
          ) : null}
        </div>

        {running ? <JobProgress job={running} /> : null}
        {failed?.error ? <ErrorBox title="A conferência falhou" error={failed.error} /> : null}
        {run.error ? <ErrorBox error={run.error} /> : null}

        {report && !data.fresh && !running ? (
          <Notice
            tone="warn"
            title="Relatório antigo"
            actions={
              <Button size="sm" icon={RefreshCw} busy={run.isPending} onClick={() => run.mutate()}>
                Conferir de novo
              </Button>
            }
          >
            Esta conferência é de outro render ou de outra versão do projeto. Abaixo ficam só os avisos rápidos de
            agora.
          </Notice>
        ) : null}

        {report && data.fresh ? (
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-lg border border-line bg-canvas p-3 text-[13px] sm:grid-cols-4">
            <div>
              <dt className="text-subtle">Volume</dt>
              <dd className="tabular-nums text-fg">{num(measures.lufs)} LUFS</dd>
            </div>
            <div>
              <dt className="text-subtle">Pico real</dt>
              <dd className="tabular-nums text-fg">{num(measures.true_peak)} dBTP</dd>
            </div>
            <div>
              <dt className="text-subtle">Duração</dt>
              <dd className="tabular-nums text-fg">{measures.duration != null ? clock(measures.duration) : "—"}</dd>
            </div>
            <div>
              <dt className="text-subtle">Voz × música</dt>
              <dd className="tabular-nums text-fg">
                {measures.pauses?.length
                  ? `${measures.pauses
                      .map((p) => p.diff_db)
                      .filter((d): d is number => d != null)
                      .map((d) => num(d, 0))
                      .join(" · ")} dB`
                  : "—"}
              </dd>
            </div>
          </dl>
        ) : null}

        <CheckList items={items} />

        {report?.contact_sheet && data.fresh ? (
          <figure className="flex flex-col gap-1.5">
            <a href={fileUrl(report.contact_sheet)} target="_blank" rel="noreferrer" title="Abrir a folha em tamanho real">
              <img
                src={`${fileUrl(report.contact_sheet)}?v=${report.id}`}
                alt="Folha de contato com 12 quadros do vídeo final"
                className="max-h-80 w-auto rounded-lg border border-line object-contain"
                loading="lazy"
              />
            </a>
            <figcaption className="text-xs text-subtle">
              12 quadros do vídeo final, espalhados do começo ao fim. Clique para ver em tamanho real.
            </figcaption>
          </figure>
        ) : null}
      </Section>
    </div>
  );
};
