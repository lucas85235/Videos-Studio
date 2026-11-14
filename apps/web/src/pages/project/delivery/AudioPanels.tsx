// Painéis "Voz" (prévia e vídeo final) e "Trilha" da prévia.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AudioLines, Music } from "lucide-react";
import React, { useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type BuildPlan,
  type LibraryMusic,
  LOCAL_VOICE_PROVIDER,
  MOOD_LABELS,
  type ProjectDetail,
  type VoiceChoice,
  type VoiceProvider,
} from "../../../api";
import { label, langLabel } from "../../../labels";
import { useProviders } from "../../../shell/data";
import {
  Button,
  Checkbox,
  ErrorBox,
  Field,
  Loading,
  SegmentedControl,
  Select,
  Skeleton,
  useToast,
} from "../../../ui";
import { useVoiceCatalog, VoicePicker } from "../../../voices";
import { Notice, shortProviderName, testProviderKinds } from "./shared";

/** Aviso de música já traduzido para texto humano (sem caminhos de arquivo). */
export type MusicWarning = { raw: string; title: string; detail: string };

type StyleOp = { op: "set_style"; path: string; value: unknown };

/** Grava ajustes de voz no estilo do projeto (uma versão nova) e avisa o que muda. */
const useVoiceOps = (detail: ProjectDetail) => {
  const client = useQueryClient();
  const { toast } = useToast();
  return useMutation({
    mutationFn: (v: { ops: StyleOp[]; message: string; title: string; description: string }) =>
      api.post(`/api/projects/${detail.doc.id}/ops`, { ops: v.ops, message: v.message }),
    onSuccess: (_data, v) => {
      client.invalidateQueries({ queryKey: ["project", detail.doc.id] });
      client.invalidateQueries({ queryKey: ["plan", detail.doc.id] });
      toast({ tone: "good", title: v.title, description: v.description });
    },
  });
};

const genderOfLocalId = (id: string | null | undefined) =>
  id && id[2] === "_" ? ({ f: "feminina", m: "masculina" } as Record<string, string>)[id[1]] : undefined;

/** Voz local que a prévia usa neste idioma (a escolhida ou uma do mesmo gênero da voz final), como no back-end. */
const effectivePreviewVoice = (
  local: VoiceProvider | undefined,
  wanted: string | null | undefined,
  finalGender: string | null | undefined,
) => {
  const voices = local?.voices ?? [];
  const gender = finalGender ?? genderOfLocalId(wanted);
  return (
    voices.find((v) => v.id === wanted) ??
    voices.find((v) => gender && v.gender === gender) ??
    voices[0] ??
    null
  );
};

/** Voz do vídeo final: a primeira da cadeia do estilo, com o nome legível do catálogo. */
export const useFinalVoice = (detail: ProjectDetail, lang: string) => {
  const voice = detail.style?.voice;
  const current: VoiceChoice | null = voice?.voice
    ? { provider: voice.chain[0], voice: voice.voice }
    : null;
  const catalog = useVoiceCatalog(lang, { purpose: "final" });
  const provider = catalog.data?.providers.find(
    (p) => p.id === (current?.provider ?? voice?.chain[0]),
  );
  const option = provider?.voices.find((v) => v.id === current?.voice);
  // Sem o catálogo (carregando ou voz fora da lista), nunca mostra o id técnico da voz.
  const label = current
    ? (option?.label ?? (catalog.isLoading ? null : "voz personalizada do estilo"))
    : "padrão do provedor";
  const providerName = provider
    ? provider.local
      ? "voz local"
      : shortProviderName(provider.name)
    : null;
  return { current, label, providerName, provider, gender: option?.gender, catalog };
};

const VOICE_TARGETS = [
  { id: "preview" as const, label: "Prévia" },
  { id: "final" as const, label: "Vídeo final" },
];

export const VoicePanel: React.FC<{
  detail: ProjectDetail;
  busy: boolean;
  lang: string;
}> = ({ detail, busy, lang }) => {
  const [target, setTarget] = useState<"preview" | "final">("preview");
  return (
    <div className="flex flex-col gap-3">
      <SegmentedControl
        label="Voz de qual etapa"
        size="sm"
        options={VOICE_TARGETS}
        value={target}
        onChange={setTarget}
      />
      {target === "preview" ? (
        <PreviewVoiceSection detail={detail} busy={busy} lang={lang} />
      ) : (
        <FinalVoiceSection detail={detail} busy={busy} lang={lang} />
      )}
    </div>
  );
};

const PreviewVoiceSection: React.FC<{
  detail: ProjectDetail;
  busy: boolean;
  lang: string;
}> = ({ detail, busy, lang }) => {
  const preview = detail.style?.voice.preview ?? { local: true, voice: null };
  const local = useVoiceCatalog(lang, { purpose: "preview" });
  const final = useFinalVoice(detail, lang);
  const ops = useVoiceOps(detail);
  const localProvider = local.data?.providers.find(
    (p) => p.id === LOCAL_VOICE_PROVIDER,
  );
  // O catálogo só lista a voz local nos idiomas que ela fala.
  const supported = Boolean(localProvider?.voices.length);
  const effective = effectivePreviewVoice(localProvider, preview.voice, final.gender);

  // A prévia saiu com a voz de teste do Windows (voz local ou final indisponível).
  const allProviders = useProviders();
  const testIds = detail.outputs.preview?.preview_only_providers ?? [];
  const testVoice = testProviderKinds(testIds, allProviders.data).voice;

  let summary: React.ReactNode;
  if (!preview.local) summary = "a mesma do vídeo final";
  else if (local.isLoading) summary = null;
  else if (!supported) summary = `a do vídeo final (${langLabel(lang)} não tem voz local)`;
  else summary = effective ? `${effective.label} · voz local` : "voz local";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start gap-2.5 text-sm">
        <AudioLines size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 text-fg">
          Voz da prévia:{" "}
          {summary ? (
            <span className="font-medium">{summary}</span>
          ) : (
            <>
              <Skeleton className="inline-block h-4 w-28 align-middle" />
              <span className="sr-only">carregando…</span>
            </>
          )}
        </div>
      </div>
      <Checkbox
        label="Usar a voz local grátis na prévia"
        description="Roda no seu computador, sem cota, para você conferir o conteúdo. A voz do vídeo final só é gravada quando você renderizar."
        checked={preview.local}
        disabled={busy || ops.isPending}
        onChange={(on) =>
          ops.mutate({
            ops: [{ op: "set_style", path: "voice.preview.local", value: on }],
            message: on ? "Prévia com a voz local" : "Prévia com a voz final",
            title: on ? "Prévia com a voz local" : "Prévia com a voz final",
            description: on
              ? "Atualize a prévia para refazer as narrações com a voz local (grátis)."
              : "Atualize a prévia: as narrações saem da voz final e gastam a cota dela.",
          })
        }
      />
      {testVoice ? (
        <Notice tone="warn">
          Esta prévia saiu com a voz de teste do Windows porque a voz{" "}
          {preview.local && supported ? "local" : "final"} não estava disponível.
          Atualize a prévia para refazer as narrações.
        </Notice>
      ) : null}
      {preview.local && (supported || local.isLoading || local.error) ? (
        <VoicePicker
          lang={lang}
          purpose="preview"
          value={effective ? { provider: LOCAL_VOICE_PROVIDER, voice: effective.id } : null}
          disabled={busy || ops.isPending}
          onChange={(choice) => {
            if (choice.voice === effective?.id) return;
            const name = localProvider?.voices.find((v) => v.id === choice.voice)?.label ?? choice.voice;
            ops.mutate({
              ops: [{ op: "set_style", path: "voice.preview.voice", value: choice.voice }],
              message: `Voz da prévia: ${name}`,
              title: "Voz da prévia trocada",
              description: "Atualize a prévia para refazer as narrações (voz local, sem cota).",
            });
          }}
        />
      ) : null}
      <ErrorBox error={ops.error} />
    </div>
  );
};

/** Escolha da voz do vídeo final (também usada na aba Vídeo final). */
export const FinalVoiceSection: React.FC<{
  detail: ProjectDetail;
  busy: boolean;
  lang: string;
  /** Esconde o resumo "Voz do vídeo final: …" quando quem chama já mostra. */
  hideSummary?: boolean;
}> = ({ detail, busy, lang, hideSummary }) => {
  const voice = detail.style?.voice;
  const final = useFinalVoice(detail, lang);
  const ops = useVoiceOps(detail);
  const previewLocal = voice?.preview?.local ?? true;

  return (
    <div className="flex flex-col gap-3">
      {hideSummary ? null : (
        <div className="flex items-start gap-2.5 text-sm">
          <AudioLines size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-muted" />
          <div className="min-w-0 text-fg">
            Voz do vídeo final:{" "}
            {final.label ? (
              <span className="font-medium">{final.label}</span>
            ) : (
              <>
                <Skeleton className="inline-block h-4 w-28 align-middle" />
                <span className="sr-only">carregando…</span>
              </>
            )}
            {final.providerName ? (
              <span className="text-muted" title={final.provider?.name}>
                {" "}
                · {final.providerName}
              </span>
            ) : null}
          </div>
        </div>
      )}
      <p className="text-[13px] text-subtle">
        {previewLocal
          ? "Gravada só quando você renderiza o vídeo final, gastando a cota ou os créditos da voz escolhida. A prévia continua com a voz local."
          : "A prévia e o vídeo final usam esta voz: cada narração refeita gasta a cota ou os créditos dela."}
      </p>
      <VoicePicker
        lang={lang}
        purpose="final"
        value={final.current}
        disabled={busy || ops.isPending}
        onChange={(choice) => {
          if (choice.provider === final.current?.provider && choice.voice === final.current?.voice) return;
          const name =
            final.catalog.data?.providers
              .find((p) => p.id === choice.provider)
              ?.voices.find((v) => v.id === choice.voice)?.label ?? choice.voice;
          ops.mutate({
            ops: [
              { op: "set_style", path: "voice.voice", value: choice.voice },
              {
                op: "set_style",
                path: "voice.chain",
                value: [choice.provider, ...(voice?.chain ?? []).filter((p) => p !== choice.provider)],
              },
            ],
            message: `Voz do vídeo final: ${name}`,
            title: "Voz do vídeo final trocada",
            description: previewLocal
              ? "Ela será gravada quando você renderizar o vídeo final."
              : "Atualize a prévia para refazer as narrações com a nova voz.",
          });
        }}
      />
      <ErrorBox error={ops.error} />
    </div>
  );
};

/** Na aba Vídeo final: qual voz grava a narração, quanto falta gravar e a troca da voz. */
export const FinalVoiceSummary: React.FC<{
  detail: ProjectDetail;
  busy: boolean;
  lang: string;
}> = ({ detail, busy, lang }) => {
  const [open, setOpen] = useState(false);
  const final = useFinalVoice(detail, lang);
  const plan = useQuery({
    queryKey: ["plan", detail.doc.id, "final", detail.project.current_version],
    queryFn: () =>
      api.get<BuildPlan>(`/api/projects/${detail.doc.id}/plan?mode=final`),
  });
  const p = plan.data;
  const toRecord = p?.tts_missing.length ?? 0;
  const status = !p
    ? null
    : toRecord === 0
      ? "A narração com esta voz já está gravada."
      : `Ao renderizar, grava ${toRecord} ${toRecord === 1 ? "trecho" : "trechos"} de narração (${p.tts_chars.toLocaleString("pt-BR")} caracteres) com esta voz${final.provider?.local ? ", sem custo" : ""}.`;
  const unavailable = Boolean(p && toRecord > 0 && !p.tts_provider);

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex min-w-0 items-start gap-2.5 text-sm">
          <AudioLines size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-muted" />
          <div className="min-w-0">
            <div className="text-fg">
              Voz do vídeo final:{" "}
              {final.label ? (
                <span className="font-medium">{final.label}</span>
              ) : (
                <>
                  <Skeleton className="inline-block h-4 w-28 align-middle" />
                  <span className="sr-only">carregando…</span>
                </>
              )}
              {final.providerName ? (
                <span className="text-muted" title={final.provider?.name}>
                  {" "}
                  · {final.providerName}
                </span>
              ) : null}
            </div>
            {status ? <div className="mt-0.5 text-[13px] text-subtle">{status}</div> : null}
          </div>
        </div>
        <Button
          size="sm"
          variant="ghost"
          icon={AudioLines}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "Fechar" : "Trocar voz"}
        </Button>
      </div>
      {unavailable ? (
        <Notice tone="warn" title="Esta voz não está disponível agora">
          A cota acabou hoje ou falta a chave do provedor. Escolha outra voz ou
          renderize quando a cota voltar.
        </Notice>
      ) : null}
      {open ? (
        <FinalVoiceSection detail={detail} busy={busy} lang={lang} hideSummary />
      ) : null}
    </div>
  );
};


export const MusicPanel: React.FC<{
  detail: ProjectDetail;
  busy: boolean;
  warnings: MusicWarning[];
}> = ({ detail, busy, warnings }) => {
  const { doc } = detail;
  const client = useQueryClient();
  const library = useQuery({
    queryKey: ["library-music"],
    queryFn: () => api.get<LibraryMusic>("/api/library/music"),
  });
  const cue = doc.music.find((c) => c.enabled) ?? doc.music[0];
  const used = detail.outputs.preview?.music;
  const source = detail.style?.audio.music.source;
  const pin = useMutation({
    mutationFn: (track: string) =>
      api.post(`/api/projects/${doc.id}/ops`, {
        ops: [{ op: "set_music", track }],
        message: track ? `Trilha fixada: ${track}` : "Trilha automática",
      }),
    onSuccess: () =>
      client
        .invalidateQueries({ queryKey: ["project", doc.id] })
        .then(() => setChoice(null)),
  });
  // Escolha local: as setas num <select> fechado mudam o valor a cada tecla; só grava no botão.
  const savedTrack = cue?.track ?? "";
  const [choice, setChoice] = useState<string | null>(null);
  const selected = choice ?? savedTrack;
  const changed = selected !== savedTrack;

  const tracks = library.data?.tracks ?? [];
  const nameOf = (path: string | null | undefined) =>
    tracks.find((t) => t.path === path)?.name ??
    (path ? (path.split("/").pop() ?? path) : "");
  const moods = Array.from(
    new Set([...(library.data?.moods ?? []), ...tracks.map((t) => t.mood)]),
  ).filter((m) => tracks.some((t) => t.mood === m));

  let playing: React.ReactNode = "Ainda não há prévia.";
  if (cue && !cue.enabled) playing = "Sem música neste projeto";
  else if (source === "none" && !used) playing = "O estilo não usa música";
  else if (detail.outputs.preview && !used) playing = "Sem música nesta prévia";
  else if (used?.source === "library")
    playing = `${nameOf(used.track)}${used.mood ? ` · ${label(MOOD_LABELS, used.mood)}` : ""}`;
  else if (used)
    playing = `Música gerada pelo app${used.mood ? ` · ${label(MOOD_LABELS, used.mood)}` : ""}`;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start gap-2.5 text-sm">
        <Music
          size={16}
          aria-hidden="true"
          className="mt-0.5 shrink-0 text-muted"
        />
        <div className="min-w-0 text-fg">
          Tocando: <span className="font-medium">{playing}</span>
        </div>
      </div>
      {library.isLoading ? (
        <Loading label="Carregando a biblioteca de músicas…" />
      ) : tracks.length ? (
        <div className="flex flex-col gap-2">
        <Field
          label="Faixa"
          hint="Fixe uma faixa da sua biblioteca ou deixe o app escolher pelo clima. Depois, atualize a prévia."
        >
          <Select
            disabled={busy || pin.isPending}
            value={selected}
            onChange={(e) => setChoice(e.target.value)}
          >
            <option value="">Automática</option>
            {moods.map((mood) => (
              <optgroup key={mood} label={label(MOOD_LABELS, mood)}>
                {tracks
                  .filter((t) => t.mood === mood)
                  .map((t) => (
                    <option key={t.path} value={t.path}>
                      {t.name}
                    </option>
                  ))}
              </optgroup>
            ))}
          </Select>
        </Field>
          {changed ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                icon={Music}
                disabled={busy}
                busy={pin.isPending}
                onClick={() => pin.mutate(selected)}
              >
                {selected ? "Usar esta faixa" : "Voltar para automática"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={pin.isPending}
                onClick={() => setChoice(null)}
              >
                Cancelar
              </Button>
            </div>
          ) : null}
        </div>
      ) : library.isSuccess ? (
        <p className="text-[13px] text-subtle">
          Para usar suas músicas, coloque faixas na biblioteca em{" "}
          <Link
            to="/configuracoes"
            className="text-fg underline underline-offset-2 hover:text-accent"
          >
            Configurações
          </Link>
          .
        </p>
      ) : null}
      {source === "procedural" && tracks.length ? (
        <p className="text-[13px] text-subtle">
          Para usar a biblioteca por padrão, mude a fonte da música no{" "}
          <Link
            to={`/estilos/${doc.style_id}`}
            className="text-fg underline underline-offset-2 hover:text-accent"
          >
            estilo
          </Link>
          .
        </p>
      ) : null}
      {warnings.map((w) => (
        <Notice key={w.raw} tone="warn" title={w.title}>
          {w.detail || null}
        </Notice>
      ))}
      <ErrorBox error={pin.error ?? library.error} />
    </div>
  );
};
