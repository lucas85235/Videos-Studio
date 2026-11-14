import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarClock, Clock, Globe, Palette, Pencil, Plus, Trash2, Tv, UserRound, X } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { api, type Channel, type ChannelSlots, LANGUAGES, type PostingPlan, type PostingSlot, type StyleItem, TIMEZONES, WEEKDAYS } from '../api';
import { langLabel } from '../labels';
import {
  Badge,
  Button,
  Empty,
  ErrorBox,
  errorMessage,
  Field,
  FieldGroup,
  IconButton,
  Input,
  Page,
  PageHeader,
  Select,
  Skeleton,
  Textarea,
  useConfirm,
  useToast,
} from '../ui';
import { chipClass, Sheet } from './ops/common';
import { browserZone, sameOffsetAsBrowser, whenIn, zoneCity, zoneLabel } from './ops/zones';

type Form = { name: string; niche: string; persona: string; languages: string[]; default_style_id: string | null; posting: PostingPlan };

const blank = (): Form => ({
  name: '',
  niche: '',
  persona: '',
  languages: ['pt-BR'],
  default_style_id: null,
  posting: { timezone: browserZone, slots: [] },
});

const formOf = (c: Channel): Form => ({
  name: c.name,
  niche: c.niche,
  persona: c.persona,
  languages: c.languages,
  default_style_id: c.default_style_id,
  posting: c.posting,
});

/* ------------------------------------------------------------------ */
/* Dias da semana (0 = segunda … 6 = domingo, como no servidor)        */
/* ------------------------------------------------------------------ */

const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
const WORK_DAYS = [0, 1, 2, 3, 4];
const WEEKEND = [5, 6];

const DAY_PRESETS: { id: string; label: string; days: number[] }[] = [
  { id: 'todos', label: 'Todos os dias', days: ALL_DAYS },
  { id: 'uteis', label: 'Dias úteis', days: WORK_DAYS },
  { id: 'fds', label: 'Fim de semana', days: WEEKEND },
];

const sameDays = (a: number[], b: number[]) => a.length === b.length && [...a].sort().every((d, i) => d === [...b].sort()[i]);

const DAY_NAMES = ['segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo'];

/** "Todos os dias", "Dias úteis", "Fim de semana" ou "seg, qua, sex". */
const describeDays = (days: number[]) => {
  const preset = DAY_PRESETS.find((p) => sameDays(p.days, days));
  if (preset) return preset.label;
  return [...days].sort().map((d) => WEEKDAYS[d]).join(', ');
};

const validTime = (t: string) => /^\d{2}:\d{2}$/.test(t);

/** Hora sugerida para um horário novo: a primeira ainda não usada. */
const NO_SLOTS_TEXT = 'Sem horários: a Fila do YouTube não agenda vídeos deste canal e a Agenda não sugere horários.';

const nextFreeTime = (slots: PostingSlot[]) => ['18:00', '12:00', '21:00', '09:00', '15:00'].find((t) => !slots.some((s) => s.time === t)) ?? '18:00';

/* ------------------------------------------------------------------ */
/* Editor dos horários                                                 */
/* ------------------------------------------------------------------ */

const SlotEditor: React.FC<{
  slot: PostingSlot;
  index: number;
  timezone: string;
  onChange: (patch: Partial<PostingSlot>) => void;
  onRemove: () => void;
}> = ({ slot, index, timezone, onChange, onRemove }) => {
  const n = index + 1;
  const onlyOne = slot.weekdays.length === 1;
  return (
    <fieldset className="relative m-0 flex min-w-0 flex-col gap-2.5 rounded-lg border border-line bg-card p-3">
      <legend className="sr-only">Horário {n}</legend>
      <div className="flex items-center gap-2">
        <Clock size={16} aria-hidden="true" className="shrink-0 text-muted" />
        <Input
          type="time"
          aria-label={`Hora do horário ${n}`}
          className="w-32 tabular-nums"
          value={slot.time}
          invalid={!validTime(slot.time)}
          onChange={(e) => onChange({ time: e.target.value })}
        />
        <span className="truncate text-[13px] text-subtle">hora de {zoneCity(timezone)}</span>
        <IconButton label={`Remover horário ${n}`} icon={Trash2} size="sm" onClick={onRemove} className="ml-auto hover:text-red-300" />
      </div>
      <div role="group" aria-label={`Dias do horário ${n}`} className="flex flex-wrap gap-1">
        {WEEKDAYS.map((label, day) => {
          const on = slot.weekdays.includes(day);
          const locked = on && onlyOne;
          return (
            <button
              key={day}
              type="button"
              aria-pressed={on}
              aria-label={DAY_NAMES[day]}
              aria-disabled={locked || undefined}
              title={locked ? 'Cada horário precisa de pelo menos um dia' : DAY_NAMES[day]}
              onClick={() => {
                if (locked) return;
                onChange({ weekdays: on ? slot.weekdays.filter((d) => d !== day) : [...slot.weekdays, day].sort() });
              }}
              className={`${chipClass(on)} min-w-10 justify-center ${locked ? 'cursor-not-allowed' : ''}`}
            >
              {label}
            </button>
          );
        })}
      </div>
      <div role="group" aria-label={`Atalhos de dias do horário ${n}`} className="flex flex-wrap items-center gap-1">
        {DAY_PRESETS.map((p) => {
          const on = sameDays(p.days, slot.weekdays);
          return (
            <button
              key={p.id}
              type="button"
              aria-pressed={on}
              onClick={() => onChange({ weekdays: p.days })}
              className={`rounded-md px-2 py-1 text-xs font-medium transition-colors ${on ? 'text-fg underline decoration-accent decoration-2 underline-offset-4' : 'text-muted hover:bg-raised hover:text-fg'}`}
            >
              {p.label}
            </button>
          );
        })}
        {onlyOne ? <span className="ml-1 text-xs text-subtle">Pelo menos um dia fica marcado.</span> : null}
      </div>
    </fieldset>
  );
};

/** Mini grade da semana com os horários de cada dia (repetidos no mesmo dia em aviso). */
const WeekOverview: React.FC<{ slots: PostingSlot[] }> = ({ slots }) => {
  const days = ALL_DAYS.map((d) => slots.filter((s) => s.weekdays.includes(d) && validTime(s.time)).map((s) => s.time).sort());
  // Horários repetidos no mesmo dia contam uma vez só (o servidor agenda um vídeo por horário).
  const total = days.reduce((n, d) => n + new Set(d).size, 0);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-[13px] text-muted">
        <span className="tabular-nums text-fg">{total}</span> {total === 1 ? 'vídeo' : 'vídeos'} por semana
      </div>
      <ul aria-label="Horários por dia da semana" className="grid grid-cols-7 gap-1">
        {days.map((times, d) => (
          <li key={d} aria-label={`${DAY_NAMES[d]}: ${times.length ? times.join(', ') : 'sem horário'}`} className="flex min-w-0 flex-col items-center gap-1 rounded-md border border-line bg-canvas px-0.5 py-1.5">
            <span aria-hidden="true" className="text-xs font-medium text-muted">{WEEKDAYS[d]}</span>
            {times.length ? (
              times.map((t, i) => {
                const dup = times.indexOf(t) !== i;
                return (
                  <span
                    key={`${t}-${i}`}
                    aria-hidden="true"
                    title={dup ? 'Horário repetido neste dia' : undefined}
                    className={`text-xs tabular-nums ${dup ? 'rounded bg-amber-300/15 px-0.5 text-amber-200' : 'text-fg'}`}
                  >
                    {t}
                  </span>
                );
              })
            ) : (
              <span aria-hidden="true" className="text-xs text-subtle">—</span>
            )}
          </li>
        ))}
      </ul>
      {days.some((times) => new Set(times).size !== times.length) ? (
        <p className="text-[13px] text-amber-200">Há horários repetidos no mesmo dia: só um vídeo é agendado por horário.</p>
      ) : null}
    </div>
  );
};

const PostingEditor: React.FC<{ value: PostingPlan; onChange: (p: PostingPlan) => void }> = ({ value, onChange }) => {
  const zones = TIMEZONES.includes(value.timezone) ? TIMEZONES : [value.timezone, ...TIMEZONES];
  const setSlot = (index: number, patch: Partial<PostingSlot>) =>
    onChange({ ...value, slots: value.slots.map((s, i) => (i === index ? { ...s, ...patch } : s)) });
  return (
    <div className="flex flex-col gap-3">
      <Field label="Fuso dos horários" hint="Os horários de postagem valem neste fuso, mesmo que o computador esteja em outro.">
        <Select value={value.timezone} onChange={(e) => onChange({ ...value, timezone: e.target.value })}>
          {zones.map((z) => (
            <option key={z} value={z}>
              {zoneLabel(z)}
              {z === browserZone ? ' · este computador' : ''}
            </option>
          ))}
        </Select>
      </Field>
      <FieldGroup
        legend="Horários de postagem"
        hint={value.slots.length ? undefined : NO_SLOTS_TEXT}
      >
        {value.slots.map((slot, index) => (
          <SlotEditor
            key={index}
            slot={slot}
            index={index}
            timezone={value.timezone}
            onChange={(patch) => setSlot(index, patch)}
            onRemove={() => onChange({ ...value, slots: value.slots.filter((_, i) => i !== index) })}
          />
        ))}
        <div>
          <Button size="sm" icon={Plus} onClick={() => onChange({ ...value, slots: [...value.slots, { weekdays: ALL_DAYS, time: nextFreeTime(value.slots) }] })}>
            Adicionar horário
          </Button>
        </div>
      </FieldGroup>
      {value.slots.length ? <WeekOverview slots={value.slots} /> : null}
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Cartão do canal                                                     */
/* ------------------------------------------------------------------ */

const NextSlots: React.FC<{ channelId: string }> = ({ channelId }) => {
  const slots = useQuery({ queryKey: ['channel-slots', channelId], queryFn: () => api.get<ChannelSlots>(`/api/channels/${channelId}/slots?count=3`) });
  if (slots.isLoading) return <Skeleton className="h-5 w-56" />;
  if (slots.error) return <p className="text-[13px] text-red-300">Não foi possível calcular os próximos horários.</p>;
  if (!slots.data?.configured) return null;
  const zone = slots.data.timezone;
  const list = slots.data.slots;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="text-[13px] text-muted">
        Próximos horários livres <span className="text-subtle">· no fuso do canal, {zoneLabel(zone)}</span>
      </div>
      {list.length ? (
        <ul className="flex flex-wrap gap-1.5">
          {list.map((s) => (
            <li key={s}>
              <Badge icon={CalendarClock} title={sameOffsetAsBrowser(zone) ? undefined : `No seu computador: ${whenIn(s)}`}>
                <span className="tabular-nums">{whenIn(s, zone)}</span>
              </Badge>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-subtle">Nenhum horário livre nos próximos 60 dias.</p>
      )}
      {list.length && !sameOffsetAsBrowser(zone) ? (
        <p className="text-[13px] text-subtle">
          No seu computador, {zoneLabel(browserZone)}: <span className="tabular-nums">{list.map((s) => whenIn(s)).join(' · ')}</span>
        </p>
      ) : null}
    </div>
  );
};

const ChannelCard: React.FC<{
  channel: Channel;
  styleName: string | null;
  editing: boolean;
  onEdit: () => void;
  onDelete: () => void;
  deleting: boolean;
}> = ({ channel: c, styleName, editing, onEdit, onDelete, deleting }) => (
  <article
    aria-label={c.name}
    className={`flex min-w-0 flex-col gap-3 rounded-xl border bg-card p-4 transition-colors ${
      editing ? 'border-accent/60 ring-1 ring-accent/30' : 'border-line hover:border-line-strong'
    }`}
  >
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-display text-[15px] font-semibold leading-6 text-fg [overflow-wrap:anywhere]">{c.name}</h2>
          {editing ? <Badge tone="accent" icon={Pencil}>Editando</Badge> : null}
        </div>
        {c.niche ? <div className="text-[13px] text-muted">{c.niche}</div> : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button size="sm" icon={Pencil} onClick={onEdit} aria-label={`Editar o canal ${c.name}`}>
          Editar
        </Button>
        <IconButton label={`Apagar o canal ${c.name}`} icon={Trash2} size="sm" busy={deleting} onClick={onDelete} className="hover:text-red-300" />
      </div>
    </div>
    <div className="flex flex-wrap gap-1.5">
      {c.languages.map((l) => (
        <Badge key={l} icon={Globe}>
          {langLabel(l)}
        </Badge>
      ))}
      <Badge icon={Palette} title="Estilo padrão dos novos projetos deste canal">
        {styleName ?? 'Sem estilo padrão'}
      </Badge>
    </div>
    {c.persona ? (
      <div className="flex gap-1.5" title={c.persona}>
        <UserRound size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-subtle" />
        <p className="line-clamp-2 min-w-0 text-[13px] leading-snug text-muted">{c.persona}</p>
      </div>
    ) : null}
    <div className="flex flex-col gap-2 border-t border-line pt-3">
      {c.posting.slots.length ? (
        <>
          <ul aria-label="Horários de postagem" className="flex flex-wrap gap-1.5">
            {c.posting.slots.map((s, i) => (
              <li key={i}>
                <Badge icon={Clock}>
                  {describeDays(s.weekdays)} · <span className="tabular-nums">{s.time}</span>
                </Badge>
              </li>
            ))}
          </ul>
          <span className="text-[13px] text-subtle">
            Hora de {zoneCity(c.posting.timezone)}
            {c.posting.timezone === browserZone ? ' (este computador)' : ''}
          </span>
        </>
      ) : (
        <p className="text-[13px] text-subtle">{NO_SLOTS_TEXT}</p>
      )}
      <NextSlots channelId={c.id} />
    </div>
  </article>
);

/* ------------------------------------------------------------------ */
/* Página                                                              */
/* ------------------------------------------------------------------ */

export const ChannelsPage: React.FC = () => {
  const client = useQueryClient();
  const confirm = useConfirm();
  const { toast } = useToast();
  const channels = useQuery({ queryKey: ['channels'], queryFn: () => api.get<Channel[]>('/api/channels') });
  const styles = useQuery({ queryKey: ['styles'], queryFn: () => api.get<StyleItem[]>('/api/styles') });
  const [form, setForm] = useState<Form>(blank);
  const [initial, setInitial] = useState<string>('');
  const [editing, setEditing] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [tried, setTried] = useState(false);

  const save = useMutation({
    mutationFn: () => (editing ? api.put(`/api/channels/${editing}`, form) : api.post('/api/channels', form)),
    onSuccess: () => {
      toast({ tone: 'good', title: editing ? `Canal “${form.name.trim()}” salvo` : `Canal “${form.name.trim()}” criado` });
      setForm(blank());
      setEditing(null);
      setOpen(false);
      client.invalidateQueries({ queryKey: ['channels'] });
      client.invalidateQueries({ queryKey: ['channel-slots'] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/api/channels/${id}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['channels'] }),
    // O erro aparece num aviso (o cartão pode estar longe do topo da página).
    onError: (err) => toast({ tone: 'bad', title: 'Não foi possível apagar o canal', description: errorMessage(err) }),
  });

  const styleName = (id: string | null) => {
    if (!id) return null;
    const found = styles.data?.find((s) => s.style.id === id)?.style.name;
    if (found) return found;
    return styles.isLoading ? 'Carregando…' : styles.error ? 'Estilo indisponível' : 'Estilo removido';
  };

  const openSheet = (next: Form, id: string | null) => {
    save.reset();
    setTried(false);
    setForm(next);
    setInitial(JSON.stringify(next));
    setEditing(id);
    setOpen(true);
  };

  const dirty = open && JSON.stringify(form) !== initial;

  // Sem useBlocker (BrowserRouter): ao menos recarregar ou fechar a janela pede confirmação com alterações pendentes.
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', h);
    return () => window.removeEventListener('beforeunload', h);
  }, [dirty]);

  const closeSheet = async () => {
    if (save.isPending) return;
    if (dirty) {
      const ok = await confirm({
        title: 'Descartar as alterações?',
        description: editing ? 'O que você mudou neste canal ainda não foi salvo.' : 'O canal novo ainda não foi salvo.',
        confirmLabel: 'Descartar',
        cancelLabel: 'Continuar editando',
        tone: 'danger',
      });
      if (!ok) return;
    }
    setOpen(false);
    setEditing(null);
    setForm(blank());
  };

  const askDelete = async (c: Channel) => {
    const ok = await confirm({
      title: `Apagar o canal “${c.name}”?`,
      description: 'A persona e os horários de postagem deste canal são apagados. Os projetos continuam existindo, mas ficam sem canal. Não dá para desfazer.',
      confirmLabel: 'Apagar canal',
      tone: 'danger',
    });
    if (!ok) return;
    remove.mutate(c.id, {
      onSuccess: () => {
        toast({ tone: 'good', title: `Canal “${c.name}” apagado` });
        if (editing === c.id) {
          setOpen(false);
          setEditing(null);
          setForm(blank());
        }
      },
    });
  };

  const nameMissing = !form.name.trim();
  const badTime = form.posting.slots.some((s) => !validTime(s.time));
  const submit = () => {
    setTried(true);
    if (nameMissing || badTime) {
      // Depois do render com os erros: leva o foco (e a rolagem) até o primeiro campo inválido.
      window.setTimeout(() => document.querySelector<HTMLElement>('dialog[open] [aria-invalid="true"]')?.focus(), 0);
      return;
    }
    if (save.isPending) return;
    save.mutate();
  };

  const list = channels.data ?? [];
  const userStyles = (styles.data ?? []).filter((s) => s.source === 'user');
  const presetStyles = (styles.data ?? []).filter((s) => s.source !== 'user');
  const extraLanguages = form.languages.slice(1);
  const knownStyle = !form.default_style_id || (styles.data ?? []).some((s) => s.style.id === form.default_style_id);

  return (
    <Page>
      <PageHeader
        title="Canais"
        description={
          // Sem canais, quem explica é o estado vazio (uma explicação só por tela).
          channels.data && !list.length
            ? undefined
            : 'A persona do canal entra nos roteiros. Os horários de postagem viram sugestões de agendamento na Agenda e na Fila do YouTube.'
        }
        actions={
          list.length || channels.error ? (
            <Button variant="primary" icon={Plus} onClick={() => openSheet(blank(), null)}>
              Novo canal
            </Button>
          ) : null
        }
      />

      {channels.isLoading ? (
        <div className="grid gap-3 lg:grid-cols-2" aria-hidden="true">
          <Skeleton className="h-48" />
          <Skeleton className="h-48" />
        </div>
      ) : null}
      <ErrorBox error={channels.error} title="Não foi possível carregar os canais" onRetry={() => channels.refetch()} />

      {channels.data && !list.length ? (
        <Empty
          icon={Tv}
          title="Nenhum canal ainda"
          action={
            <Button variant="primary" icon={Plus} onClick={() => openSheet(blank(), null)}>
              Criar primeiro canal
            </Button>
          }
        >
          Um canal guarda a persona do narrador, o idioma, o estilo padrão e os horários de postagem.
        </Empty>
      ) : null}

      {list.length ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {list.map((c) => (
            <ChannelCard
              key={c.id}
              channel={c}
              styleName={styleName(c.default_style_id)}
              editing={open && editing === c.id}
              deleting={remove.isPending && remove.variables === c.id}
              onEdit={() => openSheet(formOf(c), c.id)}
              onDelete={() => void askDelete(c)}
            />
          ))}
        </div>
      ) : null}

      {open ? (
        <Sheet
          title={editing ? `Editar “${list.find((c) => c.id === editing)?.name ?? form.name}”` : 'Novo canal'}
          description={dirty ? 'Alterações não salvas.' : editing ? 'As mudanças valem para os próximos roteiros e agendamentos.' : 'Preencha o básico; o resto pode ficar para depois.'}
          onRequestClose={() => void closeSheet()}
          onSubmit={submit}
          footer={
            <>
              {/* Erro do servidor perto do botão (o corpo do painel pode estar rolado para baixo). */}
              <ErrorBox error={save.error} title="Não foi possível salvar o canal" onClose={() => save.reset()} className="w-full" />
              {tried && (nameMissing || badTime) ? (
                <p role="alert" className="w-full text-[13px] text-red-300">
                  {nameMissing ? 'Dê um nome ao canal.' : 'Preencha a hora de todos os horários (ou remova os vazios).'}
                </p>
              ) : null}
              <Button onClick={() => void closeSheet()}>Cancelar</Button>
              <Button type="submit" variant="primary" busy={save.isPending}>
                {editing ? 'Salvar canal' : 'Criar canal'}
              </Button>
            </>
          }
        >
          <Field label="Nome" error={tried && nameMissing ? 'Dê um nome ao canal.' : undefined}>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="ex.: Curiosidades do Mundo" />
          </Field>
          <Field label="Nicho">
            <Input value={form.niche} onChange={(e) => setForm({ ...form, niche: e.target.value })} placeholder="ex.: ciência do dia a dia, finanças pessoais, história" />
          </Field>
          <Field label="Persona do narrador" hint="Vai para o Claude junto com cada pedido de roteiro deste canal.">
            <Textarea rows={3} value={form.persona} onChange={(e) => setForm({ ...form, persona: e.target.value })} placeholder="ex.: narrador sóbrio, curioso, que sempre cita fontes" />
          </Field>
          <ErrorBox error={styles.error} title="Não foi possível carregar os estilos" onRetry={() => styles.refetch()} />
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Idioma principal">
              <Select
                value={form.languages[0]}
                onChange={(e) => setForm({ ...form, languages: [e.target.value, ...extraLanguages.filter((l) => l !== e.target.value)] })}
              >
                {LANGUAGES.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Estilo padrão">
              <Select value={form.default_style_id ?? ''} onChange={(e) => setForm({ ...form, default_style_id: e.target.value || null })}>
                <option value="">Nenhum (escolher em cada projeto)</option>
                {!knownStyle && form.default_style_id ? (
                  <option value={form.default_style_id}>
                    {styles.isLoading ? 'Carregando estilos…' : styles.error ? 'Estilo atual (lista indisponível)' : 'Estilo removido (escolha outro)'}
                  </option>
                ) : null}
                {userStyles.length ? (
                  <optgroup label="Meus estilos">
                    {userStyles.map((s) => (
                      <option key={s.style.id} value={s.style.id}>
                        {s.style.name}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
                {presetStyles.length ? (
                  <optgroup label="Presets">
                    {presetStyles.map((s) => (
                      <option key={s.style.id} value={s.style.id}>
                        {s.style.name}
                      </option>
                    ))}
                  </optgroup>
                ) : null}
              </Select>
            </Field>
          </div>
          {extraLanguages.length ? (
            <div className="-mt-2 flex flex-col gap-1.5">
              <div className="text-[13px] text-muted">Outros idiomas do canal</div>
              <ul className="flex flex-wrap gap-1.5">
                {extraLanguages.map((l) => (
                  <li key={l} className="inline-flex items-center gap-0.5 rounded-md border border-line-strong bg-raised py-0.5 pl-2 pr-0.5 text-xs text-muted">
                    <Globe size={14} aria-hidden="true" className="mr-1 shrink-0" />
                    {langLabel(l)}
                    <IconButton
                      size="sm"
                      label={`Remover o idioma ${langLabel(l)}`}
                      icon={X}
                      onClick={() => setForm({ ...form, languages: form.languages.filter((x, i) => i === 0 || x !== l) })}
                    />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="border-t border-line pt-4">
            <PostingEditor value={form.posting} onChange={(posting) => setForm({ ...form, posting })} />
            {tried && badTime ? <p className="mt-2 text-[13px] text-red-300">Preencha a hora de todos os horários (ou remova os vazios).</p> : null}
          </div>
        </Sheet>
      ) : null}
    </Page>
  );
};
