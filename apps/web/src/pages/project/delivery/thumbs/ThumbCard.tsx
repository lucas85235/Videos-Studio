// Uma das miniaturas do teste A/B: prévia, tamanho, MB, erros e avisos, "Usar esta" e a edição (cena, texto, modelo).
import { Check, Pencil, TriangleAlert, X } from 'lucide-react';
import React, { useEffect, useId, useState } from 'react';
import { fileUrl } from '../../../../api';
import { THUMB_TEMPLATE_LABELS, type ThumbSpec, type ThumbVariant } from '../../../../apiVisual';
import { Badge, Button, Input, Select } from '../../../../ui';

export type SceneOption = { id: string; label: string };

type Props = {
  n: number;
  label: string;
  spec: ThumbSpec;
  variant: ThumbVariant | null;
  chosen: boolean;
  /** A escolhida vai mesmo no envio (sem erro e atualizada); senão vai a capa do vídeo final. */
  goesOut: boolean;
  /** Data da geração: evita que o navegador mostre a imagem antiga depois de "Gerar de novo". */
  version: string;
  isLong: boolean;
  scenes: SceneOption[];
  templates: string[];
  disabled: boolean;
  choosing: boolean;
  saving: boolean;
  onChoose: () => void;
  onSave: (spec: ThumbSpec) => void;
};

export const ThumbCard: React.FC<Props> = ({ n, label, spec, variant, chosen, goesOut, version, isLong, scenes, templates, disabled, choosing, saving, onChoose, onSave }) => {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<ThumbSpec>(spec);
  const id = useId();
  useEffect(() => {
    if (!editing) setDraft(spec);
  }, [spec, editing]);

  const sceneLabel = scenes.find((s) => s.id === spec.scene_id)?.label ?? 'Sem imagem';
  const errors = variant?.errors ?? [];
  const warnings = variant?.warnings ?? [];

  return (
    <li className={`flex min-w-0 flex-col gap-2 rounded-lg border p-2.5 ${chosen ? 'border-accent bg-accent/5' : 'border-line bg-canvas'}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[13px] font-medium text-fg">
          {n}. {label}
        </span>
        {chosen && goesOut ? (
          <Badge tone="accent" icon={Check}>
            Vai no envio
          </Badge>
        ) : chosen ? (
          <Badge tone="warn" icon={TriangleAlert}>
            Escolhida, mas vai a capa do vídeo final
          </Badge>
        ) : null}
      </div>
      {variant ? (
        <a
          href={`${fileUrl(variant.path)}?v=${encodeURIComponent(version)}`}
          target="_blank"
          rel="noreferrer"
          title="Abrir no tamanho real"
          className="block overflow-hidden rounded-md border border-line"
        >
          <img
            src={`${fileUrl(variant.preview)}?v=${encodeURIComponent(version)}`}
            alt={`Miniatura ${n}: ${variant.text || 'sem texto'}`}
            className={isLong ? 'aspect-video w-full object-cover' : 'mx-auto aspect-[9/16] max-h-80 object-contain'}
          />
        </a>
      ) : (
        <div className={`flex ${isLong ? 'aspect-video' : 'aspect-[9/16] max-h-80'} items-center justify-center rounded-md border border-dashed border-line text-xs text-subtle`}>Ainda não gerada</div>
      )}
      {variant ? (
        <p className="text-xs text-muted">
          {variant.size} · {variant.mb.toFixed(2)} MB
        </p>
      ) : null}
      {errors.length ? (
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-xs text-red-300">
          {errors.map((e) => (
            <li key={e} className="flex items-start gap-1">
              <TriangleAlert size={12} aria-hidden="true" className="mt-0.5 shrink-0" />
              {e}
            </li>
          ))}
        </ul>
      ) : null}
      {warnings.length ? (
        <ul className="m-0 flex list-none flex-col gap-0.5 p-0 text-xs text-amber-200">
          {warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}

      {editing ? (
        <form
          className="flex flex-col gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            onSave(draft);
            setEditing(false);
          }}
        >
          <label htmlFor={`${id}-cena`} className="text-xs font-medium text-subtle">
            Cena de fundo
          </label>
          <Select id={`${id}-cena`} value={draft.scene_id ?? ''} onChange={(e) => setDraft({ ...draft, scene_id: e.target.value || null })} className="text-[13px]">
            <option value="">Sem imagem</option>
            {scenes.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </Select>
          <label htmlFor={`${id}-texto`} className="text-xs font-medium text-subtle">
            Texto
          </label>
          <Input id={`${id}-texto`} value={draft.text} maxLength={120} onChange={(e) => setDraft({ ...draft, text: e.target.value })} className="text-[13px]" />
          <label htmlFor={`${id}-modelo`} className="text-xs font-medium text-subtle">
            Modelo
          </label>
          <Select id={`${id}-modelo`} value={draft.template} onChange={(e) => setDraft({ ...draft, template: e.target.value })} className="text-[13px]">
            {templates.map((t) => (
              <option key={t} value={t}>
                {THUMB_TEMPLATE_LABELS[t] ?? t}
              </option>
            ))}
          </Select>
          <div className="flex gap-1.5">
            <Button size="sm" type="submit" busy={saving} disabled={disabled}>
              Salvar
            </Button>
            <Button size="sm" variant="ghost" icon={X} onClick={() => setEditing(false)}>
              Cancelar
            </Button>
          </div>
        </form>
      ) : (
        <p className="text-xs text-subtle">
          {sceneLabel} · “{spec.text || 'sem texto'}” · {THUMB_TEMPLATE_LABELS[spec.template] ?? spec.template}
        </p>
      )}

      <div className="mt-auto flex flex-wrap gap-1.5">
        {variant && !chosen ? (
          <Button size="sm" disabled={disabled} busy={choosing} onClick={onChoose}>
            Usar esta
          </Button>
        ) : null}
        {!editing ? (
          <Button size="sm" variant="ghost" icon={Pencil} disabled={disabled} onClick={() => setEditing(true)}>
            Editar
          </Button>
        ) : null}
      </div>
    </li>
  );
};
