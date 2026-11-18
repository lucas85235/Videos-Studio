## Bug do card: diagnóstico e correção proposta (nada foi editado)

### 1. Que card é
É o **card de ESTILO** da página /estilos (seção "Meus estilos"). Não é card de projeto: nenhum projeto nem canal usa esse estilo. Conferi numa cópia do studio.db no scratchpad (`projects.style_id` e `channels.default_style_id` sem `ref-%`).
- Componente: `StyleCard` em `apps/web/src/pages/Styles.tsx:100-141`
  - título `:111`
  - selo no canto superior direito `:112-116`: é o `<Badge>` "Meu · v1". Parece um botão, mas é só um selo, sem clique.
  - subtítulo (description) `:118`
  - chips "Longo · 16:9" e "~10 min" `:120-121`
  - "Visual: …" `:129-131`
  - "Baseado em {parentName}" `:132-133`
  - miniatura "ASSIM FICA" vem de `StyleThumb` em `apps/web/src/pages/styles/shared.tsx:399-432`
- Linha no banco: `styles.id = ref-ytdown-com-youtube-media-ht4blj6jdg-voce-vai-zerar`, `name = "YTDown.com_YouTube_Media_-hT4bLj6Jdg_VOCÊ-VAI-ZERAR-GTA-6-PELO-YOUTUBE_001_1080p (referência)"`, **version = 1**, `parent_id = curiosidades-longo`. A referência é `reference_videos.ref_x1l83ap549` com `title` = o nome do arquivo.

### 2. Onde o nome sai do nome do arquivo
1. `backend/studio/workers/visual/references.py:67`: `name = " ".join((title or Path(filename).stem).split())`. No upload sem "Nome (opcional)" (`apps/web/src/pages/References.tsx:76-81`), o título vira o stem do arquivo cru, com prefixo do site, id do YouTube e `_001_1080p`. Nada limpa isso.
2. `backend/studio/core/styles/from_reference.py:395,433`: `data["name"] = (name or f"{title} (referência)")[:200]`.
3. `from_reference.py:438`: a description "Criado a partir da referência “{title}” sobre o preset …" repete o nome do arquivo.
4. `from_reference.py:432,460-462`: o id do estilo vem do slug do mesmo título.
5. O botão de criar estilo não manda `name`: `apps/web/src/pages/references/ReferenceStyle.tsx:111` chama `styleFromReference(reference.id, { use_llm })`. A API já aceita `name` (`backend/studio/api/routes_visual.py:46-48,202`; `apps/web/src/apiVisual.ts:164`), mas a tela não tem esse campo.

### 3. Por que "não deu para mudar o nome"
O backend funciona:
- `PUT /api/styles/{id}` (`backend/studio/api/routes_misc.py:96-99`) chega em `save_user_style`, que atualiza `row.name` (`backend/studio/core/styles/library.py:165`).
- Os dados desse estilo são válidos: `StyleSpec.model_validate` passa e nenhum `formError` dispara (`apps/web/src/pages/StyleEditor.tsx:821-827`). Câmera, transições e shot_seconds estão ok.
- O banco mostra **version = 1**: nenhum salvamento aconteceu depois da criação.

O problema está na tela:
- **(a) O card não tem como renomear.** O `<Link>` inteiro só abre o editor. O único elemento no canto superior direito é o Badge "Meu · v1" (`Styles.tsx:113`), que não é clicável.
- **(b) O título passa por cima do selo.** O `<span className="min-w-0 …">` em `Styles.tsx:111` não tem `overflow-wrap`/`break-words` nem `line-clamp`. O nome tem trechos sem ponto de quebra (`_` e `.` não quebram linha), como "YTDown.com_YouTube_Media_-" (26 caracteres, cerca de 200px em 15px semibold). Esse trecho é mais largo que a coluna de texto do card (card menos o thumb de 96px, o gap e o badge), então vaza horizontalmente e fica sobreposto ao badge. É o "botão coberto" do print.
  - O mesmo defeito existe em `apps/web/src/ui.tsx:249` (h1 do `PageHeader`, que é o título do editor de estilo e o breadcrumb) e em `ui.tsx:179` (h2 do `Section`). Na página Referências (`References.tsx:144`), o título cobre os botões "Ver planos / Analisar / Apagar".
- **(c) A renomeação fica escondida no editor.** Ela está só em aba Formulário › Formato e roteiro › campo "Nome" (`StyleEditor.tsx:1389-1391`), seguida de Salvar. Nada no card nem no cabeçalho do editor aponta para lá.
- **(d) A referência não pode ser renomeada.** Não existe `PATCH /api/references/{id}` (`routes_visual.py:141-202` só tem GET/POST/DELETE/analyze/style). `References.tsx:156-179` não tem ação de renomear.
- **(e) Mesmo renomeando o estilo, o nome do arquivo continua no card**, pela description (`from_reference.py:438`) e pelo id/URL `/estilos/ref-ytdown-com-…`. O id é imutável e deve continuar assim, porque projetos e canais apontam para ele.

Detalhe: o Input do editor usa `maxLength={120}` (`StyleEditor.tsx:1390`), mas o backend gera até 200 caracteres (`from_reference.py:433`) e aceita 200 (`routes_visual.py:47`). Um nome automático entre 121 e 200 caracteres só pode ser apagado, não editado. Convém alinhar tudo em 120.

### 4. Patch proposto

**4.1 Backend: limpar o título que vem do nome do arquivo.** Testei o protótipo no scratchpad (`C:/Users/lucas/AppData/Local/Temp/claude/367e8e73d2f5afac/d95040d1-a4f5-445b-a986-df4a299967b9/scratchpad/clean_title_test.py`). Saídas:

| Entrada | Saída |
|---|---|
| `YTDown.com_YouTube_Media_-hT4bLj6Jdg_VOCÊ-VAI-ZERAR-GTA-6-PELO-YOUTUBE_001_1080p` | `VOCÊ VAI ZERAR GTA 6 PELO YOUTUBE` |
| `y2mate.com - Rick Astley …_1080p` | `Rick Astley …` |
| `Título [dQw4w9WgXcQ]` | `Título` |
| `Top-10 jogos 2024` | sem mudança |
| `gameplay_1080p60.mp4` | `gameplay` |

Novo `backend/studio/core/media_title.py`:
```python
import re
_SITE = re.compile(r"^\s*\[?(?:www\.)?(?:ytdown|y2mate|yt1s|ytmp3|ssyoutube|savefrom|snapsave|y2down|x2download|9convert|savetube)"
                   r"(?:\.[a-z]{2,4})?\]?(?:\s*-\s*|_)(?:youtube_)?(?:media_)?", re.I)
_YTID_HEAD = re.compile(r"^[A-Za-z0-9_-]{11}_(?=\S)")          # id do YouTube logo após o prefixo do site
_YTID_TAIL = re.compile(r"\s*[\[(][A-Za-z0-9_-]{11}[\])]\s*$")  # "Título [id]" do yt-dlp
_QUALITY = re.compile(r"(?:(?:[\s_]+|^)(?:\(?\d{3,4}p(?:\d{2})?\)?|hd|fhd|4k|uhd)|_\d{1,3})+\s*$", re.I)

def clean_media_title(raw: str) -> str:
    """Nome legível a partir do nome de arquivo de downloader (sem site, id, _001, 1080p, extensão)."""
    t = re.sub(r"\.(mp4|mov|mkv|webm|m4v|avi)\s*$", "", (raw or "").strip(), flags=re.I)
    t2 = _SITE.sub("", t, count=1)
    if t2 != t:
        t = _YTID_HEAD.sub("", t2, count=1)
    t = _QUALITY.sub("", _YTID_TAIL.sub("", t))
    if " " not in t:                      # nome de arquivo: _ e - são separadores
        t = re.sub(r"[_-]+", " ", t)
    t = " ".join(t.replace("_", " ").split()).strip(" -.")
    return t or (raw or "").strip()
```
Opcional: se o título estiver todo em maiúsculas, passar para "sentence case" e manter tokens com dígito ou siglas de até 3 letras (GTA). Sem isso, a saída fica "VOCÊ VAI ZERAR GTA 6 PELO YOUTUBE (referência)".

`backend/studio/workers/visual/references.py`:
```diff
-    name = " ".join((title or Path(filename).stem).split())[:300] or "Referência"
+    name = " ".join((title or clean_media_title(Path(filename).stem)).split())[:300] or "Referência"
```
Também `:114-115`: aplicar `clean_media_title(title)` no título do yt-dlp, por garantia.

Novo `rename_reference(ref_id, title)` no mesmo arquivo:
```python
def rename_reference(ref_id: str, title: str) -> dict[str, Any]:
    name = " ".join(title.split())[:300]
    if not name:
        raise ValueError("O nome não pode ficar vazio.")
    return _update(ref_id, title=name)
```

`backend/studio/api/routes_visual.py`, nova rota:
```python
class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)

@router.patch("/references/{ref_id}")
def rename_reference(ref_id: str, body: RenameBody) -> dict[str, Any]:
    try:
        return references.rename_reference(ref_id, body.title)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="referência não encontrada") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

`backend/studio/core/styles/from_reference.py`, defesa para referências antigas:
```diff
-    title = str(reference.get("title") or "Referência")          # :395 e :460
+    title = clean_media_title(str(reference.get("title") or "")) or "Referência"
-    data["name"] = (name or f"{title} (referência)")[:200]        # :433
+    data["name"] = (name or f"{title} (referência)")[:120]       # = maxLength do editor
```
O slug do id (`:432`, `:462`) já sai limpo porque usa o mesmo `title`.

Dados existentes (1 referência e 1 estilo): uma migração de dados Alembic, ou um comando no `cli.py`.
- `reference_videos.title = clean_media_title(title)` quando o valor muda.
- `styles` com tag "referencia", `version == 1` e `name == f"{old} (referência)"`: atualizar `name`, `data.name` e `data.description` (trocar `old` por `new`).
- Não mexer no id.

**4.2 Front: o título não cobre mais o selo nem os botões.**

`apps/web/src/pages/Styles.tsx:111`:
```diff
-<span className="min-w-0 font-display text-[15px] font-semibold leading-snug text-fg">{s.name}</span>
+<span title={s.name} className="line-clamp-2 min-w-0 font-display text-[15px] font-semibold leading-snug text-fg [overflow-wrap:anywhere]">{s.name}</span>
```
- No mesmo arquivo, `:118`: `line-clamp-2 [overflow-wrap:anywhere]`. Na description, o nome do arquivo também não quebra.
- `:178` (GeneratedResult): `[overflow-wrap:anywhere]`.
- `apps/web/src/ui.tsx:249` (h1) e `:179` (h2 do Section): acrescentar `[overflow-wrap:anywhere]`. Isso corrige o título do editor e o da página Referências, onde o título cobria os botões. O Section já tem grid `minmax(0,1fr)_auto`, então basta a quebra.

**4.3 Front: renomear à vista.**

Editor (`apps/web/src/pages/StyleEditor.tsx`):
- `:1390`: `<Input id="estilo-nome" …>`.
- `headerActions` (perto de `:1128`), quando não for preset: botão ghost "Renomear" (ícone `Pencil`) com `onClick={() => { goToSection('formato'); setTimeout(() => document.getElementById('estilo-nome')?.select(), 0); }}`. Se `tab === 'json'`, chamar `changeTab('form')` antes. O salvamento continua no Salvar / Ctrl+S, que já funciona.

Card (`apps/web/src/pages/Styles.tsx`):
- Em vez do selo "Meu · v{n}" isolado, colocar um link "Renomear" abaixo dos chips, apontando para `/estilos/${s.id}#formato`. Não pode haver `<button>` dentro do `<Link>`.
- Alternativa: tirar o `<Link>` de volta do card inteiro e pôr o link só no título, com um botão "Renomear" ao lado que abre inline um `Input` e faz `PUT /api/styles/{id}` com `{ style: { ...s, name } }`.

Criação a partir da referência (`apps/web/src/pages/references/ReferenceStyle.tsx:109-135`):
- Adicionar `<Field label="Nome do estilo">`, com `Input maxLength={120}` pré-preenchido com `` `${reference.title} (referência)` `` (o título já limpo).
- Enviar `visualApi.styleFromReference(reference.id, { use_llm: useLlm, name: name.trim() || undefined })`. O tipo já aceita `name` (`apps/web/src/apiVisual.ts:164`).

Página Referências (`apps/web/src/pages/References.tsx:156-179`):
- Botão "Renomear" ao lado de "Analisar". Ele abre um Input inline e chama a nova `visualApi.renameReference(id, title)` = `api.patch('/api/references/${id}', { title })`, depois `refresh()`.

**4.4 Teste sugerido.** `backend/tests/test_references.py` (ou similar) com:
- os 6 casos de `clean_media_title` acima;
- `save_upload(..., filename="YTDown.com_YouTube_Media_-hT4bLj6Jdg_VOCÊ-VAI-ZERAR-GTA-6-PELO-YOUTUBE_001_1080p.mp4")`, que deve resultar em `title == "VOCÊ VAI ZERAR GTA 6 PELO YOUTUBE"`;
- `build_style(..., use_llm=False)` deve dar `name` sem "YTDown" e com no máximo 120 caracteres;
- `PATCH /api/references/{id}` deve devolver 200 e o título novo; com vazio, 422.

Ressalva: não reproduzi o clique do usuário no app. A conclusão de que o salvamento nunca aconteceu vem de `version = 1` no banco. A sobreposição foi deduzida do CSS (`Styles.tsx:111` sem quebra, mais um trecho de 26 caracteres sem ponto de quebra), sem screenshot.