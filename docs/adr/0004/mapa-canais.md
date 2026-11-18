## Canais (C:/Users/lucas/canais, só leitura): o que ele fazia e o que o Vulto pode superar

Os caminhos abaixo são relativos a `C:/Users/lucas/canais` quando não digo outra coisa. Contei o tipo de cada cena nos roteiros JSON. Para ler o banco, copiei para `C:\Users\lucas\AppData\Local\Temp\claude\367e8e73d2f5afac\d95040d1-a4f5-445b-a986-df4a299967b9\scratchpad\estudio-copia.db` e consultei só a cópia. Não escrevi nada no canais nem no Vulto. Para contar as cenas, criei um script temporário de leitura na pasta temporária do sistema, fora dos dois projetos, e apaguei depois.

### Achado central (o contraste com o Vulto)
- **Canais: a imagem real é a regra e a IA é exceção estilizada.** Nos 5 longos do Plantão Vice que contei (julgamento-v1, red-dead-v2, vida-real-v8, policia-5x6-v2, pc-v1) há **0 cenas `img` (imagem de IA)**. Tudo é print de página, trecho oficial, vídeo livre, foto CC ou cartão gráfico.
- No Money Rerun a IA aparece como **rabisco de caderno do Codex**, um estilo assumidamente desenhado: 50 de 136 cenas no `piloto-v7-en` (37%) e 20 de 138 no `ep2-v1` (14%). O resto é print real (46 e 72 cenas).
- **Vulto: o contrário.** `backend/studio/workers/pipeline/authoring.py:277` diz "Só em kind image, no máximo 1 a cada 3 batidas" para mídia real. `authoring.py:651` tem `REAL_SHARE = 1 / 3`. Em `authoring.py:279` a cena sempre guarda "o prompt de IA de reserva". Ou seja, a IA é o padrão e o real é teto de 1/3. Essa é a causa estrutural da "cara de IA".

### 1. Como o canais montava os vídeos

**Fontes visuais, por ordem de uso no Plantão Vice:**
- **Prints de páginas reais com marca-texto.** Saem de `motor/passos/shot_helper.py` + `shot_views.py`, que acham a frase, limpam a página e enquadram o zoom. Há 387 PNGs em `plantao-vice/assets/web`. No vida-real-v8 são 59 de 122 cenas.
- **Planos oficiais curtos.** Trailers e gameplay "An Extended Look" baixados da página de mídia da Rockstar, cortados em planos de 1 a 4 s, sem áudio e com crédito (`docs/metodo-plantao-vice.md:52-57`). Viram cena cheia (`clip`) ou fundo escurecido e desfocado (`bg`): 47 de 85 cenas no red-dead-v2 e 41 de 89 no policia-5x6-v2.
  - O `motor/passos/make_clips.py:8` tem o modo `hold` ("ida uma vez + último quadro parado") para não fazer ida e volta.
- **Gameplay de outro jogo** (Red Dead 2) baixado com `ferramentas/yt-dlp.exe` (`metodo-plantao-vice.md:92`).
- **Vídeo livre de lugar e bicho** (Pexels) e **foto CC/CC0** (Commons), lado a lado com o jogo em `duo` "JOGO x VIDA REAL" (`plantao-vice/pipeline/reais_vida_real.py:1-9`, `playbook.md:146`).
- **Vídeo viral com pessoa** entra desfocado no próprio arquivo (sigma ~96 px em 1080p), com o cartão "NÃO PODEMOS MOSTRAR" (`reais_vida_real.py:5-7`; padrão em `estudio/skills/estudio/estudio-operacao/references/padrao.md:29-41`).
- **Cartões gráficos**: big, quote, icon, versus, ficha, timeline, bars, deal. É o "esqueleto" do vídeo: no julgamento-v1 são 40 de 68 cenas, mas 32 delas com reel de trailer atrás.
- **IA só em duas coisas estilizadas:**
  - avatar ilustrado de 17 poses (Codex/GeradorMG), que só reage: sem balão, 1 vez a cada 60 a 90 s, nunca em cena séria (`plantao-vice/playbook.md:80-107`);
  - miniatura v3 pintada (`playbook.md:67-70`), que proíbe personagem do jogo feito por IA.

**Edição:**
- Ritmo de ~9 s por cena e um fato novo a cada 4 a 8 s. Abertura de 60 a 75 s com o 1º quadro já em movimento (`estudio/formatos/doc-longo.md:11-21,32`).
- O elemento principal entra em até 0,5 s (`plantao-vice/video/src/Episode.tsx:88`, função `first()`).
- Movimento só de câmera: o Reel faz Ken Burns de 1,03 para 1,10 (`video/src/media.tsx:14-29`), mais punch-in e pop por spring. "Nada tremendo" (`padrao.md:19`).
- Gatilho por palavra falada: marca-texto, carimbo e texto grande entram na palavra (`Episode.tsx:62-80`, `wordMs`).
- Texto na tela: faixa de plantão, kin (texto grande por palavra, cerca de 51 px por caractere, `playbook.md:165`), aba de capítulo, marcador "3/12" e selos CONFIRMADO / IMPRENSA / BOATO.
- Legenda queimada palavra a palavra só nos Shorts (`video/shorts/Short.tsx:171`). No longo vai só o .srt.
- **Memes: nenhum.** Não há biblioteca de meme nem inserção de clipe de reação. O humor vem do roteiro e do avatar:
  - Money Rerun: "humor ácido", frase seca depois do fato com fonte, callback e placar TEMPORARY (`money-rerun/playbook.md:32`);
  - Plantão Vice: poses `rindo` e `mao-na-testa` do avatar.
- SFX ligados ao componente:
  - capítulo = riser + impact (`chapters.tsx:16-17`);
  - corte entre planos = `corte.wav`, um chiado de TV (`media.tsx:59`);
  - carimbo = stamp, número = pop ou typeclick, cartão de foto = shutter.
- Regra dura dos efeitos: sem efeito tonal ou agudo. O `motor/passos/sfx_guard.py:20` bane whip, whip2, ding e trombone e trava o render. O whoosh fica mudo por estar quebrado (`motor/passos/sfx_levels.py:23`).

**Música:**
- Mixkit, só o trecho de batida, em loop no compasso. A curadoria automática escolhe a janela com menos energia na faixa da voz (`estudio/skills/estudio/faceless-video-pipeline/references/audio.md:6`).
- Fica de 14 a 19 dB abaixo da voz e sobe ~+3,5 dB só em pausa real (`audio.md:11`, `padrao.md:22`).
- Plantão Vice usa synthwave (`playbook.md:108`); Money Rerun usa lo-fi e trap.
- Não há corte no tempo da música, drop nem música que muda com a emoção.

### 2. Regras contra "cara de IA" / a favor de parecer editado por gente
Não existia uma regra com o nome "cara de IA". O efeito vinha de regras de prova e de ritmo:
- **"Prova na tela, não só narração"** (`padrao.md:7`). Money Rerun v1, só narrado, foi rejeitado.
- **Originalidade** (`padrao.md:14`): "Molde repetido com o assunto trocado é risco de 'conteúdo inautêntico'". E também "Maior risco: a mesmice" (`plantao-vice/playbook.md:195`).
- **Lição #32** (banco, nota do Lucas de 05/11: "está focando muito nas notícias"): imagem em movimento antes do print. A v3 trocou 8 prints e pôs 79,6 s de imagem real livre (`playbook.md:146`).
- **Lição #230**: três cartões de citação seguidos derrubam o ritmo. Fundo desfocado em toda citação, e 1 em cada 3 vira clipe. O doc-longo manda medir: acima de ~25% do tempo em citação, trocar (`doc-longo.md:28`).
- **Lição #244**: não repetir a citação ou o quadro de um vídeo de 5 dias antes; listar o que já foi usado antes do roteiro.
- **Lições #214 e #147**: plano em ida e volta acima de 4 s denuncia o loop. Usar planos distintos, ou `hold` quando a ação não pode andar para trás.
- **"Fundo de cena longa é uma sequência de planos, não um plano esticado"** (`metodo-plantao-vice.md:95`).
- **No estudo do concorrente, o que não copiar** (`plantao-vice/paineis/luskas.html`):
  - personagens da Rockstar feitos por IA na miniatura;
  - "repetir os mesmos planos", citando um comentário do público: "as mesmas imagens passando, fica bastante cansativo".
- **Divulgação de IA = Não** porque o canal é "desenho + voz de narrador + páginas reais" (`padrao.md:44`). A IA era assumida como estilo (rabisco, ilustração), nunca como foto falsa realista.
- **Revisor** (`estudio/skills/estudio/estudio-revisao/SKILL.md:32`): checa "parecido demais com o vídeo anterior" e "IA realista sem aviso".

### 3. Uso de referências de outros canais
- **Análise do LuskasGTA** (`plantao-vice/paineis/luskas.html`, resumo em `playbook.md:233-239`). Usou:
  - metadados de todos os longos, legendas de 6 vídeos, **prévias (storyboards) de 3**, 94 comentários e 15 buscas;
  - da prévia saiu a gramática visual: marcador fixo "6/12" no canto, selo CONFIRMADO, cartão grande por item, "texto que vai crescendo como checklist", número gigante, nome do personagem embaixo;
  - o resultado virou experimento (#7 a #9) e não regra: "copiamos a fórmula, nunca a frase".
- **Documentário de história** (`doc-longo.md:45`; `plantao-vice/pesquisa/historia-d-concorrentes.py`): yt-dlp `--dump-json` (capítulos, descrição) mais legenda para listar "o que o concorrente não prova". O ângulo sai dessa lacuna.
- **Skill `youtube-niche-research`**: "model a format (not copy a video)". Agrupa canais em modelar / referência / não copiar (`youtube-niche-research/references/channel-revival.md:18`).
- **Limite:** o canais também não extraía o estilo de edição quadro a quadro. Não media cortes por minuto, zoom, densidade de SFX, memes, legenda animada nem ritmo musical. Pegava embalagem, estrutura e roteiro.
  - O Vulto tem o mesmo teto: `backend/studio/core/styles/from_reference.py:1-6` mede só "ritmo, paleta, proporção" (mais fração de texto), e o LLM completa o resto.

### 4. O que o canais fazia bem que o Vulto não faz
- **Prioridade real-first.** A IA nunca é reserva de cena. O Vulto inverte isso: `authoring.py:277` e `:651`.
- **Planos oficiais cortados plano a plano**, com folha de 1º, meio e último quadro e o modo `hold` (`make_clips.py`, `faceless-video-pipeline/SKILL.md:110`). Reel de vários planos como fundo de cartão, então nunca há fundo estático.
- **Print com marca-texto ligado à palavra falada**, com receitas de limpeza por site (`faceless-video-pipeline/references/capturas.md`).
- **JOGO x VIDA REAL lado a lado** e viral desfocado com cartão de motivo. O "real" vira conteúdo, não só ilustração.
- **Travas automáticas de qualidade:** sfx_guard, check_triggers (destaque que some em menos de 2 s), scan de "abertura vazia", folhas de quadros, −14 LUFS e voz contra música medidos.
- **Memória de mesmice entre vídeos** (#244) e cota de cartões seguidos (#230).
- **Lições versionadas no banco** (302 lições, tabela `licoes`), aplicadas em checklists.

### O que o canais fazia mal ou de forma limitada (para superar, não copiar)
- **Quase tudo à mão:** um gerador Python por episódio, x/y de carimbo à mão e asserts. Qualquer byte em `video/src` refaz o vídeo inteiro (lição #29). Não escala.
- **Template rígido por canal** (neon fixo; 59% de cartões no julgamento-v1). Mesmo com fundo real, a sensação de "molde" volta.
- **Sem vocabulário de edição de YouTuber BR:** sem meme, sem clipe de reação, sem zoom na cara, sem SFX cômico (o whip e o ding foram banidos por apitarem, e nada de não tonal entrou no lugar), sem legenda animada no longo, sem corte no beat.
- **Dependência de material oficial de terceiros:** risco de Content ID avisado ao Lucas e política da Rockstar "só não comercial" (`metodo-plantao-vice.md:57`).
- **Resultado:** a qualidade não puxou crescimento. CTR de 3,0% e 4:08 de média em longos de 10 a 19 min (`playbook.md`, "Resultados no YouTube"). Para o LuskasGTA, o canal "não ganha no capricho do vídeo", ganha em assunto, embalagem e volume.

**Caminhos para o Vulto ir além:**
- **Inverter o padrão de mídia.** A ordem seria: real oficial ou gameplay → print com destaque → vídeo livre → foto CC → cartão recriado → IA só estilizada (ilustração ou colagem, nunca foto realista). Mostrar no app a % de tempo de IA por vídeo, com teto.
- **Extrair a gramática de edição da referência:** cortes por minuto, zooms e punch-ins, densidade e tipo de SFX, inserções de meme, estilo de legenda, proporção b-roll/cartão e corte no beat. Aplicar isso no plano de cenas.
- **Biblioteca de memes e SFX** com licença registrada e trava anti-apito (do canais).
- **Memória de repetição de planos** entre vídeos.

### 5. Bibliotecas de assets e licenças
- **Música:** Mixkit (`motor/passos/get_audio.py:1,120`). Licença "Mixkit Stock Music Free License": YouTube monetizado, sem crédito, proibido registrar no Content ID. Kevin MacLeod (CC BY 4.0) só a pedido (`audio.md:39-41`). Faixas synthwave: Mixkit 131, 173, 159, 141, 132, 853 (`audio.md:5`).
- **SFX:** IDs do Mixkit em `get_audio.py:48-52` (whip, scratch, coins, moneybag, cheer, shutter, paper, page, riser, impact, bass, ding, typeclick, rimshot, trombone). Ficam em `plantao-vice/video/public/sfx/` (26 arquivos).
  - click, pop, stamp, type, whoosh, static e bleep não têm origem rastreável no canais (provavelmente vêm do canal-dark).
  - O `corte.wav` é estática de TV com passa-baixa, de 0,24 s (`faceless-video-pipeline/SKILL.md:52`).
- **Fontes:** Google Fonts (OFL), carregadas por `@remotion/google-fonts`.
  - Plantão Vice: Anton, Barlow Condensed, IBM Plex Mono, Kaushan Script, Inter (`plantao-vice/video/src/theme.ts:2-6`).
  - Money Rerun: Archivo Black, Permanent Marker, Patrick Hand, Special Elite (`money-rerun/playbook.md:39`).
- **Overlays e memes:** não há biblioteca. Overlays são componentes próprios: faixa de plantão, selos, carimbo, ClipCredit, NoShow, DealCard (`video/src/kit.tsx`, `media.tsx`, `iceberg.tsx`).
- **Imagem e vídeo:**
  - trailers e gameplay oficiais da Rockstar (risco de Content ID, uso comercial não liberado);
  - Pexels (Pexels License, crédito opcional);
  - Wikimedia Commons (CC e CC0, crédito exigido; ex. `plantao-vice/pipeline/fotos_armas.py:39-40`);
  - foto de imprensa só como publicada (BBC borrada);
  - logo oficial de GTA VI em `plantao-vice/assets/oficial/` (de rockstargames.com/VI).
- **IA:** poses do avatar (Codex/GeradorMG, `video/public/apresentador/`), rabiscos do Money Rerun (Codex, `money-rerun/assets/img`) e arte da miniatura v3 (Codex, `plantao-vice/assets/codex/thumbs-v3.md`).