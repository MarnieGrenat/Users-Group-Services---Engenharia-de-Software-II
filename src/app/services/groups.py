"""Serviço de grupos e memberships — fina camada sobre as stored procedures.

Cada método mapeia 1:1 para uma função `sp_*` do banco. Toda a lógica de
negócio (soft-delete, idempotência, concorrência, contagem de membros) vive no
PostgreSQL; aqui apenas chamamos com parâmetros vinculados e devolvemos linhas.

O `Database` é injetado via `Depends`, tornando o serviço fácil de testar.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends

from ..db import Database, get_database


class GroupService:
    def __init__(self, db: Database = Depends(get_database)) -> None:
        self._db = db

    def create_group(self, group_name: str, description: str | None) -> dict[str, Any]:
        """POST /v1/group → sp_create_group."""
        return self._db.call_one(
            "SELECT * FROM sp_create_group(%s, %s)",
            (group_name, description),
        )

    def list_groups(
        self,
        page: int,
        page_size: int,
        active: bool | None,
        search: str | None,
    ) -> list[dict[str, Any]]:
        """GET /v1/group → sp_list_groups (cada linha inclui `total_items`)."""
        return self._db.call(
            "SELECT * FROM sp_list_groups(%s, %s, %s, %s)",
            (page, page_size, active, search),
        )

    def get_group(self, group_id: int) -> dict[str, Any]:
        """GET /v1/group/{id} → sp_get_group (levanta 404/410 via ServiceError)."""
        return self._db.call_one("SELECT * FROM sp_get_group(%s)", (group_id,))

    def get_group_users(self, group_id: int) -> list[dict[str, Any]]:
        """Usuários de um grupo → sp_get_group_users."""
        return self._db.call("SELECT * FROM sp_get_group_users(%s)", (group_id,))

    def update_group(
        self,
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
        return self._db.call_one(
            "SELECT * FROM sp_update_group(%s, %s, %s, %s, %s, %s)",
            (group_id, group_name, update_name, description, update_description, expected_version),
        )

    def delete_group(self, group_id: int) -> None:
        """DELETE /v1/group/{id} (soft-delete) → sp_delete_group."""
        self._db.call("SELECT sp_delete_group(%s)", (group_id,))

    def add_user_to_group(self, group_id: int, user_id: int) -> dict[str, Any]:
        """PUT /v1/group/{id}/user/{uid} → sp_add_user_to_group.

        A linha devolvida traz `created` (True → 201; False → 200).
        """
        return self._db.call_one(
            "SELECT * FROM sp_add_user_to_group(%s, %s)",
            (group_id, user_id),
        )

    def remove_user_from_group(self, group_id: int, user_id: int) -> None:
        """DELETE /v1/group/{id}/user/{uid} → sp_remove_user_from_group."""
        self._db.call("SELECT sp_remove_user_from_group(%s, %s)", (group_id, user_id))
