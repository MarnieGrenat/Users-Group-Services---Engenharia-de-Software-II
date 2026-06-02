"""Ponto de entrada e declaração dos endpoints do User & Group Service.

As rotas são declaradas aqui sobre o app criado por `create_app()`. Tudo é
resolvido por **injeção de dependência** (FastAPI `Depends`):

* `authenticate` / `require_write`  → identidade e permissão do chamador;
* `GroupService` / `UserService`    → serviços (que recebem o `Database`).

Execução:

    uvicorn main:app                # produção (atrás do service mesh)
    python main.py                  # desenvolvimento local

Por padrão escutamos apenas em loopback (ver Settings.host): o serviço é
backend-only e a exposição é feita pela infraestrutura (mTLS / service mesh).
"""

from __future__ import annotations

import logging
from math import ceil

from fastapi import Depends, Header, Path, Query, Response

from app import create_app
from app.config import get_settings
from app.etags import parse_if_match_version, user_etag
from app.schemas import (
    Group,
    GroupCreateRequest,
    GroupDetailResponse,
    GroupListResponse,
    GroupSummary,
    GroupUpdateRequest,
    PaginationMeta,
    User,
    UserDetailResponse,
    UserGroup,
)
from app.security import CallerContext, authenticate, require_write
from app.services.groups import GroupService
from app.services.users import UserService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = create_app()


# --------------------------------------------------------------------------- #
# Grupo
# --------------------------------------------------------------------------- #
@app.post("/v1/group", status_code=201, response_model=Group, tags=["Grupo"])
def create_group(
    body: GroupCreateRequest,
    response: Response,
    groups: GroupService = Depends(GroupService),
    _: CallerContext = Depends(require_write),
) -> Group:
    row = groups.create_group(body.group_name, body.description)
    response.headers["Location"] = f"/v1/group/{row['group_id']}"
    response.headers["ETag"] = row["etag"]
    return Group(**row)


@app.get("/v1/group", response_model=GroupListResponse, tags=["Grupo"])
def list_groups(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    active: bool | None = Query(default=None),
    search: str | None = Query(default=None, min_length=1, max_length=100),
    groups: GroupService = Depends(GroupService),
    _: CallerContext = Depends(authenticate),
) -> GroupListResponse:
    rows = groups.list_groups(page, page_size, active, search)
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


@app.get("/v1/group/{group_id}", response_model=GroupDetailResponse, tags=["Grupo"])
def read_group(
    response: Response,
    group_id: int = Path(ge=1),
    groups: GroupService = Depends(GroupService),
    _: CallerContext = Depends(authenticate),
) -> GroupDetailResponse:
    group_row = groups.get_group(group_id)  # 404/410 se inválido
    members = groups.get_group_users(group_id)
    response.headers["ETag"] = group_row["etag"]
    return GroupDetailResponse(
        **group_row,
        users=[User(user_id=m["user_id"]) for m in members],
    )


@app.put("/v1/group/{group_id}", response_model=Group, tags=["Grupo"])
def update_group(
    body: GroupUpdateRequest,
    response: Response,
    group_id: int = Path(ge=1),
    if_match: str | None = Header(default=None, alias="If-Match"),
    groups: GroupService = Depends(GroupService),
    _: CallerContext = Depends(require_write),
) -> Group:
    # `model_fields_set` revela quais campos o cliente realmente enviou,
    # distinguindo "não informado" de "definido como null".
    row = groups.update_group(
        group_id=group_id,
        group_name=body.group_name,
        update_name="group_name" in body.model_fields_set,
        description=body.description,
        update_description="description" in body.model_fields_set,
        expected_version=parse_if_match_version(if_match),
    )
    response.headers["ETag"] = row["etag"]
    return Group(**row)


@app.delete("/v1/group/{group_id}", status_code=204, tags=["Grupo"])
def delete_group(
    group_id: int = Path(ge=1),
    groups: GroupService = Depends(GroupService),
    _: CallerContext = Depends(require_write),
) -> Response:
    groups.delete_group(group_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Membership
# --------------------------------------------------------------------------- #
@app.put("/v1/group/{group_id}/user/{user_id}", response_model=UserGroup, tags=["Membership"])
def add_user_to_group(
    response: Response,
    group_id: int = Path(ge=1),
    user_id: int = Path(ge=1),
    groups: GroupService = Depends(GroupService),
    _: CallerContext = Depends(require_write),
) -> UserGroup:
    row = groups.add_user_to_group(group_id, user_id)
    if row["created"]:
        response.status_code = 201
        response.headers["Location"] = f"/v1/group/{group_id}/user/{user_id}"
    else:
        response.status_code = 200  # idempotente: já era membro
    return UserGroup(**row)


@app.delete("/v1/group/{group_id}/user/{user_id}", status_code=204, tags=["Membership"])
def remove_user_from_group(
    group_id: int = Path(ge=1),
    user_id: int = Path(ge=1),
    groups: GroupService = Depends(GroupService),
    _: CallerContext = Depends(require_write),
) -> Response:
    groups.remove_user_from_group(group_id, user_id)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Usuário
# --------------------------------------------------------------------------- #
@app.get("/v1/user/{user_id}", response_model=UserDetailResponse, tags=["Usuário"])
def read_user(
    response: Response,
    user_id: int = Path(ge=1),
    users: UserService = Depends(UserService),
    _: CallerContext = Depends(authenticate),
) -> UserDetailResponse:
    user_row = users.get_user(user_id)  # 404 se desconhecido
    groups = [GroupSummary(**row) for row in users.get_user_groups(user_id)]
    response.headers["ETag"] = user_etag(user_id, (g.group_id for g in groups))
    return UserDetailResponse(user_id=user_row["user_id"], groups=groups)


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
