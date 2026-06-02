"""Rotas de Grupo e Membership.

Autorização por rota:
* Leitura (GET)  → `Depends(authenticate)`  (qualquer serviço conhecido).
* Escrita        → `Depends(require_write)`  (apenas serviços CRUD).
"""

from __future__ import annotations

from math import ceil

from fastapi import APIRouter, Depends, Header, Path, Query, Response

from ..etags import parse_if_match_version
from ..schemas import (
    Group,
    GroupCreateRequest,
    GroupDetailResponse,
    GroupListResponse,
    GroupSummary,
    GroupUpdateRequest,
    PaginationMeta,
    User,
    UserGroup,
)
from ..security import CallerContext, authenticate, require_write
from ..services import groups as group_service

router = APIRouter(tags=["Grupo"])


@router.post("/group", status_code=201, response_model=Group)
def create_group(
    body: GroupCreateRequest,
    response: Response,
    _: CallerContext = Depends(require_write),
) -> Group:
    row = group_service.create_group(body.group_name, body.description)
    response.headers["Location"] = f"/v1/group/{row['group_id']}"
    response.headers["ETag"] = row["etag"]
    return Group(**row)


@router.get("/group", response_model=GroupListResponse)
def list_groups(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    active: bool | None = Query(default=None),
    search: str | None = Query(default=None, min_length=1, max_length=100),
    _: CallerContext = Depends(authenticate),
) -> GroupListResponse:
    rows = group_service.list_groups(page, page_size, active, search)
    total_items = rows[0]["total_items"] if rows else 0
    return GroupListResponse(
        items=[GroupSummary(**row) for row in rows],
        meta=PaginationMeta(
            page=page,
            page_size=page_size,
            total_items=total_items,
            total_pages=ceil(total_items / page_size) if total_items else 0,
        ),
    )


@router.get("/group/{group_id}", response_model=GroupDetailResponse)
def read_group(
    response: Response,
    group_id: int = Path(ge=1),
    _: CallerContext = Depends(authenticate),
) -> GroupDetailResponse:
    group_row = group_service.get_group(group_id)  # 404/410 se inválido
    members = group_service.get_group_users(group_id)
    response.headers["ETag"] = group_row["etag"]
    return GroupDetailResponse(
        **group_row,
        users=[User(user_id=m["user_id"]) for m in members],
    )


@router.put("/group/{group_id}", response_model=Group)
def update_group(
    body: GroupUpdateRequest,
    response: Response,
    group_id: int = Path(ge=1),
    if_match: str | None = Header(default=None, alias="If-Match"),
    _: CallerContext = Depends(require_write),
) -> Group:
    # `model_fields_set` revela quais campos o cliente realmente enviou,
    # distinguindo "não informado" de "definido como null".
    row = group_service.update_group(
        group_id=group_id,
        group_name=body.group_name,
        update_name="group_name" in body.model_fields_set,
        description=body.description,
        update_description="description" in body.model_fields_set,
        expected_version=parse_if_match_version(if_match),
    )
    response.headers["ETag"] = row["etag"]
    return Group(**row)


@router.delete("/group/{group_id}", status_code=204)
def delete_group(
    group_id: int = Path(ge=1),
    _: CallerContext = Depends(require_write),
) -> Response:
    group_service.delete_group(group_id)
    return Response(status_code=204)


@router.put("/group/{group_id}/user/{user_id}", response_model=UserGroup)
def add_user_to_group(
    response: Response,
    group_id: int = Path(ge=1),
    user_id: int = Path(ge=1),
    _: CallerContext = Depends(require_write),
) -> UserGroup:
    row = group_service.add_user_to_group(group_id, user_id)
    if row["created"]:
        response.status_code = 201
        response.headers["Location"] = f"/v1/group/{group_id}/user/{user_id}"
    else:
        response.status_code = 200  # idempotente: já era membro
    return UserGroup(**row)


@router.delete("/group/{group_id}/user/{user_id}", status_code=204)
def remove_user_from_group(
    group_id: int = Path(ge=1),
    user_id: int = Path(ge=1),
    _: CallerContext = Depends(require_write),
) -> Response:
    group_service.remove_user_from_group(group_id, user_id)
    return Response(status_code=204)
