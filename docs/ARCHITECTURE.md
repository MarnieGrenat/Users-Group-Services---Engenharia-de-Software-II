# Arquitetura — User & Group Service

Documentação de arquitetura do serviço. Os diagramas estão em [Mermaid](https://mermaid.js.org)
e renderizam diretamente no GitHub.

O serviço é **backend-only**: não é exposto à internet, é consumido apenas por
outros microsserviços sobre **mTLS** (service mesh). A identidade do chamador
chega no header `X-Service-Id` e o usuário em nome de quem a operação ocorre no
header `X-User-Id` — este serviço **não valida JWT** (isso é feito no edge do
consumidor).

---

## 1. Contexto do sistema (consumidores e confiança)

```mermaid
flowchart TB
    authz["Autorizador<br/>origem das identidades de usuario"]

    subgraph mesh["Service Mesh - mTLS"]
        direction TB
        assessment["Assessment Service<br/>X-Service-Id: assessment-service<br/>CRUD completo - WRITE"]
        report["Report Service<br/>X-Service-Id: report-service<br/>Somente leitura - READ"]
        survey["Survey Application<br/>X-Service-Id: survey-application<br/>Somente leitura - READ"]

        subgraph svc["User and Group Service - FastAPI"]
            api["API REST<br/>/v1/group e /v1/user"]
        end

        db[("PostgreSQL<br/>ugdb")]
    end

    authz -.->|"emite token, user_id propagado<br/>via X-User-Id pelo edge"| assessment
    authz -.-> report
    authz -.-> survey

    assessment -->|"X-Service-Id + X-User-Id"| api
    report -->|"X-Service-Id + X-User-Id"| api
    survey -->|"X-Service-Id + X-User-Id"| api
    api -->|"SQL / stored procedures"| db

    classDef write fill:#d4edda,stroke:#28a745,color:#000
    classDef read fill:#fff3cd,stroke:#ffc107,color:#000
    class assessment write
    class report,survey read
```

**Allowlist de serviços** (fail-closed — qualquer outro `X-Service-Id` → `401`):

| Consumidor          | `X-Service-Id`       | Permissão     |
|---------------------|----------------------|---------------|
| Assessment Service  | `assessment-service` | CRUD (WRITE)  |
| Report Service      | `report-service`     | Leitura (READ)|
| Survey Application   | `survey-application` | Leitura (READ)|

---

## 2. Arquitetura interna em camadas

Cada requisição passa por autenticação/autorização, é resolvida por **injeção de
dependência** do FastAPI, e a lógica de negócio vive nas **stored procedures** do
PostgreSQL. A camada Python é uma fina casca de orquestração e tradução de erros.

```mermaid
flowchart TB
    consumer["Serviço consumidor"]

    subgraph app["User & Group Service — FastAPI (src/)"]
        direction TB

        subgraph routing["Roteamento — main.py"]
            routes["Endpoints<br/>/v1/group · /v1/group/{id}<br/>/v1/group/{id}/user/{uid} · /v1/user/{id}"]
        end

        subgraph sec["Segurança — app/security.py"]
            auth["authenticate()<br/>valida X-Service-Id / X-User-Id"]
            write["require_write()<br/>exige permissão WRITE"]
        end

        subgraph services["Serviços — app/services/"]
            gsvc["GroupService"]
            usvc["UserService"]
        end

        subgraph data["Acesso a dados — app/db.py"]
            dbexec["Database<br/>call() / call_one()<br/>placeholders %s (sem SQL dinâmico)"]
            pool["ConnectionPool (psycopg)<br/>statement_timeout · timezone=UTC"]
        end

        subgraph cross["Suporte transversal"]
            schemas["app/schemas.py<br/>(Pydantic: validação 422)"]
            errors["app/errors.py<br/>(ServiceError · SQLSTATE→errorCode)"]
            etags["app/etags.py<br/>(ETag / If-Match)"]
            config["app/config.py<br/>(Settings)"]
            factory["app/__init__.py<br/>create_app() · error handlers"]
        end
    end

    db[("PostgreSQL<br/>stored procedures sp_*")]

    consumer -->|HTTP + headers| routes
    routes --> auth
    auth --> write
    routes --> gsvc
    routes --> usvc
    gsvc --> dbexec
    usvc --> dbexec
    dbexec --> pool
    pool -->|"SELECT * FROM sp_*(...)"| db

    routes -.-> schemas
    dbexec -.-> errors
    routes -.-> etags
    auth -.-> errors
    factory -.-> config
```

### Mapa de erros (SQLSTATE → contrato)

A regra de negócio é sinalizada pela procedure via `RAISE EXCEPTION` com SQLSTATE
customizado; `app/db.py` traduz para `ServiceError`, que vira a resposta `Erro`.

| SQLSTATE | errorCode               | HTTP |
|----------|-------------------------|------|
| UG001    | GROUP_NOT_FOUND         | 404  |
| UG002    | GROUP_DELETED           | 410  |
| UG003    | USER_NOT_FOUND          | 404  |
| UG004    | MEMBERSHIP_NOT_FOUND    | 404  |
| UG005    | STALE_RESOURCE_VERSION  | 412  |

Erros de borda da camada Python: `400 MALFORMED_REQUEST` (JSON ilegível / query
inválida), `422 VALIDATION_FAILED` (campos inválidos), `401`/`403` (autenticação
/ autorização), `500 INTERNAL_ERROR`.

---

## 3. Modelo de dados (banco)

```mermaid
erDiagram
    users ||--o{ user_groups : "tem memberships"
    groups ||--o{ user_groups : "contém membros"

    users {
        bigint user_id PK "X-User-Id (externo, do Autorizador)"
        timestamptz created_at "provisionado ao entrar em grupo"
    }

    groups {
        bigserial group_id PK
        varchar group_name "3..100, índice trigram p/ busca"
        varchar description "0..500, nullable"
        boolean active "soft-delete (false = removido)"
        integer version "incrementa por trigger; base do ETag"
        timestamptz created_at
        timestamptz updated_at "atualizado por trigger"
        timestamptz deleted_at "instante do soft-delete"
    }

    user_groups {
        bigserial user_group_id PK
        bigint user_id FK "ON DELETE CASCADE"
        bigint group_id FK "ON DELETE CASCADE"
        timestamptz joined_at
    }
```

**Notas do esquema** (`db/migrations/V001__init_schema.sql`):

- `user_groups` tem `UNIQUE (user_id, group_id)` → membership idempotente (PUT).
- `groups` usa **soft-delete**: `active=false` + `deleted_at`; nada é apagado fisicamente.
- Trigger `groups_touch` mantém `updated_at` e incrementa `version` a cada UPDATE
  (base do ETag para concorrência otimista via `If-Match`).
- Extensão `pg_trgm` + índice GIN em `group_name` para `GET /v1/group?search=`.
- `user_id` **não** é gerado aqui — chega via `X-User-Id` e é provisionado no
  primeiro ingresso em um grupo.

---

## 4. Endpoints → stored procedures

Cada método de `GroupService` / `UserService` mapeia 1:1 para uma função `sp_*`
(`db/migrations/V002__stored_procedures.sql`). Toda a lógica de negócio fica no banco.

```mermaid
flowchart LR
    subgraph ep["Endpoints (main.py)"]
        e1["POST /v1/group"]
        e2["GET /v1/group"]
        e3["GET /v1/group/{id}"]
        e4["PUT /v1/group/{id}"]
        e5["DELETE /v1/group/{id}"]
        e6["PUT /v1/group/{id}/user/{uid}"]
        e7["DELETE /v1/group/{id}/user/{uid}"]
        e8["GET /v1/user/{id}"]
    end

    subgraph sp["Stored procedures (PostgreSQL)"]
        p1["sp_create_group"]
        p2["sp_list_groups"]
        p3["sp_get_group<br/>+ sp_get_group_users"]
        p4["sp_update_group"]
        p5["sp_delete_group"]
        p6["sp_add_user_to_group"]
        p7["sp_remove_user_from_group"]
        p8["sp_get_user<br/>+ sp_get_user_groups"]
    end

    e1 --> p1
    e2 --> p2
    e3 --> p3
    e4 --> p4
    e5 --> p5
    e6 --> p6
    e7 --> p7
    e8 --> p8
```

| Endpoint                                | Procedure(s)                          | Permissão |
|-----------------------------------------|---------------------------------------|-----------|
| `POST /v1/group`                        | `sp_create_group`                     | WRITE     |
| `GET /v1/group`                         | `sp_list_groups`                      | READ      |
| `GET /v1/group/{id}`                    | `sp_get_group` + `sp_get_group_users` | READ      |
| `PUT /v1/group/{id}`                    | `sp_update_group`                     | WRITE     |
| `DELETE /v1/group/{id}`                 | `sp_delete_group` (soft-delete)       | WRITE     |
| `PUT /v1/group/{id}/user/{uid}`         | `sp_add_user_to_group` (idempotente)  | WRITE     |
| `DELETE /v1/group/{id}/user/{uid}`      | `sp_remove_user_from_group`           | WRITE     |
| `GET /v1/user/{id}`                     | `sp_get_user` + `sp_get_user_groups`  | READ      |

---

## 5. Fluxo de uma requisição (escrita)

Exemplo: `PUT /v1/group/{id}` com `If-Match` (concorrência otimista).

```mermaid
sequenceDiagram
    participant C as Consumidor
    participant R as main.py (rota)
    participant A as security.py
    participant S as GroupService
    participant D as db.py (Database)
    participant P as PostgreSQL (sp_update_group)

    C->>R: PUT /v1/group/42 (X-Service-Id, X-User-Id, If-Match)
    R->>A: require_write()
    A-->>R: CallerContext (ou 401/403)
    R->>S: update_group(..., expected_version)
    S->>D: call_one("SELECT * FROM sp_update_group(%s,...)")
    D->>P: executa procedure
    alt versão diverge
        P-->>D: RAISE UG005
        D-->>R: ServiceError(412 STALE_RESOURCE_VERSION)
        R-->>C: 412
    else sucesso
        P-->>D: linha atualizada (version++, updated_at)
        D-->>S: row
        S-->>R: row
        R-->>C: 200 + ETag
    end
```

---

## 6. Topologia de implantação (Docker Compose)

Ambiente de desenvolvimento (`docker-compose.yml`, gerenciado por `dev.sh`).
As migrações de `db/migrations/` são aplicadas automaticamente na 1ª inicialização.

```mermaid
flowchart TB
    subgraph compose["docker-compose: user-group-service"]
        api["api<br/>FastAPI / uvicorn<br/>porta 8000"]
        db[("db<br/>postgres:16-alpine<br/>porta 5432<br/>volume pgdata")]
        init["./db/migrations →<br/>/docker-entrypoint-initdb.d<br/>(V001, V002)"]
    end

    host["localhost:8000/docs"] --> api
    api -->|"UG_DATABASE_URL<br/>depends_on: service_healthy"| db
    init -.->|"aplicadas na 1ª init"| db
```

> As credenciais do `docker-compose.yml` são apenas para desenvolvimento.
