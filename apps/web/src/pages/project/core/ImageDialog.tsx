import { X } from 'lucide-react';
import React, { useEffect, useId, useRef } from 'react';
import { IconButton } from '../../../ui';

/** Imagem ampliada num <dialog> modal (Esc ou clique fora fecham; o foco volta para quem abriu). */
export const ImageDialog: React.FC<{ src: string; title: string; caption?: string; onClose: () => void }> = ({ src, title, caption, onClose }) => {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  // Quem estava com o foco ao abrir (capturado no primeiro render, antes de o diálogo roubar o foco).
  const opener = useRef<HTMLElement | null>(document.activeElement instanceof HTMLElement ? document.activeElement : null);
  useEffect(() => {
    const el = ref.current;
    if (el && !el.open) el.showModal();
    const back = opener.current;
    return () => {
      // Sem close() aqui (no StrictMode cancelaria o diálogo recém-aberto): só devolve o foco a quem abriu.
      window.setTimeout(() => {
        if (back?.isConnected) back.focus();
      }, 0);
    };
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClose={onClose}
      onMouseDown={(e) => {
        // Só o fundo escurecido (fora da caixa) fecha; o espaço interno (padding) do próprio <dialog> não.
        if (e.target !== e.currentTarget) return;
        const r = e.currentTarget.getBoundingClientRect();
        if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) onClose();
      }}
      className="m-auto max-h-[95vh] max-w-[95vw] animate-fade-in rounded-xl border border-line-strong bg-card p-3 text-fg shadow-2xl shadow-black/60 backdrop:bg-black/75 backdrop:backdrop-blur-sm"
    >
      <div className="mb-2 flex items-center justify-between gap-3">
        <h2 id={titleId} className="font-display text-[15px] font-semibold">
          {title}
        </h2>
        <IconButton label="Fechar" icon={X} size="sm" onClick={onClose} />
      </div>
      <img src={src} alt={title} className="max-h-[80vh] max-w-full rounded-lg object-contain" />
      {caption ? <p className="mt-2 max-w-prose text-[13px] text-muted">{caption}</p> : null}
    </dialog>
  );
};
