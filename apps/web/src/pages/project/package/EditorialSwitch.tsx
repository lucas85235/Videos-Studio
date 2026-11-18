// Liberação do material oficial (frames de trailer, capa, logo) nas thumbs do canal: `style_bible.thumbs`, gravado
// por PUT /api/channels/{id}/thumbs. Ligar é decisão do canal (o risco de direitos fica com ele); personagem oficial
// gerado por IA continua proibido em qualquer caso.
import { useMutation, useQueryClient } from '@tanstack/react-query';
import React from 'react';
import { api } from '../../../api';
import { packageQueryKey } from '../../../apiPackage';
import { Checkbox, ErrorBox } from '../../../ui';

export const EditorialSwitch: React.FC<{ projectId: string; channelId: string; allowed: boolean; disabled?: boolean }> = ({
  projectId,
  channelId,
  allowed,
  disabled,
}) => {
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: (allow: boolean) => api.put<unknown>(`/api/channels/${channelId}/thumbs`, { allow_editorial: allow }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: packageQueryKey(projectId) });
      void client.invalidateQueries({ queryKey: ['channels'] });
    },
  });
  return (
    <div className="flex flex-col gap-1">
      <Checkbox
        label="Usar frames e arte oficial nas thumbs deste canal"
        description="Trailers, capa e logo oficiais. Personagens oficiais nunca são gerados por IA."
        checked={save.isPending ? Boolean(save.variables) : allowed}
        disabled={disabled || save.isPending}
        onChange={(v) => save.mutate(v)}
      />
      <ErrorBox error={save.error} title="Não foi possível salvar" />
    </div>
  );
};
