// Configurações › Biblioteca de efeitos e memes (ADR 0004 §3.8): declarar licença e tipo do que foi importado,
// enviar efeitos e memes, importar do canais, baixar do Freesound (CC0) e gravar as interjeições com a voz do canal.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AudioLines, Download, FolderSearch, KeyRound, Laugh, Mic, RotateCw, Trash2, TriangleAlert, Upload } from 'lucide-react';
import React, { useState } from 'react';
import { api, type Channel, type Job } from '../api';
import { libraryApi, type MemeItem, type SfxItem, type SfxLibrary } from '../apiAudio';
import { Badge, Button, Checkbox, ErrorBox, Field, IconButton, Input, Section, Select, Skeleton, useConfirm, useToast } from '../ui';
import { useJobDone } from './project/proof/useJobDone';

const SFX_KEY = ['library-sfx'];
const MEME_KEY = ['library-memes'];

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** Acompanha um job da biblioteca e recarrega a lista quando ele termina. */
const useLibraryJob = (onDone: (job: Job) => void) => {
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJobDone(jobId, (j) => {
    setJobId(null);
    onDone(j);
  });
  return { job, start: (j: Job) => setJobId(j.id), running: Boolean(jobId) };
};

/* ------------------------------------------------------------------ */
/* Uma linha de efeito do usuário                                      */
/* ------------------------------------------------------------------ */

const SfxRow: React.FC<{ item: SfxItem; licenses: SfxLibrary['licenses'] }> = ({ item, licenses }) => {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [hit, setHit] = useState(String(item.hit_s));
  const patch = useMutation({
    mutationFn: (body: Parameters<typeof libraryApi.patchSfx>[1]) => libraryApi.patchSfx(item.id, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: SFX_KEY }),
  });
  const remove = useMutation({
    mutationFn: () => libraryApi.deleteSfx(item.id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: SFX_KEY }),
  });
  const saveHit = () => {
    const value = Number(hit.replace(',', '.'));
    if (Number.isFinite(value) && value !== item.hit_s) patch.mutate({ hit_s: value });
  };
  return (
    <li className="flex flex-col gap-2 border-b border-line py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[13px] text-fg">{item.id}</span>
        <Badge tone="neutral">{item.kind}</Badge>
        {item.missing ? (
          <Badge tone="bad" icon={TriangleAlert}>
            arquivo sumiu
          </Badge>
        ) : item.auto ? (
          <Badge tone="good">automático</Badge>
        ) : (
          <Badge tone="warn">fora do automático</Badge>
        )}
        {item.tonal ? <Badge tone="info" title={`fração tonal ${item.tonal_share}`}>tonal</Badge> : null}
        <span className="text-xs tabular-nums text-subtle">{item.dur.toFixed(2)} s</span>
        <span className="ml-auto" />
        <IconButton
          size="sm"
          label={`Apagar o efeito ${item.id}`}
          icon={Trash2}
          busy={remove.isPending}
          onClick={async () => {
            if (await confirm({ title: `Apagar o efeito ${item.id}?`, description: 'O arquivo sai da pasta da biblioteca. Não dá para desfazer.', tone: 'danger', confirmLabel: 'Apagar' })) remove.mutate();
          }}
        />
      </div>
      {item.missing ? null : <audio controls preload="none" src={libraryApi.sfxAudioUrl(item.id)} className="h-8 w-full max-w-md" />}
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-[minmax(0,14rem)_minmax(0,10rem)_minmax(0,7rem)]">
        <Field label="Licença">
          <Select value={item.license} disabled={patch.isPending} onChange={(e) => patch.mutate({ license: e.target.value as SfxItem['license'] })}>
            {licenses.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Tipo">
          <Input
            defaultValue={item.kind}
            maxLength={40}
            onBlur={(e) => {
              const kind = e.target.value.trim();
              if (kind && kind !== item.kind) patch.mutate({ kind });
            }}
          />
        </Field>
        <Field label="Ponto forte (s)" hint="onde a palavra cai">
          <Input inputMode="decimal" value={hit} onChange={(e) => setHit(e.target.value)} onBlur={saveHit} />
        </Field>
      </div>
      {item.license === 'cc_by' || item.author ? (
        <Field label="Autor (crédito)">
          <Input
            defaultValue={item.author}
            maxLength={200}
            onBlur={(e) => {
              if (e.target.value.trim() !== item.author) patch.mutate({ author: e.target.value.trim() });
            }}
          />
        </Field>
      ) : null}
      {item.warning ? <p className="text-[13px] leading-snug text-amber-200">{item.warning}</p> : null}
      {item.source_url ? (
        <a href={item.source_url} target="_blank" rel="noreferrer" className="break-all text-[13px] text-accent hover:underline">
          {item.source_url}
        </a>
      ) : null}
      <ErrorBox error={patch.error ?? remove.error} />
    </li>
  );
};

/* ------------------------------------------------------------------ */
/* Efeitos                                                             */
/* ------------------------------------------------------------------ */

const SfxUpload: React.FC<{ licenses: SfxLibrary['licenses'] }> = ({ licenses }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState('');
  const [license, setLicense] = useState('desconhecida');
  const [author, setAuthor] = useState('');
  const [tags, setTags] = useState('');
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      if (file) form.append('file', file);
      form.append('kind', kind);
      form.append('license', license);
      form.append('author', author);
      form.append('tags', tags);
      return libraryApi.uploadSfx(form);
    },
    onSuccess: (item) => {
      setFile(null);
      setKind('');
      void qc.invalidateQueries({ queryKey: SFX_KEY });
      toast({ tone: item.auto ? 'good' : 'info', title: `Efeito ${item.id} na biblioteca`, description: item.warning ?? undefined });
    },
  });
  return (
    <form
      className="flex flex-col gap-2 rounded-lg border border-line p-3"
      onSubmit={(e) => {
        e.preventDefault();
        upload.mutate();
      }}
    >
      <h3 className="text-sm font-semibold text-fg">Enviar um efeito</h3>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Field label="Arquivo" hint="wav, mp3, ogg, flac ou m4a">
          <Input type="file" accept="audio/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Field>
        <Field label="Tipo" hint="boom, palmas, scratch...">
          <Input value={kind} maxLength={40} onChange={(e) => setKind(e.target.value)} />
        </Field>
        <Field label="Licença" hint="desconhecida fica fora do automático">
          <Select value={license} onChange={(e) => setLicense(e.target.value)}>
            {licenses.map((l) => (
              <option key={l.id} value={l.id}>
                {l.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Autor" hint="obrigatório para CC BY">
          <Input value={author} maxLength={200} onChange={(e) => setAuthor(e.target.value)} />
        </Field>
        <Field label="Tags" hint="separadas por vírgula">
          <Input value={tags} maxLength={500} onChange={(e) => setTags(e.target.value)} />
        </Field>
      </div>
      <div>
        <Button type="submit" size="sm" icon={Upload} disabled={!file || !kind.trim()} busy={upload.isPending}>
          Enviar
        </Button>
      </div>
      <ErrorBox error={upload.error} />
    </form>
  );
};

const FreesoundBox: React.FC<{ state: SfxLibrary['freesound']; offline: boolean }> = ({ state, offline }) => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [key, setKey] = useState('');
  const [sound, setSound] = useState('');
  const [kind, setKind] = useState('');
  const saveKey = useMutation({
    mutationFn: () => libraryApi.setFreesoundKey(key.trim()),
    onSuccess: () => {
      setKey('');
      void qc.invalidateQueries({ queryKey: SFX_KEY });
    },
  });
  const dropKey = useMutation({ mutationFn: libraryApi.deleteFreesoundKey, onSuccess: () => void qc.invalidateQueries({ queryKey: SFX_KEY }) });
  const fetch = useMutation({
    mutationFn: () => libraryApi.fetchFreesound({ sound: sound.trim(), kind: kind.trim() }),
    onSuccess: (item) => {
      setSound('');
      void qc.invalidateQueries({ queryKey: SFX_KEY });
      toast({ tone: 'good', title: `${item.id} baixado (CC0, de ${item.author})` });
    },
  });
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line p-3">
      <h3 className="text-sm font-semibold text-fg">Freesound (só CC0)</h3>
      {state.configured ? (
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
          <Badge tone="good" icon={KeyRound}>
            chave configurada
          </Badge>
          {state.from_env ? <span>pela variável {state.from_env}</span> : <span className="font-mono">{state.fields.api_key}</span>}
          {!state.from_env ? (
            <Button size="sm" variant="ghost" icon={Trash2} busy={dropKey.isPending} onClick={() => dropKey.mutate()}>
              Remover chave
            </Button>
          ) : null}
        </div>
      ) : (
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            saveKey.mutate();
          }}
        >
          <Field label="Chave de API do Freesound" hint="freesound.org › API › Apply for new credentials" className="min-w-0 flex-1">
            <Input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
          </Field>
          <Button type="submit" size="sm" icon={KeyRound} disabled={!key.trim()} busy={saveKey.isPending}>
            Salvar chave
          </Button>
        </form>
      )}
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          fetch.mutate();
        }}
      >
        <Field label="Link ou id do som" className="min-w-0 flex-1">
          <Input value={sound} placeholder="https://freesound.org/people/.../sounds/123/" onChange={(e) => setSound(e.target.value)} />
        </Field>
        <Field label="Tipo">
          <Input value={kind} maxLength={40} onChange={(e) => setKind(e.target.value)} />
        </Field>
        <Button type="submit" size="sm" icon={Download} disabled={offline || !state.configured || !sound.trim() || !kind.trim()} busy={fetch.isPending}>
          Baixar
        </Button>
      </form>
      <ErrorBox error={saveKey.error ?? dropKey.error ?? fetch.error} />
    </div>
  );
};

const VoiceBox: React.FC = () => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const [channel, setChannel] = useState('');
  const run = useLibraryJob((job) => {
    void qc.invalidateQueries({ queryKey: SFX_KEY });
    if (job.status === 'done') toast({ tone: 'good', title: 'Interjeições gravadas com a voz do canal' });
    else toast({ tone: 'bad', title: 'Não deu para gravar as interjeições', description: job.error ?? undefined });
  });
  const start = useMutation({ mutationFn: () => libraryApi.voiceSfx({ channel_id: channel }), onSuccess: run.start });
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line p-3">
      <h3 className="text-sm font-semibold text-fg">“Ué”, “pera”, “oxe”, “hã?” na voz do canal</h3>
      <p className="text-[13px] leading-snug text-muted">Gravadas uma vez com a voz final do canal (a mesma da narração), licença própria.</p>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Canal" className="min-w-0 flex-1">
          <Select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="">Escolha o canal</option>
            {(channels.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>
        <Button size="sm" icon={Mic} disabled={!channel} busy={start.isPending || run.running} onClick={() => start.mutate()}>
          Gravar
        </Button>
      </div>
      {run.job && run.running ? <p className="text-[13px] text-muted">{run.job.message}</p> : null}
      <ErrorBox error={start.error ?? channels.error} />
    </div>
  );
};

const CanaisBox: React.FC = () => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const [checked, setChecked] = useState(false);
  const preview = useMutation({ mutationFn: () => libraryApi.canaisPreview(checked) });
  const run = useLibraryJob((job) => {
    void qc.invalidateQueries({ queryKey: SFX_KEY });
    const imported = (job.result?.imported as unknown[] | undefined)?.length ?? 0;
    toast(job.status === 'done' ? { tone: 'good', title: `Do canais: ${plural(imported, 'efeito importado', 'efeitos importados')}` } : { tone: 'bad', title: 'A importação do canais falhou', description: job.error ?? undefined });
  });
  const start = useMutation({ mutationFn: () => libraryApi.importCanais({ license_checked: checked }), onSuccess: run.start });
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line p-3">
      <h3 className="text-sm font-semibold text-fg">Importar os efeitos do canais</h3>
      <Checkbox
        label="Conferi no Mixkit que os efeitos rastreados não são “Restricted”"
        description="Com isso eles entram como Mixkit Free License (automático). Sem, entram como licença desconhecida, com o link para conferir."
        checked={checked}
        onChange={setChecked}
      />
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="ghost" busy={preview.isPending} onClick={() => preview.mutate()}>
          Ver o que entra
        </Button>
        <Button size="sm" icon={Download} busy={start.isPending || run.running} onClick={() => start.mutate()}>
          Importar
        </Button>
      </div>
      {preview.data ? (
        <ul className="flex flex-col gap-0.5 text-[13px] text-muted">
          {preview.data.imported.map((r) => (
            <li key={r.id}>
              <span className="font-mono text-fg">{r.id}</span> · {r.kind} · {r.license}
            </li>
          ))}
          {preview.data.warnings.map((w) => (
            <li key={w} className="text-amber-200">
              {w}
            </li>
          ))}
        </ul>
      ) : null}
      <ErrorBox error={preview.error ?? start.error} />
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Memes                                                               */
/* ------------------------------------------------------------------ */

const RISK_LABELS: Record<MemeItem['risk'], string> = { low: 'baixo', medium: 'médio', high: 'alto' };

const MemeBox: React.FC = () => {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const memes = useQuery({ queryKey: MEME_KEY, queryFn: () => libraryApi.memes() });
  const [file, setFile] = useState<File | null>(null);
  const [license, setLicense] = useState('');
  const [risk, setRisk] = useState('');
  const [tags, setTags] = useState('');
  const [author, setAuthor] = useState('');
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      if (file) form.append('file', file);
      form.append('license', license);
      form.append('risk', risk);
      form.append('tags', tags);
      form.append('author', author);
      return libraryApi.uploadMeme(form);
    },
    onSuccess: (item) => {
      setFile(null);
      void qc.invalidateQueries({ queryKey: MEME_KEY });
      toast({ tone: item.auto ? 'good' : 'info', title: `Meme ${item.id} (risco ${RISK_LABELS[item.risk]})`, description: item.warning ?? undefined });
    },
  });
  const remove = useMutation({ mutationFn: libraryApi.deleteMeme, onSuccess: () => void qc.invalidateQueries({ queryKey: MEME_KEY }) });
  const data = memes.data;
  const own = (data?.items ?? []).filter((m) => m.origin === 'user');
  return (
    <div className="flex flex-col gap-3">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-fg">
        <Laugh size={14} aria-hidden="true" /> Memes e reações
      </h3>
      <p className="text-[13px] leading-snug text-muted">
        Nenhum meme vem com o Vulto. Envie só o que você pode usar: próprio, CC0, CC BY (com autor) ou citação de terceiros (fica fora do automático e pode gerar reivindicação). As poses do avatar
        do canal já valem como reações.
      </p>
      <form
        className="flex flex-col gap-2 rounded-lg border border-line p-3"
        onSubmit={(e) => {
          e.preventDefault();
          upload.mutate();
        }}
      >
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Field label="Arquivo" hint="PNG ou WebM com fundo transparente">
            <Input type="file" accept="image/png,video/webm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </Field>
          <Field label="Licença" hint="obrigatória">
            <Select value={license} onChange={(e) => setLicense(e.target.value)}>
              <option value="">Escolha a licença</option>
              {(data?.licenses ?? []).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Risco de reivindicação" hint="sem escolher: baixo, ou médio para material de terceiros">
            <Select value={risk} onChange={(e) => setRisk(e.target.value)}>
              <option value="">Automático</option>
              {(data?.risks ?? []).map((r) => (
                <option key={r} value={r}>
                  {RISK_LABELS[r]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Tags" hint="choque, piada, pera...">
            <Input value={tags} maxLength={500} onChange={(e) => setTags(e.target.value)} />
          </Field>
          <Field label="Autor" hint="obrigatório para CC BY">
            <Input value={author} maxLength={200} onChange={(e) => setAuthor(e.target.value)} />
          </Field>
        </div>
        <div>
          <Button type="submit" size="sm" icon={Upload} disabled={!file || !license} busy={upload.isPending}>
            Enviar meme
          </Button>
        </div>
        <ErrorBox error={upload.error} />
      </form>
      {memes.isLoading ? <Skeleton className="h-12" /> : null}
      {own.length ? (
        <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {own.map((m) => (
            <li key={m.id} className="flex items-start gap-3 rounded-lg border border-line p-2">
              {m.media === 'png' ? <img src={m.url} alt="" className="h-14 w-14 shrink-0 rounded object-contain" /> : <video src={m.url} muted className="h-14 w-14 shrink-0 rounded" />}
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono text-[13px] text-fg">{m.id}</span>
                  <Badge tone={m.auto ? 'good' : 'warn'}>{m.auto ? 'automático' : 'só escolhido'}</Badge>
                  <Badge tone={m.risk === 'low' ? 'neutral' : 'warn'}>risco {RISK_LABELS[m.risk]}</Badge>
                </div>
                <span className="text-xs text-subtle">{m.license_label}</span>
                {m.warning ? <span className="text-xs text-amber-200">{m.warning}</span> : null}
              </div>
              <IconButton
                size="sm"
                label={`Apagar o meme ${m.id}`}
                icon={Trash2}
                onClick={async () => {
                  if (await confirm({ title: `Apagar o meme ${m.id}?`, tone: 'danger', confirmLabel: 'Apagar' })) remove.mutate(m.id);
                }}
              />
            </li>
          ))}
        </ul>
      ) : null}
      {data?.warnings.length ? (
        <ul className="flex flex-col gap-0.5 text-[13px] text-amber-200">
          {data.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
      <ErrorBox error={memes.error ?? remove.error} />
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Seção                                                               */
/* ------------------------------------------------------------------ */

export const SfxMemeLibrarySection: React.FC = () => {
  const qc = useQueryClient();
  const { toast } = useToast();
  const library = useQuery({ queryKey: SFX_KEY, queryFn: libraryApi.sfx });
  const scan = useLibraryJob((job) => {
    void qc.invalidateQueries({ queryKey: SFX_KEY });
    const found = (job.result?.new as unknown[] | undefined)?.length ?? 0;
    toast(job.status === 'done' ? { tone: 'good', title: `Pasta conferida: ${plural(found, 'efeito novo', 'efeitos novos')}` } : { tone: 'bad', title: 'A busca na pasta falhou', description: job.error ?? undefined });
  });
  const startScan = useMutation({ mutationFn: libraryApi.scanSfx, onSuccess: scan.start });
  const data = library.data;
  const own = (data?.items ?? []).filter((i) => i.origin === 'user');
  const builtin = (data?.items ?? []).filter((i) => i.origin === 'builtin');
  return (
    <div id="efeitos-memes">
      <Section
        title="Biblioteca de efeitos e memes"
        icon={AudioLines}
        description="Efeitos sonoros e memes com licença registrada. Só o que tem licença declarada entra no automático."
        actions={
          <>
            <Button size="sm" variant="ghost" icon={RotateCw} onClick={() => library.refetch()} busy={library.isFetching}>
              Atualizar
            </Button>
            <Button size="sm" icon={FolderSearch} onClick={() => startScan.mutate()} busy={startScan.isPending || scan.running}>
              Procurar arquivos soltos
            </Button>
          </>
        }
      >
        {library.isLoading ? <Skeleton className="h-16" /> : null}
        {data ? (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2 text-[13px] text-muted">
              <Badge tone="neutral">{plural(builtin.length, 'efeito embutido', 'efeitos embutidos')}</Badge>
              <Badge tone={own.length ? 'good' : 'neutral'}>{plural(own.length, 'seu', 'seus')}</Badge>
              {data.loose.length ? <Badge tone="warn">{plural(data.loose.length, 'arquivo solto', 'arquivos soltos')}</Badge> : null}
              <span className="min-w-0 break-all font-mono">{data.dir}</span>
            </div>
            {data.warnings.length ? (
              <details className="rounded-lg border border-amber-400/30 bg-amber-400/5 p-2 text-[13px] text-amber-100">
                <summary className="cursor-pointer">{plural(data.warnings.length, 'aviso', 'avisos')}</summary>
                <ul className="mt-1 flex flex-col gap-0.5">
                  {data.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </details>
            ) : null}
            {own.length ? (
              <ul aria-label="Seus efeitos" className="flex flex-col">
                {own.map((item) => (
                  <SfxRow key={item.id} item={item} licenses={data.licenses} />
                ))}
              </ul>
            ) : (
              <p className="text-[13px] text-muted">Nenhum efeito seu ainda: os vídeos usam os embutidos ({data.procedural.join(', ')}).</p>
            )}
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <SfxUpload licenses={data.licenses} />
              <CanaisBox />
              <FreesoundBox state={data.freesound} offline={data.offline} />
              <VoiceBox />
            </div>
            <MemeBox />
          </div>
        ) : null}
        <ErrorBox error={library.error ?? startScan.error} />
      </Section>
    </div>
  );
};
