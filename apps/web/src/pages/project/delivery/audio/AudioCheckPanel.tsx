// "Conferir o áudio" no painel Trilha da aba Prévia (ADR 0003, bloco G): mede a prévia sem renderizar o vídeo.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AudioWaveform } from 'lucide-react';
import type React from 'react';
import { useMemo, useState } from 'react';
import type { ProjectDetail } from '../../../../api';
import { type AudioCheckData, type AudioChapterLevel, audioApi, MUSIC_DISTANCE } from '../../../../apiAudio';
import type { CheckItem, CheckLevel } from '../../../../apiChecks';
import { Badge, type BadgeTone, Button, ErrorBox, useToast } from '../../../../ui';
import { sceneNumbers } from '../../core/model';
import { clock, db, useAudioJob } from './useAudioJob';

const TONES: Record<CheckLevel, BadgeTone> = { ok: 'good', aviso: 'warn', erro: 'bad' };
const LEVEL_TEXT: Record<CheckLevel, string> = { ok: 'ok', aviso: 'aviso', erro: 'erro' };

const DistanceBadge: React.FC<{ value: number | null }> = ({ value }) => {
  if (value == null) return <span className="text-subtle">–</span>;
  const [lo, hi] = MUSIC_DISTANCE;
  const ok = value >= lo && value <= hi;
  return (
    <Badge tone={ok ? 'good' : 'warn'} title={`O padrão é de ${lo} a ${hi} dB abaixo da voz`}>
      {db(value)} dB {ok ? 'ok' : value < lo ? 'alta' : 'baixa'}
    </Badge>
  );
};

const ChapterRow: React.FC<{ row: AudioChapterLevel; strong?: boolean }> = ({ row, strong }) => (
  <tr className={`border-t border-line ${strong ? 'font-medium' : ''}`}>
    <th scope="row" className="py-1 pr-2 text-left font-normal text-fg">
      {row.title}
      <span className="ml-1 text-subtle">{clock(row.start_s)}</span>
    </th>
    <td className="px-2 py-1 text-right tabular-nums">{db(row.voice_db)}</td>
    <td className="px-2 py-1 text-right tabular-nums">{db(row.music_speech_db)}</td>
    <td className="px-2 py-1 text-right tabular-nums">{db(row.music_pause_db)}</td>
    <td className="py-1 pl-2 text-right"><DistanceBadge value={row.distance_db} /></td>
  </tr>
);

export const AudioCheckPanel: React.FC<{ detail: ProjectDetail; lang: string }> = ({ detail, lang }) => {
  const id = detail.doc.id;
  const client = useQueryClient();
  const { toast } = useToast();
  const [jobId, setJobId] = useState<string | null>(null);
  const check = useQuery({ queryKey: ['audio-check', id, lang], queryFn: () => audioApi.audioCheck(id, lang) });
  const job = useAudioJob(jobId, (j) => {
    setJobId(null);
    client.invalidateQueries({ queryKey: ['audio-check', id, lang] });
    if (j.status !== 'done') toast({ tone: 'bad', title: 'A conferência do áudio falhou', description: j.error ?? undefined });
  });
  const run = useMutation({
    mutationFn: () => audioApi.runAudioCheck(id, { lang, source: 'preview' }),
    onSuccess: (j) => setJobId(j.id),
  });
  const numbers = useMemo(() => sceneNumbers(detail.doc), [detail.doc]);
  const hasPreview = Boolean(detail.outputs.preview?.langs?.[lang]);
  const report = check.data?.report ?? null;
  const data = (report?.data ?? {}) as AudioCheckData;
  const problems: CheckItem[] = (report?.items ?? []).filter((i) => i.level !== 'ok');
  const stale = report && report.version !== detail.project.current_version;
  const running = Boolean(jobId) || run.isPending;

  return (
    <section aria-label="Conferência do áudio" className="mt-4 flex flex-col gap-3 rounded-lg border border-line p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-medium text-fg">
          <AudioWaveform size={15} aria-hidden="true" className="text-muted" />
          Conferência do áudio
          {report ? <Badge tone={TONES[report.status]}>{LEVEL_TEXT[report.status]}</Badge> : null}
        </h3>
        <Button size="sm" icon={AudioWaveform} disabled={!hasPreview || running} busy={running} onClick={() => run.mutate()}>
          Conferir o áudio (sem renderizar)
        </Button>
      </div>
      {!hasPreview ? <p className="text-[13px] text-subtle">Gere a prévia para conferir o áudio.</p> : null}
      {running && job ? <p className="text-[13px] text-subtle">{job.message || 'Conferindo…'}</p> : null}
      {stale ? (
        <p className="text-[13px] text-subtle">Esta conferência é de uma versão anterior. Atualize a prévia e confira de novo.</p>
      ) : null}
      {report && data.chapters?.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px] text-muted">
            <caption className="sr-only">Música e voz por capítulo, em LUFS</caption>
            <thead>
              <tr className="text-left text-subtle">
                <th scope="col" className="py-1 pr-2 font-normal">Trecho</th>
                <th scope="col" className="px-2 py-1 text-right font-normal">Voz</th>
                <th scope="col" className="px-2 py-1 text-right font-normal">Trilha sob a fala</th>
                <th scope="col" className="px-2 py-1 text-right font-normal">Trilha nas pausas</th>
                <th scope="col" className="py-1 pl-2 text-right font-normal">Distância</th>
              </tr>
            </thead>
            <tbody>
              {data.chapters.map((row) => <ChapterRow key={`${row.title}-${row.start_s}`} row={row} />)}
              {data.episode && data.chapters.length > 1 ? <ChapterRow row={data.episode} strong /> : null}
            </tbody>
          </table>
          <p className="mt-1 text-[12px] text-subtle">
            A música deve ficar de {MUSIC_DISTANCE[0]} a {MUSIC_DISTANCE[1]} dB abaixo da voz em cada trecho.
          </p>
        </div>
      ) : null}
      {problems.length ? (
        <ul className="flex flex-col gap-1.5">
          {problems.map((it, k) => (
            <li key={`${it.code}-${k}`} className="flex items-start gap-2 text-[13px]">
              <Badge tone={TONES[it.level]}>{LEVEL_TEXT[it.level]}</Badge>
              <span className="min-w-0 text-fg">
                {it.title}
                {it.at_s != null ? <span className="text-subtle"> · {clock(it.at_s)}</span> : null}
                {it.detail ? <span className="block text-subtle">{it.detail}</span> : null}
              </span>
            </li>
          ))}
        </ul>
      ) : report ? (
        <p className="text-[13px] text-subtle">Nada fora do padrão.</p>
      ) : null}
      {report && data.sfx_events?.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[13px] text-muted">
            <caption className="py-1 text-left text-subtle">Efeitos mais altos</caption>
            <thead>
              <tr className="text-left text-subtle">
                <th scope="col" className="py-1 pr-2 font-normal">Tempo</th>
                <th scope="col" className="px-2 py-1 font-normal">Cena</th>
                <th scope="col" className="px-2 py-1 font-normal">Efeito</th>
                <th scope="col" className="px-2 py-1 text-right font-normal">dB × voz</th>
                <th scope="col" className="py-1 pl-2 text-right font-normal">Tom</th>
              </tr>
            </thead>
            <tbody>
              {data.sfx_events.map((e) => (
                <tr key={e.t} className="border-t border-line">
                  <td className="py-1 pr-2 tabular-nums">{clock(e.t)}</td>
                  <td className="px-2 py-1">{e.scene_id ? `Cena ${numbers.get(e.scene_id) ?? '?'}` : '–'}</td>
                  <td className="px-2 py-1">{e.kind ?? '–'}</td>
                  <td className={`px-2 py-1 text-right tabular-nums ${e.vs_voz != null && e.vs_voz > 0 ? 'text-amber-200' : ''}`}>
                    {e.vs_voz != null && e.vs_voz > 0 ? '+' : ''}{db(e.vs_voz)}
                  </td>
                  <td className={`py-1 pl-2 text-right tabular-nums ${e.tom > 0.3 ? 'text-red-300' : ''}`}>
                    {Math.round(e.tom * 100)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {report && data.sfx_files?.length ? (
        <div className="flex flex-col gap-1">
          <span className="text-[13px] text-subtle">Guarda de apito (sem apito, assobio ou efeito agudo)</span>
          <div className="flex flex-wrap gap-1.5">
            {data.sfx_files.map((f) => (
              <Badge
                key={f.kind}
                tone={f.blocked ? 'bad' : 'good'}
                title={f.blocked ? `ficou fora do mix${f.banned ? ' (banido)' : ''}` : `destaque máximo ${db(f.prom)} dB`}
              >
                {f.kind}: {f.banned ? 'banido' : f.apito ? `apito em ${f.hz} Hz` : 'sem apito'}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}
      <ErrorBox error={run.error ?? check.error} />
    </section>
  );
};
