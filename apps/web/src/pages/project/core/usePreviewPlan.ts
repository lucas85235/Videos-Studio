// A conta da próxima prévia (GET /plan, com o cache): quantas imagens ela gera de fato.

import { useQuery } from '@tanstack/react-query';
import { api, type BuildPlan, type ProjectDetail } from '../../../api';
import { docJobRunning, freshness } from './model';

/**
 * Mesma chave em toda a página (a faixa "Próximo passo" e o painel da curadoria dão o mesmo número). A hora da última
 * prévia entra na chave: uma prévia grava imagens sem criar versão nova do documento.
 */
export const usePreviewPlan = (detail: ProjectDetail, enabled = true) =>
  useQuery({
    queryKey: ['plan', detail.doc.id, 'preview', detail.project.current_version, freshness(detail).lastPreviewAt],
    queryFn: () => api.get<BuildPlan>(`/api/projects/${detail.doc.id}/plan?mode=preview`),
    enabled: enabled && Boolean(detail.style) && !docJobRunning(detail),
    retry: false,
    staleTime: 30_000,
  });
