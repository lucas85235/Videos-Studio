## Pipeline visual do Vulto: por que sai com cara de IA

Fiz só leitura, não mudei nada. Os caminhos são relativos a `backend/studio/`, menos onde está indicado. Os números de linha batem com o working tree de agora.

### 1. Do roteiro à timeline

1. **Batidas** (`workers/pipeline/authoring.py:343` `plan_beats` → `core/text.py:190` `split_beats`): a narração é cortada **por frase**.
   - Frase mais curta que `shot_seconds.min` é colada na seguinte.
   - Só frase com mais de 1,6×max é dividida.
   - Resultado: **1 batida = 1 cena = 1 visual**. A duração da cena é a da frase falada.
2. **Orçamento de IA** (`authoring.py:338`): `image_budget = max(3, ceil(n_batidas × edit.new_image_ratio))`. O padrão é 0,5 (`core/styles/schema.py:335`).
3. **Storyboard pelo LLM** (`authoring.py:1185` `storyboard`, `STORYBOARD_SYSTEM` em `:216`, pedido em `:402`, schema em `workers/pipeline/schemas.py:63`).
   - Fontes possíveis: só `STORYBOARD_SOURCES = ["new","reuse","none"]` (`schemas.py:58`).
   - Tipos: `["image","stat","compare","title","text","quote","count","chart","map"]` (`schemas.py:56`). `video` e `document` ficam fora.
   - **Não existe fonte "real", "clipe" nem "captura".** Mídia real é só uma *dica* em campos achatados: `real_kind/real_query/real_note` (`schemas.py:110`).
4. **Normalização determinística** (`authoring.py:788` `_scenes_from`):
   - `image` sem `reuse` válido vira `"new"` se ainda cabe no orçamento (`:849-854`).
   - Todo cartão ganha de fundo a última imagem nova de IA, desfocada (`:859-862`). Isso vale **mesmo se o LLM pedir fundo liso** (`"none"`).
   - Prompt vazio vira o texto da própria batida (`:867`).
   - A dica de mídia real é cortada para `max(2, ceil(n/3))` (`REAL_SHARE`, `:651-661`).
   - Imagem nova acima do orçamento vira reuse (`_enforce_budget`, `:1016`).
   - Câmera padrão: reuse = `zoom_to`, gancho = `punch_zoom`, cena sem imagem = `static` (`:897-902`).
5. **Revisão** (`workers/pipeline/storyboard_check.py`): se o gancho não for imagem, ele é **convertido à força em imagem de IA** (`:380-399`, `visual.type="ai_image"`, prompt = `art.hook`).
6. **Busca de mídia real (depois do storyboard)**:
   - `workers/tasks.py:484` `_prefetch_media` busca fotos livres (Commons/Openverse/Pexels).
   - `:491-497` dispara `media_find` (Claude Code + WebSearch, só para `official`/`news`, no máximo 8 cenas por chamada, `media_find.py:40`).
   - **Os dois só gravam candidatos** em `RealHint.found` (`media_find.py:392`).
   - **Nada anexa sozinho.** Nem o piloto automático: `tasks.py:731` chama o storyboard com `find_inline=True`, mas não anexa. Anexar só acontece pela rota `api/routes_media.py:129`, com clique do usuário.
7. **Assets** (`workers/pipeline/build.py:796` `image`): ordem de prioridade.
   - `visual.media` (mídia real anexada) → `visual.asset` → reuse → **IA**.
   - A IA usa `registry.run_chain("image", style.art.image_chain)` (`:831`). Cadeia padrão: `["codex_cli","antigravity_cli"]` (`schema.py:256`).
   - Prompt: `compose_directed_prompt` (`build.py:171`).
   - Vídeo IA vem só do cache (`build.py:1509`).
8. **Timeline** (`core/timeline.py:464`):
   - Cena = trecho de palavras da batida.
   - Câmera = `scene.motion` ou sorteio pelos pesos do estilo (`:701-705`). Mídia real sorteia só entre `ken_burns_in/out` e `drift` (`:68`, `:634`).
   - Transição sorteada (`:825`). SFX só nas transições (`:43`).

### 2. Por que o resultado puxa para IA generativa

- **A IA é a fonte padrão e única que o storyboard produz sozinho.** No pedido, `image` é definido como *"source "new" = imagem nova de IA (~35 s de cota cada…)"* (`authoring.py:248`). A mídia real é tratada como enfeite opcional:
  - *"Só em kind image, no máximo 1 a cada 3 batidas"* (`:278`).
  - *"A cena continua com o prompt de IA de reserva (o vídeo funciona sem a mídia real)"* (`:279`).
  - *"O app não baixa nada sozinho… o usuário escolhe"* (`:269-271`).
- **Teto duro para o real e piso para a IA.** O real fica limitado a 1/3 (`authoring.py:651`). A IA tem mínimo de 3 e ocupa 50% das cenas por padrão. Os outros ~50% são reuse da *mesma* imagem de IA (`zoom_to`) ou cartão **sobre imagem de IA desfocada** (`:261`, `:859-862`). Na prática, **100% da área de imagem do vídeo vem de IA** quando o usuário não anexa nada à mão.
- **O real é decidido às cegas.** O LLM marca a dica *antes* de existir qualquer acervo. A busca vem depois e não volta para o storyboard. Quando a busca falha, nada muda: a cena fica com a IA, sem aviso de "faltou real" na prévia. A contagem `real_hints` aparece só na revisão (`storyboard_check.py:235`).
- **O gancho é obrigatoriamente IA**: regra 3, *"A primeira batida é o gancho: image, source "new"…"* (`authoring.py:293`), mais a conversão forçada em `storyboard_check.py:380-399`. É justamente o quadro em que o real (trailer, print, notícia) mais vale.
- **O prompt proíbe o assunto real.** Regra 6 (`authoring.py:305`): *"Nada de logotipo, personagem oficial, … interface de jogo, "screenshot", "gameplay", "leak" ou cena de trailer"*, e personagem oficial só *"de costas, em silhueta"*. A mesma coisa entra em toda imagem via `ORIGINAL_ONLY` (`build.py:163`). Num vídeo de GTA 6, a IA desenha "cidade tropical genérica com figura de costas". Esse é o clichê de IA mais reconhecível.
- **Estética de ilustração em todos os presets.** Nenhum preset liga `art.photoreal`, então a linguagem `photo` sai do catálogo (`core/styles/visual.py:229-232`, `schema.py:274-278`). As linguagens são `key_art`/`editorial`/`render_3d`/`painting`/`isometric` (`visual.py:50-62`). A bible padrão é *"ilustração digital detalhada e limpa, cores naturais e equilibradas, luz suave, composição clara com um sujeito em destaque"* (`schema.py:253`). Somam-se `Accent colors` e `Treatment` iguais em todas as imagens (`build.py:191-200`) e a regra de variar hora e luz (`authoring.py:302`). Sai uma série de "pinturas digitais limpas" coerentes entre si, que é exatamente a assinatura de IA.
- **Código morto que puxaria para o real:**
  - `ArtSpec.sources_mix = {"ai_image": 1.0}` (`schema.py:262`) não é lido em lugar nenhum do backend; só aparece no `StyleEditor.tsx:134`.
  - `Visual.type="stock"` (Pexels) existe em `build.py:664`, mas o storyboard nunca o gera.
  - Captura de página (`workers/visual/proofs.py`, portada do canais) só roda por rota manual (`api/routes_visual.py:109`). O storyboard nunca sugere prova.
- **Custo e ordem.** A cadeia é só de geradores de IA. Os provedores de busca real não fazem parte da cadeia de imagem. Não existe "real → stock → IA".

### 3. Visuais que não são IA e quanto são usados

| Tipo | Onde | Uso real |
|---|---|---|
| Cartões stat/compare/title/text/quote/count | `authoring.py:248-262` | até 45% das cenas (`storyboard_check.py:47`), mas com fundo de IA desfocado |
| chart/map | `STORYBOARD_DATA_RULES` (`authoring.py:313`) | só com família `data_geo`: 1 preset (`dados-e-mapas`). Fora dela, chart vira compare e map vira stat (`authoring.py:732-735`) |
| Mídia real (photo/news/official) | `media/real_media.py`, `RealMedia.tsx` | ≤1/3, **só com anexo manual** |
| Prova / captura de página | `workers/visual/proofs.py` | só manual |
| Parallax 2.5D | `timeline.py:70` | excluído do automático (`schema.py:82`); ainda é a mesma imagem de IA |
| Tratamentos whiteboard/collage/screen | `schema.py:121-127` | variação de render em cima da imagem de IA |
| Vídeo IA | `video_clips.py`, `ClipLayer.tsx` | 2 por Short / 6 por longo, pago, só do cache |
| **Clipe de vídeo real (trailer/gameplay)** | — | **não existe.** `MediaRef` é só JPEG (`core/project/document.py:48-78`) |

`new_image_ratio` por preset (`presets/styles/*.yaml`): padrão 0,5 em 13 presets. Exceções: `dados-e-mapas` 0,35, `explicador-vetorial` 0,4, `interface-tela` 0,45, `games-cultura-pop-short` 0,5, `top10-listas` 0,6, `whiteboard-explicador` 0,6. `photoreal`: nenhum. Linguagens dominantes: `painting`, `editorial`, `key_art`, `render_3d`, `archival`.

### 4. Sinais de cara de IA no resultado

- **Uma imagem por frase**, com corte sempre na fronteira da frase (`core/text.py:190`). O ritmo fica metronômico: 1,8–3 s no Short, 3,5–7 s no longo. Nada de vários planos dentro de uma frase, nem corte na palavra de ênfase.
- **Ken Burns em tudo.** Toda cena de imagem tem movimento. Os pesos são dominados por `ken_burns_in/out` e `pan` em todos os presets; o padrão é `{"ken_burns_in":3,"ken_burns_out":2,"pan_left":1,"pan_right":1}` (`schema.py:337`). O easing é sempre `inOut(quad)` (`renderer/src/components/VisualLayer.tsx:68`), com `zoom_strength` de 0,06 a 0,14. É o "zoom lento suave" clássico de slideshow de IA. Até a foto real só pode ter Ken Burns ou drift (`timeline.py:68`).
- **Reuse com `zoom_to`** na mesma imagem por até 3 cenas ou 8 s (`storyboard_check.py:52-53`): o mesmo quadro reenquadrado várias vezes.
- **Transições:** ~75% `cut`, o resto flash/zoom_blur/glitch, sorteados por semente sem relação com o conteúdo (`timeline.py:825`). SFX só de transição (`timeline.py:43-50`): não há SFX de ênfase, de meme nem de "punch".
- **Legenda padrão de Short de IA:** karaokê, centro, MAIÚSCULAS, Montserrat 800, destaque amarelo #FFD400, contorno preto 6 (`schema.py:309-322`), usada em todos os presets de Short.
- **Imagens homogêneas:** mesma linguagem, mesmo "world" anexado a toda imagem (`authoring.py:224-225`), mesmas cores de destaque. Os planos giram por `SHOT_ROTATION` fixo (`visual.py:104`) e as composições por índice (`authoring.py:253`). Grain e vinheta em quase tudo (`schema.py:340`).
- **Sem planos de "verdade":** sem prints, sem clipes, sem memes ou reaction inserts, sem zoom-punch em detalhe de print. O personagem principal aparece sempre de costas ou em silhueta.
- **A referência não alimenta a gramática de edição.** `core/styles/from_reference.py:1-6` mede só ritmo, paleta e proporção. O ritmo vira `shot_seconds` (`:255-257`), mas `split_beats` não respeita isso: corta por frase e junta frases curtas. Referência com plano mediano de 1 s continua saindo com ~2–3 s. Zoom-punch, inserts e memes, densidade de texto na tela e SFX não são medidos (`media/reference_video.py:263`).

### 5. Pontos de extensão concretos

1. **Mídia real como fonte de primeira classe.**
   - Acrescentar `"real"` (e `"clip"`, `"capture"`) a `STORYBOARD_SOURCES` (`schemas.py:58`) e tratar em `_scenes_from` (`authoring.py:788`).
   - A IA vira reserva, e não o contrário.
   - Tirar o teto `REAL_SHARE` fixo (`authoring.py:651`) e levá-lo para o estilo.
2. **Acervo antes do storyboard.** Rodar `media_search` + `media_find` (e um coletor de trailer/gameplay) *antes* do LLM. Passar um bloco "Acervo disponível (id: descrição, tipo, licença)" em `storyboard_prompt` (`authoring.py:402`). O LLM escolhe por id, como já faz com `reuse_key`.
3. **Anexar sozinho no piloto automático.** Depois de `media_find.validate_all` (`media_find.py:328`), anexar o melhor candidato com `media_edit.attach_media` (`core/project/media_edit.py:44`), marcado `editorial`, no mesmo job (`tasks.py:491`). A IA fica só onde não houve candidato, com aviso explícito.
4. **Clipes de vídeo real.**
   - Estender `MediaRef` (`document.py:48`) com `kind="clip"` (mp4, início, duração, crop).
   - O renderer já toca vídeo com `ClipLayer` (`renderer/src/components/ClipLayer.tsx`).
   - A detecção de corte já existe (`media/reference_video.py:143` `cuts`, e há yt-dlp).
   - Portar e melhorar `canais/motor/passos/trailer_shots.py` (folhas de planos) e `make_clips.py` (crop/eq/hold, sem áudio).
5. **Separar cena de frase.** Uma batida passa a aceitar N planos (sub-cortes em `Scene` ou cenas filhas): corte e punch-in na palavra de ênfase, a partir das palavras alinhadas que já chegam em `timeline.py:520-560`. Fazer `split_beats` honrar `shot_seconds` < 1,5 s quando o estilo vem de referência rápida.
6. **Ativar `art.sources_mix`** (`schema.py:262`, hoje sem uso) como alvo de mistura por estilo, por exemplo `{real: .45, capture: .1, card: .2, ai_image: .25}`. Usar no orçamento (`image_budget`, `authoring.py:338`) e em `storyboard_check`. Baixar `new_image_ratio` padrão (`schema.py:335`).
7. **Gancho:** permitir mídia real ou clipe no gancho (`authoring.py:293`, `storyboard_check.py:380-399`).
8. **Câmera e ritmo:**
   - Pesos com mais `static` e punch-ins secos ligados a ênfase/SFX, em vez de Ken Burns contínuo (`timeline.py:701`, `VisualLayer.tsx:68`, easing variável).
   - Movimentos "de editor": shake, zoom de impacto, freeze, speed ramp em clipe.
   - SFX de ênfase além das transições (`timeline.py:431`).
9. **Gramática de edição a partir da referência.** Medir em `reference_video.py`: punch-ins (variação de escala entre quadros), inserts curtos menores que 0,6 s (memes/reações), texto na tela por minuto, SFX. Mapear para campos novos de `EditSpec` (`schema.py:332`) e aplicar em `from_reference.py:255`.
10. **Memes e inserts.** Novo tipo de cena ou camada "insert" (imagem ou GIF da biblioteca do canal, 0,3–1 s, com SFX), sugerido pelo storyboard nas viradas e piadas.
11. **Estética da IA que sobrar:** permitir `photo` e misturar linguagens por cena. Tirar `Accent colors` e `World` de toda imagem (`build.py:191-200`, `authoring.py:224`). Usar a IA para planos de apoio (textura, detalhe, ambiente), não para o sujeito principal.

**Comparação com o canais**: lá as cenas eram montadas com trechos de trailer oficial ("a+b" por cena, crop de punch-in, `hold` em vez de vai-e-volta), fotos e prints de página (`canais/motor/passos/make_clips.py`, `trailer_shots.py`, `render.py:95-99`). O Vulto portou só a prova de página (manual) e a mídia real estática. Para ficar melhor que o canais, o caminho é automatizar o que lá era feito à mão: acervo → escolha → anexo → cortes na ênfase, com a IA como reserva.