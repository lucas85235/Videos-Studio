import { Player, type PlayerRef } from "@remotion/player";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type TimelineProps, VideoTimeline } from "@vstudio/renderer";
import {
  ArrowRight,
  AudioLines,
  Check,
  Headphones,
  LayoutGrid,
  MonitorPlay,
  Music,
  Pencil,
  RotateCw,
} from "lucide-react";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Audio } from "remotion";
import {
  api,
  ApiError,
  fileUrl,
  JOB_LABELS,
  type ProjectDetail,
} from "../../api";
import { langLabel } from "../../labels";
import { useProviders } from "../../shell/data";
import {
  Badge,
  Button,
  Empty,
  ErrorBox,
  formatSeconds,
  PLAYBACK_RATES,
  Skeleton,
  Tabs,
  tabPanelProps,
  useToast,
} from "../../ui";
import { awaitingScenes, formatDuration, isShort, nextStep } from "./core/model";
import { tabOwnsAction } from "./core/NextStep";
import { MusicPanel, VoicePanel } from "./delivery/AudioPanels";
import { type CurrentScene, EditPanel } from "./delivery/EditPanel";
import {
  buildToken,
  findActiveJob,
  humanizeWarning,
  isActive,
  isMusicWarning,
  JobProgress,
  LangSwitcher,
  Notice,
  previewApproval,
  segRef,
  testProviderKinds,
  testProvidersLabel,
  TIMELINE_JOB_KINDS,
  useDeliveryLang,
} from "./delivery/shared";
import type { TabNavigation } from "./types";
import { useStartJob } from "../ProjectPage";

type Timeline = TimelineProps & {
  mix: string;
  duration: number;
  version: number;
  narration?: { segment_id: string; start: number }[];
  chapters: { title: string; start: number }[];
};

const PreviewComposition: React.FC<TimelineProps & { mixUrl?: string }> = ({
  mixUrl,
  ...props
}) => (
  <>
    <VideoTimeline {...props} />
    {mixUrl ? <Audio src={mixUrl} /> : null}
  </>
);

type SideTab = "editar" | "voz" | "trilha";
const SIDE_PREFIX = "previa-lateral";
/** Id do botão "Ir para Vídeo e publicação" (recebe o foco depois de aprovar). */
const GO_OUTPUT_ID = "previa-ir-saida";
const SCENES_PENDING = "Aprove as cenas primeiro (aba Cenas): é a aprovação que gera as imagens novas.";
/** Aba "Vídeo e publicação" do projeto: reserva de foco quando a faixa é dona da navegação. */
const OUTPUT_TAB_ID = "projeto-aba-output";
const sideTabDomId = (id: SideTab) => `${SIDE_PREFIX}-aba-${id}`;

export const PreviewTab: React.FC<
  { detail: ProjectDetail; busy: boolean } & TabNavigation
> = ({ detail, busy, goTo }) => {
  const { doc } = detail;
  const client = useQueryClient();
  const { toast } = useToast();
  const startJob = useStartJob(doc.id);
  const [lang, setLang] = useDeliveryLang(doc);
  const [side, setSide] = useState<SideTab>("editar");
  const [showAllWarnings, setShowAllWarnings] = useState(false);
  const playerRef = useRef<PlayerRef>(null);
  const playerBoxRef = useRef<HTMLDivElement>(null);
  const asideRef = useRef<HTMLElement>(null);
  const providers = useProviders();

  // A chave muda a cada build (manifesto de artefatos + fim do último job): o player recarrega sozinho.
  const token = buildToken(
    detail.outputs.preview,
    detail.jobs,
    TIMELINE_JOB_KINDS,
  );
  const timeline = useQuery({
    queryKey: ["timeline", doc.id, lang, token],
    queryFn: () =>
      api.get<Timeline>(
        `/api/projects/${doc.id}/timeline?lang=${encodeURIComponent(lang)}`,
      ),
    retry: false,
    // Mantém o vídeo anterior na tela enquanto o novo carrega (só no mesmo idioma).
    placeholderData: (prev, prevQuery) =>
      prevQuery?.queryKey[2] === lang ? prev : undefined,
  });

  // Reforço: quando um job termina, busca a timeline de novo.
  const wasBusy = useRef(busy);
  useEffect(() => {
    if (wasBusy.current && !busy)
      client.invalidateQueries({ queryKey: ["timeline", doc.id] });
    wasBusy.current = busy;
  }, [busy, client, doc.id]);

  const origin = window.location.origin;
  const t = timeline.data;
  const inputProps = useMemo(() => {
    if (!t) return null;
    return {
      ...t,
      isChunk: false,
      assetBaseUrl: `${origin}/files/`,
      fontBaseUrl: origin,
      mixUrl: t.mix ? `${origin}${fileUrl(t.mix)}` : undefined,
    };
  }, [t, origin]);

  // Cena e segundo que estão na tela (o estado só muda quando troca a cena ou o segundo).
  const [sceneIdx, setSceneIdx] = useState(-1);
  const [currentSec, setCurrentSec] = useState(0);
  const sceneAt = useCallback(
    (frame: number) =>
      inputProps
        ? inputProps.scenes.findIndex(
            (s) => frame >= s.from && frame < s.from + s.durationInFrames,
          )
        : -1,
    [inputProps],
  );
  useEffect(() => {
    const p = playerRef.current;
    if (!p || !inputProps) {
      // Sem player (troca de idioma, recarga): a cena anterior não vale mais.
      setSceneIdx(-1);
      setCurrentSec(0);
      return;
    }
    const fps = inputProps.fps || 30;
    const onFrame = (e: { detail: { frame: number } }) => {
      const frame = e.detail.frame;
      const i = sceneAt(frame);
      const sec = Math.floor(frame / fps);
      setSceneIdx((prev) => (prev === i ? prev : i));
      setCurrentSec((prev) => (prev === sec ? prev : sec));
    };
    p.addEventListener("frameupdate", onFrame);
    // O primeiro frameupdate do player sai antes desta assinatura: lê o quadro atual agora.
    onFrame({ detail: { frame: p.getCurrentFrame() } });
    return () => p.removeEventListener("frameupdate", onFrame);
  }, [inputProps, sceneAt]);

  const sceneCount = t?.scenes.length ?? 0;
  const current =
    t && sceneIdx >= 0 && sceneIdx < sceneCount ? t.scenes[sceneIdx] : null;
  const currentScene: CurrentScene = current
    ? { index: sceneIdx, id: current.id, total: sceneCount }
    : null;
  const getCurrentScene = (): CurrentScene => {
    const p = playerRef.current;
    if (!p || !t) return null;
    const i = sceneAt(p.getCurrentFrame());
    const scene = i >= 0 ? t.scenes[i] : undefined;
    return scene ? { index: i, id: scene.id, total: sceneCount } : null;
  };

  const seekFrame = (frame: number, play = false) => {
    const p = playerRef.current;
    if (!p) return;
    p.seekTo(frame);
    if (play) p.play();
    playerBoxRef.current?.scrollIntoView({
      block: "nearest",
      behavior: "smooth",
    });
  };
  const seekScene = (index: number) => {
    const scene = t?.scenes[index];
    if (scene) seekFrame(scene.from);
  };
  const listenSegment = (segmentId: string) => {
    if (!t) return;
    const n = t.narration?.find((x) => x.segment_id === segmentId);
    let frame = n ? Math.round(n.start * t.fps) : -1;
    if (frame < 0) {
      const scene = doc.scenes.find((s) => s.segment_id === segmentId);
      const ts = scene ? t.scenes.find((s) => s.id === scene.id) : undefined;
      if (ts) frame = ts.from;
    }
    if (frame >= 0) seekFrame(frame, true);
  };
  const sceneIndex = (id: string) =>
    t
      ? t.scenes.findIndex((s) => s.id === id)
      : doc.scenes.findIndex((s) => s.id === id);

  // Desatualizada = há versão nova que muda imagem ou som (aprovar, títulos e descrição não contam).
  const currentVersion = detail.project.current_version;
  const approval = previewApproval(detail, t?.version);
  const stale = Boolean(t) && approval.stale;
  const approved = Boolean(t) && approval.approved;
  // Uma ação, um primário: a faixa "Próximo passo" omite o botão quando esta aba hospeda a ação
  // (Gerar prévia, Aprovar prévia); aí o botão local é o primário. Quando a faixa tem o primário, o local cede.
  const { action } = nextStep(detail);
  const banner = action.kind;
  // A faixa mostra um botão primário (não é desta aba, nem "Abrir a Agenda", nem job rodando).
  const bannerPrimary =
    !tabOwnsAction(action, "preview") && banner !== "job" && banner !== "done";
  // "Ir para publicação"/"Vídeo e publicação" já estão na faixa: a barra não repete a navegação.
  const bannerGoesToOutput = banner === "publish" || banner === "done";

  const previewJob = findActiveJob(detail, ["preview", "autopilot"]);
  const otherJob = !previewJob ? detail.jobs.find(isActive) : undefined;

  const approve = useMutation({
    mutationFn: () =>
      api.post(`/api/projects/${doc.id}/approve`, {
        stage: "preview",
        continue_pipeline: true,
      }),
    // Devolve a invalidação: o botão fica ocupado até o job de render aparecer (evita um segundo render).
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["project", doc.id] }).then(() => {
        toast({
          tone: "good",
          title: "Prévia aprovada",
          description: "A renderização do vídeo final começou.",
          action: goTo
            ? {
                label: "Ir para Vídeo e publicação",
                onClick: () => goTo("output"),
              }
            : undefined,
        });
        // O botão "Aprovar" sumiu: o foco vai para o próximo passo (o botão local ou a aba de saída).
        window.requestAnimationFrame(() =>
          (
            document.getElementById(GO_OUTPUT_ID) ??
            document.getElementById(OUTPUT_TAB_ID)
          )?.focus(),
        );
      }),
  });
  const refresh = () => startJob.mutate({ kind: "preview" });
  // Cenas esperando a conferência: quem gera as imagens é a aprovação delas (aba Cenas), não a prévia.
  const scenesPending = awaitingScenes(detail);

  // Sem timeline ainda, a orientação vem do formato do estilo (o layout não pula quando os dados chegam).
  const vertical = t ? t.height > t.width : isShort(detail.style);
  const noPreview =
    timeline.error instanceof ApiError && timeline.error.status === 404;

  // Avisos da última prévia.
  const rawWarnings = detail.outputs.preview?.warnings ?? [];
  const warnings = rawWarnings.map((w) => humanizeWarning(w, doc));
  const musicWarnings = warnings.filter(
    (w) => w.kind === "other" && isMusicWarning(w.raw),
  );
  const shownWarnings = showAllWarnings ? warnings : warnings.slice(0, 3);

  const testProviders = detail.outputs.preview?.preview_only_providers ?? [];
  const providerName = (id: string) =>
    providers.data?.find((p) => p.id === id)?.name ?? id;
  const testLabel = testProvidersLabel(
    testProviderKinds(testProviders, providers.data),
  );

  const approveHint = stale
    ? "Atualize a prévia antes de aprovar."
    : busy
      ? "Aguarde a tarefa em andamento terminar."
      : "Aprovar grava a narração com a voz do vídeo final (gasta a cota ou os créditos dela) e gera o MP4 com as imagens da prévia.";
  const regenerateNote =
    (detail.style?.voice.preview?.local ?? true)
      ? "Refaz só o que mudou; imagens novas gastam cota (a narração da prévia é local, grátis)."
      : "Refaz só o que mudou; voz e imagens novas gastam cota.";
  const approveDisabled = stale || busy;
  const openSide = (id: SideTab) => {
    setSide(id);
    // A barra lateral pode estar abaixo do player: rola até ela e leva o foco para a aba.
    window.requestAnimationFrame(() => {
      asideRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      document.getElementById(sideTabDomId(id))?.focus({ preventScroll: true });
    });
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Barra: idioma · informações · ações */}
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-3">
          <LangSwitcher
            languages={doc.languages}
            value={lang}
            onChange={setLang}
          />
          {t ? (
            <span className="text-[13px] tabular-nums text-subtle">
              {formatDuration(t.duration)} · {t.scenes.length} cenas · versão{" "}
              {t.version}
            </span>
          ) : null}
          {testProviders.length ? (
            <span className="inline-flex items-center gap-1.5">
              <Badge
                tone="warn"
                title={`Provedores de teste: ${testProviders.map(providerName).join(", ")}`}
              >
                {testLabel}
                <span className="sr-only">
                  {" "}
                  (provedores de teste:{" "}
                  {testProviders.map(providerName).join(", ")})
                </span>
              </Badge>
              <Link
                to="/configuracoes"
                className="text-[13px] text-muted underline underline-offset-2 hover:text-fg"
              >
                Configurar provedores
              </Link>
            </span>
          ) : null}
        </div>
        {t ? (
          <div className="flex flex-col items-stretch gap-1 sm:items-end">
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
              {approved ? (
                <>
                  {/* "Prévia aprovada" já aparece nos selos do cabeçalho do projeto. */}
                  <Button
                    variant="ghost"
                    icon={RotateCw}
                    className="w-full sm:w-auto"
                    disabled={busy || !doc.scenes.length || scenesPending}
                    title={scenesPending ? SCENES_PENDING : undefined}
                    busy={startJob.isPending}
                    onClick={refresh}
                  >
                    Gerar de novo
                  </Button>
                  {goTo && !bannerGoesToOutput ? (
                    <Button
                      id={GO_OUTPUT_ID}
                      variant={bannerPrimary ? "secondary" : "primary"}
                      icon={ArrowRight}
                      className="w-full sm:w-auto"
                      onClick={() => goTo("output")}
                    >
                      Ir para Vídeo e publicação
                    </Button>
                  ) : null}
                </>
              ) : (
                <>
                  {!stale ? (
                    <Button
                      variant="ghost"
                      icon={RotateCw}
                      className="w-full sm:w-auto"
                      disabled={busy || !doc.scenes.length || scenesPending}
                      title={scenesPending ? SCENES_PENDING : undefined}
                      busy={startJob.isPending}
                      onClick={refresh}
                    >
                      Gerar de novo
                    </Button>
                  ) : null}
                  <Button
                    variant={
                      approveDisabled || bannerPrimary ? "secondary" : "primary"
                    }
                    icon={Check}
                    className="w-full sm:w-auto"
                    disabled={approveDisabled}
                    busy={approve.isPending}
                    title={approveHint}
                    onClick={() => approve.mutate()}
                  >
                    Aprovar e renderizar
                  </Button>
                </>
              )}
            </div>
            {!approved ? (
              <p className="text-left text-xs text-subtle sm:text-right">
                {approveHint}
              </p>
            ) : null}
            {approved || !stale ? (
              <p className="text-left text-xs text-subtle sm:text-right">
                Gerar de novo: {regenerateNote.toLowerCase()}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      <ErrorBox
        error={startJob.error ?? approve.error}
        onClose={() => {
          startJob.reset();
          approve.reset();
        }}
      />

      {/* Estado: atualizando / mudanças pendentes / outra tarefa */}
      {previewJob && t ? (
        <Notice tone="info" icon={RotateCw}>
          <JobProgress
            job={previewJob}
            title={
              previewJob.kind === "preview" ? "Atualizando a prévia" : undefined
            }
          />
        </Notice>
      ) : stale && t ? (
        <Notice
          tone="warn"
          title={`Há mudanças desde esta prévia (versão ${t.version} → ${currentVersion})`}
          actions={
            scenesPending ? (
              goTo ? (
                <Button icon={LayoutGrid} onClick={() => goTo("scenes")}>
                  Conferir as cenas
                </Button>
              ) : null
            ) : (
              <Button
                variant="primary"
                icon={RotateCw}
                disabled={busy || !doc.scenes.length}
                busy={startJob.isPending}
                onClick={refresh}
              >
                Atualizar prévia
              </Button>
            )
          }
        >
          {busy && otherJob
            ? `Aguarde: ${JOB_LABELS[otherJob.kind] ?? "uma tarefa"} em andamento.`
            : scenesPending
              ? "As cenas mudaram e esperam a sua conferência. Ao aprovar as cenas (aba Cenas), o app gera as imagens novas e atualiza a prévia."
              : "Atualize para ver o resultado antes de aprovar. Só o que mudou é refeito; voz e imagens novas gastam cota."}
        </Notice>
      ) : otherJob && t ? (
        <Notice tone="info">
          <JobProgress job={otherJob} />
        </Notice>
      ) : null}

      {/* Avisos da prévia */}
      {t && warnings.length ? (
        <section
          aria-labelledby="avisos-previa"
          className="flex flex-col gap-2 rounded-xl border border-amber-300/35 bg-amber-300/10 p-3"
        >
          <h2 id="avisos-previa" className="text-sm font-medium text-fg">
            Avisos da prévia ({warnings.length})
          </h2>
          <ul className="flex flex-col gap-2">
            {shownWarnings.map((w) => (
              <li
                key={w.raw}
                className="flex flex-wrap items-start justify-between gap-2 rounded-lg border border-line bg-card/60 p-2.5"
              >
                <div className="min-w-0 flex-1 text-[13px]">
                  <div className="font-medium text-fg">{w.title}</div>
                  {w.detail ? (
                    <div className="text-muted">{w.detail}</div>
                  ) : null}
                </div>
                {w.kind === "voice" ? (
                  w.lang === lang ? (
                    <Button
                      size="sm"
                      icon={Headphones}
                      onClick={() => listenSegment(w.segmentId)}
                      aria-label={
                        segRef(doc, w.segmentId).n
                          ? `Ouvir trecho do segmento ${segRef(doc, w.segmentId).n}`
                          : "Ouvir trecho com problema"
                      }
                    >
                      Ouvir trecho
                    </Button>
                  ) : doc.languages.includes(w.lang) ? (
                    <Button size="sm" onClick={() => setLang(w.lang)}>
                      Ver em {langLabel(w.lang)}
                    </Button>
                  ) : null
                ) : isMusicWarning(w.raw) ? (
                  <Button
                    size="sm"
                    icon={Music}
                    onClick={() => openSide("trilha")}
                  >
                    Ver trilha
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
          {warnings.length > 3 ? (
            <Button
              size="sm"
              variant="ghost"
              className="self-start"
              onClick={() => setShowAllWarnings((v) => !v)}
            >
              {showAllWarnings
                ? "Mostrar menos"
                : `Mostrar todos (${warnings.length})`}
            </Button>
          ) : null}
        </section>
      ) : null}

      <div
        className={`grid items-start gap-6 ${
          vertical
            ? "@3xl:grid-cols-[minmax(0,1fr)_320px] @5xl:grid-cols-[minmax(0,1fr)_400px]"
            : "@4xl:grid-cols-[minmax(0,1fr)_340px] @6xl:grid-cols-[minmax(0,1fr)_380px]"
        }`}
      >
        <div
          className={`flex min-w-0 flex-col gap-3 ${
            vertical ? "@3xl:sticky @3xl:top-4" : ""
          }`}
        >
          {/* Player */}
          {inputProps ? (
            <div
              ref={playerBoxRef}
              className="flex justify-center rounded-xl border border-line bg-black p-2"
            >
              <Player
                ref={playerRef}
                component={PreviewComposition}
                inputProps={inputProps}
                durationInFrames={Math.max(1, inputProps.durationInFrames)}
                fps={inputProps.fps}
                compositionWidth={inputProps.width}
                compositionHeight={inputProps.height}
                controls
                showPlaybackRateControl={PLAYBACK_RATES}
                acknowledgeRemotionLicense
                style={
                  vertical
                    ? {
                        // Cabe na tela com os controles quando as abas encostam no topo.
                        height: "min(72vh, calc(100dvh - 9rem))",
                        maxWidth: "100%",
                        aspectRatio: `${inputProps.width} / ${inputProps.height}`,
                      }
                    : {
                        width: "100%",
                        aspectRatio: `${inputProps.width} / ${inputProps.height}`,
                      }
                }
              />
            </div>
          ) : timeline.isLoading ? (
            <Skeleton
              className={
                vertical
                  ? "mx-auto h-[min(72vh,calc(100dvh-9rem))] w-full max-w-sm"
                  : "aspect-video w-full"
              }
            />
          ) : noPreview || !doc.scenes.length ? (
            <Empty
              icon={MonitorPlay}
              title="Ainda não há prévia"
              action={
                previewJob ? null : !doc.scenes.length || scenesPending ? (
                  goTo ? (
                    <Button icon={LayoutGrid} onClick={() => goTo("scenes")}>
                      Ir para Cenas
                    </Button>
                  ) : null
                ) : (
                  <Button
                    variant={busy || bannerPrimary ? "secondary" : "primary"}
                    icon={MonitorPlay}
                    disabled={busy}
                    busy={startJob.isPending}
                    onClick={refresh}
                  >
                    Gerar prévia
                  </Button>
                )
              }
            >
              {previewJob ? (
                <JobProgress
                  job={previewJob}
                  className="mt-2 w-72 max-w-full text-left"
                />
              ) : !doc.scenes.length ? (
                "Crie as cenas primeiro: a prévia monta o vídeo a partir delas."
              ) : scenesPending ? (
                "Confira e aprove as cenas primeiro (aba Cenas): ao aprovar, o app gera as imagens e a prévia."
              ) : (
                "A prévia monta o vídeo aqui no navegador, sem renderizar o MP4. Na primeira vez ela gera a narração e as imagens, o que gasta cota dos provedores."
              )}
            </Empty>
          ) : (
            <div className="flex flex-col gap-3">
              <ErrorBox
                error={timeline.error}
                title="Não foi possível carregar a prévia"
                onRetry={() => timeline.refetch()}
              />
              {doc.scenes.length && !previewJob ? (
                <div className="flex flex-col items-start gap-1">
                  <Button
                    icon={RotateCw}
                    disabled={busy || scenesPending}
                    title={scenesPending ? SCENES_PENDING : undefined}
                    busy={startJob.isPending}
                    onClick={refresh}
                  >
                    Gerar a prévia de novo
                  </Button>
                  <p className="text-xs text-subtle">
                    Se tentar de novo não resolver, gere a prévia outra vez.{" "}
                    {regenerateNote}
                  </p>
                </div>
              ) : null}
            </div>
          )}

          {/* Cena atual e capítulos */}
          {t ? (
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2 text-[13px]">
                <span className="inline-flex items-center gap-1.5 rounded-md border border-line-strong bg-raised px-2 py-0.5 tabular-nums text-fg">
                  {current
                    ? `Cena ${sceneIdx + 1}/${sceneCount} · ${formatSeconds(currentSec)}`
                    : `${sceneCount} cenas · dê play para ver a cena atual`}
                </span>
              </div>
              {t.chapters?.length ? (
                <div
                  role="group"
                  aria-label="Capítulos"
                  className="flex flex-wrap gap-1.5"
                >
                  {t.chapters.map((c, i) => {
                    const next = t.chapters[i + 1]?.start ?? Infinity;
                    // Segundo inteiro do player: o capítulo acende no segundo em que começa.
                    const activeChapter =
                      currentSec >= Math.floor(c.start) &&
                      currentSec < Math.floor(next);
                    return (
                      <button
                        key={c.start}
                        type="button"
                        aria-current={activeChapter ? "true" : undefined}
                        onClick={() => seekFrame(Math.round(c.start * t.fps))}
                        className={`rounded-md border px-2 py-1 text-xs transition-colors ${
                          activeChapter
                            ? "border-accent/60 bg-accent/10 text-fg"
                            : "border-line-strong text-muted hover:text-fg"
                        }`}
                      >
                        <span className="tabular-nums text-subtle">
                          {formatSeconds(c.start)}
                        </span>{" "}
                        {c.title}
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        {/* Barra lateral fixa: Editar | Voz | Trilha */}
        <aside
          ref={asideRef}
          aria-label="Edição da prévia"
          className={`flex min-w-0 flex-col rounded-xl border border-line bg-card ${
            vertical
              ? "@3xl:sticky @3xl:top-4 @3xl:max-h-[calc(100dvh-2rem)]"
              : "@4xl:sticky @4xl:top-4 @4xl:max-h-[calc(100dvh-2rem)]"
          }`}
        >
          <Tabs
            idPrefix={SIDE_PREFIX}
            ariaLabel="Painéis de edição"
            className="shrink-0 px-2"
            active={side}
            onChange={(id) => setSide(id as SideTab)}
            tabs={[
              { id: "editar", label: "Editar", icon: Pencil },
              { id: "voz", label: "Voz", icon: AudioLines },
              {
                id: "trilha",
                label: "Trilha",
                icon: Music,
                badge: musicWarnings.length ? (
                  <Badge tone="warn" className="ml-0.5">
                    {musicWarnings.length}
                    <span className="sr-only">
                      {musicWarnings.length === 1 ? " aviso" : " avisos"}
                    </span>
                  </Badge>
                ) : undefined,
              },
            ]}
          />
          {/* "Editar" fica sempre montado (só escondido): a instrução e a proposta não se perdem. */}
          <div
            {...tabPanelProps("editar", SIDE_PREFIX)}
            hidden={side !== "editar"}
            className="min-h-0 overflow-y-auto p-4"
          >
            <EditPanel
              detail={detail}
              lang={lang}
              currentScene={currentScene}
              getCurrentScene={getCurrentScene}
              sceneIndex={sceneIndex}
              onSeekScene={seekScene}
              goTo={goTo}
            />
          </div>
          {side === "voz" ? (
            <div
              {...tabPanelProps("voz", SIDE_PREFIX)}
              className="min-h-0 overflow-y-auto p-4"
            >
              <VoicePanel detail={detail} busy={busy} lang={lang} />
            </div>
          ) : null}
          {side === "trilha" ? (
            <div
              {...tabPanelProps("trilha", SIDE_PREFIX)}
              className="min-h-0 overflow-y-auto p-4"
            >
              <MusicPanel
                detail={detail}
                busy={busy}
                warnings={musicWarnings}
              />
            </div>
          ) : null}
        </aside>
      </div>
    </div>
  );
};
