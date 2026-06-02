#!/usr/bin/env bash
#
# Gerencia o ambiente de desenvolvimento dockerizado do User & Group Service.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

TEST_IMAGE="ug-service-test"

# Detecta o Docker Compose sob demanda (plugin v2 ou binário v1 standalone) e o
# executa. Comandos que não usam compose (test, help) não exigem sua presença.
COMPOSE=()
dc() {
  if [[ ${#COMPOSE[@]} -eq 0 ]]; then
    if docker compose version >/dev/null 2>&1; then
      COMPOSE=(docker compose)
    elif command -v docker-compose >/dev/null 2>&1; then
      COMPOSE=(docker-compose)
    else
      echo "Erro: Docker Compose não encontrado (instale o Docker Compose)." >&2
      exit 1
    fi
  fi
  "${COMPOSE[@]}" "$@"
}

usage() {
  cat <<'EOF'
Uso: ./dev.sh <comando> [args]

  up [--build]    Sobe o stack (api + db) em segundo plano.
  down            Para o stack, preservando os dados do banco.
  reset           Remove containers e o volume do banco e sobe tudo do zero
                  (reaplica as migrações).
  restart         Reinicia os serviços.
  build           (Re)constrói a imagem da api.
  logs [serviço]  Acompanha os logs (api por padrão; use 'db' para o banco).
  ps              Mostra o estado dos serviços.
  shell           Abre um shell no container da api.
  psql            Abre o psql no container do banco.
  test [args]     Roda a suíte pytest em um container (Postgres efêmero).
                  Argumentos extras são repassados ao pytest (ex.: -k membership).
  help            Mostra esta ajuda.
EOF
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  up)
    dc up -d "$@"
    echo "API disponível em http://localhost:8000  (Swagger UI em /docs)"
    ;;
  down)
    dc down "$@"
    ;;
  reset)
    dc down -v
    dc up -d --build
    echo "Banco recriado e migrações reaplicadas."
    ;;
  restart)
    dc restart "$@"
    ;;
  build)
    dc build "$@"
    ;;
  logs)
    dc logs -f "${1:-api}"
    ;;
  ps)
    dc ps
    ;;
  shell)
    dc exec api /bin/bash
    ;;
  psql)
    dc exec db psql -U ug -d ugdb
    ;;
  test)
    docker build --target test -t "$TEST_IMAGE" "$ROOT"
    docker run --rm "$TEST_IMAGE" pytest "$@"
    ;;
  help | -h | --help)
    usage
    ;;
  *)
    echo "Comando desconhecido: $cmd" >&2
    echo
    usage
    exit 1
    ;;
esac
