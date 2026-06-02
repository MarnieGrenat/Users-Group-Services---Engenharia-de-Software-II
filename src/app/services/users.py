"""Serviço de usuários — fina camada sobre as stored procedures."""

from __future__ import annotations

from typing import Any

from fastapi import Depends

from ..db import Database, get_database


class UserService:
    def __init__(self, db: Database = Depends(get_database)) -> None:
        self._db = db

    def get_user(self, user_id: int) -> dict[str, Any]:
        """GET /v1/user/{id} → sp_get_user (levanta 404 USER_NOT_FOUND)."""
        return self._db.call_one("SELECT * FROM sp_get_user(%s)", (user_id,))

    def get_user_groups(self, user_id: int) -> list[dict[str, Any]]:
        """Grupos de um usuário → sp_get_user_groups."""
        return self._db.call("SELECT * FROM sp_get_user_groups(%s)", (user_id,))
