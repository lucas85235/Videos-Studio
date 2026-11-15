// No Short cortado de um longo: faixa "Cortado de <longo>" com o link de volta.
import { ArrowLeft, Scissors } from 'lucide-react';
import React from 'react';
import { Link } from 'react-router-dom';
import type { ShortsOfProject } from '../../../../apiShorts';
import { buttonClass } from '../../../../ui';
import { Notice } from '../shared';

export const ParentBanner: React.FC<{ data: ShortsOfProject }> = ({ data }) => {
  const parent = data.parent;
  if (!parent) return null;
  const shortOf = data.short_of;
  return (
    <Notice
      tone="info"
      icon={Scissors}
      title={
        parent.exists ? (
          <>
            Cortado de <span className="font-semibold">{parent.title || 'vídeo longo'}</span>
          </>
        ) : (
          'Cortado de um vídeo longo que foi apagado'
        )
      }
      actions={
        parent.exists ? (
          <Link to={`/projetos/${parent.project_id}`} className={buttonClass({ variant: 'secondary', size: 'sm' })}>
            <ArrowLeft size={14} strokeWidth={2} aria-hidden="true" />
            Voltar ao vídeo longo
          </Link>
        ) : null
      }
    >
      Narração e imagens vêm do vídeo longo: o que já existe é reaproveitado.
      {shortOf?.warnings?.length ? ' Confira os avisos:' : ''}
      {shortOf?.warnings?.length ? (
        <ul className="mt-1 list-disc pl-4">
          {shortOf.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
    </Notice>
  );
};
