"""Migrações do banco com Alembic.

O `init_db` cria as tabelas novas (`create_all`), as colunas novas (`add_missing_columns`) e depois roda
`alembic upgrade head` (`studio.db.migrate`). Por isso as migrações daqui só precisam cuidar do que esses dois
não fazem: renomear e apagar colunas (e mexer em dados). Use as funções de `studio.migrations.helpers`, que
aguentam o caso em que `add_missing_columns` já criou a coluna nova, vazia, antes da migração rodar.
"""
