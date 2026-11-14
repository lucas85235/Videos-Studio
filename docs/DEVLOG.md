# Diário de desenvolvimento

Registro do que foi feito em cada marco, para manter o histórico e poder retomar de onde parou.

## Marcos

| # | Marco | Status |
|---|---|---|
| M0 | Repositório, ADR de stack, backlog, seção de estilos do README | feito |
| M1 | Backend: configuração, banco, FastAPI | feito |
| M2 | Núcleo: estilos, documento de projeto, versões, patches | feito |
| M3 | Provedores com cadeias de fallback, cofre de credenciais, custos | feito |
| M4 | Renderer Remotion com render por blocos | feito |
| M5 | Grafo de artefatos + pipeline do Short ponta a ponta | feito |
| M6 | API completa | feito |
| M7 | Interface web | feito |
| M8 | Agente de edição por instrução | feito |
| M9 | Publicação: pacote manual + YouTube nativo (Buffer no backlog) | feito |
| M10 | Documentação de uso e backlog final | feito |

## 14/11/2026

- **M0**: repositório criado (`main` com o README; desenvolvimento no branch `develop`). O ADR 0001 registra a stack ajustada para Windows sem Docker. A pesquisa de estilos virou a nova §5 do README.
- **Ambiente**: Node 24.21, npm 11.19, Python 3.12.10 + uv 0.11.7, Git 2.53, FFmpeg 9.0.2 (com NVENC), RTX 4070 Laptop 8 GB (driver 610.74), Windows 26200.9974. Sem Docker e sem Codex CLI. Voz SAPI pt-BR disponível: "Microsoft Maria Desktop".
- **M1–M2**: backend Python (`backend/studio`): configuração, SQLite, StyleSpec + 6 presets (famílias 1, 2 e 7), documento de projeto com IDs estáveis, operações de patch tipadas e versões.
- **M3**: provedores com cadeia de fallback. Claude pelo SDK oficial `anthropic` 2.8 (`claude-opus-5-5`, saída estruturada via `output_config`, fallback de recusa `fallbacks="default"`), Gemini (texto e TTS), Chirp 3 HD, ElevenLabs, Codex CLI, BFL, fal, OpenAI Images, Pexels, voz do Windows (prévia), mocks. Cofre Fernet + keyring.
- **M4**: renderer Remotion 4.0.543 com React 19.3. Servidor de render persistente. Teste de fumaça: primeiro bloco em 62 s (bundle + navegador), seguinte em 16 s para 5 s de vídeo com efeitos pesados.
- **M5–M6**: build incremental por hash, áudio procedural (trilhas e SFX), mix com ducking e −14 LUFS, montagem final com NVENC, SRT, thumbnails, jobs, agente de edição, API. Render real de um Short de 64 s em 182 s (incluindo bundle e navegador).

- **M7–M8**: interface Vite + React (projetos, roteiro, cenas, prévia com Remotion Player e chat de edição, vídeo e publicação, versões, estilos, canais, configurações). Teste pela interface: projeto de demonstração criado, roteiro aprovado, prévia gerada; uma edição por instrução ("cena 3 mais escura, com zoom, legenda maior") regenerou **nada** além da timeline (7 narrações, 34 imagens e o mix reaproveitados).
- **M9**: publicação nativa no YouTube (OAuth PKCE, upload resumível, agendamento, thumbnail, legendas, rótulo de IA), testada com API simulada.
- **Bug encontrado e corrigido**: a voz do Windows relata tempos de palavra além do fim do áudio; algumas cenas começavam depois do segmento seguinte e o vídeo final saía deslocado (0,8 s de preto em 60 s no demo). Timeline agora normaliza tempos e garante cenas contínuas; legendas montadas por cena. Testes de regressão adicionados.
- **M10**: README atualizado (estado atual, stack real, estrutura), docs/USO.md e backlog.
- **Controle de qualidade da narração**: com o Whisper local instalado, cada trecho narrado é transcrito e comparado ao roteiro (taxa de erro de palavras); provedores não determinísticos tentam de novo e fica a melhor tomada. A transcrição é reaproveitada no alinhamento.
- **Desempenho do render**: o grão agora usa 4 texturas de ruído pré-geradas (`assets/textures`) com deslocamento aleatório por quadro, no lugar de ruído SVG calculado a cada quadro. Teste de fumaça (150 quadros 1080×1920 com 7 efeitos): render quente de 15,8 s para 12,0 s (−24%). Intensidade calibrada em cinza médio: desvio ≈ 2 + 12 × amount (0,35 → ~6,8/255), média neutra; o grão antigo dava ~3/255 e clareava a imagem. As texturas entram na versão do renderer (mudou textura, os blocos são refeitos).
- **Biblioteca de músicas**: faixas do usuário em `library/music` (clima pela subpasta/nome), escolha determinística pela semente, faixa fixável por projeto (`set_music.track`), repetição/corte com fade e −16 LUFS em estéreo. Biblioteca vazia cai na trilha procedural com aviso. Interface: cartão em Configurações (abrir pasta, contagem por clima), fonte da trilha no editor de estilo e cartão *Trilha* na prévia.
- **Limpeza do cache** (marcar e varrer): cada build grava o manifesto dos artefatos usados; a limpeza preserva o que as últimas prévias/renders usam (builds antigos: blocos recalculados pela timeline salva) e apaga blocos, finais e mixagens sem uso, falhas, registros sem arquivo, pastas órfãs, bundles velhos e temporários. Narração e imagem nunca são varridas; 24 h de carência. Cartão *Armazenamento* em Configurações e `videos-studio clean [--dry-run]`.
- **Interface mais leve**: páginas carregadas sob demanda; o Player do Remotion só carrega na aba de prévia. Bundle inicial de 749 kB para 295 kB.
- **YouTube: títulos traduzidos** (`localizations`): opção no envio que inclui título e descrição dos outros idiomas do projeto no mesmo vídeo. Testado com API simulada.
- **Agenda e horários por canal**: horários semanais com fuso (IANA, pacote `tzdata` no Windows) em `Channel.settings.posting`; próximos horários livres (20 min de antecedência, 45 min entre vídeos do mesmo canal); sugestões no cartão do YouTube e no pacote manual (horário planejado); página Agenda (agendados, pacotes por postar com "marcar como postado", publicados recentes).
- **Bug corrigido (fuso no banco)**: o SQLite descartava o fuso das datas (18:00-03:00 voltava como 18:00 UTC) e a API devolvia horários sem fuso, que a interface lia como hora local (3 h de erro, ex.: aviso de job com falha). Novo tipo `UTCDateTime` grava em UTC e devolve com fuso.
- **Migração leve do banco**: ao iniciar, colunas e índices novos dos modelos são criados em tabelas existentes (linhas antigas recebem o padrão simples do modelo). Evita quebrar o banco do usuário em atualizações.
- **Fila do YouTube**: vídeos renderizados e ainda não enviados aparecem na Agenda; o agendamento em lote coloca cada um no próximo horário livre do seu canal (considera envios já na fila) e enfileira os uploads.
- **Claude pela conta**: novo provedor `claude_code`, primeiro da cadeia de LLM (conta → Gemini → API). Chama o Claude Code instalado (`claude -p --output-format json --json-schema … --tools "" --safe-mode --no-session-persistence`), com prompt pela entrada padrão (limite de ~32 mil caracteres da linha de comando no Windows) e sistema em arquivo; remove `ANTHROPIC_API_KEY` do ambiente para não cair na cobrança da API. Disponibilidade por `claude auth status` (em cache por 5 min; exige login `claude.ai`). Limite do plano vira falha temporária (a cadeia segue sem marcar o dia como esgotado). Custo registrado como zero, com o equivalente da API nos detalhes. Teste real: roteiro do Short em 17 s.
- **Bug grave corrigido (instrução lida na narração)**: o Gemini TTS recebia `"estilo: texto"` e lia a instrução em voz alta; o estilo documentário já tinha dois-pontos. Confirmado transcrevendo os áudios (Whisper): o pedido antigo vazava; o formato de notas de direção + transcrição ainda vazava ou repetia o texto (5 de 6 tentativas no total); só o texto saiu certo em todas. A voz do Gemini agora recebe só o texto, com a velocidade do estilo aplicada no áudio (`atempo`). Nova conferência `narration_problems`: com transcrição, detecta instrução lida (mesmo em trechos longos, onde o WER fica em ~6%), repetição e fala fora do roteiro; sem transcrição, duração acima de 1,5× o esperado + 1,5 s. Narração com problema é refeita (até 3 tentativas, a última sem estilo); o que sobrar vira aviso. Cache de narração invalidado (`v: 2`). Teste real: gancho e encerramento do estilo documentário limpos na primeira tentativa.
- **Whisper**: o `faster-whisper` quebrava ao abrir áudio com o `av` 19 (`metadata_errors`); agora o áudio é decodificado pelo ffmpeg do app. A GPU era aceita na criação do modelo e falhava ao transcrever (sem `cublas64_12.dll`); agora cai para a CPU. Extra `asr` instalado e incluído no `setup.ps1`.
- **Roteiro de exemplo não entra mais escondido**: no teste do usuário o Gemini (texto) deu 503/tempo esgotado e a cadeia caiu no LLM de regras, gerando um roteiro genérico que foi aprovado e narrado. Para LLM, o fallback de prévia só vale quando nenhum provedor real está configurado; senão o job falha com o motivo.
- **Player**: velocidade 1x–2x na prévia (controle nativo do Remotion Player) e no vídeo final (botões que mantêm a escolha ao trocar de vídeo).

## Como retomar

- Backend: `cd backend`, `$env:UV_PROJECT_ENVIRONMENT="$env:USERPROFILE\VideosStudioData\.venv-backend"`, `uv run pytest`.
- Render real nos testes: `$env:VS_TEST_RENDER="1"`.
