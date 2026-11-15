# Backlog

Itens que ficaram para depois ou que dependem de algo que não dá para resolver sozinho (contas, chaves, decisões suas). Cada item diz **por que** ficou aqui. Atualizado em 15/11/2026.

## Depende de você (contas, chaves, testes)

- [x] **Claude pela sua conta** para roteiro, tradução e agente de edição (Claude Code logado com o plano Max; testado com roteiro real).
- [ ] **Chave do Gemini (Google AI Studio)** para a voz grátis e como reserva do roteiro quando o limite do plano do Claude acabar. Sem ela a narração usa a voz do Windows (prévia).
- [ ] **Instalar o Codex CLI** e fazer login com o ChatGPT Plus (`npm install -g @openai/codex`, depois `codex login`). O adapter `codex_cli` está pronto, mas **não foi testado** porque o Codex não está instalado nesta máquina. Validar: geração sem interação, pasta `~/.codex/generated_images`, tempo por imagem e cota diária real.
- [ ] **Projeto no Google Cloud + cliente OAuth do YouTube** (passo a passo em docs/USO.md §5). O envio ao YouTube foi testado só com respostas simuladas da API; o primeiro envio real deve ser **privado**.
- [ ] **Verificar o canal do YouTube por telefone** (uploads acima de 15 min e thumbnails personalizadas).
- [ ] **Teste cego de voz em PT-BR** (README §8.4): Gemini × Chirp 3 HD × local × ElevenLabs (referência).
- [ ] **Teste cego de imagem** no estilo do canal: Codex × Z-Image local (FLUX, fal.ai e OpenAI Images foram retirados).
- [ ] Decidir os **nichos** e os estilos prioritários depois do MVP. A página **Radar** (onda 2) já mostra o veredito por nicho, a comparação EUA → Brasil e os nichos a evitar, e cria a pendência semanal "decidir os nichos"; a decisão continua sendo sua.
- [ ] **Acesso do radar à YouTube Data API** (onda 2): colar uma chave em Radar → Acesso (cofre `youtube_data_api`, campo `api_key`, ou variável `YOUTUBE_API_KEY`; criar no mesmo projeto do Google Cloud do OAuth) ou ter um canal vinculado (o radar usa o token dele). Sem os dois o radar não coleta. Cada coleta gasta ~3.500 das 10.000 unidades diárias do projeto. A rotina semanal vem **desligada** (`auto`): ligar em Radar se quiser. Nenhuma coleta real feita ainda (só API simulada).
- [ ] **Instalar o Playwright** para as provas na tela (captura de página com marca-texto): `cd backend` e `uv pip install playwright` (usa o Edge do Windows, sem baixar navegador). Não está instalado nesta máquina; sem ele a captura avisa na tela e o resto funciona. Fica fora do `pyproject.toml` de propósito (ADR 0003, decisão 4): reinstalar o ambiente apaga, refazer o comando.
- [ ] **Instalar o yt-dlp** (`winget install yt-dlp`, ou apontar `VULTO_YTDLP`) para baixar vídeo de referência por link. Não está instalado; sem ele, envie o arquivo do vídeo em Referências.
- [ ] **Primeira checagem de fatos real** com o Claude Code (único provedor com busca web, `WebSearch`/`WebFetch` liberados só na tarefa `factcheck`): conferir tempo, qualidade das fontes e se as citações literais batem nas páginas reais. Até aqui só com LLM simulado. Com outro LLM a checagem usa só as fontes do roteiro.
- [ ] **Teste A/B de miniaturas**: a API do YouTube não faz o teste; subir as 3 no "Testar e comparar" do YouTube Studio é à mão. A regra (mediana de 2 mil impressões em 7 dias nos longos) lê impressões que hoje **entram à mão** em Desempenho; sem elas o Vulto cria a pendência de trocar a miniatura em 48 h.
- [ ] **Vincular de novo o YouTube de cada canal** (Canais → YouTube) para dar a permissão nova de leitura do YouTube Analytics (`yt-analytics.readonly`, onda 1 da gestão de canal). Tokens antigos continuam publicando e trazem views, likes e comentários; retenção, duração média e inscritos ganhos ficam vazios até o novo vínculo (as telas Hoje e Desempenho avisam). Antes, no Google Cloud: **ativar a "YouTube Analytics API"** no mesmo projeto e incluir o escopo na tela de consentimento OAuth.
- [ ] **Preencher os campos novos de cada canal** em Canais (público, cadência, posts por dia, estoque mínimo, hashtag dos Shorts, créditos por caractere, voz final padrão, motivo de cada horário) e as **ofertas de afiliado** em Dinheiro, a partir do `canal.toml` de cada canal em `C:\Users\lucas\canais`. Não há importador: veio o formato, não os dados.
- [ ] **Lançar a receita das lojas** (Amazon, Mercado Livre etc.) todo mês em Dinheiro: não há API das lojas; a tela Hoje lembra nos dias 1 a 10.
- [ ] **Primeira coleta real de métricas e primeiro Short cortado de um longo real**: até aqui, só com API simulada e banco de teste.

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
- [ ] Famílias de estilo que ainda caem no modo imagens paradas: dados e geografia (mapas, gráficos, globo), ilustrado 2D (whiteboard, bonecos palito), textura artesanal (colagem), 3D, interface de tela.
- [ ] Parallax 2.5D com mapa de profundidade (Depth Anything V2 Small ou DA3, só licenças Apache).
- [ ] Vídeo IA (image-to-video via fal.ai) em cenas-chave; o tipo de cena `video` existe no esquema, mas não tem gerador.
- [ ] Estilo a partir de **vídeo de referência** (PySceneDetect/TransNetV2, paleta, ritmo). A base veio na onda 2 (página Referências: planos por corte de cena do FFmpeg, folhas de quadros, ritmo, cortes por minuto e paleta medidos); falta **criar o estilo** a partir dessas medidas (ADR 0003, consequências).
- [ ] **Style Lab**: componentes novos escritos por IA, com portões de teste.
- [ ] LUTs reais (.cube) e os efeitos nativos do `@remotion/effects`; hoje a gradação é feita com filtros CSS.
- [ ] Desempenho: a aberração cromática desenha a imagem 3 vezes com a cadeia de filtros (só o preset terror-analog usa); avaliar uma passada única. O grão já usa texturas pré-geradas.
- [x] Várias faixas de música por vídeo, trocando por segmento: uma cama por faixa, com opção "só a batida, em loop no compasso" (onda 2, 15/11/2026). Não testado ainda com a biblioteca real do usuário num render completo.
- [ ] Biblioteca de músicas: evitar repetir a mesma faixa em vídeos seguidos do mesmo canal.
- [ ] Imagens de referência/âncoras para consistência de personagens.
- [ ] Verificador de novidade (impressões digitais visuais dos últimos vídeos).

### Produto
- [ ] Ramos A/B de versões ("versão B do gancho").
- [x] Checagem de fatos com busca web no roteiro (onda 2, 15/11/2026): painel na aba Roteiro; busca web só pelo Claude Code.
- [x] Repurposing: **Shorts cortados do longo** como projeto derivado que reaproveita a narração e as imagens do longo (onda 1, 15/11/2026).
- [x] **Analytics básico**: métricas por vídeo (coleta do YouTube + lançamento à mão), experimentos com a regra calibrada do canais e lições (onda 1, 15/11/2026).
- [x] Outlier finder: aba "Vídeos fora da curva" do Radar, vídeo vira ideia (onda 2, 15/11/2026).
- [ ] Caixa de comentários.
- [ ] Séries com piloto automático agendado.

### Gestão de canal (ADR 0002)

Feito na onda 1 (15/11/2026): métricas, experimentos e lições (página Desempenho); Shorts do longo (painel na aba Vídeo final); campos novos do canal, pendências e resumo do dia (página Hoje), backup diário com 14 cópias, rotinas automáticas, tempo por etapa e retrabalho a partir dos jobs, créditos estimados antes de gerar a voz, voz final padrão do canal no Novo projeto; banco de ideias com nota, ângulo e evidência (página Ideias); ofertas de afiliado, links na descrição com aviso legal e receita por loja e mês (página Dinheiro).

Ficou para depois:
- [ ] **Lições entrando sozinhas no roteiro**: hoje são registradas e decididas, mas não chegam ao prompt; isso exige mexer em `workers/pipeline/authoring.py` (ADR 0002, consequências). Onda 2.
- [ ] **Importar o histórico do canais** (`C:\Users\lucas\canais\estudio\estudio.db`: ideias, métricas, experimentos, lições, receita) e o `canal.toml` de cada canal. Ficou de fora para não misturar migração de dados com a feature; sem isso os experimentos começam do zero e esperam 3+ vídeos por variante na idade mínima.
- [ ] **CTR, impressões e receita do YouTube automáticos**: a coleta lê views, likes, comentários, retenção, duração média e inscritos ganhos. Impressões e CTR não saem pela API do Analytics por vídeo, e a receita pede outro escopo (`yt-analytics-monetary.readonly`) e canal monetizado; por ora, lançar à mão em Desempenho.
- [ ] **Rotinas só rodam com o Vulto aberto** (laço dentro do processo, sem cron externo, ADR 0001). Com o app fechado, a rotina da manhã e o backup rodam na próxima abertura. Avaliar uma tarefa agendada do Windows se fizer falta.
- [ ] **Tempo de etapas fora do app** (revisão humana, pesquisa): o tempo por etapa sai só dos jobs; uma tabela de execuções à mão fica para quando fizer falta.
- [ ] **Shorts do longo com imagens 16:9**: no vertical elas aparecem recortadas no centro (há aviso). Reenquadrar ou gerar imagem vertical só para as cenas do Short fica para depois.

### Qualidade e pesquisa (ADR 0003, onda 2)

Feito na onda 2 (15/11/2026), 114 testes dos 7 arquivos novos passando e `npm run typecheck` limpo; nada testado ainda contra serviço real:
- [x] **Radar de nichos e vídeos fora da curva** (página Radar, grupo Gestão): catálogo editável (cópia do `nichos.toml`, com nichos a evitar e palavras de alerta), coleta pela Data API com orçamento de 3.500 unidades, veredito Modelar/Observar/Estudar o viral, comparação EUA → Brasil (Aberto/Concorrido/Morno com score), vídeos fora da curva (views ÷ inscritos), canais do sul da Ásia e com alerta marcados e fora das contas. Vídeo fora da curva e canal "Modelar" viram `Idea`; pendência semanal "decidir os nichos"; passo semanal na rotina da manhã (desligado por padrão, pula com 15+ ideias novas esperando).
- [x] **Checagem de fatos** (painel na aba Roteiro): afirmações em `FactClaim` com veredito ok/corrigir/sem_fonte/boato, citação literal conferida na página (`safe_http`, aspas e traços normalizados; usa o texto da captura de página quando o site bloqueia), aplicar correção no roteiro, afirmação "desatualizada" quando o segmento muda. Afirmação aberta, boato ou sem fonte vira erro na conferência antes de publicar.
- [x] **Conferência de áudio** (painel Trilha da aba Prévia): apito/tom puro nos SFX (guarda dos efeitos procedurais e nos eventos da mixagem), curadoria da faixa (trecho só de batida, loop no compasso, espectrograma), música × voz por capítulo pela regra de 14 a 19 dB e várias faixas por vídeo.
- [x] **Conferência do vídeo final** (painel "Antes de publicar" na aba Vídeo final): −14 LUFS e pico ≤ −1 dB, voz × só música nas pausas, trechos pretos, duração, texto a menos de 2,2 s do corte (1,3 s para carimbo), folha de contato com 12 quadros reais, checagem de fatos e áudio. Erro bloqueia envio público/agendado, com "publicar mesmo assim"; privado sempre passa.
- [x] **Provas na tela** ("Página com marca-texto" na mídia da cena): captura da página com o trecho marcado e zoom de 1 a 1,6 que não corta as linhas; vira mídia real comum (o renderer não mudou). Depende do Playwright (acima, "Depende de você").
- [x] **Planos de vídeo de referência** (página Referências): envio do arquivo ou link (yt-dlp opcional), planos, folhas de 5 × 4 quadros, ritmo e paleta.
- [x] **3 miniaturas para o teste A/B** (painel na aba Vídeo final): 3 variações em 3840 × 2160 até 2 MB, conferência em tamanhos pequenos, escolhida usada no envio e no pacote manual, regra do A/B e pendência de trocar em 48 h.

Ficou para depois:
- [ ] **Criar estilo a partir do vídeo de referência**: só a base (medidas) veio; ver "Estilos e render".
- [ ] **Lições entrando sozinhas no roteiro**: continua fora (a onda 2 não mexeu em `authoring.py`).
- [ ] **Checagem de PDF e páginas que só abrem com login**: a conferência literal marca "confira à mão"; sem leitura de PDF.
- [ ] **Primeiro uso real** de cada bloco (coleta do radar, checagem com busca web, captura com o Edge, download por link, conferência sobre um render final de verdade): só testado com serviços simulados; ver os itens em "Depende de você".

## Técnico

- [ ] Migrações completas (renomear/apagar colunas) com Alembic. Colunas novas já são criadas sozinhas ao iniciar (`add_missing_columns`).
- [ ] Limpeza do cache: opção de também apagar narrações e imagens de versões antigas (hoje elas nunca são apagadas) e limpeza automática por limite de espaço.
- [ ] Testes de interface (Playwright) e CI.
