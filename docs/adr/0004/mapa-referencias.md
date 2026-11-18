## Fluxo de REFERÊNCIAS: o que existe, o que se perde, ferramentas e o bug do nome

### 0. O caminho de hoje
- Envio e link: `backend/studio/workers/visual/references.py:46-85`. A análise roda no job `reference_shots` (`workers/visual/tasks.py:23-25`), que chama `references.analyze_reference` (:99-123) e depois `media/reference_video.analyze` (:276-291).
- Estilo: botão "Criar estilo a partir desta referência" (`apps/web/src/pages/references/ReferenceStyle.tsx:301-335`), que faz POST em `/api/references/{id}/style` (`api/routes_visual.py:192-204`) e chega em `core/styles/from_reference.create_from_reference` (:447-473).
- **A referência só vira um StyleSpec salvo em "Meus estilos".** Ela não cria projeto e nunca entra no roteiro, no storyboard ou na busca de mídia: nenhum `reference_id` aparece fora de `workers/visual`. O roteirista e o editor nunca veem a referência.
- Dados reais (lidos de uma cópia do `C:/Users/lucas/VideosStudioData/studio.db` no scratchpad):
  - Referência `ref_x1l83ap549`, título igual ao nome do arquivo, 1920x1080, 60 fps, 804,5 s.
  - Stats: `{"shots":283,"median_s":1.43,"mean_s":2.73,"cuts_per_min":21.0,"palette":["#0d090a","#392b29","#685758","#b0927c","#dad5ce"]}`.
  - 15 folhas em `library/references/ref_x1l83ap549-planos-*.jpg`.
  - Estilo gerado: `ref-ytdown-com-youtube-media-ht4blj6jdg-voce-vai-zerar`, nome "YTDown.com_…_1080p (referência)", base `curiosidades-longo`, ainda na **v1**.
  - **Nenhum projeto usa esse estilo.** Os 4 projetos de GTA usam `lucas-em-vice-longo` e `lucas-em-vice-short`.

### 1. O que é extraído hoje (tudo vem de `media/reference_video.py`)
- **Metadados:** largura, altura, fps, codec e duração, via ffprobe (`probe`, :128-140).
- **Cortes:** um único limiar, `select='gt(scene,0.3)'`, num vídeo reduzido a 320 px e **com `-an`** (:143-151). O áudio nunca é lido.
- **Planos:** são os intervalos entre cortes. Plano com menos de 0,4 s é **descartado** (:154-163, `MIN_SHOT_S` :30).
- **Folhas de contato:** 1 quadro por plano (o do meio), 384x216, 20 por folha (:191-214). A folha só aparece para o humano; nenhum código a lê.
- **Stats:** contagem de planos, mediana, média, cortes por minuto e paleta k-means de 5 cores calculada nos quadros do meio a 64 px (:255-273).
- **Não há** transcrição, OCR, análise de áudio, detecção de zoom ou overlay, nem classificação do material.

Como isso vira estilo (`from_reference.py`):
- Preset mais próximo por proporção, ritmo e gradação (:189-220).
- Trocas medidas (:223-291):
  - `format.*` sai da proporção e do fps;
  - `edit.shot_seconds` vai do 1º ao 3º quartil (deu 0,9 a 2,7 s);
  - `edit.transitions` e `transition_seconds` saem do ritmo;
  - a gradação sai da paleta (deu `warm-sepia`);
  - as cores de destaque e o fundo dos cartões também saem da paleta.
- O ramo `text_ratio` (:284-290) é **código morto**: `stats()` nunca produz `text_ratio`, e a fonte "Do texto na tela" da UI nunca aparece.
- **LLM (opcional, :306-339):** recebe só números, o título (nome do arquivo) e o estilo atual (`family`, `format`, `art`, `edit`, `audio`, :336). Ele **não vê quadro nenhum**: o provedor roda com `--tools ""` (`providers/llm.py:202-203`), então não lê imagem. Também não recebe `script` nem `voice`.
- Resultado no estilo do usuário: `art.bible` = "key art cinematográfica de videogame…", `art.sources_mix = {"ai_image": 1.0}`, `image_chain` = codex/antigravity, `video_chain` = kling, música `procedural`/`pulse`, sfx `basic`.
- **Isso contradiz a referência**, que tem zero imagem de IA.
- `art.sources_mix` (`core/styles/schema.py:262`) é um **campo morto**: nenhum código fora do schema e do editor o usa. O único botão de proporção é `edit.new_image_ratio`, descrito como "fração das cenas com imagem nova de IA" (`schema.py:335`, `authoring.py:340`).

### 2. O que se perde (conferido nas folhas 1, 7 e 14 da referência GTA 6)
O vídeo real é feito de:
- facecam ou reações de criador (com fones, rosto exagerado);
- **mascote/avatar** anime aparecendo em fundo preto ou sobre a cena, com **legenda palavra a palavra na fonte do GTA (Pricedown)**: "algo que", "pelo como", "tá fazendo isso", "MAS VAI SER LEGAL AINDA!";
- **memes** (Patrick com legenda entre aspas sobre o trailer; rosto em zoom lento por 55 s, plano #277; clipes "Venezuela no Brasil");
- **trailer oficial do GTA 6** (a maior parte do b-roll), gameplay antigo (GTA IV, San Andreas com HUD), capas dos jogos;
- **prints** (Wikipedia, notícia) com zoom e grifo;
- faixas brancas de texto no topo e listas com marcadores na fonte do GTA.

O que se perde, item a item:
- **Ritmo:** só a mediana e os quartis viram faixa de cena. 81 micro-planos (<0,4 s, 33 s no total) foram jogados fora (:160), e esses são justamente os stingers, flashes e cortes de meme. Planos longos (55,6 s, 33,9 s) escondem edição interna sem corte duro.
- **Zoom/punch-in:** é invisível. Zoom dentro do plano não dispara `scene>0.3`, e a folha tem 1 quadro por plano.
- **Memes, reações e mascote:** nada classifica o material. Um overlay sobre o trailer (Patrick) não é corte.
- **SFX:** o áudio é ignorado (`-an`). O estilo herda `sfx.pack="basic"` do preset.
- **Música e drops:** ignorados. O estilo fica com trilha `procedural`.
- **Texto na tela e tipografia:** não há OCR. A fonte da legenda vem do preset (Montserrat 800 / Bebas Neue), não a Pricedown da referência.
- **B-roll de gameplay e prints:** não é medido. `sources_mix` fica 100% `ai_image`.
- **Estrutura narrativa, humor e tom da narração:** sem transcrição. O LLM nem recebe `script` e `voice` (:336).
- A paleta k-means do quadro inteiro vira "warm-sepia" e "key art". Ela mede a cor do trailer, não a gramática de edição.

Em resumo, o fluxo mede o envelope (proporção, ritmo médio, cor). Tudo o que é "estilo de conteúdo" fica no plano de "IA completa", e a IA preenche com arte gerada.

Nota sobre o canais: no `C:/Users/lucas/canais`, o mesmo `trailer_shots.py` (`motor/passos/trailer_shots.py`) servia para **cortar trailers oficiais e reusar os trechos** ("folhas de miniaturas para escolher trechos"). No Vulto ele virou "estudar, não reusar" (`reference_video.py:3`), e o material real deixou de ser o produto da ferramenta.

### 3. Ferramentas locais disponíveis ou viáveis
- **ffmpeg 9.0 full** (WinGet Gyan). Tem `scdet`, `freezedetect` (meme congelado ou print parado), `blackdetect`, `siti` e `signalstats` (movimento e energia por quadro), `mpdecimate`, `cropdetect`, `thumbnail`, `photosensitivity` (flashes), `ebur128`, `astats`, `silencedetect`, `showspectrum`.
- **O ffmpeg tem o filtro `whisper` (whisper.cpp).** Precisa de um modelo ggml baixado.
- **faster-whisper:**
  - já existe o provider `faster_whisper` em `providers/align.py:70-121`, e `pyproject.toml:27` tem o extra `asr`;
  - o pacote **não está instalado** no `backend/.venv`;
  - o modelo `models--Systran--faster-whisper-small` **já está no cache** HF (`~/.cache/huggingface/hub`);
  - `VS_WHISPER_MODEL` vale "small" (`config.py`).
  - Daria transcrição com tempo por palavra: palavras por minuto, pausas, gancho dos primeiros 30 s e estrutura via LLM.
- **Visão:**
  - `CodexCLIImage` já passa imagens com `codex exec -i` (`providers/image.py:188-196`). O `claude -p` poderia receber `--tools Read` limitado ao workdir temporário, com as folhas copiadas para lá (hoje `--tools ""`, `llm.py:203`). O antigravity (Gemini) também serve.
  - Uso: classificar cada plano (facecam, trailer/gameplay, print/notícia, meme, mascote, cartão de texto), transcrever o texto na tela e descrever a tipografia. Para isso as folhas precisam de mais quadros por plano (início, meio e fim) e de resolução maior; 384x216 deixa o texto ilegível.
- **numpy + PIL** (já são dependências):
  - diferença entre quadros a 4-5 fps dentro do plano, para achar overlay (caixa de mudança local) e punch-in (escala por correlação de fase ou bordas);
  - fluxo espectral no áudio, para os ataques de SFX alinhados aos cortes;
  - energia fora da fala, para os drops de música.
- **`media/thumb_check.dhash` (:45) e `hamming` (:57):** hash perceptual pronto para achar **elementos recorrentes**, como o mascote ou um meme repetido em vários planos.
- `rembg` e `onnxruntime` estão instalados na venv. Dá para recortar o mascote ou um rosto e virar ativo.
- Não há tesseract nem OCR clássico: o OCR teria de vir da visão do LLM.

Saída sugerida: um "perfil de edição" da referência, com:
- mistura de material (% trailer oficial, % reação/facecam, % meme, % print, % mascote, % IA ≈ 0);
- densidade de stingers e overlays por minuto;
- estilo de texto na tela (fonte, posição, palavra a palavra);
- SFX por corte;
- trilha e drops;
- palavras por minuto e tom;
- beats de humor ("meme de reação a cada 20-40 s").

Esse perfil iria para três lugares:
- para o estilo: dar vida a `sources_mix` e trocar `new_image_ratio` por uma razão de real × IA;
- para o **prompt do roteiro e do storyboard**, como gramática de edição;
- para a busca de mídia real.

### 4. Onde nasce o nome "(referência)" e por que não dá para trocar
- `backend/studio/workers/visual/references.py:67`: no envio sem "Nome (opcional)", o título vira `Path(filename).stem`, o nome do arquivo do YTDown.
- `backend/studio/core/styles/from_reference.py:433`: `data["name"] = (name or f"{title} (referência)")[:200]`. O id vem do slug do título (:432 e :462, `ref-ytdown-com-youtube-media-…`) e é imutável, porque o PUT mantém o id (`api/routes_misc.py:96-99`).
- `apps/web/src/pages/references/ReferenceStyle.tsx:307` não manda `name`. A API aceita (`RefStyleBody.name`, `routes_visual.py:202`), mas a UI não tem campo para isso.
- **Não existe rota para renomear a referência.** `routes_visual.py` só tem upload, link, GET, DELETE, analyze e style. O card da referência (`apps/web/src/pages/References.tsx:142-180`) também não tem ação de renomear.
- O nome do **estilo** é editável, mas só dentro do editor: `StyleEditor.tsx:1390`, seção "Formato e roteiro". O card em `Styles.tsx:111` não tem renomear. O backend aceita a troca: validei o JSON salvo contra o `StyleSpec` atual, com nome trocado, e passou.
- O estilo continua na v1 no banco: nunca foi salvo depois de criado.
- Correção natural:
  - pedir ou limpar o nome no envio (tirar prefixos `YTDown.com_YouTube_Media_<id>_`, `_001_1080p` e trocar `-` por espaço);
  - PATCH do título da referência, com edição inline no card;
  - campo de nome no botão de criar estilo;
  - renomear direto no card do estilo.

Arquivos de apoio no scratchpad (cópia do banco, só leitura): `C:\Users\lucas\AppData\Local\Temp\claude\367e8e73d2f5afac\d95040d1-a4f5-445b-a986-df4a299967b9\scratchpad\db\studio.db` e o estilo salvo em `C:\Users\lucas\AppData\Local\Temp\claude\367e8e73d2f5afac\d95040d1-a4f5-445b-a986-df4a299967b9\scratchpad\refstyle.json`. Nenhum arquivo do Vulto nem do canais foi alterado.