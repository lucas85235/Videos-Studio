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
| M7 | Interface web | em andamento |
| M8 | Agente de edição por instrução (backend feito; falta a interface) | parcial |
| M9 | Publicação (pacote manual feito; YouTube nativo pendente) | parcial |
| M10 | Documentação de uso e backlog final | pendente |

## 14/11/2026

- **M0**: repositório criado (`main` com o README; desenvolvimento no branch `develop`). O ADR 0001 registra a stack ajustada para Windows sem Docker. A pesquisa de estilos virou a nova §5 do README.
- **Ambiente**: Node 24.21, npm 11.19, Python 3.12.10 + uv 0.11.7, Git 2.53, FFmpeg 9.0.2 (com NVENC), RTX 4070 Laptop 8 GB (driver 610.74), Windows 26200.9974. Sem Docker e sem Codex CLI. Voz SAPI pt-BR disponível: "Microsoft Maria Desktop".
- **M1–M2**: backend Python (`backend/studio`): configuração, SQLite, StyleSpec + 6 presets (famílias 1, 2 e 7), documento de projeto com IDs estáveis, operações de patch tipadas e versões.
- **M3**: provedores com cadeia de fallback. Claude pelo SDK oficial `anthropic` 2.8 (`claude-opus-5-5`, saída estruturada via `output_config`, fallback de recusa `fallbacks="default"`), Gemini (texto e TTS), Chirp 3 HD, ElevenLabs, Codex CLI, BFL, fal, OpenAI Images, Pexels, voz do Windows (prévia), mocks. Cofre Fernet + keyring.
- **M4**: renderer Remotion 4.0.543 com React 19.3. Servidor de render persistente. Teste de fumaça: primeiro bloco em 62 s (bundle + navegador), seguinte em 16 s para 5 s de vídeo com efeitos pesados.
- **M5–M6**: build incremental por hash, áudio procedural (trilhas e SFX), mix com ducking e −14 LUFS, montagem final com NVENC, SRT, thumbnails, jobs, agente de edição, API. Render real de um Short de 64 s em 182 s (incluindo bundle e navegador).

## Como retomar

- Backend: `cd backend`, `$env:UV_PROJECT_ENVIRONMENT="$env:USERPROFILE\VideosStudioData\.venv-backend"`, `uv run pytest`.
- Render real nos testes: `$env:VS_TEST_RENDER="1"`.
