# ADR 0002: Migrações do banco com Alembic, depois da migração leve

- **Status:** aceita
- **Data:** 15/11/2026
- **Contexto:** o banco (SQLite) já se atualizava sozinho para colunas e tabelas novas (`create_all` + `add_missing_columns`), mas renomear ou apagar colunas exigia mexer à mão no banco do usuário. Não podemos quebrar bancos que já existem, nem obrigar o usuário a rodar comandos.

## Decisões

1. **Alembic** (licença MIT, do mesmo autor do SQLAlchemy) com as revisões em `backend/studio/migrations/versions`.
2. **Base sem mudanças** (`0001_base`): o banco como estava antes do Alembic. Banco antigo, sem a tabela `alembic_version`, é marcado nela e sobe até a última revisão. Banco novo (nenhuma tabela do app antes do `create_all`) já nasce com o esquema atual e é marcado direto na última revisão.
3. **Ordem no `init_db`**: `create_all` → `add_missing_columns` → `alembic upgrade head`. Colunas e tabelas novas continuam sem precisar de revisão. Como a coluna nova pode já existir quando a revisão roda, as revisões usam `studio.migrations.helpers` (`rename_column` copia os dados para a coluna nova se ela já existe; `drop_column` e `rename_column` não fazem nada se já foram aplicadas).
4. **Chaves estrangeiras desligadas durante a migração** (`PRAGMA foreign_keys=OFF`, fora de transação, religadas no fim, com `PRAGMA foreign_key_check`). O modo batch do Alembic no SQLite recria a tabela; com as chaves ligadas, apagar a tabela velha de `projects` dispararia o `ON DELETE CASCADE` e levaria versões e publicações. A documentação do Alembic (página *Running "Batch" Migrations for SQLite*) pede o mesmo.
5. **Linha de comando** para criar revisões: `uv run alembic revision -m "..."` dentro de `backend/` (o `alembic.ini` não tem URL; o `env.py` usa o banco configurado no app).

## Consequências

- Atualizar o app nunca exige passo manual: as revisões rodam ao iniciar, e o `scripts\start.ps1` sincroniza o venv quando o `backend/uv.lock` muda (`uv sync --inexact`, que não remove pacotes). Se o Alembic faltar no venv e só existir a base, o `init_db` avisa e segue; com revisão nova, para com a mensagem de rodar o `setup.ps1`.
- Banco marcado numa revisão que esta versão do app não conhece (voltou para uma versão anterior, trocou de branch): o `init_db` avisa e pula as migrações em vez de derrubar o app, como era antes com `add_missing_columns`.
- Revisões precisam ser idempotentes (os helpers ajudam). `downgrade` não roda pelo app.
- Testes em `backend/tests/test_migrations.py` cobrem banco novo, banco antigo sintético e uma revisão futura que renomeia e apaga coluna em `projects` mantendo as versões.
