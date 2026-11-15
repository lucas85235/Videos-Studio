// Shorts já cortados deste longo, com o link para cada projeto.
import { ExternalLink, TriangleAlert } from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';
import { STATUS_LABELS } from '../../../../api';
import type { ShortChild } from '../../../../apiShorts';
import { Badge, buttonClass } from '../../../../ui';
import { fmtSeconds } from './model';

export const ChildrenList: React.FC<{ items: ShortChild[] }> = ({ items }) => {
  if (!items.length) return null;
  return (
    <div className="flex flex-col gap-2">
      <div className="text-sm font-medium text-fg">Shorts criados ({items.length})</div>
      <ul className="flex flex-col gap-1.5">
        {items.map((c) => (
          <li key={c.project_id} className="flex flex-wrap items-center gap-3 rounded-lg border border-line px-3 py-2">
            <div className="min-w-0 flex-1">
              <Link to={`/projetos/${c.project_id}`} className="text-sm font-medium text-fg hover:underline">
                {c.title}
              </Link>
              <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
                {c.duration_s != null ? <span className="font-mono">{fmtSeconds(c.duration_s)}</span> : null}
                <span>· {STATUS_LABELS[c.status] ?? c.status}</span>
                {c.tipo !== 'normal' ? <Badge tone="info">{c.tipo}</Badge> : null}
                {c.warnings.length ? (
                  <Badge tone="warn" icon={TriangleAlert} title={c.warnings.join('\n')}>
                    {c.warnings.length} {c.warnings.length === 1 ? 'aviso' : 'avisos'}
                  </Badge>
                ) : null}
              </div>
            </div>
            <Link
              to={`/projetos/${c.project_id}`}
              aria-label={`Abrir o Short ${c.title}`}
              className={buttonClass({ variant: 'ghost', size: 'sm' })}
            >
              <ExternalLink size={14} strokeWidth={2} aria-hidden="true" />
              Abrir
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
};
