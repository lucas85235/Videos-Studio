/**
 * Canais › Pronúncia e glossário.
 *
 * - Dicionário de pronúncia: "termo → como falar", por idioma ou para todos. Vai para a voz (todas as vozes); a
 *   legenda continua como está no roteiro.
 * - Glossário de tradução: termos que não se traduzem ou têm tradução fixa por idioma. Entra no pedido de tradução e
 *   é conferido depois (o roteiro avisa quando um termo não foi respeitado).
 */
import { BookA, Languages, Plus, Trash2 } from 'lucide-react';
import React from 'react';
import { LANGUAGES, type GlossaryEntry, type PronunciationEntry } from '../api';
import { langLabel } from '../labels';
import { Button, Checkbox, FieldGroup, IconButton, Input, Select } from '../ui';

/** Linhas preenchidas pela metade (só o termo ou só a fala) impedem o salvamento. */
export const halfFilledPronunciation = (rows: PronunciationEntry[]) =>
  rows.some((r) => Boolean(r.term.trim()) !== Boolean(r.say.trim()));

/** Corpo enviado ao servidor: sem linhas vazias e sem traduções em branco. */
export const cleanPronunciation = (rows: PronunciationEntry[]): PronunciationEntry[] =>
  rows.filter((r) => r.term.trim() && r.say.trim()).map((r) => ({ term: r.term.trim(), say: r.say.trim(), lang: r.lang }));

export const cleanGlossary = (rows: GlossaryEntry[]): GlossaryEntry[] =>
  rows
    .filter((r) => r.term.trim())
    .map((r) => ({
      term: r.term.trim(),
      keep: r.keep,
      note: r.note.trim(),
      translations: Object.fromEntries(Object.entries(r.translations).filter(([, text]) => text.trim()).map(([l, text]) => [l, text.trim()])),
    }));

const ROW = 'flex flex-col gap-2 rounded-lg border border-line bg-raised/40 p-2.5';

export const PronunciationEditor: React.FC<{
  value: PronunciationEntry[];
  languages: string[];
  invalid?: boolean;
  onChange: (rows: PronunciationEntry[]) => void;
}> = ({ value, languages, invalid, onChange }) => {
  const set = (index: number, patch: Partial<PronunciationEntry>) => onChange(value.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  return (
    <FieldGroup
      legend="Pronúncia"
      hint={
        <>
          Como a voz deve falar um nome ou termo (ex.: “Nvidia” → “envídia”). Vale para todas as vozes; a legenda continua como no roteiro. A
          grafia de como falar só funciona no idioma em que foi escrita: “envídia” é português, então escolha o idioma da linha e use “Todos os
          idiomas” só quando a fala servir para todos. Na voz local da prévia, números, datas, siglas, símbolos e palavras comuns em inglês já
          são lidos por extenso sem precisar de nada aqui.
        </>
      }
    >
      {value.map((row, index) => {
        const half = Boolean(row.term.trim()) !== Boolean(row.say.trim());
        return (
          <div key={index} className={ROW}>
            <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto_auto] sm:items-center">
              <Input
                aria-label={`Termo ${index + 1}`}
                value={row.term}
                maxLength={100}
                invalid={invalid && half && !row.term.trim()}
                onChange={(e) => set(index, { term: e.target.value })}
                placeholder="Como está escrito (ex.: Nvidia)"
              />
              <Input
                aria-label={`Como falar o termo ${index + 1}`}
                value={row.say}
                maxLength={200}
                invalid={invalid && half && !row.say.trim()}
                onChange={(e) => set(index, { say: e.target.value })}
                placeholder="Como falar (ex.: envídia)"
              />
              <Select aria-label={`Idioma do termo ${index + 1}`} value={row.lang} onChange={(e) => set(index, { lang: e.target.value })}>
                <option value="">Todos os idiomas</option>
                {(languages.includes(row.lang) || !row.lang ? languages : [row.lang, ...languages]).map((l) => (
                  <option key={l} value={l}>
                    {langLabel(l)}
                  </option>
                ))}
              </Select>
              <IconButton label={`Remover o termo ${index + 1}`} icon={Trash2} onClick={() => onChange(value.filter((_, i) => i !== index))} />
            </div>
          </div>
        );
      })}
      <div>
        <Button size="sm" icon={Plus} onClick={() => onChange([...value, { term: '', say: '', lang: languages[0] ?? '' }])}>
          Adicionar pronúncia
        </Button>
      </div>
      {invalid && halfFilledPronunciation(value) ? (
        <p role="alert" className="text-[13px] text-red-300">
          Preencha o termo e como falar em todas as linhas (ou remova as incompletas).
        </p>
      ) : null}
    </FieldGroup>
  );
};

export const GlossaryEditor: React.FC<{
  value: GlossaryEntry[];
  /** Idiomas do canal; o primeiro é o do roteiro. */
  languages: string[];
  onChange: (rows: GlossaryEntry[]) => void;
}> = ({ value, languages, onChange }) => {
  const targets = languages.slice(1);
  const set = (index: number, patch: Partial<GlossaryEntry>) => onChange(value.map((r, i) => (i === index ? { ...r, ...patch } : r)));
  return (
    <FieldGroup
      legend="Glossário da tradução"
      hint="Termos que não se traduzem (nome do canal, marcas) ou que têm tradução fixa. Vão no pedido de tradução e, depois de traduzir, o roteiro avisa quando um termo não foi respeitado. A tradução fixa aparece para os idiomas do canal; se um projeto traduz para outro idioma, escolha-o em “Tradução fixa em outro idioma”."
    >
      {value.map((row, index) => {
        // Idiomas do canal e os que a linha já tem (um projeto pode traduzir para idiomas fora do canal).
        const shown = [...targets, ...Object.keys(row.translations).filter((l) => l !== languages[0] && !targets.includes(l))];
        const others = LANGUAGES.filter((l) => l.code !== languages[0] && !shown.includes(l.code));
        return (
          <div key={index} className={ROW}>
            <div className="grid gap-2 sm:grid-cols-[1fr_auto] sm:items-center">
              <Input
                aria-label={`Termo do glossário ${index + 1}`}
                value={row.term}
                maxLength={100}
                onChange={(e) => set(index, { term: e.target.value })}
                placeholder={`Como está no roteiro em ${langLabel(languages[0] ?? 'pt-BR')} (ex.: Vulto)`}
              />
              <IconButton label={`Remover o termo ${index + 1} do glossário`} icon={Trash2} onClick={() => onChange(value.filter((_, i) => i !== index))} />
            </div>
            <Checkbox
              label="Não traduzir"
              description="Fica igual em todos os idiomas, menos onde houver tradução fixa abaixo."
              checked={row.keep}
              onChange={(keep) => set(index, { keep })}
            />
            {shown.length ? (
              <div className="grid gap-2 sm:grid-cols-2">
                {shown.map((l) => (
                  <label key={l} className="flex min-w-0 flex-col gap-1 text-[13px] text-muted">
                    <span className="inline-flex items-center gap-1">
                      <Languages size={14} aria-hidden="true" />
                      Em {langLabel(l)}
                    </span>
                    <Input
                      value={row.translations[l] ?? ''}
                      maxLength={100}
                      onChange={(e) => set(index, { translations: { ...row.translations, [l]: e.target.value } })}
                      placeholder={row.keep ? `${row.term.trim() || 'igual ao termo'} (sem traduzir)` : 'Tradução livre'}
                    />
                  </label>
                ))}
              </div>
            ) : null}
            {others.length ? (
              <Select
                aria-label={`Tradução fixa do termo ${index + 1} em outro idioma`}
                value=""
                onChange={(e) => {
                  if (e.target.value) set(index, { translations: { ...row.translations, [e.target.value]: '' } });
                }}
              >
                <option value="">Tradução fixa em outro idioma…</option>
                {others.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.label}
                  </option>
                ))}
              </Select>
            ) : null}
            <Input
              aria-label={`Observação do termo ${index + 1}`}
              value={row.note}
              maxLength={300}
              onChange={(e) => set(index, { note: e.target.value })}
              placeholder="Observação para o tradutor (opcional; ex.: nome do canal)"
            />
          </div>
        );
      })}
      <div>
        <Button size="sm" icon={BookA} onClick={() => onChange([...value, { term: '', keep: true, translations: {}, note: '' }])}>
          Adicionar termo
        </Button>
      </div>
    </FieldGroup>
  );
};
