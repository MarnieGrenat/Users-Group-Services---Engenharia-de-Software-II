"""Erros de domínio e tradução de falhas do banco para o schema `Erro`.

As regras de negócio são sinalizadas pelas stored procedures via SQLSTATE
customizado (ver db/README.md). Aqui traduzimos esses códigos para
`ServiceError`, que a camada HTTP serializa no formato `Erro` (RFC 7807).
"""

from __future__ import annotations

from datetime import datetime, timezone
from http import HTTPStatus
from typing import Any

import psycopg

# SQLSTATE devolvido pelas funções sp_* -> (status HTTP, errorCode do contrato).
_SQLSTATE_MAP: dict[str, tuple[int, str]] = {
    "UG001": (404, "GROUP_NOT_FOUND"),
    "UG002": (410, "GROUP_DELETED"),
    "UG003": (404, "USER_NOT_FOUND"),
    "UG004": (404, "MEMBERSHIP_NOT_FOUND"),
    "UG005": (412, "STALE_RESOURCE_VERSION"),
    "23514": (422, "VALIDATION_FAILED"),  # check_violation (ex.: group_name curto)
}

_GENERIC_500_MESSAGE = "Ocorreu um erro inesperado ao processar a requisição."


class ServiceError(Exception):
    """Erro de domínio com a forma exata exigida pelo contrato."""

    def __init__(
        self,
        status: int,
        error_code: str,
        message: str,
        details: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_code = error_code
        self.message = message
        self.details = details or []


def translate_db_error(exc: psycopg.Error) -> ServiceError:
    """Converte uma exceção do psycopg em `ServiceError`.

    Falhas não mapeadas viram `500 INTERNAL_ERROR` genérico — nunca expomos
    detalhes internos do banco ao chamador.
    """
    mapping = _SQLSTATE_MAP.get(exc.sqlstate or "")
    if mapping is None:
        return ServiceError(500, "INTERNAL_ERROR", _GENERIC_500_MESSAGE)

    status, error_code = mapping
    if status == 422:
        message = "A requisição não passou na validação de campos."
    else:
        # As funções sp_* colocam a mensagem amigável no DETAIL da exceção.
        message = exc.diag.message_detail or exc.diag.message_primary or error_code
    return ServiceError(status, error_code, message)


def build_error_body(
    status: int,
    error_code: str,
    message: str,
    path: str,
    details: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Monta o corpo de resposta no schema `Erro`."""
    body: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "error": HTTPStatus(status).phrase,
        "errorCode": error_code,
        "message": message,
        "path": path,
    }
    if details:
        body["details"] = details
    return body
