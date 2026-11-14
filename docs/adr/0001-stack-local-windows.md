# ADR 0001: Stack ajustada para rodar num notebook Windows sem Docker

- **Status:** aceita
- **Data:** 14/11/2026
- **Contexto:** o README (§11) previa PostgreSQL + Redis + Celery em Docker Compose e frontend em Next.js. O notebook (Galaxy Book3 Ultra, Windows 11) **não tem Docker**, o app é de **um usuário só** e o repositório está numa pasta do **OneDrive**.

## Decisões

1. **Banco SQLite (modo WAL) + fila de jobs própria, dentro do processo**, no lugar de PostgreSQL + Redis + Celery.
   - O Celery não tem suporte oficial a Windows desde a versão 4. Redis e Postgres exigiriam Docker ou instalações extras.
   - Um app local de usuário único não precisa de broker. Os jobs ficam salvos no banco, então sobrevivem a reinícios.
   - O SQLAlchemy mantém a porta aberta: basta trocar `VS_DATABASE_URL` para usar PostgreSQL no futuro.
   - As filas do README continuam existindo como filas lógicas com concorrência própria: `gpu` (1), `codex` (1), `render` (1), `cpu` (4), `net` (4) e `publish` (2).

2. **Frontend em Vite + React (SPA) servido pelo próprio FastAPI**, no lugar de Next.js.
   - O app não precisa de SSR nem de rotas de servidor no Node: toda a lógica fica no backend Python.
   - É um processo a menos, e a pasta `.next` (cache pesado) não fica sincronizando no OneDrive.
   - O **Remotion Player** funciona igual em qualquer app React, então a prévia continua com o mesmo código do render.

3. **Dados e ambiente Python fora do OneDrive**:
   - os dados ficam em `VS_DATA_DIR`, que por padrão é `%USERPROFILE%\VideosStudioData` (banco, cache de artefatos, mídia);
   - o venv do backend fica em `%USERPROFILE%\VideosStudioData\.venv-backend` (via `UV_PROJECT_ENVIRONMENT`).

4. **Código Python num pacote único `studio`** em `backend/`, com os subpacotes `core`, `providers`, `workers` e `api`. É a mesma divisão lógica do README §13, num formato que o Python empacota direito.

5. **Render**:
   - um **servidor de render em Node** (API do `@remotion/renderer`) mantém o navegador aberto e renderiza **um bloco por grupo de cenas**;
   - os blocos são identificados pelo **conteúdo** (hash), então mudanças de tempo em outros pontos não os invalidam;
   - elementos que dependem do tempo global do vídeo (barra de progresso, marca d'água) entram na **montagem final pelo FFmpeg**, para não invalidar os blocos.

6. **Provedores de desenvolvimento offline**, para testar o pipeline inteiro sem nenhuma chave:
   - LLM `mock`, que gera roteiro e prompts por regras;
   - voz **SAPI do Windows** ("Microsoft Maria", pt-BR), **só para prévia**: o app bloqueia esse provedor na publicação, porque o direito de uso comercial não está claro;
   - imagens **procedurais** (`placeholder`);
   - música e SFX **procedurais via FFmpeg** (drones, impactos, whooshes), livres de direitos por construção.

## Consequências

- `scripts/setup.ps1` instala tudo com `uv` e `npm`, sem Docker.
- Rodar com PostgreSQL ou com um worker separado continua possível, mas fica no backlog.
- O README §11 e §13 foram atualizados para refletir esta decisão.
