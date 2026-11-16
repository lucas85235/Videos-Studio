import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ImagePlus, Plus, Trash2, UserRound, X } from 'lucide-react';
import React, { useEffect, useRef, useState } from 'react';
import { api, CHARACTER_MAX_IMAGES, type Character, fileUrl, type ProjectCharacters } from '../../api';
import { Button, ErrorBox, errorMessage, Field, IconButton, Input, Skeleton, Textarea, useConfirm, useToast } from '../../ui';
import { Sheet } from '../ops/common';
import { Notice } from '../project/delivery/shared';

/** De onde vêm e para onde vão os personagens: o canal (lista própria) ou o projeto (soma aos do canal). */
export type CharactersTarget = { kind: 'channel'; id: string; name: string } | { kind: 'project'; id: string; name: string };

type Draft = Character & { key: string };

let nextKey = 0;
const withKey = (c: Character): Draft => ({ ...c, key: `k${nextKey++}` });
const strip = ({ key: _key, ...c }: Draft): Character => c;

/** Id curto a partir do nome ("Detetive Sam" → "detetive_sam"), sem repetir os outros. */
const slugFor = (name: string, taken: Set<string>) => {
  const base =
    name
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 34) || 'personagem';
  let id = base;
  for (let n = 2; taken.has(id); n++) id = `${base}_${n}`;
  return id;
};

const pathOf = (t: CharactersTarget) => (t.kind === 'channel' ? `/api/channels/${t.id}/characters` : `/api/projects/${t.id}/characters`);

const same = (a: Draft[], b: Draft[]) => JSON.stringify(a.map(strip)) === JSON.stringify(b.map(strip));

const CharacterEditor: React.FC<{
  value: Draft;
  onChange: (c: Draft) => void;
  onRemove: () => void;
}> = ({ value: c, onChange, onRemove }) => {
  const { toast } = useToast();
  const fileInput = useRef<HTMLInputElement>(null);
  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const paths: string[] = [];
      for (const file of files) {
        const form = new FormData();
        form.append('file', file);
        paths.push((await api.upload<{ path: string; url: string }>('/api/characters/images', form)).path);
      }
      return paths;
    },
    onSuccess: (paths) => onChange({ ...c, images: [...c.images, ...paths.filter((p) => !c.images.includes(p))].slice(0, CHARACTER_MAX_IMAGES) }),
    onError: (err) => toast({ tone: 'bad', title: 'Não foi possível enviar a imagem', description: errorMessage(err) }),
  });
  const free = CHARACTER_MAX_IMAGES - c.images.length;
  const label = c.name.trim() || 'personagem sem nome';

  return (
    <li className="flex flex-col gap-3 rounded-xl border border-line bg-card p-3">
      <div className="flex items-start gap-2">
        <Field label="Nome" className="min-w-0 flex-1" hint="Só para achar o personagem na narração; não vai para o gerador de imagem.">
          <Input value={c.name} onChange={(e) => onChange({ ...c, name: e.target.value })} placeholder="ex.: Detetive Sam" maxLength={80} />
        </Field>
        <IconButton label={`Remover ${label}`} icon={Trash2} size="sm" onClick={onRemove} className="mt-6 hover:text-red-300" />
      </div>
      <Field label="Outros nomes na narração" hint="Separe por vírgula (ex.: o detetive, Sam).">
        <Input
          value={c.aliases.join(', ')}
          onChange={(e) => onChange({ ...c, aliases: e.target.value.split(',').map((a) => a.trimStart()) })}
          onBlur={() => onChange({ ...c, aliases: c.aliases.map((a) => a.trim()).filter(Boolean) })}
          placeholder="ex.: o detetive"
        />
      </Field>
      <Field label="Descrição visual (em inglês)" hint="Entra no prompt das cenas onde o personagem aparece. Diga quem é e o que se vê: idade, corpo, cabelo, roupa.">
        <Textarea
          rows={3}
          value={c.look}
          maxLength={400}
          onChange={(e) => onChange({ ...c, look: e.target.value })}
          placeholder="ex.: old man in his 70s, thin, grey beard, brown trench coat and felt hat"
        />
      </Field>
      <div className="flex flex-col gap-1.5">
        <div className="text-[13px] text-muted">
          Imagens âncora ({c.images.length} de {CHARACTER_MAX_IMAGES}). Vão junto para o gerador que aceita referência (Codex), para o rosto e a roupa
          ficarem iguais em todas as cenas.
        </div>
        <ul className="flex flex-wrap gap-2">
          {c.images.map((path, i) => (
            <li key={path} className="relative">
              <img src={fileUrl(path)} alt={`Imagem âncora ${i + 1} de ${label}`} className="h-24 w-24 rounded-lg border border-line object-cover" />
              <IconButton
                label={`Tirar a imagem âncora ${i + 1} de ${label}`}
                icon={X}
                size="sm"
                variant="secondary"
                className="absolute right-1 top-1"
                onClick={() => onChange({ ...c, images: c.images.filter((p) => p !== path) })}
              />
            </li>
          ))}
          {free > 0 ? (
            <li>
              <Button size="sm" icon={ImagePlus} busy={upload.isPending} onClick={() => fileInput.current?.click()} className="h-24">
                Enviar imagem
              </Button>
              <input
                ref={fileInput}
                type="file"
                accept="image/png,image/jpeg,image/webp"
                multiple
                hidden
                onChange={(e) => {
                  const files = Array.from(e.target.files ?? []).slice(0, free);
                  e.target.value = '';
                  if (files.length) upload.mutate(files);
                }}
              />
            </li>
          ) : null}
        </ul>
      </div>
    </li>
  );
};

/** Painel para cadastrar os personagens fixos de um canal ou de um projeto. */
export const CharactersSheet: React.FC<{ target: CharactersTarget; onClose: () => void }> = ({ target, onClose }) => {
  const client = useQueryClient();
  const { toast } = useToast();
  const confirm = useConfirm();
  const queryKey = ['characters', target.kind, target.id];
  const query = useQuery({
    queryKey,
    queryFn: async (): Promise<{ own: Character[]; inherited: Character[] }> => {
      if (target.kind === 'channel') return { own: await api.get<Character[]>(pathOf(target)), inherited: [] };
      const data = await api.get<ProjectCharacters>(pathOf(target));
      return { own: data.project, inherited: data.channel };
    },
  });
  const [items, setItems] = useState<Draft[] | null>(null);
  const [initial, setInitial] = useState<Draft[]>([]);
  useEffect(() => {
    if (query.data && items === null) {
      const drafts = query.data.own.map(withKey);
      setItems(drafts);
      setInitial(drafts);
    }
  }, [query.data, items]);

  const list = items ?? [];
  const dirty = items !== null && !same(items, initial);
  const ownIds = new Set(list.map((c) => c.id));
  const inherited = (query.data?.inherited ?? []).filter((c) => !ownIds.has(c.id));
  const empty = list.filter((c) => !c.name.trim() && !c.look.trim());

  const save = useMutation({
    mutationFn: () => api.put(pathOf(target), { characters: list.map(strip).map((c) => ({ ...c, aliases: c.aliases.map((a) => a.trim()).filter(Boolean) })) }),
    onSuccess: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ['characters'] }),
        client.invalidateQueries({ queryKey: target.kind === 'channel' ? ['channels'] : ['project', target.id] }),
      ]);
      toast({ tone: 'good', title: 'Personagens salvos', description: 'Valem para as próximas cenas e imagens.' });
      onClose();
    },
  });

  const add = (base?: Character) => {
    const taken = new Set([...list.map((c) => c.id), ...(query.data?.inherited ?? []).map((c) => c.id)]);
    const fresh: Character = base ? { ...base } : { id: slugFor('', taken), name: '', aliases: [], look: '', images: [] };
    setItems([...list, withKey(fresh)]);
  };

  const update = (key: string, next: Draft) =>
    setItems(
      list.map((c) => {
        if (c.key !== key) return c;
        // Personagem novo (ainda sem nome salvo): o id acompanha o nome.
        const isNew = !initial.some((i) => i.key === key) && !(query.data?.inherited ?? []).some((i) => i.id === c.id);
        if (!isNew || next.name === c.name) return next;
        const taken = new Set([...list.filter((o) => o.key !== key).map((o) => o.id), ...(query.data?.inherited ?? []).map((o) => o.id)]);
        return { ...next, id: slugFor(next.name, taken) };
      }),
    );

  const close = async () => {
    if (dirty && !(await confirm({ title: 'Descartar as alterações?', description: 'Os personagens que você mudou não foram salvos.', confirmLabel: 'Descartar', tone: 'danger' })))
      return;
    onClose();
  };

  return (
    <Sheet
      title={`Personagens de “${target.name}”`}
      description={
        target.kind === 'channel'
          ? 'Personagens que voltam nos vídeos do canal. As cenas onde eles aparecem usam a descrição e as imagens âncora.'
          : 'Personagens só deste projeto. Somam aos do canal; com o mesmo personagem, vale o do projeto.'
      }
      onRequestClose={() => void close()}
      onSubmit={() => {
        if (!save.isPending && !empty.length) save.mutate();
      }}
      footer={
        <>
          <ErrorBox error={save.error} title="Não foi possível salvar os personagens" onClose={() => save.reset()} className="w-full" />
          {empty.length ? (
            <p role="alert" className="w-full text-[13px] text-red-300">
              Dê um nome ou uma descrição a cada personagem (ou remova os vazios).
            </p>
          ) : null}
          <Button onClick={() => void close()}>Cancelar</Button>
          <Button type="submit" variant="primary" busy={save.isPending} disabled={!dirty || Boolean(empty.length)}>
            Salvar personagens
          </Button>
        </>
      }
    >
      <ErrorBox error={query.error} title="Não foi possível carregar os personagens" onRetry={() => query.refetch()} />
      {query.isLoading ? <Skeleton className="h-40" /> : null}
      {items !== null ? (
        <>
          <Notice tone="info" icon={UserRound} title="O nome não vai para a imagem">
            O app acha o personagem na narração pelo nome e coloca nas cenas a descrição visual. Rode “Criar cenas” de novo para marcar as cenas onde ele
            aparece; mudar a descrição ou as imagens refaz só as imagens dessas cenas.
          </Notice>
          {list.length ? (
            <ul className="flex flex-col gap-3">
              {list.map((c) => (
                <CharacterEditor key={c.key} value={c} onChange={(next) => update(c.key, next)} onRemove={() => setItems(list.filter((o) => o.key !== c.key))} />
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-muted">Nenhum personagem {target.kind === 'channel' ? 'neste canal' : 'próprio deste projeto'}.</p>
          )}
          <div>
            <Button size="sm" icon={Plus} onClick={() => add()}>
              Adicionar personagem
            </Button>
          </div>
          {inherited.length ? (
            <div className="flex flex-col gap-2 border-t border-line pt-4">
              <div className="text-[13px] text-muted">Do canal (valem neste projeto)</div>
              <ul className="flex flex-col gap-2">
                {inherited.map((c) => (
                  <li key={c.id} className="flex items-center gap-3 rounded-lg border border-line bg-canvas p-2">
                    {c.images[0] ? (
                      <img src={fileUrl(c.images[0])} alt="" className="h-10 w-10 shrink-0 rounded-md object-cover" />
                    ) : (
                      <UserRound size={20} aria-hidden="true" className="mx-2.5 shrink-0 text-muted" />
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm text-fg">{c.name || c.id}</div>
                      <div className="truncate text-xs text-muted">{c.look || 'sem descrição'}</div>
                    </div>
                    <Button size="sm" onClick={() => add(c)}>
                      Mudar neste projeto
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : null}
    </Sheet>
  );
};
