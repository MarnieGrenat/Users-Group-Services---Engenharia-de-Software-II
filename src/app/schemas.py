"""Modelos Pydantic que espelham os schemas do openapi.yaml.

Convenções:
* JSON em **camelCase** (groupName, memberCount...); atributos Python em
  snake_case via `alias_generator`.
* Modelos de **requisição** proíbem campos extras (`additionalProperties: false`)
  e validam tamanhos — primeira barreira contra payloads maliciosos/inválidos.
* Modelos de **resposta** ignoram campos extras, permitindo construí-los
  diretamente das linhas devolvidas pelas stored procedures (que trazem
  colunas auxiliares como `etag`/`total_items`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator
from pydantic.alias_generators import to_camel

# Datas no contrato são UTC com sufixo "Z" (ex.: "2026-03-05T09:00:00Z").
# Normalizamos qualquer datetime para UTC nesse formato, independente do fuso
# de origem do banco/servidor.
UtcDateTime = Annotated[
    datetime,
    PlainSerializer(
        lambda dt: dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        return_type=str,
    ),
]


class _RequestModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class _ResponseModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


# --------------------------------------------------------------------------- #
# Requisições
# --------------------------------------------------------------------------- #
class GroupCreateRequest(_RequestModel):
    group_name: str = Field(min_length=3, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class GroupUpdateRequest(_RequestModel):
    group_name: str | None = Field(default=None, min_length=3, max_length=100)
    description: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "GroupUpdateRequest":
        # Equivale a `minProperties: 1` do contrato.
        if not self.model_fields_set:
            raise ValueError("Pelo menos um campo deve ser informado.")
        return self


# --------------------------------------------------------------------------- #
# Respostas
# --------------------------------------------------------------------------- #
class GroupSummary(_ResponseModel):
    group_id: int
    group_name: str
    description: str | None = None
    member_count: int
    active: bool


class Group(_ResponseModel):
    group_id: int
    group_name: str
    description: str | None = None
    active: bool
    member_count: int
    created_at: UtcDateTime
    updated_at: UtcDateTime


class User(_ResponseModel):
    user_id: int


class UserGroup(_ResponseModel):
    user_group_id: int
    user_id: int
    group_id: int
    joined_at: UtcDateTime


class PaginationMeta(_ResponseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int


class GroupListResponse(_ResponseModel):
    items: list[GroupSummary]
    meta: PaginationMeta


class GroupDetailResponse(Group):
    users: list[User]


class UserDetailResponse(User):
    groups: list[GroupSummary]
