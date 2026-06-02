"""Serviço de grupos e memberships — fina camada sobre as stored procedures.

Cada função mapeia 1:1 para uma função `sp_*` do banco. Toda a lógica de
negócio (soft-delete, idempotência, concorrência, contagem de membros) vive no
PostgreSQL; aqui apenas chamamos com parâmetros vinculados e devolvemos linhas.
"""

from __future__ import annotations

from typing import Any

from ..db import call, call_one


def create_group(group_name: str, description: str | None) -> dict[str, Any]:
    """POST /v1/group → sp_create_group."""
    return call_one(
        "SELECT * FROM sp_create_group(%s, %s)",
        (group_name, description),
    )


def list_groups(
    page: int,
    page_size: int,
    active: bool | None,
    search: str | None,
) -> list[dict[str, Any]]:
    """GET /v1/group → sp_list_groups (cada linha inclui `total_items`)."""
    return call(
        "SELECT * FROM sp_list_groups(%s, %s, %s, %s)",
        (page, page_size, active, search),
    )


def get_group(group_id: int) -> dict[str, Any]:
    """GET /v1/group/{id} → sp_get_group (levanta 404/410 via ServiceError)."""
    return call_one("SELECT * FROM sp_get_group(%s)", (group_id,))


def get_group_users(group_id: int) -> list[dict[str, Any]]:
    """Usuários de um grupo → sp_get_group_users."""
    return call("SELECT * FROM sp_get_group_users(%s)", (group_id,))


def update_group(
    group_id: int,
    group_name: str | None,
    update_name: bool,
    description: str | None,
    update_description: bool,
    expected_version: int | None,
) -> dict[str, Any]:
    """PUT /v1/group/{id} → sp_update_group.

    `update_name`/`update_description` indicam quais campos alterar (permite
    definir `description = NULL` explicitamente). `expected_version` aplica a
    concorrência otimista do `If-Match`.
    """
    return call_one(
        "SELECT * FROM sp_update_group(%s, %s, %s, %s, %s, %s)",
        (group_id, group_name, update_name, description, update_description, expected_version),
    )


def delete_group(group_id: int) -> None:
    """DELETE /v1/group/{id} (soft-delete) → sp_delete_group."""
    call("SELECT sp_delete_group(%s)", (group_id,))


def add_user_to_group(group_id: int, user_id: int) -> dict[str, Any]:
    """PUT /v1/group/{id}/user/{uid} → sp_add_user_to_group.

    A linha devolvida traz `created` (True → 201; False → 200).
    """
    return call_one(
        "SELECT * FROM sp_add_user_to_group(%s, %s)",
        (group_id, user_id),
    )


def remove_user_from_group(group_id: int, user_id: int) -> None:
    """DELETE /v1/group/{id}/user/{uid} → sp_remove_user_from_group."""
    call("SELECT sp_remove_user_from_group(%s, %s)", (group_id, user_id))
