import { BadgeDollarSign, ChartLine, FileText, Hand, KeyRound, type LucideIcon, UserCheck } from 'lucide-react';
import React from 'react';
import type { WaitingItem, WaitingKind } from '../../apiOps';
import { Badge, Section } from '../../ui';
import { WhereLink } from './shared';

const KIND: Record<WaitingKind, { icon: LucideIcon; label: string; action: string }> = {
  review: { icon: FileText, label: 'Revisão', action: 'Abrir' },
  post: { icon: Hand, label: 'Postar à mão', action: 'Ver na Agenda' },
  youtube: { icon: KeyRound, label: 'YouTube', action: 'Ir para Canais' },
  analytics: { icon: ChartLine, label: 'Métricas', action: 'Ir para Canais' },
  offer: { icon: BadgeDollarSign, label: 'Afiliado', action: 'Ir para Dinheiro' },
};

/** O que só você pode fazer: revisões, pacotes manuais, tokens do YouTube e ofertas a conferir. */
export const WaitingSection: React.FC<{ items: WaitingItem[] }> = ({ items }) => (
  <Section
    title="Esperando você"
    icon={UserCheck}
    description="Coisas que só você pode fazer. A lista se atualiza sozinha quando você resolve cada uma."
    actions={items.length ? <Badge tone="warn">{items.length}</Badge> : null}
  >
    {items.length === 0 ? (
      <p className="text-sm text-muted">Nada esperando você agora.</p>
    ) : (
      <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
        {items.map((item, i) => {
          const k = KIND[item.kind] ?? KIND.review;
          const Icon = k.icon;
          return (
            <li key={`${item.kind}-${item.project_id ?? item.channel_id ?? ''}-${i}`} className="flex flex-wrap items-center gap-3 px-3 py-2.5">
              <Icon size={16} aria-hidden="true" className="shrink-0 text-muted" />
              <span className="min-w-[12rem] flex-1 text-sm text-fg">{item.text}</span>
              <Badge>{k.label}</Badge>
              <WhereLink where={item.where} label={k.action} />
            </li>
          );
        })}
      </ul>
    )}
  </Section>
);
