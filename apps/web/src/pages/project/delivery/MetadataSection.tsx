// Passo 2 da aba Vídeo e publicação: título, descrição, tags e hashtags (editáveis).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Save, Sparkles, TriangleAlert } from "lucide-react";
import React, { useEffect, useId } from "react";
import { api, type Job, type Metadata, type ProjectDetail } from "../../../api";
import { langLabel } from "../../../labels";
import {
  Button,
  ErrorBox,
  Field,
  Input,
  Section,
  Textarea,
  useToast,
} from "../../../ui";
import { createKeyedStore, useKeyedStore } from "../core/drafts";
import { EDITORIAL_NOTICE, mediaCredits } from "../core/media";
import {
  CopyButton,
  guardUnload,
  JobProgress,
  METADATA_EDIT_MESSAGE,
  Notice,
} from "./shared";

const TITLE_MAX = 100;
const DESCRIPTION_MAX = 5000;
/** O envio ao YouTube acrescenta até 15 hashtags no fim da descrição. */
const HASHTAGS_IN_DESCRIPTION = 15;

const parseTags = (text: string) =>
  text
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);

const parseHashtags = (text: string) =>
  text
    .split(/[\s,]+/)
    .map((t) => t.trim())
    .filter(Boolean)
    .map((t) => (t.startsWith("#") ? t : `#${t}`));

const sameList = (a: string[], b: string[]) =>
  a.length === b.length && a.every((x, i) => x === b[i]);

/* ------------------------------------------------------------------ */
/* Rascunho (fora do componente)                                       */
/* ------------------------------------------------------------------ */

type MetaValues = {
  title: string;
  description: string;
  tags: string;
  hashtags: string;
};

/** Rascunho por idioma: sobrevive à troca de idioma, de aba do projeto e à chegada de dados novos. */
type MetaDraft = MetaValues & {
  /** Metadados salvos quando o rascunho começou (para avisar se mudaram por baixo). */
  base: string;
};

const metaDrafts = createKeyedStore<MetaDraft>();
// Só guarda entrada enquanto difere do salvo: qualquer entrada é edição não salva.
guardUnload(() => metaDrafts.hasAny());

const metaBase = (meta: Metadata | undefined) =>
  meta ? JSON.stringify(meta) : "";

const fromMeta = (meta: Metadata | undefined): MetaValues => ({
  title: meta?.title ?? "",
  description: meta?.description ?? "",
  tags: (meta?.tags ?? []).join(", "),
  hashtags: (meta?.hashtags ?? []).join(" "),
});

/** Compara sempre a forma normalizada dos dois lados (o agente de edição grava valores crus). */
const differs = (v: MetaValues, meta: Metadata | undefined) => {
  const saved = fromMeta(meta);
  return (
    v.title.trim() !== saved.title.trim() ||
    v.description !== saved.description ||
    !sameList(parseTags(v.tags), parseTags(saved.tags)) ||
    !sameList(parseHashtags(v.hashtags), parseHashtags(saved.hashtags))
  );
};

/** Idiomas com edições não salvas no título e na descrição. */
export const useUnsavedMeta = (detail: ProjectDetail) => {
  const bucket = useKeyedStore(metaDrafts, detail.doc.id);
  return (lang: string) => {
    const draft = bucket[lang];
    return Boolean(draft && differs(draft, detail.doc.metadata[lang]));
  };
};

/** Descarta o rascunho de um idioma (ex.: antes de gerar de novo com a IA, já confirmado). */
export const discardMetaDraft = (projectId: string, lang: string) =>
  metaDrafts.set(projectId, lang, null);

/* ------------------------------------------------------------------ */
/* Formulário                                                          */
/* ------------------------------------------------------------------ */

const Counter: React.FC<{
  id: string;
  value: number;
  max: number;
  extra?: string;
}> = ({ id, value, max, extra }) => (
  <span
    id={id}
    className={`tabular-nums ${value > max ? "text-red-300" : "text-subtle"}`}
  >
    {value.toLocaleString("pt-BR")}/{max.toLocaleString("pt-BR")}
    {extra ? ` ${extra}` : ""}
    {value > max ? " · o excesso é cortado no envio" : ""}
  </span>
);

type Props = {
  detail: ProjectDetail;
  lang: string;
  /** Há prévia ou vídeo final com capítulos para a descrição. */
  hasChapterSource: boolean;
  /** Pede a geração (quem chama confirma antes de substituir o texto). */
  onGenerate: () => void;
  generating: boolean;
  generateError: unknown;
  onCloseGenerateError?: () => void;
  /** Motivo para não gerar agora (outra tarefa rodando, sem roteiro). */
  generateBlock: string | null;
  /** Job de metadados deste idioma. */
  metadataJob?: Job;
  /** Este passo tem a ação principal da aba. */
  emphasis: boolean;
  /** Abre a etapa "Título e thumb" (o título travado no pacote é editado lá). */
  onEditPackage?: () => void;
};

export const MetadataSection: React.FC<Props> = ({
  detail,
  lang,
  hasChapterSource,
  onGenerate,
  generating,
  generateError,
  onCloseGenerateError,
  generateBlock,
  metadataJob,
  emphasis,
  onEditPackage,
}) => {
  const { doc } = detail;
  // Pacote aprovado (título e thumb primeiro): o título é o do candidato escolhido e não se edita aqui.
  const packageLocked = Boolean(doc.package?.locked && doc.package.chosen_id);
  const projectId = doc.id;
  const meta = doc.metadata[lang];
  const client = useQueryClient();
  const { toast } = useToast();
  const ids = useId();
  const titleId = `${ids}-campo-titulo`;

  const bucket = useKeyedStore(metaDrafts, projectId);
  const draft = bucket[lang];
  const values: MetaValues = draft ?? fromMeta(meta);
  const { title, description, tags, hashtags } = values;
  const dirty = Boolean(draft) && differs(values, meta);
  const changedUnder = dirty && draft!.base !== metaBase(meta);
  // O servidor passou a ter o mesmo texto do rascunho (ex.: salvo em outra aba): o rascunho sai.
  useEffect(() => {
    if (draft && !differs(draft, meta)) metaDrafts.set(projectId, lang, null);
  }, [draft, meta, projectId, lang]);

  const update = (field: keyof MetaValues, value: string) => {
    const next = { ...values, [field]: value };
    metaDrafts.set(
      projectId,
      lang,
      differs(next, meta)
        ? { ...next, base: draft?.base ?? metaBase(meta) }
        : null,
    );
  };

  const save = useMutation({
    mutationFn: (v: MetaValues & { lang: string }) =>
      api.post(`/api/projects/${projectId}/ops`, {
        ops: [
          {
            op: "set_metadata",
            lang: v.lang,
            title: v.title.trim(),
            description: v.description,
            tags: parseTags(v.tags),
            hashtags: parseHashtags(v.hashtags),
          },
        ],
        message: METADATA_EDIT_MESSAGE,
      }),
    onSuccess: (_data, v) =>
      client
        .invalidateQueries({ queryKey: ["project", projectId] })
        .then(() => {
          metaDrafts.set(projectId, v.lang, null);
          toast({ tone: "good", title: "Título e descrição salvos" });
          // "Salvar" some junto com as edições: o foco volta para o título.
          window.requestAnimationFrame(() =>
            document.getElementById(titleId)?.focus(),
          );
        }),
  });

  // Créditos da mídia real: o envio põe a lista no fim da descrição (só busca quando alguma cena tem mídia real).
  const hasMedia = doc.scenes.some((s) => Boolean(s.visual.media));
  const credits = useQuery({
    queryKey: [
      "media-credits",
      projectId,
      lang,
      detail.project.current_version,
    ],
    queryFn: () => mediaCredits(projectId, lang),
    enabled: hasMedia,
    retry: false,
    placeholderData: (prev) => prev,
  });
  const creditsText = hasMedia ? (credits.data?.text ?? "").trim() : "";

  const locked = Boolean(metadataJob) || save.isPending;
  const titleTooLong = title.length > TITLE_MAX;
  const tagsForDescription = parseHashtags(hashtags).slice(
    0,
    HASHTAGS_IN_DESCRIPTION,
  );
  // O envio junta descrição + créditos + hashtags (separados por linha em branco) e corta só a descrição em 5.000.
  const descriptionLength =
    description.length +
    (creditsText ? 2 + creditsText.length : 0) +
    (tagsForDescription.length ? 2 + tagsForDescription.join(" ").length : 0);
  const counterExtra =
    creditsText && tagsForDescription.length
      ? "com os créditos e as hashtags"
      : creditsText
        ? "com os créditos"
        : tagsForDescription.length
          ? "com as hashtags"
          : undefined;
  const generateDisabled = Boolean(generateBlock) || locked;
  // O custo fica junto do botão (no cabeçalho), não abaixo da divisória.
  const generateNote =
    generateBlock ?? "Gerar com IA usa a IA de texto (Claude) e gasta cota.";

  return (
    <Section
      icon={FileText}
      title="Título e descrição"
      description={
        <>
          <div>
            {doc.languages.length > 1
              ? `Versão em ${langLabel(lang)}`
              : "Como o vídeo aparece no YouTube e nas redes."}
          </div>
          <div className="mt-0.5 text-xs text-subtle">{generateNote}</div>
        </>
      }
      actions={
        <Button
          variant={
            emphasis && !meta && !dirty && !generateDisabled
              ? "primary"
              : "secondary"
          }
          icon={Sparkles}
          busy={generating}
          disabled={generateDisabled}
          title={generateBlock ?? undefined}
          onClick={onGenerate}
        >
          {meta ? "Gerar de novo" : "Gerar com IA"}
        </Button>
      }
    >
      {metadataJob ? (
        <JobProgress job={metadataJob} title="Gerando título e descrição" />
      ) : null}
      <ErrorBox
        error={generateError}
        title="Não foi possível gerar"
        onClose={onCloseGenerateError}
      />
      <ErrorBox
        error={save.error}
        title="Não foi possível salvar"
        onClose={() => save.reset()}
      />
      {changedUnder ? (
        <Notice
          tone="warn"
          title="O texto salvo mudou depois que você começou a editar"
          actions={
            <Button
              size="sm"
              variant="ghost"
              onClick={() => metaDrafts.set(projectId, lang, null)}
            >
              Usar o texto salvo
            </Button>
          }
        >
          Salvar mantém as suas edições e substitui o texto salvo.
        </Notice>
      ) : null}

      <div className="flex flex-col gap-1">
        <Field label="Título">
          <Input
            id={titleId}
            value={title}
            disabled={locked}
            readOnly={packageLocked}
            invalid={titleTooLong}
            placeholder="Escreva ou gere com IA"
            aria-describedby={`${ids}-titulo${title.trim() ? "" : ` ${ids}-titulo-vazio`}`}
            onChange={(e) => {
              if (!packageLocked) update("title", e.target.value);
            }}
          />
        </Field>
        {packageLocked ? (
          <p className="m-0 text-xs text-subtle">
            Título travado no pacote (o roteiro foi escrito para ele).{" "}
            {onEditPackage ? (
              <button
                type="button"
                className="text-accent underline-offset-2 hover:underline"
                onClick={onEditPackage}
              >
                Editar no pacote
              </button>
            ) : (
              "Para trocar, destrave na etapa “Título e thumb”."
            )}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <Counter id={`${ids}-titulo`} value={title.length} max={TITLE_MAX} />
            {!title.trim() ? (
              <span id={`${ids}-titulo-vazio`} className="text-subtle">
                · Vazio: o envio usa o título do projeto.
              </span>
            ) : null}
          </div>
          {!title.trim() && doc.title ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={locked || packageLocked}
              onClick={() => update("title", doc.title.slice(0, TITLE_MAX))}
            >
              Usar o título do projeto
            </Button>
          ) : (
            <CopyButton text={title} label="Copiar título" />
          )}
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <Field
          label="Descrição"
          hint={
            hasChapterSource
              ? undefined
              : "Gere a prévia antes para os capítulos entrarem na descrição."
          }
        >
          <Textarea
            rows={6}
            value={description}
            disabled={locked}
            invalid={descriptionLength > DESCRIPTION_MAX}
            aria-describedby={`${ids}-descricao`}
            onChange={(e) => update("description", e.target.value)}
          />
        </Field>
        <div className="flex items-center justify-between gap-2 text-xs">
          <Counter
            id={`${ids}-descricao`}
            value={descriptionLength}
            max={DESCRIPTION_MAX}
            extra={counterExtra}
          />
          <CopyButton text={description} label="Copiar descrição" />
        </div>
      </div>

      {creditsText ? (
        <div
          role="group"
          aria-labelledby={`${ids}-creditos`}
          className="flex flex-col gap-1.5"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span
              id={`${ids}-creditos`}
              className="text-sm font-medium text-fg"
            >
              Créditos das imagens (vão no fim da descrição ao publicar)
            </span>
            <CopyButton
              text={creditsText}
              label="Copiar créditos"
              ariaLabel="Copiar os créditos das imagens"
            />
          </div>
          <pre
            className="m-0 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-canvas px-2.5 py-2 font-sans text-[13px] leading-relaxed text-muted"
          >
            {creditsText}
          </pre>
          {credits.data?.editorial ? (
            <p className="flex items-start gap-1.5 text-[13px] text-amber-200">
              <TriangleAlert
                size={14}
                aria-hidden="true"
                className="mt-0.5 shrink-0"
              />
              {EDITORIAL_NOTICE}
            </p>
          ) : null}
        </div>
      ) : null}

      <Field label="Tags" hint="Separe por vírgula.">
        <Input
          value={tags}
          disabled={locked}
          onChange={(e) => update("tags", e.target.value)}
        />
      </Field>
      <Field
        label="Hashtags"
        hint={`Separe por espaço; o # é colocado se faltar. No YouTube, até ${HASHTAGS_IN_DESCRIPTION} hashtags vão para o fim da descrição.`}
      >
        <Input
          value={hashtags}
          disabled={locked}
          onChange={(e) => update("hashtags", e.target.value)}
        />
      </Field>

      {doc.thumbnail.text[lang] ? (
        <div className="text-[13px] text-subtle">
          Texto da capa:{" "}
          <span className="text-muted">{doc.thumbnail.text[lang]}</span>
        </div>
      ) : null}

      {dirty ? (
        <div className="flex flex-col gap-1 border-t border-line pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              icon={Save}
              busy={save.isPending}
              disabled={Boolean(metadataJob) || !title.trim()}
              onClick={() => save.mutate({ ...values, lang })}
            >
              Salvar alterações
            </Button>
            <Button
              variant="ghost"
              disabled={save.isPending}
              onClick={() => {
                metaDrafts.set(projectId, lang, null);
                document.getElementById(titleId)?.focus();
              }}
            >
              Desfazer
            </Button>
          </div>
          <p className="text-xs text-subtle">
            {!title.trim()
              ? "Dê um título para salvar."
              : metadataJob
                ? "Aguarde a geração terminar para salvar."
                : "As edições ficam guardadas ao trocar de idioma ou de aba, mas só valem para o envio depois de salvas."}
          </p>
        </div>
      ) : null}
    </Section>
  );
};
