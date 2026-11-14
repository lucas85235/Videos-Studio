import { useMutation, useQueryClient } from '@tanstack/react-query';
import React from 'react';
import { api, type ProjectDetail } from '../../api';
import { Badge, Button, Card, ErrorBox } from '../../ui';

const AUTHOR: Record<string, string> = { user: 'você', ai: 'IA', 'ai-edit': 'agente de edição' };

export const VersionsTab: React.FC<{ detail: ProjectDetail }> = ({ detail }) => {
  const client = useQueryClient();
  const checkout = useMutation({
    mutationFn: (number: number) => api.post(`/api/projects/${detail.doc.id}/checkout`, { number }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['project', detail.doc.id] }),
  });
  return (
    <Card>
      <div className="mb-3 text-sm text-zinc-400">
        Cada mudança vira uma versão. Voltar para uma versão cria uma versão nova com o conteúdo antigo, então nada se perde. Os arquivos gerados continuam no cache: voltar não paga de novo.
      </div>
      <ErrorBox error={checkout.error} />
      <div className="flex flex-col divide-y divide-zinc-800">
        {detail.versions.map((v) => (
          <div key={v.number} className="flex items-center gap-3 py-2 text-sm">
            <span className="w-12 font-mono text-zinc-500">v{v.number}</span>
            <span className="min-w-0 flex-1 truncate text-zinc-200">{v.message}</span>
            <Badge>{AUTHOR[v.author] ?? v.author}</Badge>
            <span className="w-40 text-right text-xs text-zinc-500">{v.created_at ? new Date(v.created_at).toLocaleString('pt-BR') : ''}</span>
            {v.number === detail.project.current_version ? (
              <Badge tone="good">atual</Badge>
            ) : (
              <Button className="text-xs" variant="ghost" busy={checkout.isPending} onClick={() => checkout.mutate(v.number)}>
                Voltar
              </Button>
            )}
          </div>
        ))}
      </div>
    </Card>
  );
};
