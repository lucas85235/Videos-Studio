// Passo 3 da aba Vídeo e publicação: Reel direto pela API oficial do Instagram (ou escolha do pacote manual).
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import React, { useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  type InstagramDriver,
  type LangOutput,
  type ProjectDetail,
} from "../../../api";
import { useInstagramStatus } from "../../../instagram";
import { langLabel } from "../../../labels";
import {
  Button,
  buttonClass,
  Card,
  Checkbox,
  ErrorBox,
  Field,
  Input,
  SegmentedControl,
  Select,
  useConfirm,
  useToast,
} from "../../../ui";
import { findActiveJob, JobProgress, Notice } from "./shared";

const DRIVER_OPTIONS: { id: InstagramDriver; label: string }[] = [
  { id: "native", label: "API oficial (direto)" },
  { id: "manual", label: "Pacote manual" },
];

/** URL que o servidor monta com o endereço público das opções (só para mostrar). O endereço espelha só a
 * pasta outputs, então o caminho vai sem o "outputs/" do começo; arquivos fora dela não viram URL. */
const autoUrl = (base: string, rel: string | undefined) => {
  if (!base || !rel) return "";
  const parts = rel.replace(/\\/g, "/").split("/").filter((p) => p && p !== ".");
  if (parts.length < 2 || parts[0] !== "outputs" || parts.includes("..")) return "";
  return `${base}/${parts.slice(1).map(encodeURIComponent).join("/")}`;
};

export const InstagramCard: React.FC<{
  detail: ProjectDetail;
  lang: string;
  entry: LangOutput | undefined;
  previewOnly: string[];
  busy: boolean;
  unsavedMeta: boolean;
  finalStale: boolean;
}> = ({ detail, lang, entry, previewOnly, busy, unsavedMeta, finalStale }) => {
  const { doc } = detail;
  const projectId = doc.id;
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const status = useInstagramStatus(projectId);
  const [localDriver, setLocalDriver] = useState<InstagramDriver | null>(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [shareToFeed, setShareToFeed] = useState(true);
  const s = status.data;
  const driver: InstagramDriver = localDriver ?? s?.driver ?? "manual";
  const multi = doc.languages.length > 1;

  const refresh = () =>
    client.invalidateQueries({ queryKey: ["instagram-status"] });
  // Com canal, a escolha fica salva no canal; sem canal, vale só nesta tela.
  const saveChannel = useMutation({
    mutationFn: (body: { driver?: InstagramDriver; user_id?: string | null }) =>
      api.put(`/api/channels/${doc.channel_id}/instagram`, body),
    onSuccess: refresh,
  });
  const chooseDriver = (value: InstagramDriver) => {
    setLocalDriver(value);
    if (doc.channel_id) saveChannel.mutate({ driver: value });
  };

  // Envio deste idioma que ficou esperando o Instagram (tempo esgotado, falha depois de criar ou cancelado):
  // retomar publica o mesmo envio; publicar de novo criaria um segundo Reel igual.
  const pending = detail.publications.find(
    (p) =>
      p.driver === "instagram_native" &&
      p.language === lang &&
      p.status === "processing" &&
      Boolean(p.details?.container_id),
  );
  const publish = useMutation({
    mutationFn: () =>
      api.post(
        `/api/projects/${projectId}/publish/instagram`,
        pending
          ? { publication_id: pending.id }
          : {
              lang,
              video_url: videoUrl.trim() || null,
              share_to_feed: shareToFeed,
            },
      ),
    onSuccess: () =>
      client
        .invalidateQueries({ queryKey: ["project", projectId] })
        .then(() =>
          toast({
            tone: "info",
            title: "Publicação no Instagram iniciada",
            description:
              "O Instagram baixa e processa o vídeo (alguns minutos). O progresso aparece aqui.",
          }),
        ),
  });

  const job = findActiveJob(
    detail,
    ["publish_instagram"],
    (j) => String(j.payload?.lang ?? doc.primary_language) === lang,
  );
  const alreadySent = detail.publications.some(
    (p) =>
      p.driver === "instagram_native" &&
      p.language === lang &&
      p.status === "published",
  );
  const suggested = autoUrl(s?.public_base_url ?? "", entry?.video);
  const testOnly = previewOnly.length > 0;
  const typedUrl = videoUrl.trim();
  const block = !entry?.video
    ? "Renderize o vídeo final deste idioma primeiro."
    : pending
      ? job || busy
        ? "Aguarde a tarefa em andamento terminar."
        : null
      : testOnly
      ? "Este render usou voz ou imagens de teste e o Instagram não tem post privado."
      : job
        ? null
        : busy
          ? "Aguarde a tarefa em andamento terminar."
          : unsavedMeta
            ? "Salve o título e a descrição antes de publicar."
            : !typedUrl && !suggested
              ? "Informe a URL pública do vídeo."
              : typedUrl && !typedUrl.startsWith("https://")
                ? "A URL precisa começar com https://."
                : entry.duration && (entry.duration < 3 || entry.duration > 900)
                  ? "O Reel precisa ter de 3 s a 15 min."
                  : null;

  const send = async () => {
    if (pending) {
      const ok = await confirm({
        title: "Retomar o envio ao Instagram?",
        description:
          "Este Reel já foi enviado e ficou esperando o Instagram. Retomar publica o mesmo envio (vale 24 h), sem criar outro post.",
        confirmLabel: "Retomar envio",
      });
      if (ok) publish.mutate();
      return;
    }
    const ok = await confirm({
      title: alreadySent
        ? "Publicar outra cópia no Instagram?"
        : "Publicar o Reel agora?",
      description: `${alreadySent ? `Este vídeo${multi ? ` em ${langLabel(lang)}` : ""} já foi publicado; um novo envio cria outro post. ` : ""}O Instagram publica na hora, como público (a API não agenda nem faz post privado).${finalStale ? " Atenção: houve mudanças depois deste render; o vídeo não as inclui." : ""}`,
      confirmLabel: alreadySent ? "Publicar outra cópia" : "Publicar agora",
    });
    if (ok) publish.mutate();
  };

  return (
    <Card className="flex flex-col gap-3">
      <div>
        <h3 className="font-display text-[15px] font-semibold text-fg">
          Instagram (Reels)
        </h3>
        <p className="mt-0.5 text-[13px] text-muted">
          Publique direto pela API oficial do Instagram ou monte o pacote para
          postar à mão.
        </p>
      </div>
      {!s ? (
        status.error ? (
          <ErrorBox error={status.error} />
        ) : (
          <p className="text-[13px] text-subtle">Verificando a conta…</p>
        )
      ) : (
        <>
          <SegmentedControl
            label="Como publicar no Instagram"
            options={DRIVER_OPTIONS}
            value={driver}
            onChange={chooseDriver}
            size="sm"
          />
          {!doc.channel_id ? (
            <p className="text-xs text-subtle">
              Projeto sem canal: a escolha vale só nesta tela.
            </p>
          ) : null}
          {driver === "manual" ? (
            <p className="text-[13px] text-muted">
              Marque Instagram no cartão de envio manual para gerar a pasta com
              o vídeo, a legenda e o checklist.
            </p>
          ) : !s.accounts.length ? (
            <div className="flex flex-col gap-2 text-[13px] text-muted">
              <p>
                Conecte uma conta profissional do Instagram em Configurações
                (login do Instagram ou token do painel da Meta).
              </p>
              <Link
                to="/configuracoes#instagram"
                className={`${buttonClass({ size: "sm" })} self-start`}
              >
                Abrir Configurações
              </Link>
            </div>
          ) : (
            <>
              {doc.channel_id && s.accounts.length > 1 ? (
                <Field label="Conta do canal" className="max-w-xs">
                  <Select
                    value={s.channel_user_id ?? ""}
                    onChange={(e) =>
                      saveChannel.mutate({ user_id: e.target.value || null })
                    }
                  >
                    <option value="">Escolha a conta</option>
                    {s.accounts.map((a) => (
                      <option key={a.user_id} value={a.user_id}>
                        @{a.username || a.user_id}
                      </option>
                    ))}
                  </Select>
                </Field>
              ) : null}
              {s.ready && s.account ? (
                <p className="text-[13px] text-muted">
                  Conta:{" "}
                  <span className="font-medium text-fg">
                    @{s.account.username || s.account.user_id}
                  </span>
                </p>
              ) : (
                <Notice tone="warn" title="Sem conta para este projeto">
                  {s.reason}
                </Notice>
              )}
              <Field
                label="URL pública do vídeo"
                hint={
                  suggested
                    ? "Vazio usa o endereço público das opções (o arquivo precisa estar lá antes de publicar)."
                    : "O Instagram baixa o vídeo desta URL https (o app roda só neste computador). Suba o MP4 para um lugar público e cole o link direto do arquivo."
                }
              >
                <Input
                  value={videoUrl}
                  onChange={(e) => setVideoUrl(e.target.value)}
                  placeholder={suggested || "https://…/video.mp4"}
                  invalid={Boolean(typedUrl) && !typedUrl.startsWith("https://")}
                />
              </Field>
              <Checkbox
                checked={shareToFeed}
                onChange={setShareToFeed}
                label="Mostrar também no feed do perfil"
                description="Desmarcado, o Reel fica só na aba Reels."
              />
              <p className="text-xs text-subtle">
                A legenda usa o título, o primeiro parágrafo da descrição e as
                hashtags (até 2.200 caracteres). O rótulo de IA segue o estilo;
                se a API recusar, o app avisa para marcar no celular.
              </p>
              {job ? (
                <JobProgress job={job} title="Publicando no Instagram" />
              ) : null}
              <div className="flex flex-col gap-1">
                <Button
                  variant="secondary"
                  icon={Send}
                  className="self-start"
                  disabled={Boolean(block) || Boolean(job) || !s.ready}
                  busy={publish.isPending}
                  onClick={send}
                >
                  {pending
                    ? "Retomar envio"
                    : alreadySent
                    ? "Publicar outra cópia"
                    : multi
                      ? `Publicar Reel (${langLabel(lang)})`
                      : "Publicar Reel"}
                </Button>
                <p className="text-xs text-subtle">
                  {block ??
                    (pending
                      ? `Há um envio esperando o Instagram${pending.error ? ` (${pending.error})` : ""}.`
                      : "Publica na hora. Limite da Meta: 50 posts por conta a cada 24 h.")}
                </p>
              </div>
            </>
          )}
        </>
      )}
      <ErrorBox error={saveChannel.error ?? publish.error} />
    </Card>
  );
};
