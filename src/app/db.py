"""Acesso ao PostgreSQL: pool de conexões e chamada das stored procedures.

Toda interação com o banco passa por aqui. Regras de segurança:

* **Sem SQL dinâmico**: as funções sp_* são chamadas com placeholders (`%s`);
  os valores nunca são interpolados na string SQL (imune a SQL injection).
* **statement_timeout**: cada conexão limita o tempo de execução de queries.
* **Tradução de erros**: falhas do banco viram `ServiceError` — nada de
  stack trace ou detalhe interno vaza para o chamador.

O `Database` é injetado nas rotas via `get_database` (Depends), permitindo
substituí-lo facilmente em testes.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings
from .errors import translate_db_error

_pool: ConnectionPool | None = None


def init_pool() -> None:
    """Abre o pool de conexões. Chamado no startup da aplicação."""
    global _pool
    if _pool is not None:
        return

    settings = get_settings()
    _pool = ConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        kwargs={
            "row_factory": dict_row,
            "options": f"-c statement_timeout={settings.db_statement_timeout_ms}",
        },
        open=True,
    )


def close_pool() -> None:
    """Fecha o pool de conexões. Chamado no shutdown da aplicação."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def _connection() -> Iterator[psycopg.Connection]:
    if _pool is None:
        raise RuntimeError("Pool de conexões não inicializado (init_pool não foi chamado).")
    # O context manager do pool faz COMMIT em caso de sucesso e ROLLBACK em erro.
    with _pool.connection() as conn:
        yield conn


class Database:
    """Executor de stored procedures sobre o pool de conexões."""

    def call(self, function_sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        """Executa uma chamada de stored procedure e devolve todas as linhas.

        `function_sql` deve usar placeholders `%s` para todos os argumentos.
        """
        try:
            with _connection() as conn, conn.cursor() as cur:
                cur.execute(function_sql, params)
                if cur.description is None:  # função RETURNS VOID
                    return []
                return cur.fetchall()
        except psycopg.Error as exc:
            # Converte SQLSTATE de regra de negócio em erro de domínio.
            raise translate_db_error(exc) from exc

    def call_one(self, function_sql: str, params: Sequence[Any]) -> dict[str, Any] | None:
        """Igual a `call`, mas retorna a primeira linha (ou None)."""
        rows = self.call(function_sql, params)
        return rows[0] if rows else None


def get_database() -> Database:
    """Dependência FastAPI: fornece um executor de banco por requisição."""
    return Database()
