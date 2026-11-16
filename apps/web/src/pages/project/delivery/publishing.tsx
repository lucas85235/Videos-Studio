// Passo 3 da aba Vídeo e publicação: YouTube, outras redes (pacote manual) e lista de publicações.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ExternalLink,
  FolderOpen,
  PackageOpen,
  Send,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";
import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type ChannelSlots,
  formatWhen,
  type LangOutput,
  PLATFORM_LABELS,
  type ProjectDetail,
  type Publication,
  toLocalInput,
} from "../../../api";
import { label, langLabel } from "../../../labels";
import {
  Badge,
  type BadgeTone,
  Button,
  buttonClass,
  Card,
  Checkbox,
  ErrorBox,
  Field,
  FieldGroup,
  formatRelative,
  Input,
  Select,
  useConfirm,
  useToast,
} from "../../../ui";
import { createKeyedStore, useKeyedStore } from "../core/drafts";
import {
  CopyButton,
  finalJobKinds,
  findActiveJob,
  isFutureLocal,
  JobProgress,
  lastDoneJob,
  Notice,
  nowLocalInput,
} from "./shared";

/** Horário livre escolhido no topo da seção (o contador faz o mesmo horário valer de novo). */
export type PickedSlot = { value: string; n: number } | null;

/** Pacotes preparados nesta sessão com voz ou imagens de teste (o backend não grava isso na publicação). */
const testPackages = createKeyedStore<true>();

const UNSAVED_META_BLOCK = "Salve o título e a descrição antes de enviar.";

export type YouTubeStatus = {
  client_configured: boolean;
  connected: boolean;
  channel_title: string;
  /** Com project_id: por que este projeto não pode enviar (null quando pode). */
  reason?: string | null;
};

/** Conta que o projeto usa de fato para publicar (a do canal dele, ou a global). */
export const useYouTubeStatus = (projectId: string) =>
  useQuery({
    queryKey: ["youtube-status", projectId],
    queryFn: () =>
      api.get<YouTubeStatus>(
        `/api/youtube/status?project_id=${encodeURIComponent(projectId)}`,
      ),
    refetchInterval: 10_000,
  });

const LINK = "text-fg underline underline-offset-2 hover:text-accent";

/* ------------------------------------------------------------------ */
/* Horários livres do canal                                             */
/* ------------------------------------------------------------------ */

const useChannelSlots = (channelId: string | null) =>
  useQuery({
    queryKey: ["channel-slots", channelId],
    queryFn: () =>
      api.get<ChannelSlots>(`/api/channels/${channelId}/slots?count=3`),
    enabled: Boolean(channelId),
  });

const browserTimeZone = (() => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return "";
  }
})();

const tzName = (tz: string) => (tz.split("/").pop() ?? tz).replace(/_/g, " ");

/** Horários livres do canal, mostrados uma vez no topo da seção Publicar. */
export const ChannelSlotsRow: React.FC<{
  channelId: string | null;
  onPick: (localValue: string) => void;
}> = ({ channelId, onPick }) => {
  const slots = useChannelSlots(channelId);
  if (!channelId)
    return (
      <p className="text-xs text-subtle">
        Este projeto não tem canal, então não há horários livres para sugerir.
        O canal é escolhido ao criar o projeto; os horários de postagem ficam
        em{" "}
        <Link to="/canais" className={LINK}>
          Canais
        </Link>
        .
      </p>
    );
  if (!slots.data) return null;
  if (!slots.data.configured) {
    return (
      <p className="text-xs text-subtle">
        Configure os horários de postagem em{" "}
        <Link to="/canais" className={LINK}>
          Canais
        </Link>{" "}
        para ter sugestões aqui.
      </p>
    );
  }
  if (!slots.data.slots.length) return null;
  const otherTz =
    slots.data.timezone &&
    browserTimeZone &&
    slots.data.timezone !== browserTimeZone;
  return (
    <div className="flex flex-col gap-1">
      <div
        role="group"
        aria-label="Horários livres do canal"
        className="flex flex-wrap items-center gap-1.5 text-xs"
      >
        <span className="text-subtle">
          Horários livres (vale para o YouTube e para o pacote):
        </span>
        {slots.data.slots.map((iso) => (
          <button
            key={iso}
            type="button"
            className="rounded-md border border-line-strong px-2 py-1 tabular-nums text-muted transition-colors hover:border-accent/60 hover:text-fg"
            onClick={() => onPick(toLocalInput(iso))}
          >
            {formatWhen(iso)}
          </button>
        ))}
      </div>
      {otherTz ? (
        <p className="text-xs text-subtle">
          Horários no fuso deste computador (o canal usa o de{" "}
          {tzName(slots.data.timezone)}).
        </p>
      ) : null}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* YouTube                                                              */
/* ------------------------------------------------------------------ */

const PRIVACY_LABELS: Record<string, string> = {
  private: "Privado",
  unlisted: "Não listado",
  public: "Público agora",
  scheduled: "Agendado",
};

export const YouTubeCard: React.FC<{
  detail: ProjectDetail;
  lang: string;
  entry: LangOutput | undefined;
  previewOnly: string[];
  busy: boolean;
  onGenerateMeta: (lang: string) => void;
  /** Idiomas com geração de título pedida ou em andamento. */
  generatingLangs: string[];
  /** Já há geração de título pedida ou rodando (o backend roda um job por vez). */
  metaBusy: boolean;
  /** Há edições não salvas no título e descrição deste idioma. */
  unsavedMeta: boolean;
  /** O vídeo final é anterior às últimas mudanças do projeto. */
  finalStale: boolean;
  /** Este cartão tem a ação principal da aba (passo atual = Publicar). */
  emphasis: boolean;
  pickedSlot: PickedSlot;
}> = ({
  detail,
  lang,
  entry,
  previewOnly,
  busy,
  onGenerateMeta,
  generatingLangs,
  metaBusy,
  unsavedMeta,
  finalStale,
  emphasis,
  pickedSlot,
}) => {
  const { doc } = detail;
  const projectId = doc.id;
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const status = useYouTubeStatus(projectId);
  const [privacyChoice, setPrivacy] = useState("private");
  const [when, setWhen] = useState("");
  const [localize, setLocalize] = useState(false);
  const testOnly = previewOnly.length > 0;
  const privacy = testOnly ? "private" : privacyChoice;
  useEffect(() => {
    if (!pickedSlot || testOnly) return;
    setPrivacy("scheduled");
    setWhen(pickedSlot.value);
  }, [pickedSlot, testOnly]);
  const meta = doc.metadata[lang];
  const otherLangs = doc.languages.filter(
    (l) => l !== lang && doc.metadata[l]?.title,
  );
  const missingLangs = doc.languages.filter(
    (l) => l !== lang && !doc.metadata[l]?.title,
  );
  const multi = doc.languages.length > 1;

  const connect = useMutation({
    mutationFn: () => api.post<{ auth_url: string }>("/api/youtube/connect"),
    onSuccess: (data) => window.open(data.auth_url, "_blank", "noopener"),
  });
  const publish = useMutation({
    mutationFn: () =>
      api.post(`/api/projects/${projectId}/publish/youtube`, {
        lang,
        privacy,
        publish_at:
          privacy === "scheduled" && when ? new Date(when).toISOString() : null,
        localize: localize && otherLangs.length > 0,
      }),
    // Devolve a invalidação: o botão fica ocupado até o job novo aparecer (evita um segundo envio).
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: ["project", projectId] }),
        client.invalidateQueries({ queryKey: ["agenda"] }),
        client.invalidateQueries({ queryKey: ["channel-slots"] }),
      ]).then(() =>
        toast({
          tone: "info",
          title: "Envio ao YouTube iniciado",
          description: "O progresso aparece aqui e na barra lateral.",
        }),
      ),
  });

  const publishJob = findActiveJob(
    detail,
    ["publish_youtube"],
    (j) => String(j.payload?.lang ?? doc.primary_language) === lang,
  );
  const alreadySent = detail.publications.some(
    (p) => p.driver === "youtube_native" && p.language === lang,
  );

  const block = !entry?.video
    ? "Renderize o vídeo final deste idioma primeiro."
    : publishJob
      ? null
      : busy
        ? "Aguarde a tarefa em andamento terminar."
        : unsavedMeta
          ? UNSAVED_META_BLOCK
          : privacy === "scheduled" && !when
          ? "Escolha a data e a hora do agendamento."
          : privacy === "scheduled" && !isFutureLocal(when)
            ? "O horário do agendamento precisa estar no futuro."
            : null;

  const send = async () => {
    if (finalStale) {
      const ok = await confirm({
        title: "Enviar um vídeo desatualizado?",
        description:
          "Houve mudanças no projeto depois deste render (voz, cenas ou edições). O vídeo enviado não terá essas mudanças. Para incluí-las, renderize de novo antes.",
        confirmLabel: "Enviar assim mesmo",
      });
      if (!ok) return;
    }
    if (alreadySent) {
      const ok = await confirm({
        title: "Enviar outra cópia ao YouTube?",
        description: `Este vídeo${multi ? ` em ${langLabel(lang)}` : ""} já foi enviado. Um novo envio cria um segundo vídeo no canal.`,
        confirmLabel: "Enviar outra cópia",
      });
      if (!ok) return;
    }
    publish.mutate();
  };

  const s = status.data;
  const sendLabel = alreadySent
    ? "Enviar outra cópia"
    : multi
      ? `Enviar ao YouTube (${langLabel(lang)})`
      : "Enviar ao YouTube";

  return (
    <Card className="flex flex-col gap-3">
      <h3 className="font-display text-[15px] font-semibold text-fg">
        Publicar no YouTube
      </h3>
      {!s ? (
        status.error ? (
          <ErrorBox error={status.error} />
        ) : (
          <p className="text-[13px] text-subtle">Verificando a conexão…</p>
        )
      ) : !s.client_configured ? (
        <div className="flex flex-col gap-2 text-[13px] text-muted">
          <p>
            Para enviar direto ao YouTube, configure o acesso do app (cliente
            OAuth) em Configurações, na parte de Publicação.
          </p>
          <Link
            to="/configuracoes"
            className={`${buttonClass({ size: "sm" })} self-start`}
          >
            Abrir Configurações
          </Link>
        </div>
      ) : !s.connected ? (
        <div className="flex flex-col gap-2">
          <p className="text-[13px] text-muted">
            {s.reason
              ? `Não dá para enviar: ${s.reason}`
              : "Conecte o seu canal: o login abre numa nova aba do navegador."}
          </p>
          {/* Projeto com canal: o vínculo é feito em Canais. Sem canal: a conta global resolve. */}
          {doc.channel_id ? (
            <Link
              to="/canais"
              className={`${buttonClass({ variant: emphasis ? "primary" : "secondary", size: "sm" })} self-start`}
            >
              Abrir Canais
            </Link>
          ) : (
            <Button
              variant={emphasis ? "primary" : "secondary"}
              icon={ExternalLink}
              className="self-start"
              busy={connect.isPending}
              onClick={() => connect.mutate()}
            >
              Conectar canal do YouTube
            </Button>
          )}
        </div>
      ) : (
        <>
          <p className="text-[13px] text-muted">
            Canal:{" "}
            <span className="font-medium text-fg">
              {s.channel_title || "conectado"}
            </span>
          </p>

          {testOnly ? (
            <Notice
              tone="warn"
              title="Só envio privado"
              actions={
                <Link
                  to="/configuracoes"
                  className={buttonClass({ size: "sm" })}
                >
                  Configurar provedores
                </Link>
              }
            >
              Este render usou voz ou imagens de teste. Configure provedores
              comerciais e renderize de novo para publicar de verdade.
            </Notice>
          ) : null}

          <div className="flex flex-wrap items-end gap-2">
            <Field label="Visibilidade" className="min-w-40">
              <Select
                value={privacy}
                onChange={(e) => setPrivacy(e.target.value)}
                disabled={testOnly}
              >
                {Object.entries(PRIVACY_LABELS).map(([id, name]) => (
                  <option
                    key={id}
                    value={id}
                    disabled={testOnly && id !== "private"}
                  >
                    {name}
                  </option>
                ))}
              </Select>
            </Field>
            {privacy === "scheduled" ? (
              <Field label="Data e hora">
                <Input
                  type="datetime-local"
                  value={when}
                  min={nowLocalInput()}
                  invalid={Boolean(when) && !isFutureLocal(when)}
                  onChange={(e) => setWhen(e.target.value)}
                />
              </Field>
            ) : null}
          </div>

          {otherLangs.length ? (
            <Checkbox
              checked={localize}
              onChange={setLocalize}
              label={`Incluir título e descrição traduzidos (${otherLangs.map(langLabel).join(", ")})`}
              description="O YouTube mostra a versão no idioma de quem assiste. Deixe desmarcado se cada idioma vai ganhar o próprio vídeo ou canal."
            />
          ) : null}
          {missingLangs.length ? (
            <div className="flex flex-col gap-1.5">
              <ul className="flex flex-col gap-1.5">
                {missingLangs.map((l) => (
                  <li
                    key={l}
                    className="flex flex-wrap items-center justify-between gap-2 text-[13px] text-muted"
                  >
                    <span>Sem título em {langLabel(l)} para a tradução.</span>
                    <Button
                      size="sm"
                      icon={Sparkles}
                      disabled={busy || metaBusy}
                      busy={generatingLangs.includes(l)}
                      onClick={() => onGenerateMeta(l)}
                      aria-label={`Gerar título em ${langLabel(l)}`}
                    >
                      Gerar
                    </Button>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-subtle">
                Gerar usa a IA de texto (Claude): gasta cota.
              </p>
            </div>
          ) : null}

          {/* Resumo do que vai subir */}
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-lg border border-line bg-canvas p-3 text-[13px]">
            <dt className="text-subtle">Título</dt>
            <dd className="min-w-0">
              <div className="truncate text-fg" title={meta?.title || doc.title}>
                {meta?.title || doc.title}
              </div>
              {unsavedMeta ? (
                <div className="text-xs text-amber-200">
                  Há edições não salvas no título e na descrição: o envio usa o
                  texto salvo.
                </div>
              ) : null}
            </dd>
            <dt className="text-subtle">Idioma</dt>
            <dd className="text-fg">{langLabel(lang)}</dd>
            <dt className="text-subtle">Visibilidade</dt>
            <dd className="text-fg">
              {PRIVACY_LABELS[privacy]}
              {privacy === "scheduled" && when && isFutureLocal(when)
                ? ` · ${formatWhen(new Date(when).toISOString())}`
                : ""}
            </dd>
            <dt className="text-subtle">Capa</dt>
            <dd className="text-fg">{entry?.thumbnail ? "Sim" : "Não"}</dd>
            <dt className="text-subtle">Legendas</dt>
            <dd className="text-fg">{entry?.srt ? "Sim" : "Não"}</dd>
            {localize && otherLangs.length ? (
              <>
                <dt className="text-subtle">Traduções</dt>
                <dd className="text-fg">
                  {otherLangs.map(langLabel).join(", ")}
                </dd>
              </>
            ) : null}
          </dl>

          {!meta ? (
            <Notice
              tone="warn"
              title="Sem descrição e tags"
              actions={
                <Button
                  size="sm"
                  icon={Sparkles}
                  disabled={busy || metaBusy}
                  busy={generatingLangs.includes(lang)}
                  onClick={() => onGenerateMeta(lang)}
                >
                  Gerar agora
                </Button>
              }
            >
              O vídeo vai só com o título do projeto. Gerar usa a IA de texto
              (Claude) e gasta cota.
            </Notice>
          ) : null}

          {publishJob ? (
            <JobProgress job={publishJob} title="Enviando ao YouTube" />
          ) : null}

          <div className="flex flex-col gap-1">
            <Button
              variant={
                emphasis && !alreadySent && !block && !publishJob
                  ? "primary"
                  : "secondary"
              }
              icon={Send}
              className="self-start"
              disabled={Boolean(block) || Boolean(publishJob)}
              busy={publish.isPending}
              onClick={send}
            >
              {sendLabel}
            </Button>
            <p className="text-xs text-subtle">
              {block ??
                "Envia o vídeo, a capa e as legendas. O agendamento fica no próprio YouTube: o computador pode estar desligado na hora."}
            </p>
          </div>
        </>
      )}
      <ErrorBox error={connect.error ?? publish.error} />
    </Card>
  );
};

/* ------------------------------------------------------------------ */
/* Buffer (TikTok e Instagram Reels)                                    */
/* ------------------------------------------------------------------ */

type BufferStatus = {
  api_key: boolean;
  storage: boolean;
  ready: boolean;
  reason: string | null;
  platforms: string[];
  /** Perfil usado da última vez pelo canal do projeto, por rede. */
  remembered: Record<string, string>;
};

type BufferChannel = {
  id: string;
  service: string;
  name: string;
  organization: string;
  disconnected: boolean;
  locked: boolean;
  queue_paused: boolean;
};

const BUFFER_PLATFORMS = ["tiktok", "instagram"];

const BUFFER_MODES: Record<string, string> = {
  queue: "Fila do Buffer",
  next: "Primeiro da fila",
  now: "Publicar agora",
  scheduled: "Horário marcado",
};

const BUFFER_DONE = new Set(["failed", "canceled"]);

export const BufferCard: React.FC<{
  detail: ProjectDetail;
  lang: string;
  hasVideo: boolean;
  previewOnly: string[];
  busy: boolean;
  unsavedMeta: boolean;
  finalStale: boolean;
  pickedSlot: PickedSlot;
}> = ({
  detail,
  lang,
  hasVideo,
  previewOnly,
  busy,
  unsavedMeta,
  finalStale,
  pickedSlot,
}) => {
  const { doc } = detail;
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const status = useQuery({
    queryKey: ["buffer-status", doc.id],
    queryFn: () =>
      api.get<BufferStatus>(
        `/api/buffer/status?project_id=${encodeURIComponent(doc.id)}`,
      ),
  });
  const ready = Boolean(status.data?.ready);
  const channels = useQuery({
    queryKey: ["buffer-channels"],
    queryFn: () =>
      api.get<{ channels: BufferChannel[] }>("/api/buffer/channels"),
    enabled: ready,
    staleTime: 5 * 60_000,
  });
  const [platforms, setPlatforms] = useState<string[]>(BUFFER_PLATFORMS);
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [mode, setMode] = useState("queue");
  const [when, setWhen] = useState("");
  useEffect(() => {
    if (!pickedSlot) return;
    setMode("scheduled");
    setWhen(pickedSlot.value);
  }, [pickedSlot]);

  const list = channels.data?.channels ?? [];
  const usable = (service: string) =>
    list.filter((c) => c.service === service && !c.disconnected && !c.locked);
  /** Perfil escolhido: o da tela, o lembrado pelo canal, ou o único da rede. */
  const channelFor = (service: string) => {
    const options = usable(service);
    const chosen = picked[service] ?? status.data?.remembered?.[service];
    if (chosen && options.some((c) => c.id === chosen)) return chosen;
    return options.length === 1 ? options[0].id : "";
  };
  const active = platforms.filter((pl) => usable(pl).length > 0);

  const publish = useMutation({
    mutationFn: async () => {
      for (const platform of active) {
        const id = channelFor(platform);
        await api.post(`/api/projects/${doc.id}/publish/buffer`, {
          lang,
          platform,
          buffer_channel_id: id,
          buffer_channel_name: list.find((c) => c.id === id)?.name ?? "",
          mode,
          publish_at:
            mode === "scheduled" && when ? new Date(when).toISOString() : null,
        });
      }
    },
    onSuccess: () =>
      Promise.all([
        client.invalidateQueries({ queryKey: ["project", doc.id] }),
        client.invalidateQueries({ queryKey: ["buffer-status", doc.id] }),
        client.invalidateQueries({ queryKey: ["agenda"] }),
        client.invalidateQueries({ queryKey: ["channel-slots"] }),
      ]).then(() =>
        toast({
          tone: "info",
          title: "Envio ao Buffer iniciado",
          description:
            "O vídeo sobe ao R2 e o post é criado no Buffer. O progresso aparece aqui.",
        }),
      ),
  });

  const job = findActiveJob(
    detail,
    ["publish_buffer"],
    (j) => String(j.payload?.lang ?? doc.primary_language) === lang,
  );
  const testOnly = previewOnly.length > 0;
  const missingChannel = active.find((pl) => !channelFor(pl));
  const block = !hasVideo
    ? "Renderize o vídeo final deste idioma primeiro."
    : job
      ? null
      : testOnly
        ? "Este render usou voz ou imagens de teste: o Buffer publica como público."
        : busy
          ? "Aguarde a tarefa em andamento terminar."
          : unsavedMeta
            ? UNSAVED_META_BLOCK
            : !active.length
              ? "Escolha pelo menos uma rede com perfil conectado no Buffer."
              : missingChannel
                ? `Escolha o perfil do ${label(PLATFORM_LABELS, missingChannel)}.`
                : mode === "scheduled" && !when
                  ? "Escolha a data e a hora do agendamento."
                  : mode === "scheduled" && !isFutureLocal(when)
                    ? "O horário do agendamento precisa estar no futuro."
                    : null;

  const send = async () => {
    if (finalStale) {
      const ok = await confirm({
        title: "Enviar um vídeo desatualizado?",
        description:
          "Houve mudanças no projeto depois deste render. O post usa o vídeo atual, sem essas mudanças. Para incluí-las, renderize de novo antes.",
        confirmLabel: "Enviar assim mesmo",
      });
      if (!ok) return;
    }
    const repeated = active.filter((pl) =>
      detail.publications.some(
        (p) =>
          p.driver === "buffer" &&
          p.language === lang &&
          p.platform === pl &&
          !BUFFER_DONE.has(p.status),
      ),
    );
    if (repeated.length) {
      const ok = await confirm({
        title: `Já há post no Buffer para ${repeated.map((pl) => label(PLATFORM_LABELS, pl)).join(", ")}. Criar outro?`,
        description:
          "Cada envio cria um post novo no Buffer (e mais um vídeo na rede quando ele sair).",
        confirmLabel: "Criar outro post",
      });
      if (!ok) return;
    }
    publish.mutate();
  };

  const s = status.data;
  return (
    <Card className="flex flex-col gap-3">
      <div>
        <h3 className="font-display text-[15px] font-semibold text-fg">
          TikTok e Instagram pelo Buffer
        </h3>
        <p className="mt-0.5 text-[13px] text-muted">
          O vídeo fica numa URL pública do Cloudflare R2 até o post sair e é
          apagado depois. A fila e o horário ficam no Buffer: o computador pode
          estar desligado.
        </p>
      </div>
      {!s ? (
        status.error ? (
          <ErrorBox error={status.error} />
        ) : (
          <p className="text-[13px] text-subtle">Verificando o Buffer…</p>
        )
      ) : !s.ready ? (
        <div className="flex flex-col gap-2 text-[13px] text-muted">
          <p>
            {s.api_key
              ? "Falta configurar o Cloudflare R2 (onde o vídeo fica até o Buffer publicar)."
              : "Cole a chave de API do Buffer e configure o Cloudflare R2 em Configurações, na parte de Publicação."}
          </p>
          <Link
            to="/configuracoes"
            className={`${buttonClass({ size: "sm" })} self-start`}
          >
            Abrir Configurações
          </Link>
        </div>
      ) : channels.error ? (
        <ErrorBox error={channels.error} />
      ) : !channels.data ? (
        <p className="text-[13px] text-subtle">Buscando os perfis do Buffer…</p>
      ) : !list.length ? (
        <p className="text-[13px] text-muted">
          Nenhum perfil de TikTok ou Instagram conectado no Buffer. Conecte em
          buffer.com e volte aqui.
        </p>
      ) : (
        <>
          <ul className="flex flex-col gap-2">
            {BUFFER_PLATFORMS.map((pl) => {
              const options = usable(pl);
              const on = platforms.includes(pl) && options.length > 0;
              const chosen = channelFor(pl);
              const paused = list.find((c) => c.id === chosen)?.queue_paused;
              return (
                <li key={pl} className="flex flex-wrap items-end gap-2">
                  <Checkbox
                    checked={on}
                    disabled={!options.length}
                    onChange={(v) =>
                      setPlatforms((prev) =>
                        v ? [...prev, pl] : prev.filter((x) => x !== pl),
                      )
                    }
                    label={label(PLATFORM_LABELS, pl)}
                    description={
                      !options.length
                        ? "Sem perfil conectado no Buffer."
                        : paused
                          ? "A fila deste perfil está pausada no Buffer."
                          : undefined
                    }
                  />
                  {on && options.length > 1 ? (
                    <Field label="Perfil" className="min-w-44">
                      <Select
                        value={chosen}
                        onChange={(e) =>
                          setPicked((prev) => ({ ...prev, [pl]: e.target.value }))
                        }
                      >
                        <option value="">Escolha…</option>
                        {options.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.name}
                            {c.organization ? ` (${c.organization})` : ""}
                          </option>
                        ))}
                      </Select>
                    </Field>
                  ) : on && options[0] ? (
                    <span className="text-[13px] text-muted">
                      {options[0].name}
                    </span>
                  ) : null}
                </li>
              );
            })}
          </ul>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Quando" className="min-w-40">
              <Select value={mode} onChange={(e) => setMode(e.target.value)}>
                {Object.entries(BUFFER_MODES).map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </Select>
            </Field>
            {mode === "scheduled" ? (
              <Field label="Data e hora">
                <Input
                  type="datetime-local"
                  value={when}
                  min={nowLocalInput()}
                  invalid={Boolean(when) && !isFutureLocal(when)}
                  onChange={(e) => setWhen(e.target.value)}
                />
              </Field>
            ) : null}
          </div>
          {testOnly ? (
            <Notice tone="warn" title="Render com voz ou imagens de teste">
              O Buffer publica como público. Configure provedores comerciais e
              renderize de novo.{" "}
              <Link to="/configuracoes" className={LINK}>
                Configurar provedores
              </Link>
            </Notice>
          ) : null}
          {job ? <JobProgress job={job} title="Enviando ao Buffer" /> : null}
          <div className="flex flex-col gap-1">
            <Button
              variant="secondary"
              icon={Send}
              className="self-start"
              disabled={Boolean(block) || Boolean(job)}
              busy={publish.isPending}
              onClick={send}
            >
              Enviar ao Buffer
            </Button>
            <p className="text-xs text-subtle">
              {block ??
                "A legenda é a mesma do pacote manual (com hashtags e créditos) e o rótulo de IA segue o estilo."}
            </p>
          </div>
        </>
      )}
      <ErrorBox error={publish.error} />
    </Card>
  );
};

/* ------------------------------------------------------------------ */
/* Outras redes (pacote manual)                                         */
/* ------------------------------------------------------------------ */

const MANUAL_PLATFORMS = [
  "tiktok",
  "instagram",
  "facebook",
  "threads",
  "x",
  "youtube",
];

export const ManualPackageCard: React.FC<{
  detail: ProjectDetail;
  lang: string;
  hasVideo: boolean;
  previewOnly: string[];
  /** Este cartão tem a ação principal da aba (sem envio direto ao YouTube). */
  primary: boolean;
  unsavedMeta: boolean;
  finalStale: boolean;
  pickedSlot: PickedSlot;
}> = ({
  detail,
  lang,
  hasVideo,
  previewOnly,
  primary,
  unsavedMeta,
  finalStale,
  pickedSlot,
}) => {
  const { doc } = detail;
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const [platforms, setPlatforms] = useState<string[]>(["tiktok", "instagram"]);
  const [planned, setPlanned] = useState("");
  useEffect(() => {
    if (pickedSlot) setPlanned(pickedSlot.value);
  }, [pickedSlot]);
  const makePackage = useMutation({
    mutationFn: () =>
      api.post<{ items: { id: string; blocked?: boolean }[] }>(
        `/api/projects/${doc.id}/publish/package`,
        {
          lang,
          platforms,
          planned_at: planned ? new Date(planned).toISOString() : null,
        },
      ),
    onSuccess: (data) => {
      // O backend avisa por item quando o pacote é só para teste; a lista mostra isso em cada linha.
      data.items
        .filter((i) => i.blocked)
        .forEach((i) => testPackages.set(doc.id, i.id, true));
      return Promise.all([
        client.invalidateQueries({ queryKey: ["project", doc.id] }),
        client.invalidateQueries({ queryKey: ["agenda"] }),
        client.invalidateQueries({ queryKey: ["channel-slots"] }),
      ]).then(() =>
        toast({
          tone: "good",
          title: "Pacote pronto",
          description: `${data.items.length} ${data.items.length === 1 ? "pasta criada" : "pastas criadas"}. Veja em Publicações deste projeto.`,
        }),
      );
    },
  });
  const testOnly = previewOnly.length > 0;
  const renderJob = findActiveJob(detail, ["render", "autopilot"]);
  const block = !hasVideo
    ? "Renderize o vídeo final deste idioma primeiro."
    : renderJob
      ? "Aguarde o render terminar: o pacote copia o vídeo final."
      : unsavedMeta
        ? UNSAVED_META_BLOCK
        : !platforms.length
          ? "Escolha pelo menos uma rede."
          : null;

  const prepare = async () => {
    if (finalStale) {
      const ok = await confirm({
        title: "Preparar pacote com um vídeo desatualizado?",
        description:
          "Houve mudanças no projeto depois deste render. O pacote usa o vídeo atual, sem essas mudanças. Para incluí-las, renderize de novo antes.",
        confirmLabel: "Preparar assim mesmo",
      });
      if (!ok) return;
    }
    const repeated = platforms.filter((pl) =>
      detail.publications.some(
        (p) =>
          p.driver === "manual_helper" &&
          p.language === lang &&
          p.status === "package_ready" &&
          p.platform === pl,
      ),
    );
    if (repeated.length) {
      const names = repeated
        .map((pl) => (pl === "youtube" ? "YouTube (manual)" : label(PLATFORM_LABELS, pl)))
        .join(", ");
      const ok = await confirm({
        title: `Já há pacote pronto para ${names}. Criar outro?`,
        description:
          "Cada pacote cria uma pasta nova e um novo item na Agenda. Se o anterior ainda serve, use-o em Publicações deste projeto.",
        confirmLabel: "Criar outro pacote",
      });
      if (!ok) return;
    }
    makePackage.mutate();
  };

  return (
    <Card className="flex flex-col gap-3">
      <div>
        <h3 className="font-display text-[15px] font-semibold text-fg">
          Outras redes (envio manual)
        </h3>
        <p className="mt-0.5 text-[13px] text-muted">
          O app monta uma pasta por rede com o vídeo, a legenda adaptada, a capa
          e um checklist (com o rótulo de IA). Você envia pela página oficial da
          rede.
        </p>
      </div>
      <FieldGroup legend="Redes">
        <div className="flex flex-wrap gap-1.5">
          {MANUAL_PLATFORMS.map((id) => {
            const on = platforms.includes(id);
            return (
              <button
                key={id}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  setPlatforms((prev) =>
                    prev.includes(id)
                      ? prev.filter((x) => x !== id)
                      : [...prev, id],
                  )
                }
                className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs transition-colors ${
                  on
                    ? "border-accent/60 bg-accent/10 text-fg"
                    : "border-line-strong text-muted hover:text-fg"
                }`}
              >
                {on ? <Check size={12} aria-hidden="true" /> : null}
                {id === "youtube"
                  ? "YouTube (manual)"
                  : label(PLATFORM_LABELS, id)}
              </button>
            );
          })}
        </div>
      </FieldGroup>
      <Field
        label="Planejar para (opcional)"
        hint="O horário aparece na Agenda até você marcar como postado."
      >
        <Input
          type="datetime-local"
          value={planned}
          min={nowLocalInput()}
          onChange={(e) => setPlanned(e.target.value)}
        />
      </Field>
      {testOnly ? (
        <Notice tone="warn" title="Render com voz ou imagens de teste">
          Serve para conferir o fluxo, não para publicar num canal monetizado.{" "}
          <Link to="/configuracoes" className={LINK}>
            Configurar provedores
          </Link>
        </Notice>
      ) : null}
      <div className="flex flex-col gap-1">
        <Button
          variant={primary && !block ? "primary" : "secondary"}
          icon={PackageOpen}
          className="self-start"
          disabled={Boolean(block)}
          busy={makePackage.isPending}
          onClick={prepare}
        >
          {testOnly ? "Preparar pacote (só para teste)" : "Preparar pacote"}
        </Button>
        {block ? <p className="text-xs text-subtle">{block}</p> : null}
      </div>
      <ErrorBox error={makePackage.error} />
    </Card>
  );
};

/* ------------------------------------------------------------------ */
/* Publicações deste projeto                                            */
/* ------------------------------------------------------------------ */

type PubDetails = Publication["details"] & {
  privacy?: string;
  publish_at?: string | null;
  published_at?: string;
  warnings?: string[];
  buffer_channel_name?: string;
  tracking?: boolean;
  r2_deleted?: boolean;
  cleanup_error?: string;
};

const STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  uploaded: { label: "Enviado", tone: "good" },
  scheduled: { label: "Agendado", tone: "info" },
  package_ready: { label: "Pacote pronto", tone: "neutral" },
  published: { label: "Postado", tone: "good" },
  failed: { label: "Falhou", tone: "bad" },
  canceled: { label: "Cancelado", tone: "neutral" },
};

const PublicationRow: React.FC<{
  pub: Publication;
  projectId: string;
  testOnly: boolean;
}> = ({ pub, projectId, testOnly }) => {
  const client = useQueryClient();
  const d = pub.details as PubDetails;
  const st = STATUS[pub.status] ?? {
    label: "Em andamento",
    tone: "neutral" as BadgeTone,
  };
  const manual = pub.driver === "manual_helper";
  const viaBuffer = pub.driver === "buffer";
  const confirm = useConfirm();
  const platformName =
    pub.platform === "youtube" && manual
      ? "YouTube (manual)"
      : label(PLATFORM_LABELS, pub.platform);
  const open = useMutation({
    mutationFn: () => api.post(`/api/publications/${pub.id}/open`),
  });
  const markPosted = useMutation({
    mutationFn: () => api.post(`/api/publications/${pub.id}/published`, {}),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["project", projectId] });
      client.invalidateQueries({ queryKey: ["agenda"] });
    },
  });

  const cancelBuffer = useMutation({
    mutationFn: () => api.post(`/api/publications/${pub.id}/buffer/cancel`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["project", projectId] });
      client.invalidateQueries({ queryKey: ["agenda"] });
    },
  });
  const askCancel = async () => {
    const ok = await confirm({
      title: `Cancelar o post no ${platformName}?`,
      description:
        "O post é apagado do Buffer e o vídeo sai do R2. Isso não pode ser desfeito; para postar de novo, envie outra vez.",
      confirmLabel: "Cancelar post",
      tone: "danger",
    });
    if (ok) cancelBuffer.mutate();
  };

  let when = pub.created_at
    ? `${pub.driver === "youtube_native" || viaBuffer ? "enviado" : "criado"} ${formatRelative(pub.created_at)}`
    : "";
  if (pub.status === "scheduled" && d.publish_at)
    when = `para ${formatWhen(d.publish_at)}`;
  if (pub.status === "published" && d.published_at)
    when = `postado ${formatRelative(d.published_at)}`;

  return (
    <li className="flex flex-col gap-2 rounded-lg border border-line bg-canvas p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium text-fg">{platformName}</span>
        <Badge tone={st.tone}>{st.label}</Badge>
        {testOnly ? <Badge tone="bad">Só para teste</Badge> : null}
        {viaBuffer ? (
          <Badge title={d.buffer_channel_name || undefined}>
            Buffer{d.buffer_channel_name ? ` · ${d.buffer_channel_name}` : ""}
          </Badge>
        ) : null}
        {pub.driver === "youtube_native" &&
        d.privacy &&
        pub.status !== "scheduled" ? (
          <Badge>{PRIVACY_LABELS[d.privacy] ?? "Visibilidade própria"}</Badge>
        ) : null}

        {when ? (
          <span className="text-[13px] tabular-nums text-subtle">{when}</span>
        ) : null}
      </div>
      {pub.error ? (
        <p className="text-[13px] text-red-300">{pub.error}</p>
      ) : null}
      {viaBuffer && d.cleanup_error ? (
        <p className="text-xs text-amber-200">
          O vídeo ainda está no R2 (nova tentativa na próxima conferência):{" "}
          {d.cleanup_error}
        </p>
      ) : null}
      <div className="flex flex-wrap items-center gap-1">
        {manual && pub.package_path ? (
          <Button
            size="sm"
            variant="ghost"
            icon={FolderOpen}
            busy={open.isPending}
            onClick={() => open.mutate()}
            aria-label={`Abrir pasta (${platformName})`}
          >
            Abrir pasta
          </Button>
        ) : null}
        {manual && d.url && pub.status !== "published" ? (
          <a
            href={d.url}
            target="_blank"
            rel="noreferrer"
            className={buttonClass({ variant: "ghost", size: "sm" })}
            aria-label={`Página de upload (${platformName})`}
          >
            <ExternalLink size={14} aria-hidden="true" />
            Página de upload
          </a>
        ) : null}
        {manual && d.caption ? (
          <CopyButton
            text={d.caption}
            label="Copiar legenda"
            ariaLabel={`Copiar legenda (${platformName})`}
          />
        ) : null}
        {manual && pub.status === "package_ready" ? (
          <Button
            size="sm"
            variant="ghost"
            icon={Check}
            busy={markPosted.isPending}
            onClick={() => markPosted.mutate()}
            aria-label={`Marcar como postado (${platformName})`}
          >
            Marcar como postado
          </Button>
        ) : null}
        {viaBuffer && (pub.status === "scheduled" || pub.status === "uploaded") ? (
          <Button
            size="sm"
            variant="ghost"
            icon={X}
            busy={cancelBuffer.isPending}
            onClick={askCancel}
            aria-label={`Cancelar post (${platformName})`}
          >
            Cancelar post
          </Button>
        ) : null}
        {pub.url ? (
          <a
            href={pub.url}
            target="_blank"
            rel="noreferrer"
            className={buttonClass({ variant: "ghost", size: "sm" })}
            aria-label={
              pub.driver === "youtube_native"
                ? undefined
                : `Ver post (${platformName})`
            }
          >
            <ExternalLink size={14} aria-hidden="true" />
            {pub.driver === "youtube_native" ? "Ver no YouTube" : "Ver post"}
          </a>
        ) : null}
      </div>
      <ErrorBox error={open.error ?? markPosted.error ?? cancelBuffer.error} />
    </li>
  );
};

export const PublicationsList: React.FC<{
  detail: ProjectDetail;
  lang: string;
}> = ({ detail, lang }) => {
  const multi = detail.doc.languages.length > 1;
  const items = detail.publications.filter((p) => p.language === lang);
  const marked = useKeyedStore(testPackages, detail.doc.id);
  // Pacotes feitos depois do último render herdam o "só para teste" dele.
  const finalTest = (detail.outputs.final?.preview_only_providers ?? []).length > 0;
  const lastRender = lastDoneJob(detail, finalJobKinds(detail));
  const renderedAt = lastRender?.finished_at ? Date.parse(lastRender.finished_at) : NaN;
  const isTest = (p: Publication) =>
    p.driver === "manual_helper" &&
    (Boolean(marked[p.id]) ||
      (finalTest &&
        Number.isFinite(renderedAt) &&
        p.created_at != null &&
        Date.parse(p.created_at) >= renderedAt));
  const others = detail.publications.length - items.length;
  const client = useQueryClient();
  const tracking = items.some(
    (p) => p.driver === "buffer" && (p.details as PubDetails).tracking,
  );
  const syncBuffer = useMutation({
    mutationFn: () => api.post("/api/buffer/sync"),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["project", detail.doc.id] });
      client.invalidateQueries({ queryKey: ["agenda"] });
    },
  });
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-display text-[15px] font-semibold text-fg">
          Publicações deste projeto
        </h3>
        {tracking ? (
          <Button
            size="sm"
            variant="ghost"
            icon={RefreshCw}
            busy={syncBuffer.isPending}
            onClick={() => syncBuffer.mutate()}
            title="Com o app aberto, o Vulto confere sozinho de 10 em 10 minutos"
          >
            Conferir no Buffer
          </Button>
        ) : null}
      </div>
      <ErrorBox error={syncBuffer.error} />
      {items.length ? (
        <ul className="flex flex-col gap-2">
          {items.map((p) => (
            <PublicationRow
              key={p.id}
              pub={p}
              projectId={detail.doc.id}
              testOnly={isTest(p)}
            />
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-subtle">
          Nada enviado ou preparado{multi ? ` em ${langLabel(lang)}` : ""}{" "}
          ainda.
        </p>
      )}
      {multi && others > 0 ? (
        <p className="text-xs text-subtle">
          Mais {others} {others === 1 ? "publicação" : "publicações"} em outros
          idiomas: troque o idioma no topo para ver.
        </p>
      ) : null}
    </Card>
  );
};
