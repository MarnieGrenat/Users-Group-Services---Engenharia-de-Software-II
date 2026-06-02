"""Serviço de usuários — fina camada sobre as stored procedures."""

from __future__ import annotations

from typing import Any

from ..db import call, call_one


def get_user(user_id: int) -> dict[str, Any]:
    """GET /v1/user/{id} → sp_get_user (levanta 404 USER_NOT_FOUND)."""
    return call_one("SELECT * FROM sp_get_user(%s)", (user_id,))


def get_user_groups(user_id: int) -> list[dict[str, Any]]:
    """Grupos de um usuário → sp_get_user_groups."""
    return call("SELECT * FROM sp_get_user_groups(%s)", (user_id,))
