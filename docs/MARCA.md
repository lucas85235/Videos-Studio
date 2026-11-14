# Vulto: marca e sistema visual

## Nome

**Vulto**: figura que se vê sem distinguir as feições. É o criador de um canal sem rosto: a presença aparece, o rosto não. Em português, "de vulto" também quer dizer "de peso, importante".

- **Slogan**: *Seu canal aparece. Seu rosto, não.*
- **Descritor** (abaixo do nome, quando houver espaço): *estúdio de vídeos sem rosto*.
- **Como escrever**: "Vulto" (só a inicial maiúscula) em textos; o logotipo usa VULTO em caixa alta.
- Escolhido em 14/11/2026 entre 13 candidatos, pontuados por três juízes: memória e pronúncia, encaixe com o produto e risco de conflito. Vulto empatou com Breu na nota e ganhou pelo encaixe com "sem rosto". Não há app de vídeo ou IA com esse nome; existe um jogo de nicho no Steam ("Vulto") e nomes parecidos de outras áreas (Vultr, nuvem).
- **Identificadores internos não mudam**: o pacote e o comando `videos-studio`, a pasta de dados `VideosStudioData` e o repositório continuam com os nomes antigos, para não quebrar instalações.

## Símbolo

Busto em silhueta (cabeça e ombros, sem rosto) recortado num quadrado arredondado âmbar. Em 16 px vira uma forma de fechadura que continua legível.

```svg
<svg viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg">
  <defs><clipPath id="v"><rect width="32" height="32" rx="8"/></clipPath></defs>
  <g clip-path="url(#v)">
    <rect width="32" height="32" fill="#F5A524"/>
    <circle cx="16" cy="13" r="5.5" fill="#0B0B0E"/>
    <path d="M5 33C5 24 9.9 19.9 16 19.9S27 24 27 33Z" fill="#0B0B0E"/>
  </g>
</svg>
```

- Sobre fundo escuro: o quadrado âmbar com a silhueta escura (acima).
- Logotipo: símbolo + **VULTO** em Montserrat 800, espaçamento de letras 0,14 em, cor `fg`.
- Área de respiro: metade da altura do símbolo em volta. Não aplicar sombra, contorno nem degradê.

## Cores (tokens em `apps/web/src/index.css`, bloco `@theme`)

| Token | Hex | Uso |
|---|---|---|
| `canvas` | `#09090B` | fundo do app |
| `panel` | `#0F0F13` | barra lateral, cabeçalhos fixos |
| `card` | `#15151A` | cartões |
| `raised` | `#1C1C22` | campos, menus, itens elevados, hover de cartão |
| `line` | `#26262E` | bordas |
| `line-strong` | `#34343E` | bordas em hover/foco de cartão, divisórias fortes |
| `fg` | `#EDEDF2` | texto principal |
| `muted` | `#A9A9B5` | texto secundário (contraste AA sobre `card`) |
| `subtle` | `#8B8B97` | dicas e metadados (mínimo 12 px; contraste ≥ 4,5:1 sobre `canvas`) |
| `accent` | `#F5A524` | ação principal, item ativo, foco, progresso |
| `accent-strong` | `#FFB84D` | hover da ação principal |
| `on-accent` | `#141005` | texto sobre `accent` |

Estados: sucesso `emerald-400`, aviso `amber-300`, erro `red-400`, informação `sky-400`, sempre com fundo da mesma cor a 10–15% e borda a 30–40%.

O âmbar é a única cor de destaque: aparece na ação principal de cada tela, no item ativo da navegação, no foco do teclado e no progresso. Não use âmbar em decoração.

## Tipografia

- **Interface**: IBM Plex Sans (`font-sans`), 14 px no corpo, 13 px em metadados; nada abaixo de 12 px.
- **Títulos e marca**: Montserrat (`font-display`), semibold ou bold. H1 de página com 22–24 px.
- Números que mudam (progresso, tempos, contadores) em `tabular-nums`.
- As duas fontes já vêm no app (`assets/fonts`, servidas em `/fonts`).

## Forma e espaçamento

- Raio: cartões `rounded-xl` (12 px), botões e campos `rounded-lg` (8 px), selos e chips `rounded-md` (6 px).
- Espaçamento em múltiplos de 4 px. Páginas com respiro de 24–32 px e largura máxima conforme o conteúdo (listas 1200 px, formulários 960 px).
- Sombras quase nunca: o relevo vem das superfícies (`canvas` → `card` → `raised`) e das bordas.

## Ícones

`lucide-react` (1.65.0), traço 1,75–2, 16 px em botões e navegação e 20 px em estados vazios. Ícone sozinho só com `aria-label` e dica (`title`). Mapa de referência:

| Conceito | Ícone |
|---|---|
| Projetos | `Clapperboard` |
| Novo projeto | `Plus` |
| Agenda | `CalendarClock` |
| Estilos | `Palette` |
| Canais | `Tv` |
| Configurações | `Settings` |
| Roteiro | `FileText` |
| Cenas | `LayoutGrid` |
| Prévia | `MonitorPlay` |
| Vídeo e publicação | `Film` / `Upload` |
| Versões | `History` |
| Piloto automático | `Wand2` |
| Aprovar | `Check` / `CircleCheck` |
| Aviso / erro | `TriangleAlert` / `CircleX` |
| Voz / música | `AudioLines` / `Music` |
| Travar | `Lock` / `LockOpen` |
| Apagar | `Trash2` |
| Copiar / abrir pasta / link externo | `Copy` / `FolderOpen` / `ExternalLink` |

## Princípios de usabilidade

1. **Próximo passo sempre à vista**: cada tela de projeto diz em que etapa o vídeo está e qual é a ação principal (um único botão âmbar).
2. **Nada some sem aviso**: jobs em andamento aparecem na barra lateral; quando terminam ou falham, um aviso aparece em qualquer página.
3. **Gasto de cota é explícito**: ações que geram voz, imagens ou chamam o Claude dizem isso perto do botão.
4. **Português claro, sem ids técnicos**: nada de `ken_burns_in`, `seg_ab12` ou `api_key` na interface; use rótulos e nomes humanos.
5. **Teclado e foco**: todo controle tem foco visível em âmbar; abas e grupos de botões com semântica ARIA.
6. **Ações destrutivas confirmam** num diálogo do app (não `window.confirm`) e dizem o que se perde.
