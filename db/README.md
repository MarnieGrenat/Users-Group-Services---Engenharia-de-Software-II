# Banco de Dados — User & Group Service

PostgreSQL (relacional). Esquema e procedimentos armazenados derivados de
[`../README.md`](../README.md) e [`../openapi.yaml`](../openapi.yaml).

## Migrações

Aplicar em ordem (convenção `VNNN__descricao.sql`):

| Arquivo | Conteúdo |
|---------|----------|
| `migrations/V001__init_schema.sql` | Extensão `pg_trgm`, tabelas `users`, `groups`, `user_groups`, índices e trigger de `updated_at`/`version`. |
| `migrations/V002__stored_procedures.sql` | Funções (`sp_*`) que implementam as operações do contrato. |

Exemplo de aplicação:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/V001__init_schema.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/V002__stored_procedures.sql
```

## Modelo

```
users (user_id PK)                      -- identidade externa (Autorizador)
  └─< user_groups (user_id, group_id) >─┘  -- membership, único por (user_id, group_id)
                    └─> groups (group_id PK, active, version, ...)
```

- **`users`** — armazena apenas o `userId` (vindo do header `X-User-Id`). É
  provisionado automaticamente ao ingressar em um grupo (`sp_add_user_to_group`).
- **`groups`** — soft-delete via `active`/`deleted_at`; `version` (incrementada
  por trigger) é a base do `ETag`.
- **`user_groups`** — associação idempotente; `UNIQUE (user_id, group_id)`.
- **`memberCount`** — derivado (não denormalizado): calculado por contagem nas
  funções de leitura.

## Procedimentos × Endpoints

| Função | Endpoint |
|--------|----------|
| `sp_create_group(name, description)` | `POST /v1/group` |
| `sp_list_groups(page, page_size, active, search)` | `GET /v1/group` |
| `sp_get_group(group_id)` + `sp_get_group_users(group_id)` | `GET /v1/group/{id}` |
| `sp_update_group(group_id, name, update_name, description, update_description, expected_version)` | `PUT /v1/group/{id}` |
| `sp_delete_group(group_id)` | `DELETE /v1/group/{id}` (soft-delete) |
| `sp_add_user_to_group(group_id, user_id)` | `PUT /v1/group/{id}/user/{uid}` |
| `sp_remove_user_from_group(group_id, user_id)` | `DELETE /v1/group/{id}/user/{uid}` |
| `sp_get_user(user_id)` + `sp_get_user_groups(user_id)` | `GET /v1/user/{uid}` |

Notas:
- **Atualização parcial:** `update_name`/`update_description` (booleanos) indicam
  quais campos alterar — permite definir `description = NULL` explicitamente sem
  apagar o nome.
- **Concorrência otimista:** passe `expected_version` (extraído do `If-Match`);
  se divergir da versão atual → `STALE_RESOURCE_VERSION`. Se `NULL`, sem checagem.
- **Idempotência do membership:** `sp_add_user_to_group` retorna `created`
  (`TRUE` → responder `201`; `FALSE` → responder `200`).
- **Paginação:** `sp_list_groups` retorna `total_items` (window count) repetido em
  cada linha — use-o para montar `PaginationMeta` (`totalPages = ceil(total/size)`).
- **ETag:** as funções de grupo retornam `etag` no formato `"<version>-<hash8>"`.

## Mapeamento de erros (SQLSTATE → contrato)

As regras de negócio são sinalizadas via `RAISE EXCEPTION` com SQLSTATE
customizado. A camada de aplicação deve traduzir para o schema `Erro`:

| SQLSTATE | `errorCode`              | HTTP | Mensagem (em `DETAIL`) |
|----------|--------------------------|------|------------------------|
| `UG001`  | `GROUP_NOT_FOUND`        | 404  | Grupo não encontrado. |
| `UG002`  | `GROUP_DELETED`          | 410  | Grupo removido (soft-delete). |
| `UG003`  | `USER_NOT_FOUND`         | 404  | Usuário não encontrado. |
| `UG004`  | `MEMBERSHIP_NOT_FOUND`   | 404  | Associação inexistente. |
| `UG005`  | `STALE_RESOURCE_VERSION` | 412  | `If-Match` divergente. |

Além desses, o `CHECK` de `group_name` (3–100 caracteres) gera
`check_violation` (`SQLSTATE 23514`) → mapear para `422 VALIDATION_FAILED`.

A `MESSAGE` da exceção carrega o `errorCode` e o `DETAIL` a mensagem humana;
em Python (psycopg) leia `error.sqlstate`, `error.diag.message_primary` e
`error.diag.message_detail`.

> Autenticação/autorização (`X-Service-Id`, `X-User-Id`, `READ_ONLY_SERVICE`)
> e rate limiting são responsabilidade da camada de aplicação, não do banco.
