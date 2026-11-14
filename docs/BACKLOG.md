# Backlog

Itens que ficaram para depois ou que dependem de algo que não dá para resolver sozinho (contas, chaves, decisões suas). Cada item diz **por que** ficou aqui. Atualizado em 14/11/2026.

## Depende de você (contas, chaves, testes)

- [ ] **Chave do Gemini (Google AI Studio)** para roteiro, agente de edição e voz grátis. Sem ela o app usa o modo prévia.
- [ ] **Instalar o Codex CLI** e fazer login com o ChatGPT Plus (`npm install -g @openai/codex`, depois `codex login`). O adapter `codex_cli` está pronto, mas **não foi testado** porque o Codex não está instalado nesta máquina. Validar: geração sem interação, pasta `~/.codex/generated_images`, tempo por imagem e cota diária real.
- [ ] **Projeto no Google Cloud + cliente OAuth do YouTube** (passo a passo em docs/USO.md §5). O envio ao YouTube foi testado só com respostas simuladas da API; o primeiro envio real deve ser **privado**.
- [ ] **Verificar o canal do YouTube por telefone** (uploads acima de 15 min e thumbnails personalizadas).
- [ ] **Teste cego de voz em PT-BR** (README §8.4): Gemini × Chirp 3 HD × local × ElevenLabs (referência).
- [ ] **Teste cego de imagem** no estilo do canal: Codex × FLUX 3 Klein × Z-Image.
- [ ] Decidir os **nichos** e os estilos prioritários depois do MVP.

## Funcionalidades planejadas (README), ainda não feitas

### Publicação
- [ ] **Driver do Buffer** (TikTok e Instagram pelo plano grátis, API e MCP). Ficou de fora porque a API atual do Buffer precisa ser conferida na documentação oficial e testada com uma conta; hoje há o pacote manual por rede.
- [ ] Hospedagem das URLs públicas que o Buffer e o Instagram exigem (Cloudflare R2).
- [ ] Calendário com slots por canal, fusos e fila de publicação.
- [ ] YouTube: playlists, `localizations` (títulos traduzidos no mesmo vídeo), medidor de cota, várias contas/canais.
- [ ] Driver nativo do Instagram (Standard Access) e teste de visibilidade pública.

### Voz e idiomas
- [ ] **Instalar o Whisper local** (`uv sync --project backend --extra asr`): ativa o controle de qualidade da narração (já implementado: transcreve, mede o erro e tenta de novo) e legendas mais precisas. Ficou opcional porque baixa ~500 MB de modelo na primeira vez.
- [ ] Clonagem da sua voz: assistente de gravação, Applio/RVC e Qwen3-TTS local (o adapter `qwen3_tts_local` é só um marcador).
- [ ] **Modo B multi-idioma** (várias faixas de áudio no mesmo vídeo): tradução com orçamento de duração, time-stretch e pacote para o YouTube Studio.
- [ ] Normalização de números e estrangeirismos para TTS local.
- [ ] Glossário por canal na tradução.

### Estilos e render
- [ ] Famílias de estilo que ainda caem no modo imagens paradas: dados e geografia (mapas, gráficos, globo), ilustrado 2D (whiteboard, bonecos palito), textura artesanal (colagem), 3D, interface de tela.
- [ ] Parallax 2.5D com mapa de profundidade (Depth Anything V2 Small ou DA3, só licenças Apache).
- [ ] Vídeo IA (image-to-video via fal.ai) em cenas-chave; o tipo de cena `video` existe no esquema, mas não tem gerador.
- [ ] Estilo a partir de **vídeo de referência** (PySceneDetect/TransNetV2, paleta, ritmo).
- [ ] **Style Lab**: componentes novos escritos por IA, com portões de teste.
- [ ] LUTs reais (.cube) e os efeitos nativos do `@remotion/effects`; hoje a gradação é feita com filtros CSS.
- [ ] Desempenho: a aberração cromática desenha a imagem 3 vezes com a cadeia de filtros (só o preset terror-analog usa); avaliar uma passada única. O grão já usa texturas pré-geradas.
- [ ] Trilhas da biblioteca do usuário (`audio.music.source: library`) e várias faixas de música por vídeo (hoje vale a primeira faixa ativa).
- [ ] Imagens de referência/âncoras para consistência de personagens.
- [ ] Verificador de novidade (impressões digitais visuais dos últimos vídeos).

### Produto
- [ ] Ramos A/B de versões ("versão B do gancho").
- [ ] Checagem de fatos com busca web no roteiro.
- [ ] Repurposing (Shorts a partir de longos), caixa de comentários, analytics e outlier finder.
- [ ] Séries com piloto automático agendado.

## Técnico

- [ ] Migrações com Alembic (hoje o schema é criado com `create_all`).
- [ ] Limpeza de artefatos órfãos no cache (ex.: blocos de render de versões antigas).
- [ ] O tamanho do bundle da interface (740 kB) pode ser dividido por rota.
- [ ] Testes de interface (Playwright) e CI.
