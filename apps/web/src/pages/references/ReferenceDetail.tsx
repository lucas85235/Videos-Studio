// Uma referência analisada: ritmo, paleta, folhas de contato e a tabela de planos.
import React from 'react';
import { fileUrl } from '../../api';
import type { ReferenceVideo } from '../../apiVisual';
import { formatSeconds } from '../../ui';
import { ReferenceStyleButton } from './ReferenceStyle';

const num = (v: number | undefined, digits = 1) => (v ?? 0).toLocaleString('pt-BR', { maximumFractionDigits: digits, minimumFractionDigits: 0 });

export const ReferenceDetail: React.FC<{ reference: ReferenceVideo }> = ({ reference: ref }) => {
  const st = ref.stats;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13px] text-fg">
        Ritmo: <strong>{num(st.cuts_per_min)} cortes por minuto</strong>, plano mediano de <strong>{num(st.median_s, 2)} s</strong> (média{' '}
        {num(st.mean_s, 2)} s, {st.shots ?? ref.shots.length} planos
        {ref.info.dur ? `, ${formatSeconds(ref.info.dur)}` : ''}
        {ref.info.w && ref.info.h ? `, ${ref.info.w}x${ref.info.h}` : ''}).
      </p>
      {st.palette?.length ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-subtle">Paleta:</span>
          {st.palette.map((c) => (
            <span key={c} className="flex items-center gap-1 text-xs text-muted">
              <span aria-hidden="true" className="inline-block h-5 w-5 rounded border border-line" style={{ background: c }} />
              {c}
            </span>
          ))}
        </div>
      ) : null}
      <ReferenceStyleButton reference={ref} />
      {ref.sheets.map((sheet, i) => (
        <a key={sheet} href={fileUrl(sheet)} target="_blank" rel="noreferrer" className="block">
          <img src={fileUrl(sheet)} alt={`Folha de contato ${i + 1} de ${ref.title}`} loading="lazy" className="w-full rounded-md border border-line" />
        </a>
      ))}
      {ref.shots.length ? (
        <details>
          <summary className="cursor-pointer text-[13px] text-muted">Tabela de planos ({ref.shots.length})</summary>
          <div className="mt-2 max-h-72 overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-subtle">
                <tr>
                  <th className="py-1 pr-3 font-medium">#</th>
                  <th className="py-1 pr-3 font-medium">Início (s)</th>
                  <th className="py-1 pr-3 font-medium">Fim (s)</th>
                  <th className="py-1 font-medium">Duração (s)</th>
                </tr>
              </thead>
              <tbody className="text-fg">
                {ref.shots.map((s) => (
                  <tr key={s.k} className="border-t border-line">
                    <td className="py-1 pr-3">{s.k}</td>
                    <td className="py-1 pr-3">{num(s.a, 2)}</td>
                    <td className="py-1 pr-3">{num(s.b, 2)}</td>
                    <td className="py-1">{num(s.dur, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </div>
  );
};
