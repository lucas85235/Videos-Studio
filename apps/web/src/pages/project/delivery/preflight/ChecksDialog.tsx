// Diálogo do envio público/agendado quando a conferência achou problema: publicar mesmo assim ou enviar como privado.
import { OctagonAlert } from "lucide-react";
import type React from "react";
import { useEffect, useId, useRef } from "react";
import type { CheckItem } from "../../../../apiChecks";
import { Button, buttonClass } from "../../../../ui";
import { CheckList } from "./CheckList";

export type ChecksChoice = "ignore" | "private" | "cancel";

export const ChecksDialog: React.FC<{
  /** Erros da conferência (do estado atual ou montados a partir da resposta 409). */
  items: CheckItem[];
  /** Mensagem do servidor (409), quando veio de lá. */
  message?: string | null;
  /** O servidor já recusou "publicar mesmo assim" (item que não dá para ignorar). */
  locked?: boolean;
  onResult: (choice: ChecksChoice) => void;
}> = ({ items, message, locked, onResult }) => {
  // Voz ou imagens só de prévia: o envio público é recusado mesmo confirmando; sobra o privado.
  const canIgnore = !locked && !items.some((i) => i.blocking);
  const titleId = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el && !el.open) el.showModal();
    cancelRef.current?.focus();
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onResult("cancel");
      }}
      onClose={() => onResult("cancel")}
      className="m-auto w-[calc(100%-2rem)] max-w-lg animate-fade-in rounded-xl border border-line-strong bg-card p-5 text-fg shadow-2xl shadow-black/60 backdrop:bg-black/65 backdrop:backdrop-blur-sm"
    >
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-red-400/10 text-red-300">
          <OctagonAlert size={18} aria-hidden="true" />
        </div>
        <div className="flex min-w-0 flex-col gap-3">
          <h2 id={titleId} className="font-display text-lg font-semibold text-fg">
            A conferência achou problema
          </h2>
          <p className="text-sm text-muted">
            {canIgnore
              ? "Vídeo com problema não deveria ir a público. Corrija, envie como privado para conferir no YouTube ou publique mesmo assim."
              : "Este problema impede o envio público. Corrija e renderize de novo, ou envie como privado para conferir no YouTube."}
          </p>
          {items.length ? (
            <div className="max-h-64 overflow-y-auto rounded-lg border border-line bg-canvas p-3">
              <CheckList items={items} levels={["erro"]} />
            </div>
          ) : message ? (
            <p className="rounded-lg border border-line bg-canvas p-3 text-[13px] text-muted">{message}</p>
          ) : null}
        </div>
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button ref={cancelRef} type="button" className={buttonClass({ variant: "ghost" })} onClick={() => onResult("cancel")}>
          Cancelar
        </button>
        {canIgnore ? (
          <Button variant="secondary" onClick={() => onResult("ignore")}>
            Publicar mesmo assim
          </Button>
        ) : null}
        <Button variant="primary" onClick={() => onResult("private")}>
          Enviar como privado
        </Button>
      </div>
    </dialog>
  );
};
