-- =============================================================================
-- V001__init_schema.sql
-- User & Group Service — esquema relacional inicial (PostgreSQL).
--
-- Modelo derivado de README.md e openapi.yaml:
--   users        -> usuário (apenas o id, originado no Autorizador)
--   groups       -> grupo (soft-delete via `active`, versionamento p/ ETag)
--   user_groups  -> associação (membership) entre usuário e grupo
--
-- Convenções:
--   * Identificadores de grupo/membership são int64 -> BIGINT/BIGSERIAL.
--   * O `userId` NÃO é gerado aqui: chega via header X-User-Id (Autorizador).
--   * Datas em UTC (TIMESTAMPTZ).
-- =============================================================================

BEGIN;

-- Busca textual case-insensitive eficiente em group_name (GET /v1/group?search=).
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- -----------------------------------------------------------------------------
-- users
-- O usuário é uma identidade externa (Autorizador). Armazenamos apenas o id,
-- provisionado na primeira vez que o usuário é associado a um grupo.
-- -----------------------------------------------------------------------------
CREATE TABLE users (
    user_id     BIGINT      PRIMARY KEY CHECK (user_id > 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  users           IS 'Identidades de usuário (origem: Autorizador). Provisionadas ao ingressar em um grupo.';
COMMENT ON COLUMN users.user_id   IS 'Identificador externo do usuário (X-User-Id). Não é gerado por este serviço.';

-- -----------------------------------------------------------------------------
-- groups
-- -----------------------------------------------------------------------------
CREATE TABLE groups (
    group_id    BIGSERIAL    PRIMARY KEY,
    group_name  VARCHAR(100) NOT NULL CHECK (char_length(group_name) BETWEEN 3 AND 100),
    description VARCHAR(500),
    active      BOOLEAN      NOT NULL DEFAULT TRUE,
    -- versão incrementada a cada UPDATE; base do ETag (concorrência otimista).
    version     INTEGER      NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    deleted_at  TIMESTAMPTZ
);

COMMENT ON TABLE  groups            IS 'Grupos. Soft-delete via active=false / deleted_at.';
COMMENT ON COLUMN groups.active     IS 'false indica grupo removido via soft-delete (DELETE).';
COMMENT ON COLUMN groups.version    IS 'Versão do recurso; incrementada por trigger a cada UPDATE. Base do ETag.';
COMMENT ON COLUMN groups.deleted_at IS 'Instante do soft-delete (NULL quando ativo).';

-- Busca textual por nome (ILIKE '%termo%') usando índice trigram.
CREATE INDEX idx_groups_name_trgm ON groups USING gin (group_name gin_trgm_ops);
-- Filtro frequente por estado lógico.
CREATE INDEX idx_groups_active ON groups (active);

-- -----------------------------------------------------------------------------
-- user_groups (membership)
-- -----------------------------------------------------------------------------
CREATE TABLE user_groups (
    user_group_id BIGSERIAL   PRIMARY KEY,
    user_id       BIGINT      NOT NULL REFERENCES users (user_id)  ON DELETE CASCADE,
    group_id      BIGINT      NOT NULL REFERENCES groups (group_id) ON DELETE CASCADE,
    joined_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Um usuário pertence a um grupo no máximo uma vez (idempotência do PUT).
    CONSTRAINT uq_user_groups_user_group UNIQUE (user_id, group_id)
);

COMMENT ON TABLE user_groups IS 'Associação (membership) entre usuário e grupo. Única por (user_id, group_id).';

-- Acelera "membros de um grupo" e "grupos de um usuário".
CREATE INDEX idx_user_groups_group ON user_groups (group_id);
CREATE INDEX idx_user_groups_user  ON user_groups (user_id);

-- -----------------------------------------------------------------------------
-- Trigger: mantém updated_at e version a cada alteração de um grupo.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_groups_touch() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    NEW.version    := OLD.version + 1;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER groups_touch
    BEFORE UPDATE ON groups
    FOR EACH ROW
    EXECUTE FUNCTION trg_groups_touch();

COMMIT;
