/* Painel "Lições usadas" da aba Roteiro: quais lições aplicadas entraram no pedido do roteiro e dos metadados.
   Fica gravado no projeto (settings.lessons_used) para conferir depois, em Desempenho, se a lição ajudou. */
import { BookOpen } from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';
import type { ProjectDetail } from '../../../api';
import { Badge, formatRelative, Section } from '../../../ui';

type LessonUsed = { id: string; scope: string; text: string };
type LessonsUsage = { ids: string[]; items: LessonUsed[]; key: string; version: number | null; at: string };
type LessonsUsed = { script?: LessonsUsage; metadata?: LessonsUsage };

const scopeLabel = (scope: string, channelName?: string) => {
  if (scope === 'global') return 'Todos os canais';
  if (scope === 'formato:short') return 'Shorts';
  if (scope === 'formato:long') return 'Vídeos longos';
  if (scope.startsWith('canal:')) return channelName ? `Canal ${channelName}` : 'Este canal';
  if (scope.startsWith('estilo:')) return `Estilo ${scope.slice(7)}`;
  return scope;
};

const LessonList: React.FC<{ items: LessonUsed[]; channelName?: string; badge?: (item: LessonUsed) => React.ReactNode }> = ({
  items,
  channelName,
  badge,
}) => (
  <ul className="flex flex-col gap-2 text-[13px]">
    {items.map((item) => (
      <li key={item.id} className="flex flex-col gap-1">
        <span className="text-fg">{item.text}</span>
        <span className="flex flex-wrap gap-1">
          <Badge>{scopeLabel(item.scope, channelName)}</Badge>
          {badge?.(item)}
        </span>
      </li>
    ))}
  </ul>
);

const NONE_TEXT = 'Nenhuma lição aplicada valia para este projeto (todos os canais, o canal, o estilo ou o formato). Aplique lições em Desempenho.';

export const LessonsUsedPanel: React.FC<{ detail: ProjectDetail; channelName?: string }> = ({ detail, channelName }) => {
  const used = detail.project.settings.lessons_used as LessonsUsed | undefined;
  const script = used?.script;
  const metadata = used?.metadata;
  if (!script && !metadata) return null;
  const actions = (
    <Link to="/desempenho#licoes-aplicadas" className="text-[13px] text-accent hover:underline">
      Ver lições aplicadas
    </Link>
  );
  if (!script && metadata) {
    // Roteiro feito antes de registrar as lições (ou Short derivado, sem LLM): só os metadados têm registro.
    return (
      <Section
        title="Lições usadas"
        icon={BookOpen}
        actions={actions}
        description={`O roteiro foi gerado antes das lições serem registradas. Nos metadados${metadata.version ? ` (versão ${metadata.version})` : ''}, ${formatRelative(metadata.at)}, entraram estas:`}
      >
        {metadata.items.length ? (
          <LessonList items={metadata.items} channelName={channelName} badge={() => <Badge tone="info">Só nos metadados</Badge>} />
        ) : (
          <p className="text-[13px] text-subtle">{NONE_TEXT}</p>
        )}
      </Section>
    );
  }
  if (!script) return null;
  const metaIds = metadata?.ids ?? [];
  const scriptIds = script.ids;
  const metaOnly = (metadata?.items ?? []).filter((item) => !scriptIds.includes(item.id));
  return (
    <Section
      title="Lições usadas"
      icon={BookOpen}
      actions={actions}
      description={`Lições aplicadas que entraram no pedido do roteiro${script.version ? ` (versão ${script.version})` : ''}, ${formatRelative(script.at)}.`}
    >
      {script.items.length ? (
        <LessonList
          items={script.items}
          channelName={channelName}
          badge={(item) => (metaIds.includes(item.id) ? <Badge tone="info">Também nos metadados</Badge> : null)}
        />
      ) : (
        <p className="text-[13px] text-subtle">{NONE_TEXT}</p>
      )}
      {metaOnly.length ? (
        <div className="mt-3">
          <LessonList items={metaOnly} channelName={channelName} badge={() => <Badge tone="info">Só nos metadados</Badge>} />
        </div>
      ) : null}
    </Section>
  );
};
