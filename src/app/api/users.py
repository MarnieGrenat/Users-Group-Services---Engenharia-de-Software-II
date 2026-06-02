"""Rotas de Usuário."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Response

from ..etags import user_etag
from ..schemas import GroupSummary, UserDetailResponse
from ..security import CallerContext, authenticate
from ..services import users as user_service

router = APIRouter(tags=["Usuário"])


@router.get("/user/{user_id}", response_model=UserDetailResponse)
def read_user(
    response: Response,
    user_id: int = Path(ge=1),
    _: CallerContext = Depends(authenticate),
) -> UserDetailResponse:
    user_row = user_service.get_user(user_id)  # 404 se desconhecido
    group_rows = user_service.get_user_groups(user_id)
    groups = [GroupSummary(**row) for row in group_rows]
    response.headers["ETag"] = user_etag(user_id, (g.group_id for g in groups))
    return UserDetailResponse(user_id=user_row["user_id"], groups=groups)
