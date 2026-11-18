# Renderer (renderer/src) e timeline (backend/studio/core/timeline.py): inventário e lacunas

Só li arquivos, nada foi alterado. O canais foi lido em C:/Users/lucas/canais/plantao-vice/video/src.

## 1. Como uma cena é montada hoje
`SceneView.tsx:183-238` monta cada cena com camadas fixas, de baixo para cima:
1. Um fundo, só um destes: `DocumentCard`, `ClipLayer`, `TreatmentLayer` ou `VisualLayer`.
2. `EffectsLayer`.
3. Um cartão por tipo de cena.
4. `NewsCard`, `Headline`, `Stamp`, `SourceChip`.
5. `PresenterLayer`.
6. `Captions`.
7. `TransitionIn`.

Cada cena tem um fundo e um cartão. Não existe lista de eventos dentro da cena ancorados em palavras. Só três coisas entram na hora de uma palavra:
- `reveal` dos cartões de dado (`timeline.py:411-415`)
- `stamp_from` (`timeline.py:416-423`)
- `presenter.from` (`timeline.py:775-785`)

O canais fazia isso em tudo: `at(key)` por palavra em todo elemento (`Episode.tsx:86`), vários overlays por cena e `<Sfx>` por palavra (`Episode.tsx:139`). Essa é a maior diferença estrutural.

## 2. Primitivas: existe? quem escolhe?

**Fundos e camadas visuais**
- **Imagem com câmera:** existe (`VisualLayer.tsx:64-270`). A imagem vem do storyboard (LLM): `source` new/reuse/none mais `prompt` (`schemas.py:87-91`). O padrão é imagem de IA: `Visual.type="ai_image"` (`document.py:206`) e `new_image_ratio=0.5` (`schema.py:335`). Este é o motor da "cara de IA".
- **Tipos de cena:** image, text, title, count, document, quote, video, stat, compare, chart, map (`types.ts:15`). O LLM escolhe entre os de `STORYBOARD_KINDS` (`schemas.py:56`). `video` e `document` ficam de fora do LLM.
- **Tratamentos lousa/colagem/tela:** existem (`Treatments.tsx:146`, `:248`, `:401`). São sorteados pela semente com os pesos do estilo (`timeline.py:931-940`). A "tela" é interface falsa, "nunca tem texto legível" (`Treatments.tsx:344`), o que reforça o visual de template.
- **Fundo sem imagem:** gradiente animado genérico `MotionBackground` (`DataCards.tsx:863`).
- **Parallax 2.5D:** existe (`VisualLayer.tsx:178-213`). Só entra se o usuário escolher; não está em `AUTO_CAMERA_MOVES` (`schema.py:82`).
- **Reenquadramento 16:9→9:16 (crop/blur):** existe (`frame.ts`, `VisualLayer.tsx:231-263`).

**Câmera**
- **Movimentos:** ken_burns_in/out, pan_left/right/up/down, punch_zoom, drift, zoom_to, static, parallax (`VisualLayer.tsx:83-124`).
- **Quem escolhe:** `scene.motion` (o LLM sugere em `schemas.py:95`, depois edição em `set_scene_motion`, `schemas.py:171`) ou sorteio pelos pesos `edit.camera` (`timeline.py:701-705`). O padrão é só Ken Burns e pan (`schema.py:337`).
- **Mídia real** só sorteia movimentos suaves (`timeline.py:68`, `:634`).
- **Ponto de foco:** 9 pontos (`types.ts:18-30`).
- **Zoom-punch:** é o movimento `punch_zoom`, mas só uma mola no quadro 0 da cena (`VisualLayer.tsx:103-107`). Não existe punch-in na palavra nem vários punches por cena.
- **Shake:** não existe. Há só um tremor horizontal do glitch e do vhs (`VisualLayer.tsx:143-150`).

**Transições**
- cut, flash, fade_black, glitch, light_leak, zoom_blur, film_burn, static_burst (`TransitionIn.tsx:18-87`, `schema.py:83`).
- São só de entrada, sobrepostas no início da cena nova (`TransitionIn.tsx:6-9`). Não há whip-pan, slide, push ou zoom-through entre duas cenas.
- **Quem escolhe:** sorteio por `edit.transitions` (padrão cut 6 / flash 1, `schema.py:338`) ou `scene.transition_in` pela edição (`schemas.py:172`). A primeira cena é sempre corte (`timeline.py:822-825`).

**Legendas e texto cinético**
- **Legendas:** karaoke, word ou line (`Captions.tsx:147-240`), com quebra esperta (`timeline.py:207-346`).
- O destaque da palavra ativa é tímido: sobe 6% (`Captions.tsx:228`); no modo palavra, escala de 0,7 a 1 (`:207-209`).
- Há um estilo único para o vídeo inteiro (`timeline.py:990`). A ênfase não muda por palavra-chave, não há emoji nem cor por palavra escolhida pelo LLM.
- **`KineticText`:** só como cena inteira do tipo `text`, com o fundo escurecido a 0,62 e desfocado (`SceneView.tsx:42-44`, `TextCards.tsx:126-171`). O destaque vai por heurística: palavra de 7 letras ou mais, ou número (`TextCards.tsx:138`). Não existe "texto grande que pula" por cima da imagem em palavra-chave.

**Cartões de texto**
- Title, count, quote e document existem (`TextCards.tsx:45`, `:64`, `:86`, `:174`).
- O marca-texto do `DocumentCard` é falso: posição fixa em 62% da altura (`TextCards.tsx:196`).
- Entradas fade_up, typewriter, pop, slide e flicker, uma por estilo (`TextCards.tsx:7-26`).

**Overlays**
- `Headline` (faixa do topo, `DataCards.tsx:713`), `Stamp` (carimbo, `:774`) e `SourceChip` (`:825`). Vêm do LLM: `headline`/`stamp` (`schemas.py:101-102`).
- `NewsCard` é desenhado pelo app, "nunca captura de tela" (`RealMedia.tsx:56-61`). Não tem marca-texto nem câmera indo até o trecho.
- `page_capture.py:1-8` gera a prova com marca-texto queimado no PNG. O renderer mostra isso como imagem comum com Ken Burns. Não há o equivalente ao `WebShot` do canais (`web.tsx:29-87`: câmera indo ao retângulo, marca-texto animado por palavra, som de obturador e whoosh).

**Efeitos de imagem**
- grain, vignette, scanlines, vhs, flicker, dust, film_scratches, light_leak, halftone, duotone, blur_edges (`EffectsLayer.tsx:22-200`).
- grade com 11 presets via CSS (`look.ts:6-40`), chromatic via filtro SVG (`VisualLayer.tsx:282-308`), sepia (`look.ts:55`).
- LUT `.cube` aplicado antes do render (`schema.py:99`, `build.py:955`).
- **Quem escolhe:** fixo por estilo para o vídeo inteiro (`timeline.py:970-981`). A direção de arte só troca o preset do grade. O padrão é grain + vignette (`schema.py:340`); "grão + vinheta + Ken Burns" é a assinatura de canal faceless com IA.

**Gráficos e mapas**
- Stat, compare, chart (bar/line/pie/donut) e map existem (`DataCards.tsx:399`, `:510`; `ChartCard.tsx:362`; `MapCard.tsx:53`).

**SFX**
- Não há `<Audio>` no renderer. O som é mixado no FFmpeg a partir de `timeline.sfx` (`build.py:1213-1238`).
- São só 5 sons sintéticos: whoosh, impact, glitch, static, riser (`audio.py:344`, `:401-445`).
- `sfx.pack="basic"` não é usado em lugar nenhum (`schema.py:392`).
- `Scene.sfx` existe (`document.py:357`), mas nada no backend o preenche.
- **Escolha automática:** som pela transição, impacto nos cartões, faixa do topo e carimbo (`timeline.py:43-50`, `:431-461`). Nunca há som numa palavra, nem meme sonoro.
- O canais tinha 27 arquivos .wav (pop, ding, rimshot, trombone, scratch, cheer, whip, shutter, type, stamp…) em `plantao-vice/video/public/sfx`, tocados com `<Sfx>` por palavra (`kit.tsx:14-15`).

**Música e ducking**
- Procedural, biblioteca ou nenhuma (`schema.py` MusicSpec). Ducking com sidechaincompress (`audio.py:490`). Camas por faixa (`build.py:1021-1137`). Está bom.

**Avatar e reação**
- `PresenterLayer` mostra poses do próprio avatar, modo reação ou explica (`PresenterLayer.tsx:45-89`). O gatilho é uma palavra (`timeline.py:748-815`). Some no vertical (`PresenterLayer.tsx:53`).

**Vídeo real, gameplay e cartelas**
- **Vídeo:** `ClipLayer` só toca o vídeo de IA pago da cena `kind='video'` (`ClipLayer.tsx:32`, `SceneView.tsx:132`, `types.ts:121`, `video_clips.py:1`).
- Não existe fonte de vídeo real ou gameplay: `Visual.type` é `ai_image|stock|ai_video|upload|none` e a mídia real é só `photo|news|official` (`document.py:206`, `:27`).
- O clipe sai sempre mudo, sem ponto de entrada nem montagem de vários planos dentro da cena.
- O canais tinha `Reel`/`Montage`, com vários planos que entram na palavra, flash e som de corte (`media.tsx:14-68`).
- **Captura de tela real:** só como imagem estática (ver Overlays).
- **Cartelas:** só o título de capítulo (`TitleCard`). Não há sting, cartela final, botões de inscrever/curtir nem aba de capítulo; o canais tinha `chapters.tsx` e `cta.tsx`.

**O que não existe de jeito nenhum**
- Split screen (o canais tinha `Duo`, `media.tsx:115`).
- Picture-in-picture (o canais tinha `InsetCard`, `media.tsx:250`).
- Setas e círculos de destaque.
- Memes, reações de meme e stickers. O contorno de adesivo existe só para o avatar (`stickerFilter`, `PresenterLayer.tsx:29`).
- Congelar imagem, rampa de velocidade, rewind e zoom dramático.
- Emoji nas legendas.

## 3. Por que a referência só pega o superficial
O `EditSpec` (`schema.py:332-350`) só tem pesos de câmera, transição, efeito e tratamento, mais legenda, cartões, `shot_seconds` e `zoom_strength`. Não há nenhum campo de densidade de edição: punch por minuto, meme por minuto, SFX por minuto, ênfase de legenda, máximo de segundos por plano.

A análise de referência mede cortes, folha de contato, paleta e `cuts_per_min` (`reference_video.py:143-263`). Não analisa áudio (onsets de SFX), nem texto na tela (OCR), nem detecta zoom ou meme.

Mesmo uma análise perfeita não teria onde gravar nem o que o renderer desenhar.

## 4. O que falta para a edição "YouTuber BR de curiosidades/games"
Em ordem de impacto:

1. **Faixa de eventos por cena ancorada em palavra.** Ex.: `scene.fx: [{type, at, dur, params}]`, resolvida na timeline com o mesmo `trigger_index`/`parse_at` do avatar (`timeline.py:775-785`). Chave nova só quando preenchida, para manter o hash dos blocos (padrão já usado em `timeline.py:564-576`). No renderer, uma `FxLayer` entre `EffectsLayer` e `Captions` (`SceneView.tsx:213-236`). Tudo abaixo depende disso.
2. **Câmera por keyframes.** Um contexto de câmera que `VisualLayer`, `ClipLayer` e `TreatmentLayer` leiam, com:
   - punch-in de 2-3 quadros na palavra (1,0→1,2, segura, solta);
   - shake com decaimento no impacto;
   - zoom no retângulo da prova ou do foco.

   Hoje `punch_zoom` é só no quadro 0 (`VisualLayer.tsx:103-107`).
3. **SFX por biblioteca de arquivos e por palavra.** O `_sfx_event` aceita qualquer `type` (`timeline.py:426`), mas o `generate_sfx` cai em whoosh para tipo desconhecido (`audio.py:445`). Falta resolver nomes para arquivos da biblioteca (vine boom, bruh, pop, ding, record scratch, rimshot, trombone, shutter, type, whoosh, boom), com o LLM ou o estilo marcando a palavra. Dá para manter a mixagem no FFmpeg.
4. **Memes e reações.** Biblioteca local (PNG com alfa, GIF/WebM com alfa ou chroma, clipe curto com som), num overlay que entra com mola, com contorno de adesivo (o `stickerFilter` já existe), posição e saída. Variantes "congela + zoom + vine boom" e "zoom dramático".
5. **Texto grande que pula sobre a imagem.** O LLM marca a palavra-chave e entram uma ou duas palavras gigantes com bounce, rotação e cor de destaque por cima do fundo, sem escurecer. A legenda ganha ênfase por palavra (cor, escala 1,3, emoji). Hoje o cinético é uma cena inteira escurecida (`SceneView.tsx:42`).
6. **Print de notícia/página com marca-texto animado.** Usar os `rects` que `page_capture.py:159` já mede (hoje queimados no PNG), com câmera até o trecho, marca-texto varrendo na palavra e som de obturador. Porte melhorado do `WebShot` do canais.
7. **Clipes reais e gameplay.**
   - `Visual.type` de vídeo real (upload, link ou trecho com entrada/saída) tocado pelo `ClipLayer` em qualquer tipo de cena, com início (`startFrom`), volume opcional e montagem de vários planos trocando na palavra (como `Montage`, `media.tsx:50`).
   - Pipeline de corte de clipes, como o `make_clips` do canais.
   - Isso troca a imagem de IA como padrão: hoje a regra é `new_image_ratio` com fallback em `ai_image`.
8. **Split screen, PiP, setas e círculos:** componentes novos ancorados ao foco ou ao retângulo.
9. **Ritmo de cortes.** Hoje uma cena equivale a uma imagem, com 2-3,5 s (`schema.py:333`), e o número de cortes fica preso ao número de imagens de IA. Falta permitir vários planos dentro da cena (jump cut, reaproveitar com outro enquadramento, B-roll) e transições entre duas cenas (whip-pan, slide), não só overlay de entrada.
10. **Efeitos por cena, não por vídeo.** Hoje é um conjunto fixo (`timeline.py:970`). Grain, vignette e grade "cinema" em tudo empurram o visual de IA. Para curiosidades e games: imagem limpa, saturada, com efeito só como pontuação.
11. **Cartelas e pacote do canal:** sting de capítulo, botão de inscrever/curtir animado, cartela final e aba de capítulo. Dá para portar `chapters.tsx` e `cta.tsx` do canais, parametrizados pelo estilo.

## Arquivos-chave
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/renderer/src/components/SceneView.tsx
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/renderer/src/components/VisualLayer.tsx
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/renderer/src/components/TransitionIn.tsx
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/renderer/src/components/EffectsLayer.tsx
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/renderer/src/components/Captions.tsx
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/renderer/src/components/TextCards.tsx
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/renderer/src/components/ClipLayer.tsx
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/renderer/src/components/RealMedia.tsx
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/renderer/src/components/Treatments.tsx
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/renderer/src/types.ts
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/backend/studio/core/timeline.py
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/backend/studio/core/styles/schema.py
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/backend/studio/workers/pipeline/schemas.py
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/backend/studio/media/audio.py
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/backend/studio/media/page_capture.py
- C:/Users/lucas/Documents/Projects/YouTube/Videos-Studio/backend/studio/media/reference_video.py
- Canais (referência): C:/Users/lucas/canais/plantao-vice/video/src/media.tsx, web.tsx, kit.tsx, Episode.tsx, chapters.tsx, cta.tsx
