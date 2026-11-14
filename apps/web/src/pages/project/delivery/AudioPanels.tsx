// Painéis "Voz" e "Trilha" da prévia.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AudioLines, Music } from "lucide-react";
import React, { useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type LibraryMusic,
  MOOD_LABELS,
  type ProjectDetail,
  type VoiceCatalog,
  type VoiceChoice,
} from "../../../api";
import { label } from "../../../labels";
import { useProviders } from "../../../shell/data";
import {
  Button,
  ErrorBox,
  Field,
  Loading,
  Select,
  Skeleton,
  useToast,
} from "../../../ui";
import { VoicePicker } from "../../../voices";
import { Notice, shortProviderName, testProviderKinds } from "./shared";

/** Aviso de música já traduzido para texto humano (sem caminhos de arquivo). */
export type MusicWarning = { raw: string; title: string; detail: string };

export const VoicePanel: React.FC<{
  detail: ProjectDetail;
  busy: boolean;
  lang: string;
}> = ({ detail, busy, lang }) => {
  const { doc } = detail;
  const client = useQueryClient();
  const { toast } = useToast();
  const voice = detail.style?.voice;
  const current = voice?.voice
    ? { provider: voice.chain[0], voice: voice.voice }
    : null;
  // Mesma chave do VoicePicker: a lista vem do cache, sem pedido extra.
  const providers = voice?.chain;
  const catalog = useQuery({
    queryKey: ["voices", lang, providers?.join(",") ?? ""],
    queryFn: () =>
      api.get<VoiceCatalog>(
        `/api/voices?lang=${encodeURIComponent(lang)}${providers?.length ? `&providers=${providers.join(",")}` : ""}`,
      ),
  });
  const provider = catalog.data?.providers.find(
    (p) => p.id === current?.provider,
  );
  // Sem o catálogo (carregando ou voz fora da lista), nunca mostra o id técnico da voz.
  const currentLabel = current
    ? (provider?.voices.find((v) => v.id === current.voice)?.label ??
      (catalog.isLoading ? null : "voz personalizada do estilo"))
    : "padrão do provedor";
  const providerShort = provider ? shortProviderName(provider.name) : null;

  // A prévia saiu com uma voz de teste (ex.: cota esgotada): a voz escolhida não é a que se ouve.
  const allProviders = useProviders();
  const testIds = detail.outputs.preview?.preview_only_providers ?? [];
  const testVoice = testProviderKinds(testIds, allProviders.data).voice;
  const chosen = allProviders.data?.find((p) => p.id === current?.provider);
  const chosenShort = chosen
    ? shortProviderName(chosen.name)
    : (providerShort ?? "provedor escolhido");
  const testVoiceName = testIds.includes("windows_sapi")
    ? "a voz de teste do Windows"
    : "uma voz de teste";
  const testVoiceReason = chosen?.quota_exhausted_today
    ? `: a cota do ${chosenShort} acabou hoje.`
    : chosen && !chosen.available
      ? `: o ${chosenShort} não está disponível.`
      : ".";

  const change = useMutation({
    mutationFn: (choice: VoiceChoice) =>
      api.post(`/api/projects/${doc.id}/ops`, {
        ops: [
          { op: "set_style", path: "voice.voice", value: choice.voice },
          {
            op: "set_style",
            path: "voice.chain",
            value: [
              choice.provider,
              ...(voice?.chain ?? []).filter((p) => p !== choice.provider),
            ],
          },
        ],
        message: `Voz: ${choice.voice}`,
      }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["project", doc.id] });
      toast({
        tone: "good",
        title: "Voz trocada",
        description:
          "Atualize a prévia para refazer as narrações com a nova voz.",
      });
    },
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start gap-2.5 text-sm">
        <AudioLines
          size={16}
          aria-hidden="true"
          className="mt-0.5 shrink-0 text-muted"
        />
        <div className="min-w-0">
          <div className="text-fg">
            Voz escolhida:{" "}
            {currentLabel ? (
              <span className="font-medium">{currentLabel}</span>
            ) : (
              <Skeleton className="inline-block h-4 w-28 align-middle" />
            )}
            {!currentLabel ? <span className="sr-only">carregando…</span> : null}
            {providerShort ? (
              <span className="text-muted" title={provider?.name}>
                {" "}
                · {providerShort}
              </span>
            ) : null}
          </div>
        </div>
      </div>
      {testVoice && current ? (
        <Notice tone="warn">
          Esta prévia saiu com {testVoiceName}
          {testVoiceReason}
        </Notice>
      ) : null}
      <p className="text-[13px] text-subtle">
        Ouça as amostras e escolha outra. Depois, atualize a prévia: as
        narrações são refeitas, o que gasta cota se a voz for paga.
      </p>
      {/* Sem rolagem própria: quem rola é o painel lateral (evita uma rolagem dentro de outra). */}
      <div>
        <VoicePicker
          lang={lang}
          providers={providers}
          value={current}
          disabled={busy || change.isPending}
          onChange={(choice) => {
            if (
              choice.provider !== current?.provider ||
              choice.voice !== current?.voice
            )
              change.mutate(choice);
          }}
        />
      </div>
      <ErrorBox error={change.error} />
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
