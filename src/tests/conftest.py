"""Fixtures de teste: sobe um PostgreSQL efêmero e aplica as migrações.

Cada teste roda contra um banco limpo (tabelas truncadas entre testes). Se as
ferramentas do PostgreSQL não estiverem instaladas, a suíte é ignorada (skip).
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator

import pytest

DB_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "db"))
MIGRATIONS = sorted(glob.glob(os.path.join(DB_DIR, "migrations", "V*.sql")))


def _find_pg_bin() -> str | None:
    """Localiza o diretório bin do PostgreSQL (pg_ctl/initdb)."""
    if shutil.which("pg_ctl"):
        return os.path.dirname(shutil.which("pg_ctl"))
    for path in sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if os.path.exists(os.path.join(path, "pg_ctl")):
            return path
    return None


PG_BIN = _find_pg_bin()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Sobe um cluster PostgreSQL temporário e devolve a DSN."""
    if PG_BIN is None or shutil.which("psql") is None:
        pytest.skip("Ferramentas do PostgreSQL não encontradas.")

    root = tempfile.mkdtemp(prefix="ug_pg_")
    data = os.path.join(root, "data")
    sock = os.path.join(root, "sock")
    os.makedirs(sock)

    def pg(tool: str, *args: str, **kw) -> None:
        subprocess.run([os.path.join(PG_BIN, tool), *args], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)

    try:
        pg("initdb", "-D", data, "-U", "test", "--auth=trust")
        pg("pg_ctl", "-D", data, "-w", "start",
           "-o", f"-c listen_addresses='' -c unix_socket_directories='{sock}'")
        pg("createdb", "-h", sock, "-U", "test", "ugtest")
        for migration in MIGRATIONS:
            subprocess.run(
                ["psql", "-h", sock, "-U", "test", "-d", "ugtest",
                 "-v", "ON_ERROR_STOP=1", "-q", "-f", migration],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
        yield f"postgresql://test@/ugtest?host={sock}"
    finally:
        subprocess.run([os.path.join(PG_BIN, "pg_ctl"), "-D", data, "-w", "stop"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def client(database_url: str):
    """TestClient com o pool apontando para o banco temporário."""
    os.environ["UG_DATABASE_URL"] = database_url

    from app.config import get_settings

    get_settings.cache_clear()  # descarta a DSN padrão memoizada

    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def clean_db(database_url: str) -> None:
    """Limpa as tabelas antes de cada teste, garantindo isolamento."""
    sock = database_url.split("host=", 1)[1]
    subprocess.run(
        ["psql", "-h", sock, "-U", "test", "-d", "ugtest", "-q", "-c",
         "TRUNCATE user_groups, groups, users RESTART IDENTITY CASCADE;"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def write_headers() -> dict[str, str]:
    return {"X-Service-Id": "assessment-service", "X-User-Id": "1001"}


@pytest.fixture
def read_headers() -> dict[str, str]:
    return {"X-Service-Id": "report-service", "X-User-Id": "1001"}
