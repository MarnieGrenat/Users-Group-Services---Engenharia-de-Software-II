"""Utilitários de ETag / concorrência otimista.

As funções de grupo já devolvem o ETag no formato `"<version>-<hash8>"`. Aqui
extraímos a versão de um cabeçalho `If-Match` para repassá-la à stored procedure
`sp_update_group`, e geramos um ETag fraco para o recurso de usuário.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from .errors import ServiceError

# Captura a versão (parte inteira) de um valor de ETag: "3-9a8d7c6b" -> 3.
_ETAG_VERSION = re.compile(r'^\s*(?:W/)?"?(\d+)-')


def parse_if_match_version(if_match: str | None) -> int | None:
    """Extrai a versão de um cabeçalho `If-Match`.

    * Ausente → `None` (sem checagem de concorrência).
    * Presente, porém malformado → `412`, em vez de ignorar silenciosamente uma
      pré-condição que o cliente acredita estar em vigor.
    """
    if if_match is None:
        return None

    match = _ETAG_VERSION.match(if_match)
    if match is None:
        raise ServiceError(
            412,
            "STALE_RESOURCE_VERSION",
            "A versão informada em If-Match não corresponde à versão atual do recurso.",
        )
    return int(match.group(1))


def user_etag(user_id: int, group_ids: Iterable[int]) -> str:
    """ETag opaco para o recurso de usuário, derivado de seus grupos."""
    basis = f"{user_id}:" + ",".join(str(gid) for gid in group_ids)
    digest = hashlib.md5(basis.encode("utf-8")).hexdigest()[:8]
    return f'"u-{digest}"'
