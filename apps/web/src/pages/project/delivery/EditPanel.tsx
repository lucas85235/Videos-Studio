// Painel "Editar" da prévia: instrução em texto → proposta do agente → aplicar.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, Play, Sparkles, X } from "lucide-react";
import React from "react";
import {
  api,
  type EditProposal,
  type EditResult,
  JOB_LABELS,
  type ProjectDetail,
} from "../../../api";
import {
  Badge,
  Button,
  Checkbox,
  ErrorBox,
  Field,
  formatMoney,
  formatRelative,
  formatSeconds,
  Textarea,
  useToast,
} from "../../../ui";
import { createKeyedStore, useKeyedStore } from "../core/drafts";
import { awaitingScenes, humanizeVersionMessage } from "../core/model";
import type { ProjectTab } from "../types";
import { describeOp } from "./editOps";
import { guardUnload, isActive, trackName } from "./shared";

/** Exemplos clicáveis; os que falam da "cena atual" viram "cena N" com a cena na tela. */
const EXAMPLES = [
  { text: "Deixa a cena atual mais clara", scene: true },
  { text: "Legenda maior", scene: false },
  { text: "Outra imagem na cena atual", scene: true },
  { text: "Música mais baixa", scene: false },
];

/** Id do campo da instrução (para devolver o foco depois de aplicar). */
export const EDIT_INSTRUCTION_ID = "previa-instrucao";

export type CurrentScene = { index: number; id: string; total: number } | null;

/**
 * Rascunho do painel guardado fora do componente (por projeto): sobrevive à troca de painel lateral, de aba
 * do projeto e de idioma. A proposta já gastou uma chamada do Claude, então só sai quando o usuário aplica
 * ou descarta.
 */
type EditDraft = {
  instruction: string;
  onlyCurrent: boolean;
  proposal: EditProposal | null;
};
const EMPTY_DRAFT: EditDraft = {
  instruction: "",
  onlyCurrent: false,
  proposal: null,
};
const editDrafts = createKeyedStore<EditDraft>();
const DRAFT_KEY = "editar";
guardUnload(() => editDrafts.hasAny());

const patchDraft = (projectId: string, patch: Partial<EditDraft>) =>
  editDrafts.update(projectId, (current) => {
    const next = { ...(current[DRAFT_KEY] ?? EMPTY_DRAFT), ...patch };
    if (!next.instruction && !next.onlyCurrent && !next.proposal) {
      if (!(DRAFT_KEY in current)) return current;
      const { [DRAFT_KEY]: _removed, ...rest } = current;
      return rest;
    }
    return { ...current, [DRAFT_KEY]: next };
  });

/** Mensagem de versão sem ids técnicos (voz, caminho da faixa, provedor, código de idioma). */
export const versionLabel = (message: string) => {
  if (/^Voz: /.test(message)) return "Voz trocada";
  const track = /^Trilha fixada: (.+)$/.exec(message);
  if (track) return `Trilha: ${trackName(track[1])}`;
  return humanizeVersionMessage(message);
};

export const EditPanel: React.FC<{
  detail: ProjectDetail;
  lang: string;
  currentScene: CurrentScene;
  /** Cena na tela agora, lida do player na hora (o estado pode estar um quadro atrás). */
  getCurrentScene: () => CurrentScene;
  /** Índice (0..n-1) da cena na prévia, ou -1. */
  sceneIndex: (id: string) => number;
  onSeekScene: (index: number) => void;
  goTo?: (tab: ProjectTab) => void;
}> = ({
  detail,
  lang,
  currentScene,
  getCurrentScene,
  sceneIndex,
  onSeekScene,
  goTo,
}) => {
  const { doc } = detail;
  const projectId = doc.id;
  const client = useQueryClient();
  const { toast } = useToast();
  const bucket = useKeyedStore(editDrafts, projectId);
  const { instruction, onlyCurrent, proposal } =
    bucket[DRAFT_KEY] ?? EMPTY_DRAFT;
  const setInstruction = (value: string) =>
    patchDraft(projectId, { instruction: value });
  const setOnlyCurrent = (value: boolean) =>
    patchDraft(projectId, { onlyCurrent: value });

  const active = detail.jobs.filter(isActive);
  const otherJob = active.find((j) => j.kind !== "preview");
  const proposeBlock = otherJob
    ? `Aguarde: ${JOB_LABELS[otherJob.kind] ?? "uma tarefa"} em andamento.`
    : null;
  const applyBlock = active.length
    ? otherJob
      ? `Aguarde: ${JOB_LABELS[otherJob.kind] ?? "uma tarefa"} em andamento.`
      : "Aguarde a prévia atual terminar para aplicar."
    : null;

  const propose = useMutation({
    mutationFn: (vars: { instruction: string; sceneId: string | null }) =>
      api.post<EditProposal>(`/api/projects/${projectId}/edit/propose`, {
        instruction: vars.instruction,
        scope_scene_ids: vars.sceneId ? [vars.sceneId] : null,
        lang,
      }),
    // Grava no rascunho do projeto: a proposta (paga) não se perde se o painel sair da tela no meio.
    onSuccess: (data) => patchDraft(projectId, { proposal: data }),
  });
  // Cenas esperando a conferência: a edição fica salva, mas a prévia (que gera as imagens) espera a aprovação.
  const scenesPending = awaitingScenes(detail);
  const apply = useMutation({
    mutationFn: (vars: { proposal: EditProposal; rebuild: boolean }) =>
      api.post<EditResult>(`/api/projects/${projectId}/edit/apply`, {
        ops: vars.proposal.ops,
        summary: vars.proposal.summary || instruction,
        rebuild: vars.rebuild,
      }),
    onSuccess: (result) =>
      client
        .invalidateQueries({ queryKey: ["project", projectId] })
        .then(() => {
          patchDraft(projectId, {
            proposal: null,
            instruction: "",
            onlyCurrent: false,
          });
          // O servidor diz por que não atualizou a prévia (cenas por aprovar, estilo trocado).
          toast({
            tone: "good",
            title: "Edição aplicada",
            description: result.job
              ? "A prévia está sendo atualizada com a mudança."
              : (result.notice ?? "A mudança foi salva numa versão nova."),
          });
          // O botão "Aplicar" sumiu com a proposta: o foco volta para a instrução.
          window.requestAnimationFrame(() =>
            document.getElementById(EDIT_INSTRUCTION_ID)?.focus(),
          );
        }),
  });

  const canPropose = Boolean(instruction.trim()) && !proposeBlock;
  const runPropose = () => {
    if (!canPropose || propose.isPending) return;
    // A cena é lida do player na hora do pedido, não do último estado.
    const scene = onlyCurrent ? (getCurrentScene() ?? currentScene) : null;
    propose.mutate({ instruction, sceneId: scene?.id ?? null });
  };
  const exampleText = (ex: (typeof EXAMPLES)[number]) =>
    ex.scene && currentScene
      ? ex.text.replace("cena atual", `cena ${currentScene.index + 1}`)
      : ex.text;
  const pickExample = (ex: (typeof EXAMPLES)[number]) => {
    const text = exampleText(ex);
    // Não apaga o que já foi digitado: acrescenta numa linha nova.
    setInstruction(
      instruction.trim() ? `${instruction.trimEnd()}\n${text}` : text,
    );
  };

  const ctx = { doc, sceneIndex, trackName };
  const est = proposal?.estimate;
  // Troca de estilo em cenas dirigidas: a direção de arte some e as imagens só saem depois de refazer as cenas.
  const lostDirection = (est?.direction_missing?.length ?? 0) > 0;
  const rebuild = !scenesPending && !lostDirection;
  // A narração da prévia sai da voz local: só a voz de API gasta cota.
  const spendsQuota = Boolean(
    rebuild &&
      est &&
      (est.images_missing.length > 0 || (est.tts_chars > 0 && !est.tts_local)),
  );

  return (
    <div className="flex flex-col gap-4">
      <Field
        label="O que você quer mudar?"
        hint={
          <>
            Descreva em português. Para falar de uma cena, use o número que
            aparece sob o player.
          </>
        }
      >
        <Textarea
          id={EDIT_INSTRUCTION_ID}
          rows={3}
          value={instruction}
          placeholder="Ex.: deixa a cena 5 mais clara e quente"
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              runPropose();
            }
          }}
        />
      </Field>

      <div className="flex flex-col gap-1">
        <div
          role="group"
          aria-label="Exemplos de instrução"
          className="flex flex-wrap gap-1.5"
        >
          {EXAMPLES.map((ex) => {
            const needsScene = ex.scene && !currentScene;
            return (
              <button
                key={ex.text}
                type="button"
                disabled={needsScene}
                title={needsScene ? "Dê play para escolher a cena" : undefined}
                onClick={() => pickExample(ex)}
                className="rounded-md border border-line bg-raised px-2 py-1 text-xs text-muted transition-colors hover:border-line-strong hover:text-fg disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-line disabled:hover:text-muted"
              >
                {exampleText(ex)}
              </button>
            );
          })}
        </div>
        {!currentScene ? (
          <p className="text-xs text-subtle">
            Os exemplos com cena ficam disponíveis quando há uma cena na tela:
            dê play.
          </p>
        ) : null}
      </div>

      <Checkbox
        checked={onlyCurrent && Boolean(currentScene)}
        disabled={!currentScene}
        onChange={setOnlyCurrent}
        label={
          currentScene
            ? `Só a cena ${currentScene.index + 1} (na tela agora)`
            : "Só a cena que está na tela"
        }
        description={
          currentScene
            ? undefined
            : "Dê play ou clique na linha do tempo do player para escolher a cena."
        }
      />

      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant={canPropose && !proposal ? "primary" : "secondary"}
            icon={Sparkles}
            busy={propose.isPending}
            disabled={!canPropose}
            onClick={runPropose}
          >
            Propor mudança
          </Button>
          <span className="text-xs text-subtle">
            ou{" "}
            <kbd className="rounded border border-line-strong bg-raised px-1 font-sans text-xs text-muted">
              Ctrl
            </kbd>
            +
            <kbd className="rounded border border-line-strong bg-raised px-1 font-sans text-xs text-muted">
              Enter
            </kbd>
          </span>
        </div>
        <p className="text-xs text-subtle">
          {proposeBlock ??
            "Pede a proposta à IA de texto (Claude): gasta cota do seu plano. Nada muda antes de você aplicar."}
        </p>
      </div>

      <ErrorBox
        error={propose.error ?? apply.error}
        onClose={() => {
          propose.reset();
          apply.reset();
        }}
      />

      {proposal ? (
        <section
          aria-label="Proposta do agente"
          className="flex flex-col gap-3 rounded-lg border border-line-strong bg-raised p-3 text-sm"
        >
          <div className="font-medium text-fg">
            {proposal.summary || "O agente não propôs nenhuma mudança."}
          </div>
          {proposal.ops.length ? (
            <ol className="flex flex-col gap-2">
              {proposal.ops.map((op, i) => {
                const d = describeOp(op, ctx);
                return (
                  <li
                    key={i}
                    className="flex flex-col gap-1 rounded-md border border-line bg-card p-2 text-[13px]"
                  >
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge tone="info">{d.label}</Badge>
                      {d.scene ? (
                        <button
                          type="button"
                          onClick={() => onSeekScene(d.scene!.index)}
                          className="inline-flex items-center gap-1 rounded-md border border-line-strong px-1.5 py-0.5 text-xs font-medium text-fg hover:bg-canvas"
                          aria-label={`Mostrar a cena ${d.scene.index + 1} no player`}
                          title="Mostrar esta cena no player"
                        >
                          <Play size={12} aria-hidden="true" />
                          Cena {d.scene.index + 1}
                        </button>
                      ) : null}
                      {d.target ? (
                        <span className="text-muted">{d.target}</span>
                      ) : null}
                    </div>
                    {d.quote ? (
                      <blockquote className="line-clamp-2 border-l-2 border-line-strong pl-2 text-muted">
                        “{d.quote}”
                      </blockquote>
                    ) : null}
                    {d.change ? (
                      <div className="flex items-center gap-1 text-fg">
                        <ArrowRight
                          size={12}
                          aria-hidden="true"
                          className="shrink-0 text-subtle"
                        />
                        {d.change}
                      </div>
                    ) : null}
                    {d.reason ? (
                      <div className="text-xs text-subtle">{d.reason}</div>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          ) : null}

          {est ? (
            <div className="flex flex-wrap gap-1.5" aria-label="Estimativa">
              {lostDirection ? (
                <Badge tone="warn">estilo novo: refaça as cenas</Badge>
              ) : null}
              {est.images_missing.length ? (
                <Badge>
                  {est.images_missing.length}{" "}
                  {est.images_missing.length === 1
                    ? "imagem nova"
                    : "imagens novas"}
                </Badge>
              ) : null}
              {est.tts_chars ? (
                <Badge>
                  {est.tts_chars.toLocaleString("pt-BR")} caracteres de narração
                  {est.tts_local ? " (voz local)" : ""}
                </Badge>
              ) : null}
              {est.estimated_seconds ? (
                <Badge>
                  ~{formatSeconds(est.estimated_seconds)} para atualizar
                </Badge>
              ) : null}
              <Badge tone={est.estimated_cost_usd > 0 ? "warn" : "good"}>
                {est.estimated_cost_usd > 0
                  ? `custo estimado ${formatMoney(est.estimated_cost_usd)}`
                  : "sem custo"}
              </Badge>
            </div>
          ) : null}

          <details className="text-xs text-subtle">
            <summary className="w-fit cursor-pointer rounded-sm hover:text-fg">
              Detalhes técnicos
            </summary>
            <div className="mt-1">
              Proposta feita por: {proposal.provider} · versão base v
              {proposal.base_version}
            </div>
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-canvas p-2 font-mono text-xs text-muted">
              {JSON.stringify(proposal.ops, null, 2)}
            </pre>
          </details>

          <div className="flex flex-col gap-1.5">
            <div className="flex flex-wrap gap-2">
              <Button
                variant="primary"
                icon={Check}
                disabled={!proposal.ops.length || Boolean(applyBlock)}
                busy={apply.isPending}
                onClick={() => apply.mutate({ proposal, rebuild })}
              >
                {rebuild ? "Aplicar e atualizar prévia" : "Aplicar"}
              </Button>
              <Button
                variant="ghost"
                icon={X}
                disabled={apply.isPending}
                onClick={() => {
                  patchDraft(projectId, { proposal: null });
                  document.getElementById(EDIT_INSTRUCTION_ID)?.focus();
                }}
              >
                Descartar
              </Button>
            </div>
            <p className="text-xs text-subtle">
              {applyBlock ??
                (lostDirection
                  ? "Aplicar troca o estilo e não gera imagens. Depois, refaça as cenas na aba Cenas: as imagens saem com a direção de arte nova."
                  : scenesPending
                    ? "Aplicar salva a mudança numa versão nova. As imagens e a prévia só são feitas quando você aprovar as cenas (aba Cenas)."
                    : spendsQuota
                      ? est?.tts_local || !est?.tts_chars
                        ? "Aplicar gera as imagens que faltam (gasta cota do provedor de imagem) e atualiza a prévia."
                        : "Aplicar gera a narração e as imagens que faltam: gasta cota dos provedores de voz e imagem."
                      : "Aplicar cria uma versão nova e atualiza a prévia.")}
            </p>
          </div>
        </section>
      ) : null}

      {detail.versions.length ? (
        <div className="flex flex-col gap-1.5 border-t border-line pt-3">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-[13px] font-medium text-muted">
              Últimas versões
            </h3>
            {goTo ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => goTo("versions")}
              >
                Ver todas
              </Button>
            ) : null}
          </div>
          <ul className="flex flex-col gap-1 text-[13px]">
            {detail.versions.slice(0, 5).map((v) => (
              <li key={v.number} className="flex min-w-0 items-baseline gap-2">
                <span className="shrink-0 tabular-nums text-subtle">
                  v{v.number}
                </span>
                <span
                  className="min-w-0 flex-1 truncate text-muted"
                  title={versionLabel(v.message)}
                >
                  {versionLabel(v.message)}
                </span>
                {v.created_at ? (
                  <span className="shrink-0 text-xs text-subtle">
                    {formatRelative(v.created_at)}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
};
