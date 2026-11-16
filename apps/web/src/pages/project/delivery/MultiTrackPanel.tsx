import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AudioLines, Download, FileText, FolderOpen, Languages, PackageOpen } from "lucide-react";
import React from "react";
import {
  api,
  fileUrl,
  type LanguageDelivery,
  type ProjectDetail,
} from "../../../api";
import { langLabel } from "../../../labels";
import {
  Badge,
  Button,
  buttonClass,
  ErrorBox,
  Section,
  SegmentedControl,
  useConfirm,
  useToast,
} from "../../../ui";
import { findActiveJob, Notice } from "./shared";

const MULTI: LanguageDelivery = "multi_audio_track";

const DELIVERY_OPTIONS: { id: LanguageDelivery; label: string; title: string }[] = [
  {
    id: "separate_videos",
    label: "Um vídeo por idioma",
    title: "Cada idioma vira um vídeo próprio (serve para todas as redes).",
  },
  {
    id: MULTI,
    label: "Faixas no mesmo vídeo (YouTube)",
    title: "Um vídeo só, com uma faixa de áudio por idioma.",
  },
];

/** Aceleração em texto ("+6%"); 1 vira "sem ajuste". */
const tempoText = (tempo: number) =>
  tempo > 1.0005 ? `acelerada até +${Math.round((tempo - 1) * 100)}%` : "sem ajuste de velocidade";

/**
 * Entrega dos idiomas (README §7.3). No Modo B o render faz um vídeo só, mixa uma faixa por idioma com a mesma
 * duração e monta o pacote para o YouTube Studio (a API não aceita faixas de áudio extras).
 */
export const MultiTrackPanel: React.FC<{ detail: ProjectDetail; busy: boolean }> = ({ detail, busy }) => {
  const { doc } = detail;
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const delivery: LanguageDelivery = detail.style?.languages.delivery ?? "separate_videos";
  const final = detail.outputs.final;
  const finalIsMulti = final?.delivery === MULTI;
  const renderJob = findActiveJob(detail, ["render", "autopilot"]);

  const save = useMutation({
    mutationFn: (value: LanguageDelivery) =>
      api.put<{ version: number }>(`/api/projects/${doc.id}/delivery`, { delivery: value }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["project", doc.id] }),
  });
  const pack = useMutation({
    mutationFn: () => api.post<{ folder: string }>(`/api/projects/${doc.id}/publish/dub-package`),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ["project", doc.id] }).then(() =>
        toast({ tone: "good", title: "Pacote de faixas pronto", description: "Siga o checklist da pasta." }),
      ),
  });
  const open = useMutation({
    mutationFn: () => api.post(`/api/projects/${doc.id}/publish/dub-package/open`),
  });

  if (doc.languages.length < 2) return null;

  const change = async (value: LanguageDelivery) => {
    if (value === delivery) return;
    const ok = await confirm({
      title: value === MULTI ? "Entregar os idiomas como faixas no mesmo vídeo?" : "Voltar a um vídeo por idioma?",
      description:
        value === MULTI
          ? "O próximo render faz um vídeo só (na imagem do idioma principal), sem legenda queimada, e uma faixa de áudio por idioma com a mesma duração. As faixas vão à mão no YouTube Studio."
          : "O próximo render volta a fazer um vídeo completo por idioma, com legendas queimadas.",
      confirmLabel: "Trocar",
    });
    if (ok) save.mutate(value);
  };

  const pkg = final?.dub_package;
  const summary = final?.dub;
  const others = doc.languages.filter((l) => l !== doc.primary_language);

  return (
    <Section
      icon={Languages}
      title="Idiomas no YouTube"
      description="Um vídeo por idioma, ou um vídeo só com uma faixa de áudio por idioma (soma as views num vídeo)."
    >
      <SegmentedControl
        label="Entrega dos idiomas"
        options={DELIVERY_OPTIONS}
        value={delivery}
        onChange={change}
        disabled={busy || save.isPending}
      />
      <ErrorBox error={save.error} title="Não foi possível trocar a entrega" onClose={() => save.reset()} />

      {delivery === MULTI ? (
        <ul className="list-disc space-y-1 pl-5 text-[13px] text-muted">
          <li>
            O canal precisa de acesso aos <span className="text-fg">recursos avançados</span> do YouTube.
          </li>
          <li>
            A tradução recebe o tempo de cada trecho; a narração que passa dele é acelerada até +10% (sem mudar o
            tom). Passou disso, o trecho ganha uma pausa no vídeo e o app avisa.
          </li>
          <li>
            Legendas não são queimadas: vão como legenda de cada idioma. Textos na tela ficam em{" "}
            {langLabel(doc.primary_language)}.
          </li>
          <li>Publicando pelo app, títulos, descrições e legendas de todos os idiomas vão juntos com o vídeo.</li>
        </ul>
      ) : null}

      {delivery === MULTI && final && !finalIsMulti && !renderJob ? (
        <Notice tone="info" title="Renderize de novo para gerar as faixas">
          O último vídeo final foi feito com um vídeo por idioma.
        </Notice>
      ) : null}
      {delivery !== MULTI && finalIsMulti && !renderJob ? (
        <Notice tone="info" title="O último render ainda é de faixas no mesmo vídeo">
          Renderize de novo para ter um vídeo completo por idioma.
        </Notice>
      ) : null}

      {finalIsMulti ? (
        <div className="flex flex-col gap-3">
          {summary && summary.added_pause_s > 0.25 ? (
            <Notice tone="warn" title={`O vídeo ganhou ${summary.added_pause_s.toFixed(1)} s de pausas`}>
              Alguma tradução não coube no tempo nem acelerada. Os trechos estão nos avisos da renderização; encurte
              a tradução e renderize de novo.
            </Notice>
          ) : null}
          <ul className="flex flex-col gap-2">
            {others.map((l) => {
              const entry = final?.langs?.[l];
              const info = summary?.langs[l];
              return (
                <li
                  key={l}
                  className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-canvas p-3 text-sm"
                >
                  <AudioLines size={15} aria-hidden="true" className="text-muted" />
                  <span className="font-medium text-fg">{langLabel(l)}</span>
                  {entry ? (
                    <>
                      <Badge tone={info && info.longest_in > 0 ? "warn" : "neutral"}>
                        {tempoText(entry.max_tempo ?? 1)}
                      </Badge>
                      {info && info.stretched > 0 ? (
                        <span className="text-[13px] text-subtle">
                          {info.stretched} de {info.segments} trechos ajustados
                        </span>
                      ) : null}
                      <span className="ml-auto flex flex-wrap gap-1">
                        <a className={buttonClass({ variant: "ghost", size: "sm" })} href={fileUrl(entry.mix)} download>
                          <Download size={14} aria-hidden="true" />
                          Faixa (.wav)
                        </a>
                        <a className={buttonClass({ variant: "ghost", size: "sm" })} href={fileUrl(entry.srt)} download>
                          <Download size={14} aria-hidden="true" />
                          Legendas (.srt)
                        </a>
                      </span>
                    </>
                  ) : (
                    <Badge tone="warn">sem faixa: renderize de novo</Badge>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              icon={PackageOpen}
              busy={pack.isPending}
              disabled={Boolean(renderJob)}
              onClick={() => pack.mutate()}
              title="Refaz a pasta com os títulos atuais e o link do vídeo, se já foi enviado"
            >
              {pkg ? "Atualizar pacote do Studio" : "Gerar pacote do Studio"}
            </Button>
            {pkg ? (
              <>
                <Button variant="ghost" icon={FolderOpen} busy={open.isPending} onClick={() => open.mutate()}>
                  Abrir pasta
                </Button>
                <a
                  className={buttonClass({ variant: "ghost", size: "md" })}
                  href={fileUrl(pkg.checklist)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <FileText size={14} aria-hidden="true" />
                  Checklist
                </a>
              </>
            ) : null}
          </div>
          <ErrorBox error={pack.error ?? open.error} title="Não foi possível preparar o pacote" onClose={() => { pack.reset(); open.reset(); }} />
          <p className="text-[13px] text-subtle">
            No Studio: Idiomas → escolha o vídeo → Adicionar idioma → Dublagem → envie a faixa do idioma. A API do
            YouTube não aceita faixas de áudio extras, por isso esse passo é manual.
          </p>
        </div>
      ) : null}
    </Section>
  );
};
