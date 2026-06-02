"""Autenticação e autorização na camada de aplicação.

Modelo de confiança (ver README/openapi):

* O transporte é protegido por **mTLS** no service mesh — fora do escopo deste
  código. É essa camada que torna os cabeçalhos abaixo confiáveis.
* `X-Service-Id` identifica o serviço chamador. Aceitamos apenas uma allowlist
  fixa; qualquer outro valor (ou ausência) → `401`. **Fail closed**.
* `X-User-Id` é o usuário final em nome de quem a operação ocorre. Deve ser um
  inteiro positivo; caso contrário → `401`.
* Serviços somente-leitura que tentam escrever → `403 READ_ONLY_SERVICE`.

Este serviço **não valida JWT**: a validação do token é feita no edge do
consumidor; aqui apenas confiamos na identidade propagada sobre mTLS.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from fastapi import Depends, Header

from .errors import ServiceError


class Permission(str, Enum):
    READ = "read"          # apenas operações GET
    WRITE = "write"        # CRUD completo


# Allowlist fixa de serviços consumidores e suas permissões.
_SERVICE_PERMISSIONS: dict[str, Permission] = {
    "assessment-service": Permission.WRITE,
    "report-service": Permission.READ,
    "survey-application": Permission.READ,
}


@dataclass(frozen=True)
class CallerContext:
    """Identidade resolvida do chamador, propagada às rotas."""

    service_id: str
    permission: Permission
    user_id: int


def authenticate(
    x_service_id: str | None = Header(default=None, alias="X-Service-Id"),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> CallerContext:
    """Valida os cabeçalhos de identidade e devolve o contexto do chamador.

    Usado como dependência em todas as rotas (autenticação obrigatória).
    """
    if not x_service_id:
        raise ServiceError(401, "AUTHENTICATION_REQUIRED", "Cabeçalho X-Service-Id ausente.")

    permission = _SERVICE_PERMISSIONS.get(x_service_id)
    if permission is None:
        raise ServiceError(
            401,
            "UNKNOWN_SERVICE",
            "O serviço informado em X-Service-Id não está autorizado a consumir esta API.",
        )

    if not x_user_id:
        raise ServiceError(401, "AUTHENTICATION_REQUIRED", "Cabeçalho X-User-Id ausente.")

    return CallerContext(
        service_id=x_service_id,
        permission=permission,
        user_id=_parse_user_id(x_user_id),
    )


def require_write(caller: CallerContext = Depends(authenticate)) -> CallerContext:
    """Dependência para rotas de escrita: exige permissão WRITE."""
    if caller.permission is not Permission.WRITE:
        raise ServiceError(
            403,
            "READ_ONLY_SERVICE",
            f"O serviço {caller.service_id} possui acesso somente de leitura.",
        )
    return caller


def _parse_user_id(raw: str) -> int:
    """Converte X-User-Id em inteiro positivo, rejeitando valores inválidos."""
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value <= 0:
        raise ServiceError(
            401,
            "AUTHENTICATION_REQUIRED",
            "Cabeçalho X-User-Id malformado: deve ser um inteiro positivo.",
        )
    return value
