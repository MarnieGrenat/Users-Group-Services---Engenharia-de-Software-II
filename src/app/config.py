"""Configuração da aplicação, carregada de variáveis de ambiente.

Nenhum segredo é embutido no código: a DSN do banco e demais parâmetros vêm
do ambiente (ou de um arquivo `.env` em desenvolvimento).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UG_", env_file=".env", extra="ignore")

    app_name: str = "User & Group Service"

    # DSN PostgreSQL. Em produção, aponte para um usuário com privilégio mínimo
    # (apenas EXECUTE nas funções sp_*), nunca o superusuário.
    database_url: str = "postgresql://localhost:5432/ugtest"

    # Limites do pool de conexões — protegem o banco de exaustão de conexões.
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10

    # Limite de tempo por statement (ms): barra consultas patológicas/abusivas.
    db_statement_timeout_ms: int = 5_000

    # Backend-only: por padrão escutamos apenas em loopback. A exposição via
    # service mesh (mTLS) é responsabilidade da infraestrutura.
    host: str = "127.0.0.1"
    port: int = 8000


@lru_cache
def get_settings() -> Settings:
    """Retorna as configurações (memoizadas para reuso em todo o processo)."""
    return Settings()
