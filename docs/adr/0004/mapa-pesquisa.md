PESQUISA WEB: "cara de IA", gramática de edição, mídia real e análise de referência

Escopo: só pesquisa na web. Não li nem alterei nenhum arquivo do Vulto nem do canais, por isso não há citações arquivo:linha. Onde escrevo [FATO], há fonte primária ou reportagem. [OPINIÃO] é heurística de mercado ou análise minha.

======================================================================
1. O QUE FAZ UM VÍDEO FACELESS PARECER "AI SLOP"
======================================================================

[FATO] Política do YouTube (YPP)
- Em 15/07/2025 a regra "repetitious content" mudou de nome para "inauthentic content". Na página atual o título é "Generic or Repetitive Content". O YouTube disse que foi só esclarecimento, não regra nova. Fontes: https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/ e https://www.plagiarismtoday.com/2025/07/08/youtube-targets-inauthentic-content/
- O que a página oficial proíbe:
  - conteúdo que "looks like it's made with a template";
  - "AI-generated content made with generic or unoriginal templates" sem perspectiva original;
  - os exemplos da imprensa citam "slideshows" com a mesma narração e histórias narradas com diferenças superficiais.
- O que ela permite: mesma intro/outro com miolo diferente, e séries em que "each video has a distinct storyline". Fonte: https://support.google.com/youtube/answer/1311392
- A punição vale para o CANAL INTEIRO, não só para o vídeo.
- Reused content: só é aceito se "viewers can tell that there's a meaningful difference" do original. A regra vale mesmo com permissão do dono. Comentário crítico e reação com comentário são permitidos.
- Usar IA não é violação por si só. Rene Ritchie: o YouTube "welcomes creators using AI tools to enhance storytelling". Fonte: https://www.searchenginejournal.com/youtubes-ai-slop-problem-and-how-marketers-can-compete/567297/
- Jan/2026: na carta anual, Neal Mohan usou o termo "AI slop" e prometeu ampliar a detecção de spam e clickbait. Houve onda de remoção de canais. Fonte: mesma SEJ.

[FATO] Rótulo "altered or synthetic", fonte https://support.google.com/youtube/answer/14328491
- Obrigatório para conteúdo REALISTA gerado ou alterado por IA: cena realista que não aconteceu, pessoa real dizendo o que não disse, evento real alterado.
- Dispensado: conteúdo claramente irreal ou animado, ajustes estéticos, legendas, ajuda de IA em roteiro ou thumbnail, voz clonada do próprio criador, gameplay.
- Declarar não reduz alcance nem monetização. Quem omite repetidamente pode levar o rótulo forçado, ter conteúdo removido ou sair do YPP.
- Onde aparece: no player, se for fotorrealista; na descrição, se for estilizado.
- Implicação [OPINIÃO]: imagem IA fotorrealista de "cena histórica" ou de "notícia" exige rótulo e grita IA. Imagem estilizada ou ilustrada não exige.

[FATO] Dados de percepção. Fonte: https://www.searchenginejournal.com/youtubes-ai-slop-problem-and-how-marketers-can-compete/567297/
- Estudo Kapwing com 15.000 canais em alta: 278 só faziam slop, somando 63 bi de views.
- Dos primeiros 500 Shorts servidos a uma conta nova, 21% eram slop.
- Raptive: a confiança cai cerca de 50% quando o conteúdo é percebido como IA.

[FATO/fraco] Sinais citados por guias e fóruns. São fontes de fornecedor e de fórum, não estudos.
- Voz TTS padrão, a mesma em muitos canais. A ElevenLabs "Adam" é associada a slop em fórum: https://boards.straightdope.com/t/whats-up-with-this-ubiquitous-ai-voice/1020355
- Visuais genéricos que não batem com a narração, pausas estranhas, nomes pronunciados errado, texto ilegível em imagem, mãos e física erradas.
- Padrão de canal: alto volume com template fixo.
- Fontes: https://www.overseeros.com/blog/ai-slop-faceless-youtube e https://aiforensics.org/work/ai-detection-guide

[OPINIÃO] Sinais concretos que provavelmente denunciam o Vulto hoje. Todos dá para medir no próprio render.
- Visual:
  - (a) quase todo plano é still gerado com Ken Burns de mesma velocidade e direção previsível;
  - (b) planos de duração quase constante. Cutting mostrou que a montagem humana segue um padrão 1/f, com durações variadas; regularidade é assinatura de máquina: https://news.cornell.edu/node/271563
  - (c) mesma paleta, iluminação "cinematográfica" e brilho plástico em todas as cenas;
  - (d) zero material "sujo" do mundo real: print de tweet, manchete, UI do jogo, gameplay, foto de celular, página de wiki, gráfico;
  - (e) nenhum humor nem aparte.
- Som:
  - (f) TTS sem respiração e com prosódia uniforme;
  - (g) trilha contínua no mesmo volume, sem drop antes de revelação;
  - (h) nenhum SFX ou só whoosh genérico.
- Roteiro:
  - (i) bordões de LLM ("mas aqui está o problema", "prepare-se") e estrutura idêntica entre vídeos. Isso esbarra no "template" da política.

======================================================================
2. GRAMÁTICA DE EDIÇÃO DE ALTA RETENÇÃO (curiosidades, games, explicativo)
======================================================================

[FATO] Números com fonte. Não existe estudo acadêmico de YouTube sobre cortes x retenção, só análises de mercado.
- Cinema (Cutting, Cornell): o plano médio caiu de cerca de 12 s (1930) para cerca de 2,5 a 4 s hoje. O padrão de alternância importa mais que a média. https://news.cornell.edu/node/271563 e https://www.flickfilosopher.com/2014/02/people-complain-fast-edits-modern-films-need-know.html
- Análise de mercado: cortar mais NÃO garante retenção. MrBeast passou de 38 cortes num minuto amostrado (mar/2023) para 23 (mar/2024). Público acima de 25 anos cansa com excesso de edição.
- Heurísticas da AIR para retenção, opinião de mercado:
  - abertura: troca visual a cada 10–15 s nos primeiros 30 s;
  - minutos 0–3: a cada 10–20 s;
  - depois: a cada 25–40 s;
  - pattern interrupt em ~25–35 s;
  - re-hook a cada ~2 min;
  - plano estático por mais de 40 s achata a curva.
  - Atenção: "troca visual" ali é mudança de bloco, não corte. Talking-head corta mais.
  - Fonte: https://air.io/en/youtube-hacks/advanced-retention-editing-cutting-patterns-that-keep-viewers-past-minute-8
- Som (mesma fonte AIR):
  - whoosh no movimento, pop discreto no texto, impacto amplificado na revelação ou no punchline;
  - trilha a −20/−25 dB, 60–80 BPM nos trechos calmos; −8/−12 dB, 100–120 BPM nos energéticos;
  - cortar a música logo antes da revelação.
- Zoom (mesma fonte AIR):
  - zoom lento de entrada cria tensão; de saída, relaxa ou fecha;
  - shake simulado ou zoom in-out dá vida a plano parado;
  - manter o sujeito a até ~10% da posição anterior no corte.
- Humor (https://air.io/youtube-hacks/editing-tricks-for-fast-paced-funny-videos):
  - corte de reação "as quickly as possible" após o punchline;
  - pausa constrangedora antes do próximo corte;
  - zoom rápido no gag;
  - inserção de meme FRESCO, porque meme velho envelhece o vídeo;
  - freeze frame e replay;
  - montagem não linear (começar no meio da ação).
- MrBeast, documento vazado e de autenticidade contestada: o 1º minuto é o mais crítico, com marcos em 1–3 min e 3–6 min, e re-engajamento em ~3 min. https://www.creatorhandbook.net/leaked-document-allegedly-reveals-mrbeasts-secrets-to-youtube-success-the-key-takeaways/
- Nostalgia/Castanhari: há artigo acadêmico sobre a "Estética Easter Egg", camadas de referência em texto, som, imagem, tempo e edição. Não achei breakdown de cortes ou memes. https://interin.utp.br/index.php/i/article/view/3262/2815
- Johnny Harris: mapa animado com GEOlayers e material de arquivo (mapas antigos, documentos). https://aescripts.com/learn/how-johnny-harris-makes-maps e https://studybreaks.com/college/johnny-harris-mapping-history
- Não achei fonte séria que quantifique canais BR de games e curiosidades.

[OPINIÃO] Gramática concreta para o estilo "VOCÊ VAI ZERAR GTA 6 PELO YOUTUBE". É minha leitura do gênero; validar medindo a referência real (seção 4).
- Plano médio: 2–4 s no hook (0–30 s) e 3–7 s no corpo, com desvio alto. Alternar rajadas de 0,5–1,5 s (piada, lista) com respiros de 6–10 s (gameplay ou trailer narrado).
- Mídia por função:
  - b-roll principal = gameplay, trailer oficial, prints de UI, tweets, manchetes e Reddit;
  - meme e reação = pontuação de 0,4–1,5 s, com SFX colado;
  - IA = só para o que não existe (hipótese, futuro, conceito), de preferência estilizada.
- Punch-in: corte seco para 115–135% no punchline ou palavra-chave, voltando a 100% no próximo plano. Zoom lento de 100 para 105–110% nos respiros. Whip-pan ou motion blur só entre blocos.
- SFX: impacto ou boom no reveal; record scratch ou freeze com "pera…" no aparte; whoosh nas transições; ding ou pop no texto; risada enlatada ou "bruh" curtos; silêncio antes do reveal. Densidade orientativa: 4–10 SFX por minuto no hook, 2–5 no corpo.
- Texto na tela:
  - palavra-chave grande, não legenda corrida, 1–3 palavras e cor de destaque;
  - setas e círculos vermelhos sobre print;
  - "contador" ou "checklist" em vídeo de lista;
  - quebra da quarta parede ("sim, isso é real" sobre um print).
- Pattern interrupt: trocar de tipo de mídia, por exemplo trailer → print → meme → gameplay. Trocar só a imagem não basta.
- Humor: contradição entre narração e imagem ("totalmente seguro" sobre gameplay de acidente), meme literal da frase, autoironia. É aqui que o "estilo" mora, e é isso que a análise de referência precisa capturar, não só paleta e fonte.

======================================================================
3. FONTES DE MÍDIA REAL: legalidade, API e limites
======================================================================

Banco | Licença e crédito | API e limites
- Pexels (foto e vídeo): uso livre, inclusive comercial, sem crédito. Proibido mostrar pessoa identificável de forma ofensiva ou insinuar endosso. https://www.pexels.com/license/
  - API /v1/videos/search, 200 req/h e 20.000/mês por padrão, aumento grátis sob pedido.
  - O app deve mostrar link "Photos provided by Pexels". Tentar contornar o rate limit encerra o acesso. https://www.pexels.com/api/documentation/
- Pixabay (foto e vídeo): sem crédito, uso comercial ok. Proibido revender "standalone" e usar marcas reconhecíveis de forma comercial. https://pixabay.com/service/license-summary/
  - API 100 req/60 s por chave; cache OBRIGATÓRIO de 24 h; proibido download em massa automatizado; baixar em vez de hotlink. Endpoint /api/videos/. A API NÃO cobre música nem SFX. https://pixabay.com/api/docs/
  - Música da Pixabay: alguns autores registram no Content ID. Guardar o certificado de licença para contestar. Relato secundário: https://www.foximusic.com/blog/need-background-music-that-wont-trigger-content-id/
- Unsplash (só foto): crédito ao fotógrafo E ao Unsplash obrigatório. Demo 50 req/h, produção 1.000 req/h. Exige hotlink das URLs e chamar /photos/:id/download para tracking. Não serve para vídeo. Ruim para pipeline offline. https://unsplash.com/documentation
- Wikimedia Commons: licença por arquivo (PD, CC BY, CC BY-SA…). Crédito ao AUTOR (não ao uploader) + licença + link. BY-SA pode impor share-alike. https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia
  - Metadados via api.php?action=query&prop=imageinfo&iiprop=extmetadata (campos LicenseShortName, Artist, AttributionRequired: conhecimento meu, conferir). https://commons.wikimedia.org/wiki/Commons:API/MediaWiki
- Openverse (agrega CC de imagem e áudio): filtro license_type=commercial,modification. Registro grátis dá limite maior; números oficiais não publicados na página. Terceiro cita 20 req/min e 200/dia anônimo, máximo de 240 resultados: não verificado. https://wordpress.github.io/openverse-api/reference/api/authentication_and_throttling.html
- NASA: em geral sem copyright nos EUA. Dar crédito à NASA. Logos são protegidos. Não insinuar endosso. Material de terceiros marcado exige permissão. Astronautas ativos não podem aparecer em uso promocional. https://www.nasa.gov/nasa-brand-center/images-and-media/
- Internet Archive (Prelinger, domínio público): licença por item; rate limit não documentado. Ferramentas usam por conta própria ≤2 req/s com backoff em 429; paginação da busca tem teto de 10k. Não confirmei os termos do Prelinger.
- GIPHY: os termos (support.giphy.com) bloquearam fetch (403). A API exige "Powered by GIPHY". O GIF em si costuma ser recorte de obra de terceiros (filme, TV): a GIPHY não te licencia isso para vídeo monetizado. Risco de claim ou takedown. https://developers.giphy.com/docs/api
- Tenor: a API foi ENCERRADA em 30/06/2026; chaves novas cessaram em 13/01/2026. NÃO integrar. https://9to5google.com/2026/06/30/google-tenor-api-gif-updates/
- Freesound (SFX): filtrar license:"Creative Commons 0" (CC0 dispensa crédito; CC BY exige; evitar BY-NC em monetizado). https://freesound.org/docs/api/overview.html
  - 60 req/min e 2.000/dia; download do ORIGINAL exige OAuth2 e tem limite de 30/min e 500/dia. Previews mp3/ogg sem OAuth2.
- Mixkit (SFX, vídeo, música): "Free License" permite comercial e YouTube, sem crédito. Itens com "Restricted License" são só não comerciais: checar por item. Sem API pública. https://mixkit.co/llm-info/
- YouTube Audio Library: faixas da licença padrão sem crédito; faixas CC BY pedem crédito na descrição. Não tem API: baixar manualmente e curar um pacote local. https://support.google.com/youtube/answer/3376882
- Games:
  - Rockstar: política de 10/01/2025 restringe uso a "non-commercial" e veta cutscene isolada e vazamento. A página oficial deu timeout aqui; resumo via busca. https://support.rockstargames.com/articles/7bNaeoMFTV0iUDGhStTXvz
  - Microsoft, Game Content Usage Rules (2015): permite anúncios do YouTube, sem usar o nome do jogo no título; a versão atual não foi verificada. https://news.xbox.com/en-us/?p=177574
  - Nintendo historicamente restritiva.
  - Na prática: trailer oficial e gameplay comentado são rotina no gênero, mas o detentor pode reivindicar a receita. Usar com comentário transformativo e trechos curtos.

[FATO] Direito brasileiro (Lei 9.610/98). Texto: https://www.planalto.gov.br/ccivil_03/leis/l9610.htm
- Art. 46, III: citação de passagens "para fins de estudo, crítica ou polêmica, na medida justificada", com nome do autor e origem.
- Art. 46, VIII: "pequenos trechos", desde que a reprodução não seja o objetivo principal da obra nova nem prejudique a exploração normal da obra.
- Art. 47: paródia livre se não for reprodução verdadeira nem desacreditar a obra.
- O rol é taxativo e não existe "fair use" amplo. Migalhas: https://www.migalhas.com.br/depeso/261009/os-aspectos-juridicos-dos-memes
- STJ REsp 1.967.264/SP: paródia ligada à liberdade de expressão. https://informativos.trilhante.com.br/julgados/stj-resp-1967264-sp
- O Content ID ignora o art. 46: a disputa é na plataforma, não na lei. https://www.conjur.com.br/?p=375561
- [OPINIÃO] Meme em vídeo monetizado: risco jurídico baixo e risco de claim médio. Mitigar com:
  - trecho curto (<2 s);
  - função de comentário ou paródia;
  - template de meme recriado (imagem base CC ou PD + texto próprio) em vez de clipe de filme;
  - biblioteca local curada de reações e SFX com licença registrada por item.

======================================================================
4. FERRAMENTAS LOCAIS PARA EXTRAIR O ESTILO DE UMA REFERÊNCIA
======================================================================

[FATO] Ferramentas e parâmetros
- PySceneDetect (https://www.scenedetect.com/docs/latest/api/detectors.html):
  - ContentDetector (HSV, threshold 27);
  - AdaptiveDetector (média móvel; menos falso positivo em movimento rápido, que é o caso de gameplay);
  - ThresholdDetector (fades, 12);
  - HashDetector (0,35);
  - HistogramDetector (0,2);
  - min_scene_len padrão de 15 frames (0,6 s no CLI).
- TransNetV2 (rede neural, porta PyTorch): F1 de 77,9 no ClipShots, 96,2 no BBC e 93,9 no RAI. Concorda F1 0,96 com o PySceneDetect num filme testado. Avisa que há resultado não determinístico em GPU. https://pypi.org/project/transnetv2-pytorch/
- ffmpeg:
  - scdet: metadados lavfi.scd.score e lavfi.scd.time;
  - ebur128: loudness LUFS ao longo do tempo;
  - silencedetect: pausas.
  - Isso vem do meu conhecimento; ver `ffmpeg -h filter=scdet`.
- faster-whisper: word_timestamps (±100–500 ms) e vad_filter (Silero).
- WhisperX: alinhamento wav2vec2 (~±30 ms) e diarização; cerca de 70x tempo real numa 4090, segundo blog.
- Uso: palavras por minuto, pausas, onde caem os cortes em relação às palavras, e bordões. https://localaimaster.com/blog/faster-whisper-guide
- OCR: PaddleOCR é geralmente mais rápido e preciso que EasyOCR em vídeo (~30–60 ms por frame 1080p numa T4, estimativa de fornecedor). Os dois suportam português. Amostrar 2 fps e deduplicar. https://www.forasoft.com/learn/ai-for-video-engineering/articles-ai/paddleocr-text-detection-video
- Eventos sonoros:
  - YAMNet: 521 classes AudioSet, 16 kHz mono. https://github.com/tensorflow/models/blob/master/research/audioset/yamnet/README.md
  - PANNs: tagging e SED com início e fim.
  - CLAP: zero-shot por texto ("vine boom", "record scratch").
- Fingerprint de SFX conhecidos: audfprint acha trechos curtos com offset em áudio longo. Ideal para contar quais SFX da SUA biblioteca a referência usa. https://github.com/dpwe/audfprint/
- Separação voz/música: Demucs, para medir o nível da trilha relativo à voz e detectar drops. Beats com librosa.beat_track, para checar se os cortes caem no beat. Ambos de conhecimento meu.
- Rosto e reação: OpenCV YuNet ou MediaPipe (conhecimento meu). Rosto grande e curto num vídeo faceless ≈ inserção de meme ou reação.
- LLM com visão:
  - Gemini amostra 1 fps por padrão (fps configurável), cerca de 258 tokens por frame + 32 tokens/s de áudio ≈ 300 tokens/s. Fontes secundárias; conferir na doc oficial. https://www.s-anand.net/blog/how-does-gemini-process-videos/
  - Alternativa barata: contact sheet de 1 keyframe por plano, em grades 4x4, para classificar o tipo de cada plano.

[OPINIÃO] Pipeline proposto para o "DNA de estilo" da referência, saindo em JSON e indo além do superficial:
1. Cortes: TransNetV2 ou AdaptiveDetector. Gera cortes por minuto por janela de 30 s, durações em mediana, p10 e p90, e coeficiente de variação. Isso mede ritmo, inclusive a irregularidade humana.
2. Movimento dentro do plano: optical flow do OpenCV ou razão de escala entre frames. Classifica punch-in seco (salto de escala sem corte), zoom lento (%/s), shake e estático.
3. Tipo de mídia por plano: VLM sobre os keyframes, com rótulos gameplay | trailer/cinemática | print de tweet/notícia/site | meme/reação | foto real | ilustração/IA | cartão de texto | mapa/gráfico. Gera a proporção e as transições mais frequentes.
4. Texto na tela: PaddleOCR. Mede frequência, palavras por overlay, posição, cor dominante, caixa alta ou não, e duração.
5. Áudio:
   - SFX por minuto: PANNs + audfprint contra a biblioteca local, produzindo um "dicionário de SFX" da referência;
   - nível da trilha com Demucs + ebur128;
   - drops antes de reveal (silêncio na trilha seguido de pico);
   - fala com faster-whisper: palavras por minuto, pausas, perguntas retóricas e gírias.
6. Humor e memes: cruzar a transcrição com os planos meme/reação, vendo qual frase dispara o meme e com qual SFX. Gera exemplos few-shot de "frase → inserção" para o roteirista ou LLM do Vulto reproduzir a lógica, não os arquivos.
7. Comparação automática: rodar o mesmo extrator no render do Vulto e dar nota de distância (ritmo, % de IA, densidade de SFX e de texto). Isso serve também de "detector de cara de IA" interno, por exemplo % de planos IA acima de X, CV de duração baixo, ou zero SFX.

[OPINIÃO] Regra de produto para tirar a cara de IA:
- Hierarquia de mídia por cena: real específico (gameplay, trailer, print ou documento do próprio assunto) > real genérico (Pexels, Pixabay, Commons) > meme ou reação curada > IA estilizada > IA fotorrealista.
- A IA fotorrealista só quando não existe nada real, e com rótulo.
- Meta: IA em no máximo 20–30% do tempo de tela, com a duração dos planos variando e não constante.

Observação sobre o bug do card (nome herdado do arquivo da referência): fora do escopo da pesquisa web, não investiguei.
