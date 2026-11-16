# Backlog

Itens que ficaram para depois ou que dependem de algo que não dá para resolver sozinho (contas, chaves, decisões suas). Cada item diz **por que** ficou aqui. Atualizado em 14/11/2026.

## Depende de você (contas, chaves, testes)

- [x] **Claude pela sua conta** para roteiro, tradução e agente de edição (Claude Code logado com o plano Max; testado com roteiro real).
- [ ] **Chave do Gemini (Google AI Studio)** para a voz grátis e como reserva do roteiro quando o limite do plano do Claude acabar. Sem ela a narração usa a voz do Windows (prévia).
- [ ] **Instalar o Codex CLI** e fazer login com o ChatGPT Plus (`npm install -g @openai/codex`, depois `codex login`). O adapter `codex_cli` está pronto, mas **não foi testado** porque o Codex não está instalado nesta máquina. Validar: geração sem interação, pasta `~/.codex/generated_images`, tempo por imagem e cota diária real.
- [ ] **Projeto no Google Cloud + cliente OAuth do YouTube** (passo a passo em docs/USO.md §5). O envio ao YouTube foi testado só com respostas simuladas da API; o primeiro envio real deve ser **privado**.
- [ ] **Verificar o canal do YouTube por telefone** (uploads acima de 15 min e thumbnails personalizadas).
- [ ] **Teste cego de voz em PT-BR** (README §8.4): Gemini × Chirp 3 HD × local × ElevenLabs (referência).
- [ ] **Teste cego de imagem** no estilo do canal: Codex × Z-Image local (FLUX, fal.ai e OpenAI Images foram retirados).
- [ ] Decidir os **nichos** e os estilos prioritários depois do MVP.

## Funcionalidades planejadas (README), ainda não feitas

### Publicação
- [ ] **Driver do Buffer** (TikTok e Instagram pelo plano grátis, API e MCP). Ficou de fora porque a API atual do Buffer precisa ser conferida na documentação oficial e testada com uma conta; hoje há o pacote manual por rede.
- [ ] Hospedagem das URLs públicas que o Buffer e o Instagram exigem (Cloudflare R2).
- [ ] YouTube: playlists, medidor de cota, várias contas/canais. (Títulos traduzidos no mesmo vídeo já funcionam.)
- [ ] Driver nativo do Instagram (Standard Access) e teste de visibilidade pública.

### Voz e idiomas
- [x] **Whisper local** instalado e no instalador (`--extra asr`): confere cada narração. Roda na CPU porque faltam as DLLs do CUDA 12 (cuBLAS/cuDNN); instalar o CUDA 12 deixaria mais rápido.
- [x] Voz local grátis na prévia (Kokoro) e voz do vídeo final escolhida à parte, com amostra; vozes da conta ElevenLabs na lista (14/11/2026).
- [ ] Vozes locais em japonês e chinês: o Kokoro precisa de outro conversor de fonemas (misaki) para esses idiomas; hoje a prévia usa a voz final neles.
- [ ] Voz do Gemini com estilo: hoje vai só o texto (a instrução vazava). Testar de novo quando o Google atualizar o modelo de TTS (`VS_GEMINI_TTS_STYLE=1`).
- [ ] Clonagem da sua voz: assistente de gravação, Applio/RVC e Qwen3-TTS local (o adapter `qwen3_tts_local` é só um marcador).
- [ ] **Modo B multi-idioma** (várias faixas de áudio no mesmo vídeo): tradução com orçamento de duração, time-stretch e pacote para o YouTube Studio.
- [ ] Normalização de números e estrangeirismos para TTS local.
- [ ] Glossário por canal na tradução.

### Estilos e render
- [x] **Família dados e geografia** (15/11/2026): cartões `chart` (barra, linha, pizza, rosca, com animação de entrada) e `map` (países do Natural Earth 1:110m e estados dos EUA, com destaque, rótulo e valor; projeção Equal Earth no código), gerados pelo storyboard quando o estilo é da família e a batida traz série de números ou lugares; preset `dados-e-mapas`. O número grande que conta continua sendo o `stat`.
- [ ] Dados e geografia, o que ficou de fora: **estados/províncias de outros países** (o Natural Earth 1:110m só tem os dos EUA; os do Brasil precisam de uma base simplificada de licença livre, como a do IBGE, conferida antes), globo 3D, corrida de barras, rotas e setas no mapa, países pequenos demais para a escala 1:110m (Singapura, Malta e outros não têm contorno) e edição manual dos itens do gráfico/mapa na aba Cenas (hoje só pelo storyboard ou trocando o tipo).
- [ ] Famílias de estilo que ainda caem no modo imagens paradas: ilustrado 2D (whiteboard, bonecos palito), textura artesanal (colagem), 3D, interface de tela.
- [ ] Parallax 2.5D com mapa de profundidade (Depth Anything V2 Small ou DA3, só licenças Apache).
- [ ] Vídeo IA (image-to-video via fal.ai) em cenas-chave; o tipo de cena `video` existe no esquema, mas não tem gerador.
- [ ] Estilo a partir de **vídeo de referência** (PySceneDetect/TransNetV2, paleta, ritmo).
- [ ] **Style Lab**: componentes novos escritos por IA, com portões de teste.
- [ ] LUTs reais (.cube) e os efeitos nativos do `@remotion/effects`; hoje a gradação é feita com filtros CSS.
- [ ] Desempenho: a aberração cromática desenha a imagem 3 vezes com a cadeia de filtros (só o preset terror-analog usa); avaliar uma passada única. O grão já usa texturas pré-geradas.
- [ ] Várias faixas de música por vídeo, trocando por segmento (hoje vale a primeira faixa ativa; a biblioteca do usuário já funciona).
- [ ] Biblioteca de músicas: evitar repetir a mesma faixa em vídeos seguidos do mesmo canal.
- [ ] Imagens de referência/âncoras para consistência de personagens.
- [ ] Verificador de novidade (impressões digitais visuais dos últimos vídeos).

### Produto
- [ ] Ramos A/B de versões ("versão B do gancho").
- [ ] Checagem de fatos com busca web no roteiro.
- [ ] Repurposing (Shorts a partir de longos), caixa de comentários, analytics e outlier finder.
- [ ] Séries com piloto automático agendado.

## Técnico

- [ ] Migrações completas (renomear/apagar colunas) com Alembic. Colunas novas já são criadas sozinhas ao iniciar (`add_missing_columns`).
- [ ] Limpeza do cache: opção de também apagar narrações e imagens de versões antigas (hoje elas nunca são apagadas) e limpeza automática por limite de espaço.
- [ ] Testes de interface (Playwright) e CI.
