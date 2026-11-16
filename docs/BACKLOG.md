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
- [ ] **Rodar a importação do canais** (Configurações › Importar do canais, ou `python -m studio importar-canais --dry-run` e depois sem `--dry-run`): traz o histórico e preenche os campos novos de cada canal e as ofertas de afiliado a partir do `canal.toml` de `C:\Users\lucas\canais`. Depois, conferir em Canais o que o importador não reconhece: a voz final do Money Rerun (o `canal.toml` não traz o id da voz) e o motivo de cada horário.
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
- [x] Estilo a partir de **vídeo de referência**: "Criar estilo a partir desta referência" na página Referências (`core/styles/from_reference.py`). Parte do preset mais próximo (proporção, ritmo, gradação), troca duração das cenas, transições, gradação, cores dos cartões/legenda/miniatura, proporção e fps pelas medidas, e a IA (se houver) completa só o resto; salva em Meus estilos com o preset como pai e abre no editor mostrando de onde veio cada valor. Falta: medir presença de texto na tela (o código já usa `stats.text_ratio` se existir) e testar com um vídeo real.
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
- [x] **Lições entrando sozinhas no roteiro**: as lições **aplicadas** do projeto (todos os canais, o canal, o estilo e o formato) entram no pedido do roteiro e dos metadados, as mais recentes primeiro (até 8 e 1.500 caracteres; `workers/analytics/lessons.py`). As usadas ficam em `settings["lessons_used"]` e aparecem no painel "Lições usadas" da aba Roteiro. O storyboard não recebe as lições.
- [x] **Importar o histórico do canais** (15/11/2026): `workers/canais_import` lê o `estudio/estudio.db` (por uma cópia temporária: nada é escrito na pasta do canais) e o `canal.toml` de cada canal, e leva ideias, métricas, experimentos, lições, pendências, receita, campos do canal, horários, monetização e ofertas para as tabelas do Vulto. O canal é associado pelo vínculo de antes, pelo canal do YouTube ou pelo nome (ou criado); só campos vazios são preenchidos; rodar de novo não duplica (as métricas só completam números vazios). Comando `python -m studio importar-canais [--pasta] [--dry-run] [--canal slug=auto|pular|<id>]` e tela com prévia em Configurações › Importar do canais. Ficam de fora: `execucoes` (o tempo por etapa vem das tarefas do Vulto, ADR 0002), `videos` (o canais não guarda o documento do vídeo; ideias, lições e experimentos citam o vídeo no texto e `Experiment.project_ids` fica vazio), métricas de experimento que o Vulto não calcula (`views_48h`, `cliques_afiliado`… viram `views`, com a original anotada na hipótese), radar e eventos. Lições `skill:*` entram com o escopo original e não vão para o roteiro.
- [x] **CTR, impressões e receita do YouTube automáticos**: impressões e CTR da miniatura vêm da YouTube **Reporting API** (relatório `channel_reach_basic_a1`, criado sozinho na primeira coleta; os números chegam 2 dias depois e há histórico de 30 dias), porque a lista de métricas do YouTube Analytics (`reports.query`) não tem essas métricas (conferido em 15/11/2026). Mesmo escopo `yt-analytics.readonly` de antes; é preciso ativar a "YouTube Reporting API" no Google Cloud (a tela avisa). Receita: opcional, marcada ao vincular o canal ("Trazer a receita do YouTube", escopo `yt-analytics-monetary.readonly`); grava receita/RPM por vídeo e a receita do mês em Dinheiro (loja `youtube`, US$, substitui ao coletar de novo; mês lançado à mão não é tocado). Miniaturas A/B mostram o CTR coletado e dizem se é hora de trocar. Só testado com o Google simulado.
- [ ] **CTR em escala incerta**: a documentação da Reporting API não diz se `video_thumbnail_impressions_ctr` vem 0–1 ou 0–100; o Vulto decide por arquivo (algum valor > 1 → 0–100). Conferir no primeiro relatório real.
- [x] **Rotinas com o Vulto fechado**: comando `videos-studio rotinas` (jobs inline, trava `rotinas.lock` contra o app aberto e outra execução) e tarefa agendada do Windows por `scripts\agendar-rotinas.ps1` (ao entrar e todo dia no horário); a página Hoje mostra se a tarefa existe e o comando para instalar. Ver USO, "Rotinas com o Vulto fechado". Ainda não testado com a tarefa de verdade instalada.
- [ ] **Tempo de etapas fora do app** (revisão humana, pesquisa): o tempo por etapa sai só dos jobs; uma tabela de execuções à mão fica para quando fizer falta.
- [x] **Shorts do longo com imagens 16:9**: ponto de foco sugerido por cena (saliência simples), editável com um clique em "Enquadramento vertical" no Short; modo "fundo desfocado" por cena e padrão por Short; "Gerar imagem vertical" pede a imagem de novo em 9:16 (operação "Outra imagem"). O foco vale no recorte e na câmera.

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
- [x] **Criar estilo a partir do vídeo de referência**: feito; ver "Estilos e render".
- [x] **Lições entrando sozinhas no roteiro**: feito (ver "Gestão de canal", acima).
- [ ] **Páginas que só abrem com login e PDF escaneado**: a conferência literal marca "confira à mão" (PDF com texto já é lido página por página; PDF só com imagem precisaria de OCR).
- [ ] **Primeiro uso real** de cada bloco (coleta do radar, checagem com busca web, captura com o Edge, download por link, conferência sobre um render final de verdade): só testado com serviços simulados; ver os itens em "Depende de você".

## Técnico

- [ ] Migrações completas (renomear/apagar colunas) com Alembic. Colunas novas já são criadas sozinhas ao iniciar (`add_missing_columns`).
- [ ] Limpeza do cache: opção de também apagar narrações e imagens de versões antigas (hoje elas nunca são apagadas) e limpeza automática por limite de espaço.
- [ ] Testes de interface (Playwright) e CI.
