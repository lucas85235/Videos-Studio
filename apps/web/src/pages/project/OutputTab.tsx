import {
  AudioLines,
  Check,
  Download,
  Film,
  MonitorPlay,
  RotateCw,
  Upload,
} from "lucide-react";
import React, { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { fileUrl, JOB_LABELS, type ProjectDetail } from "../../api";
import { langLabel } from "../../labels";
import {
  Badge,
  Button,
  buttonClass,
  Empty,
  ErrorBox,
  formatSeconds,
  Section,
  SpeedButtons,
  useConfirm,
} from "../../ui";
import { formatDuration, nextStep } from "./core/model";
import { FinalVoiceSummary } from "./delivery/AudioPanels";
import { MultiTrackPanel } from "./delivery/MultiTrackPanel";
import { tabOwnsAction } from "./core/NextStep";
import {
  discardMetaDraft,
  MetadataSection,
  useUnsavedMeta,
} from "./delivery/MetadataSection";
import {
  ChannelSlotsRow,
  ManualPackageCard,
  type PickedSlot,
  PublicationsList,
  useYouTubeStatus,
  YouTubeCard,
} from "./delivery/publishing";
import {
  artifactsToken,
  BuildSummary,
  buildToken,
  finalJobKinds,
  findActiveJob,
  humanizeWarning,
  isActive,
  isFinalStale,
  JobProgress,
  LangSwitcher,
  Notice,
  previewApproval,
  useDeliveryLang,
  withBust,
} from "./delivery/shared";
import type { TabNavigation } from "./types";
import { useStartJob } from "../ProjectPage";

// Um vídeo novo volta para 1x ao carregar; `defaultPlaybackRate` mantém a escolha.
const applyRate = (video: HTMLVideoElement, rate: number) => {
  video.defaultPlaybackRate = rate;
  video.playbackRate = rate;
};

const STEP_IDS = {
  video: "saida-video",
  meta: "saida-metadados",
  publish: "saida-publicar",
} as const;

/** Rola até o passo e leva o foco junto (quem usa teclado continua dali). */
const scrollToStep = (id: string) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "start" });
  el.focus({ preventScroll: true });
};

const DONE_PUBLICATION = new Set(["uploaded", "scheduled", "published"]);

const metaLangsOf = (payload: Record<string, unknown> | undefined) =>
  Array.isArray(payload?.langs) ? (payload.langs as string[]) : null;

export const OutputTab: React.FC<
  { detail: ProjectDetail; busy: boolean } & TabNavigation
> = ({ detail, busy, goTo }) => {
  const { doc } = detail;
  const startJob = useStartJob(doc.id);
  const confirm = useConfirm();
  const [lang, setLang] = useDeliveryLang(doc);
  const final = detail.outputs.final;
  const entry = final?.langs?.[lang];
  const meta = doc.metadata[lang];
  const ytStatus = useYouTubeStatus(doc.id);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [rate, setRate] = useState(1);
  const [pickedSlot, setPickedSlot] = useState<PickedSlot>(null);
  const unsavedMeta = useUnsavedMeta(detail);
  const metaDirty = unsavedMeta(lang);
  // Uma ação, um primário: a faixa "Próximo passo" omite "Ir para publicação" nesta aba, então o primário
  // é local. Quando a faixa mostra o primário dela (renderizar, aprovar a prévia…), a aba não repete o âmbar.
  const { action } = nextStep(detail);
  const bannerPrimary =
    !tabOwnsAction(action, "output") &&
    action.kind !== "job" &&
    action.kind !== "done";

  // O MP4 e a capa têm caminho fixo: o manifesto de artefatos do render força o navegador a buscar o novo
  // (só muda quando o conteúdo muda: um piloto automático que não renderizou não remonta o vídeo).
  const finalKinds = finalJobKinds(detail);
  const token = artifactsToken(final, detail.jobs, finalKinds);
  const videoUrl = entry?.video ? withBust(fileUrl(entry.video), token) : "";
  const thumbUrl = entry?.thumbnail
    ? withBust(fileUrl(entry.thumbnail), token)
    : "";
  // A prévia também regrava legendas.srt no mesmo caminho: o token do .srt inclui os jobs de prévia.
  const srtToken = buildToken(final, detail.jobs, [
    "render",
    "autopilot",
    "preview",
  ]);
  const srtUrl = entry?.srt ? withBust(fileUrl(entry.srt), srtToken) : "";

  // Capítulos para a descrição: do vídeo final, ou da prévia como reserva.
  const chapterSource = (l: string) =>
    final?.langs?.[l] ?? detail.outputs.preview?.langs?.[l];
  const chaptersFor = (l: string) =>
    chapterSource(l)?.chapters?.map(
      (c) => `${formatSeconds(c.start)} ${c.title}`,
    ) ?? [];

  /** Gera título e descrição de um idioma, confirmando antes de substituir texto salvo ou edições. */
  const generateMeta = async (l: string) => {
    const dirty = unsavedMeta(l);
    if (doc.metadata[l] || dirty) {
      const where = doc.languages.length > 1 ? ` em ${langLabel(l)}` : "";
      const ok = await confirm({
        title: `Gerar título e descrição${where} de novo?`,
        description: dirty
          ? "O texto atual e as suas edições não salvas serão substituídos pelo que a IA gerar."
          : "O título, a descrição e as tags atuais serão substituídos. A versão anterior fica no histórico de versões.",
        confirmLabel: dirty ? "Descartar edições e gerar" : "Gerar de novo",
        tone: dirty ? "danger" : "primary",
      });
      if (!ok) return;
    }
    discardMetaDraft(doc.id, l);
    startJob.mutate({
      kind: "metadata",
      payload: { langs: [l], chapters: chaptersFor(l) },
    });
  };

  const startKind = startJob.isPending ? startJob.variables?.kind : undefined;
  const lastKind = startJob.variables?.kind;
  const renderJob = findActiveJob(detail, finalKinds);
  const anyMetadataJob = findActiveJob(detail, ["metadata"]);
  // Job de metadados deste idioma (sem lista de idiomas, vale para todos).
  const metadataJob = findActiveJob(detail, ["metadata"], (j) => {
    const langs = metaLangsOf(j.payload);
    return !langs || langs.includes(lang);
  });
  const pendingMetaLang =
    startKind === "metadata"
      ? metaLangsOf(startJob.variables?.payload)?.[0]
      : undefined;
  const generatingLangs = [
    ...(pendingMetaLang ? [pendingMetaLang] : []),
    ...(anyMetadataJob
      ? (metaLangsOf(anyMetadataJob.payload) ?? doc.languages)
      : []),
  ];
  const metaBusy = Boolean(pendingMetaLang) || Boolean(anyMetadataJob);
  const blocking = detail.jobs.find((j) => isActive(j) && j !== metadataJob);
  const generateBlock = !doc.segments.length
    ? "Escreva o roteiro antes de gerar o título e a descrição."
    : metadataJob
      ? null
      : blocking
        ? `Aguarde: ${JOB_LABELS[blocking.kind] ?? "uma tarefa"} em andamento.`
        : null;

  const previewOnly = final?.preview_only_providers ?? [];
  const finalWarnings = (final?.warnings ?? []).map((w) =>
    humanizeWarning(w, doc),
  );
  const previewEntry = detail.outputs.preview?.langs?.[lang];
  const { approved: previewApproved } = previewApproval(detail);
  const finalStale = Boolean(entry?.video) && isFinalStale(detail);

  // Atalhos para as seções (a trilha de etapas fica só na faixa "Próximo passo").
  const videoDone = Boolean(entry?.video) && !finalStale;
  const metaDone = Boolean(meta?.title) && !metaDirty;
  const publishDone = detail.publications.some(
    (p) => p.language === lang && DONE_PUBLICATION.has(p.status),
  );
  const doneFlags = [videoDone, metaDone, publishDone];
  const firstTodo = doneFlags.findIndex((d) => !d);
  const sections = [
    {
      id: STEP_IDS.video,
      label: finalStale ? "Vídeo final (desatualizado)" : "Vídeo final",
      done: videoDone,
    },
    {
      id: STEP_IDS.meta,
      label: metaDirty ? "Título e descrição (não salvo)" : "Título e descrição",
      done: metaDone,
    },
    { id: STEP_IDS.publish, label: "Publicar", done: publishDone },
  ];

  const renderDisabled = busy || !doc.scenes.length;
  // Primário só quando está liberado e a faixa não mostra o primário dela.
  const renderEmphasis =
    !renderDisabled && (finalStale || !entry?.video) && !bannerPrimary;
  const renderButton = () => (
    <Button
      variant={renderEmphasis ? "primary" : "secondary"}
      icon={entry?.video ? RotateCw : Film}
      disabled={renderDisabled}
      busy={startKind === "render"}
      title={busy ? "Aguarde a tarefa em andamento terminar." : undefined}
      onClick={() => startJob.mutate({ kind: "render" })}
    >
      {entry?.video ? "Renderizar de novo" : "Renderizar vídeo final"}
    </Button>
  );

  const previewState = !previewEntry ? (
    <span>
      Ainda não há prévia
      {doc.languages.length > 1 ? ` em ${langLabel(lang)}` : ""}.{" "}
    </span>
  ) : previewApproved ? (
    <Badge tone="good">Prévia aprovada</Badge>
  ) : (
    <span>Prévia ainda não aprovada: confira antes de renderizar. </span>
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav aria-label="Seções desta aba">
          <ul className="flex flex-wrap items-center gap-x-1 gap-y-1">
            {sections.map((sec) => (
              <li key={sec.id}>
                <button
                  type="button"
                  onClick={() => scrollToStep(sec.id)}
                  className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[13px] text-muted hover:bg-raised hover:text-fg"
                >
                  {sec.done ? (
                    <Check
                      size={14}
                      strokeWidth={2.5}
                      aria-hidden="true"
                      className="text-emerald-300"
                    />
                  ) : null}
                  {sec.label}
                  {sec.done ? <span className="sr-only"> (concluído)</span> : null}
                </button>
              </li>
            ))}
          </ul>
        </nav>
        <LangSwitcher
          languages={doc.languages}
          value={lang}
          onChange={setLang}
          ready={(l) => Boolean(final?.langs?.[l]?.video)}
          readyLabel="vídeo pronto"
        />
      </div>

      <ErrorBox
        error={lastKind !== "metadata" ? startJob.error : null}
        title="Não foi possível iniciar a renderização"
        onClose={() => startJob.reset()}
      />

      <div className="grid items-start gap-6 @5xl:grid-cols-[minmax(0,1fr)_400px]">
        {/* 1. Vídeo final */}
        <div id={STEP_IDS.video} tabIndex={-1} className="focus:outline-none">
          <Section
            icon={Film}
            title="Vídeo final"
            description={
              entry?.video
                ? `${formatDuration(entry.duration)}${doc.languages.length > 1 ? ` · ${langLabel(lang)}` : ""} · Renderizar de novo só grava o que mudou.`
                : "A renderização grava a narração com a voz do vídeo final e monta o MP4 com as imagens da prévia."
            }
            actions={entry?.video && !finalStale ? renderButton() : undefined}
          >
            {renderJob ? null : (
              <FinalVoiceSummary detail={detail} busy={busy} lang={lang} />
            )}
            {finalStale && !renderJob ? (
              <Notice
                tone="warn"
                title="Há mudanças depois desta renderização"
                actions={renderButton()}
              >
                Voz, cenas ou edições mudaram depois que este vídeo foi
                renderizado. Renderize de novo para publicar com as mudanças.
              </Notice>
            ) : null}
            {previewOnly.length ? (
              <Notice
                tone="warn"
                title="Este vídeo usou voz ou imagens de teste"
                actions={
                  <Link
                    to="/configuracoes"
                    className={buttonClass({ size: "sm" })}
                  >
                    Configurar provedores
                  </Link>
                }
              >
                Serve para conferir, não para um canal monetizado: no YouTube só
                dá para enviar como privado. Configure provedores comerciais e
                renderize de novo.
              </Notice>
            ) : null}

            {renderJob ? (
              <Notice tone="info" icon={RotateCw}>
                <JobProgress
                  job={renderJob}
                  title={
                    renderJob.kind === "render"
                      ? "Renderizando o vídeo final"
                      : undefined
                  }
                />
                {entry?.video ? (
                  <div className="mt-1.5">
                    O vídeo abaixo é a versão anterior.
                  </div>
                ) : null}
              </Notice>
            ) : null}

            {entry?.dub_of && !entry.video ? (
              <Notice tone="info" icon={AudioLines} title={`Faixa de áudio do vídeo em ${langLabel(entry.dub_of)}`}>
                <p>
                  Com as faixas no mesmo vídeo, {langLabel(lang)} não tem vídeo próprio: a faixa abaixo vai no
                  YouTube Studio (veja Idiomas no YouTube, mais abaixo).
                </p>
                <audio controls preload="none" src={withBust(fileUrl(entry.mix), token)} className="mt-2 w-full" />
              </Notice>
            ) : null}
            {entry?.video ? (
              <>
                <video
                  ref={videoRef}
                  key={token}
                  src={videoUrl}
                  poster={thumbUrl || undefined}
                  controls
                  preload="metadata"
                  onLoadedMetadata={(e) => applyRate(e.currentTarget, rate)}
                  className="max-h-[70vh] w-full rounded-lg bg-black"
                />
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <SpeedButtons
                    value={rate}
                    onChange={(r) => {
                      setRate(r);
                      if (videoRef.current) applyRate(videoRef.current, r);
                    }}
                  />
                  <div
                    role="group"
                    aria-label="Baixar arquivos"
                    className="flex flex-wrap items-center gap-1"
                  >
                    <a
                      className={buttonClass({ variant: "ghost", size: "sm" })}
                      href={videoUrl}
                      download
                    >
                      <Download size={14} aria-hidden="true" />
                      MP4
                    </a>
                    {srtUrl ? (
                      <a
                        className={buttonClass({
                          variant: "ghost",
                          size: "sm",
                        })}
                        href={srtUrl}
                        download
                      >
                        <Download size={14} aria-hidden="true" />
                        Legendas (.srt)
                      </a>
                    ) : null}
                    {thumbUrl ? (
                      <a
                        className={buttonClass({
                          variant: "ghost",
                          size: "sm",
                        })}
                        href={thumbUrl}
                        download
                      >
                        <Download size={14} aria-hidden="true" />
                        Capa
                      </a>
                    ) : null}
                  </div>
                </div>
                <div className="flex flex-wrap items-start gap-3">
                  {thumbUrl ? (
                    <img
                      src={thumbUrl}
                      alt="Capa do vídeo"
                      className="aspect-video w-40 rounded-md border border-line object-cover"
                    />
                  ) : null}
                  {final ? (
                    <BuildSummary build={final} className="min-w-0 flex-1" />
                  ) : null}
                </div>
              </>
            ) : renderJob || entry?.dub_of ? null : (
              <Empty
                icon={Film}
                title="O vídeo final ainda não foi renderizado"
                action={
                  <>
                    {renderButton()}
                    {goTo && !previewApproved ? (
                      <Button
                        variant="ghost"
                        icon={MonitorPlay}
                        onClick={() => goTo("preview")}
                      >
                        Abrir prévia
                      </Button>
                    ) : null}
                  </>
                }
              >
                <div className="flex flex-wrap items-center justify-center gap-1.5">
                  {previewState}
                </div>
              </Empty>
            )}
          </Section>
        </div>

        {/* 2. Título e descrição */}
        <div id={STEP_IDS.meta} tabIndex={-1} className="focus:outline-none">
          <MetadataSection
            detail={detail}
            lang={lang}
            hasChapterSource={Boolean(chapterSource(lang))}
            onGenerate={() => generateMeta(lang)}
            generating={pendingMetaLang === lang}
            generateError={lastKind === "metadata" ? startJob.error : null}
            onCloseGenerateError={() => startJob.reset()}
            generateBlock={
              generateBlock ??
              (metaBusy && !metadataJob
                ? "Aguarde a geração do outro idioma terminar."
                : null)
            }
            metadataJob={metadataJob}
            emphasis={firstTodo === 1 && !bannerPrimary}
          />
        </div>
      </div>

      <MultiTrackPanel detail={detail} busy={busy} />

      {/* 3. Publicar */}
      <div id={STEP_IDS.publish} tabIndex={-1} className="focus:outline-none">
        <Section
          icon={Upload}
          title="Publicar"
          description={
            doc.languages.length > 1
              ? `Versão em ${langLabel(lang)}`
              : undefined
          }
        >
          {finalWarnings.length ? (
            <Notice
              tone="warn"
              title={`Avisos da renderização (${finalWarnings.length})`}
              actions={
                goTo ? (
                  <Button
                    size="sm"
                    icon={MonitorPlay}
                    onClick={() => goTo("preview")}
                  >
                    Conferir na prévia
                  </Button>
                ) : undefined
              }
            >
              <ul className="flex flex-col gap-1">
                {finalWarnings.map((w) => (
                  <li key={w.raw}>
                    <span className="text-fg">{w.title}</span>
                    {w.detail ? ` ${w.detail}` : ""}
                  </li>
                ))}
              </ul>
            </Notice>
          ) : null}
          {entry?.video ? (
            <ChannelSlotsRow
              channelId={doc.channel_id}
              onPick={(value) =>
                setPickedSlot((prev) => ({ value, n: (prev?.n ?? 0) + 1 }))
              }
            />
          ) : null}
          <div className="grid items-start gap-4 @4xl:grid-cols-2">
            <YouTubeCard
              detail={detail}
              lang={lang}
              entry={entry}
              previewOnly={previewOnly}
              busy={busy}
              onGenerateMeta={generateMeta}
              generatingLangs={generatingLangs}
              metaBusy={metaBusy}
              unsavedMeta={metaDirty}
              finalStale={finalStale}
              emphasis={
                firstTodo === 2 &&
                !bannerPrimary &&
                Boolean(ytStatus.data?.client_configured)
              }
              pickedSlot={pickedSlot}
            />
            <ManualPackageCard
              detail={detail}
              lang={lang}
              hasVideo={Boolean(entry?.video)}
              previewOnly={previewOnly}
              primary={
                firstTodo === 2 &&
                !bannerPrimary &&
                Boolean(ytStatus.data) &&
                !ytStatus.data?.client_configured
              }
              unsavedMeta={metaDirty}
              finalStale={finalStale}
              pickedSlot={pickedSlot}
            />
          </div>
          <PublicationsList detail={detail} lang={lang} />
        </Section>
      </div>
    </div>
  );
};
