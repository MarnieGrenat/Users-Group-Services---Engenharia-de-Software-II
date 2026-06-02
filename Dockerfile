# syntax=docker/dockerfile:1

# =============================================================================
# User & Group Service — imagem multi-stage.
#   * base    : dependências + código (camada compartilhada)
#   * runtime : imagem enxuta de execução (usada pelo compose, usuário não-root)
#   * test    : base + PostgreSQL para a suíte pytest (sobe um cluster efêmero)
# =============================================================================

# --------------------------------------------------------------------------- #
# base
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

# Instala as dependências primeiro (camada cacheável entre builds).
COPY src/requirements.txt ./src/requirements.txt
RUN pip install -r src/requirements.txt

# Código da aplicação e migrações (estrutura do repositório preservada,
# pois os testes referenciam ../../db a partir de src/tests).
COPY src/ ./src/
COPY db/ ./db/

# Usuário sem privilégios (não rodar como root).
RUN useradd --create-home --uid 1000 app && chown -R app:app /workspace

WORKDIR /workspace/src

# --------------------------------------------------------------------------- #
# runtime
# --------------------------------------------------------------------------- #
FROM base AS runtime
USER app
EXPOSE 8000
# Dentro do container escutamos em todas as interfaces; a exposição segura
# (mTLS / service mesh) é responsabilidade da infraestrutura.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# --------------------------------------------------------------------------- #
# test
# --------------------------------------------------------------------------- #
FROM base AS test
# A suíte sobe um PostgreSQL efêmero (initdb/pg_ctl), então precisamos do
# servidor e do cliente dentro da imagem.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql postgresql-client \
    && rm -rf /var/lib/apt/lists/*
RUN pip install -r requirements-dev.txt
USER app
CMD ["pytest", "-q"]
