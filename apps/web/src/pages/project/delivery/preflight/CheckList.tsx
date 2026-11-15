// Lista de itens de conferência agrupada por nível (problemas, avisos, tudo certo).
import type React from "react";
import type { CheckItem, CheckLevel } from "../../../../apiChecks";
import { LEVEL_ICON, LEVEL_ORDER } from "./state";

const LEVEL_HEADINGS: Record<CheckLevel, string> = {
  erro: "Problemas",
  aviso: "Avisos",
  ok: "Tudo certo",
};

const LEVEL_TEXT: Record<CheckLevel, string> = {
  erro: "text-red-300",
  aviso: "text-amber-200",
  ok: "text-emerald-300",
};

export const clock = (seconds: number) => {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

export const sortItems = (items: CheckItem[]) =>
  [...items].sort((a, b) => LEVEL_ORDER[a.level] - LEVEL_ORDER[b.level]);

export const CheckList: React.FC<{ items: CheckItem[]; levels?: CheckLevel[] }> = ({
  items,
  levels = ["erro", "aviso", "ok"],
}) => (
  <div className="flex flex-col gap-3">
    {levels.map((level) => {
      const group = items.filter((i) => i.level === level);
      if (!group.length) return null;
      const Icon = LEVEL_ICON[level];
      return (
        <div key={level} className="flex flex-col gap-1.5">
          <h4 className={`text-xs font-semibold uppercase tracking-wide ${LEVEL_TEXT[level]}`}>
            {LEVEL_HEADINGS[level]} ({group.length})
          </h4>
          <ul className="flex flex-col gap-1.5">
            {group.map((it, i) => (
              <li key={`${it.code}-${i}`} className="flex items-start gap-2 text-[13px]">
                <Icon size={15} strokeWidth={2} aria-hidden="true" className={`mt-0.5 shrink-0 ${LEVEL_TEXT[level]}`} />
                <div className="min-w-0">
                  <span className="text-fg">{it.title}</span>
                  {it.at_s != null ? (
                    <span className="ml-1.5 tabular-nums text-subtle">em {clock(it.at_s)}</span>
                  ) : null}
                  {it.detail ? <div className="text-muted">{it.detail}</div> : null}
                </div>
              </li>
            ))}
          </ul>
        </div>
      );
    })}
  </div>
);
