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

## Como retomar

- Backend: `cd backend`, `$env:UV_PROJECT_ENVIRONMENT="$env:USERPROFILE\VideosStudioData\.venv-backend"`, `uv run pytest`.
- Render real nos testes: `$env:VS_TEST_RENDER="1"`.
