-- =============================================================================
-- V002__stored_procedures.sql
-- User & Group Service — procedimentos armazenados (PostgreSQL functions).
--
-- Cada função corresponde a uma operação do contrato (openapi.yaml). As regras
-- de negócio (404/410/412) são sinalizadas via RAISE EXCEPTION com SQLSTATE
-- customizado; a camada de aplicação mapeia o SQLSTATE -> errorCode do contrato:
--
--   SQLSTATE | errorCode                | HTTP
--   ---------+--------------------------+-----
--   UG001    | GROUP_NOT_FOUND          | 404
--   UG002    | GROUP_DELETED            | 410
--   UG003    | USER_NOT_FOUND           | 404
--   UG004    | MEMBERSHIP_NOT_FOUND     | 404
--   UG005    | STALE_RESOURCE_VERSION   | 412
--
-- (Violações de CHECK em group_name -> 422 VALIDATION_FAILED, tratadas no app.)
--
-- Observação sobre ETag: derivado de (version, updated_at) no formato
-- "<version>-<hash8>", compatível com o exemplo do contrato ("3-9a8d7c6b").
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Helper: ETag de um grupo.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_group_etag(p_version INTEGER, p_updated_at TIMESTAMPTZ)
RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT format('"%s-%s"', p_version,
                  substr(md5(p_version::text || '-' || p_updated_at::text), 1, 8));
$$;

-- -----------------------------------------------------------------------------
-- sp_create_group  ->  POST /v1/group
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sp_create_group(
    p_group_name  VARCHAR,
    p_description VARCHAR DEFAULT NULL
)
RETURNS TABLE (
    group_id     BIGINT,
    group_name   VARCHAR,
    description  VARCHAR,
    active       BOOLEAN,
    member_count BIGINT,
    created_at   TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ,
    etag         TEXT
)
LANGUAGE plpgsql AS $$
DECLARE
    v_id BIGINT;
BEGIN
    INSERT INTO groups (group_name, description)
    VALUES (p_group_name, p_description)
    RETURNING groups.group_id INTO v_id;

    RETURN QUERY
    SELECT g.group_id, g.group_name, g.description, g.active,
           0::BIGINT AS member_count,
           g.created_at, g.updated_at,
           fn_group_etag(g.version, g.updated_at)
    FROM groups g
    WHERE g.group_id = v_id;
END;
$$;

-- -----------------------------------------------------------------------------
-- sp_list_groups  ->  GET /v1/group
-- Paginação base-1. `p_active` NULL = ambos; TRUE = ativos; FALSE = removidos.
-- `p_search` NULL/'' = sem filtro; senão ILIKE '%termo%' em group_name.
-- A coluna total_items (window count) repete o total em todas as linhas.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sp_list_groups(
    p_page      INTEGER DEFAULT 1,
    p_page_size INTEGER DEFAULT 20,
    p_active    BOOLEAN DEFAULT NULL,
    p_search    VARCHAR DEFAULT NULL
)
RETURNS TABLE (
    group_id     BIGINT,
    group_name   VARCHAR,
    description  VARCHAR,
    active       BOOLEAN,
    member_count BIGINT,
    total_items  BIGINT
)
LANGUAGE sql STABLE AS $$
    SELECT g.group_id,
           g.group_name,
           g.description,
           g.active,
           (SELECT count(*) FROM user_groups ug WHERE ug.group_id = g.group_id) AS member_count,
           count(*) OVER () AS total_items
    FROM groups g
    WHERE (p_active IS NULL OR g.active = p_active)
      AND (p_search IS NULL OR p_search = '' OR g.group_name ILIKE '%' || p_search || '%')
    ORDER BY g.group_id
    LIMIT  GREATEST(p_page_size, 1)
    OFFSET GREATEST(p_page - 1, 0) * GREATEST(p_page_size, 1);
$$;

-- -----------------------------------------------------------------------------
-- sp_get_group  ->  GET /v1/group/{group-id}
-- 404 GROUP_NOT_FOUND se inexistente; 410 GROUP_DELETED se soft-deleted.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sp_get_group(p_group_id BIGINT)
RETURNS TABLE (
    group_id     BIGINT,
    group_name   VARCHAR,
    description  VARCHAR,
    active       BOOLEAN,
    member_count BIGINT,
    created_at   TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ,
    etag         TEXT
)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_active BOOLEAN;
BEGIN
    SELECT g.active INTO v_active FROM groups g WHERE g.group_id = p_group_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'GROUP_NOT_FOUND'
            USING ERRCODE = 'UG001',
                  DETAIL  = format('Grupo com id %s não encontrado.', p_group_id);
    END IF;

    IF NOT v_active THEN
        RAISE EXCEPTION 'GROUP_DELETED'
            USING ERRCODE = 'UG002',
                  DETAIL  = format('O grupo %s foi removido e não está mais disponível.', p_group_id);
    END IF;

    RETURN QUERY
    SELECT g.group_id, g.group_name, g.description, g.active,
           (SELECT count(*) FROM user_groups ug WHERE ug.group_id = g.group_id),
           g.created_at, g.updated_at,
           fn_group_etag(g.version, g.updated_at)
    FROM groups g
    WHERE g.group_id = p_group_id;
END;
$$;

-- -----------------------------------------------------------------------------
-- sp_get_group_users  ->  usuários do grupo (compõe GroupDetailResponse.users)
-- Pressupõe que sp_get_group já validou existência/estado do grupo.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sp_get_group_users(p_group_id BIGINT)
RETURNS TABLE (user_id BIGINT)
LANGUAGE sql STABLE AS $$
    SELECT ug.user_id
    FROM user_groups ug
    WHERE ug.group_id = p_group_id
    ORDER BY ug.user_id;
$$;

-- -----------------------------------------------------------------------------
-- sp_update_group  ->  PUT /v1/group/{group-id}
-- Atualização parcial: p_update_name / p_update_description indicam quais
-- campos alterar (permite definir description = NULL explicitamente).
-- p_expected_version: se informado e divergente -> 412 STALE_RESOURCE_VERSION.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sp_update_group(
    p_group_id           BIGINT,
    p_group_name         VARCHAR,
    p_update_name        BOOLEAN,
    p_description        VARCHAR,
    p_update_description BOOLEAN,
    p_expected_version   INTEGER DEFAULT NULL
)
RETURNS TABLE (
    group_id     BIGINT,
    group_name   VARCHAR,
    description  VARCHAR,
    active       BOOLEAN,
    member_count BIGINT,
    created_at   TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ,
    etag         TEXT
)
LANGUAGE plpgsql AS $$
DECLARE
    v_active  BOOLEAN;
    v_version INTEGER;
BEGIN
    -- Bloqueia a linha para uma atualização consistente.
    SELECT g.active, g.version INTO v_active, v_version
    FROM groups g WHERE g.group_id = p_group_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'GROUP_NOT_FOUND'
            USING ERRCODE = 'UG001',
                  DETAIL  = format('Grupo com id %s não encontrado.', p_group_id);
    END IF;

    IF NOT v_active THEN
        RAISE EXCEPTION 'GROUP_DELETED'
            USING ERRCODE = 'UG002',
                  DETAIL  = format('O grupo %s foi removido e não está mais disponível.', p_group_id);
    END IF;

    IF p_expected_version IS NOT NULL AND p_expected_version <> v_version THEN
        RAISE EXCEPTION 'STALE_RESOURCE_VERSION'
            USING ERRCODE = 'UG005',
                  DETAIL  = 'A versão informada em If-Match não corresponde à versão atual do recurso.';
    END IF;

    UPDATE groups g
    SET group_name  = CASE WHEN p_update_name        THEN p_group_name  ELSE g.group_name  END,
        description = CASE WHEN p_update_description THEN p_description ELSE g.description END
    WHERE g.group_id = p_group_id;

    RETURN QUERY
    SELECT g.group_id, g.group_name, g.description, g.active,
           (SELECT count(*) FROM user_groups ug WHERE ug.group_id = g.group_id),
           g.created_at, g.updated_at,
           fn_group_etag(g.version, g.updated_at)
    FROM groups g
    WHERE g.group_id = p_group_id;
END;
$$;

-- -----------------------------------------------------------------------------
-- sp_delete_group  ->  DELETE /v1/group/{group-id} (soft-delete)
-- 404 se inexistente; 410 se já removido. Idempotência: já-removido = 410.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sp_delete_group(p_group_id BIGINT)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_active BOOLEAN;
BEGIN
    SELECT g.active INTO v_active
    FROM groups g WHERE g.group_id = p_group_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'GROUP_NOT_FOUND'
            USING ERRCODE = 'UG001',
                  DETAIL  = format('Grupo com id %s não encontrado.', p_group_id);
    END IF;

    IF NOT v_active THEN
        RAISE EXCEPTION 'GROUP_DELETED'
            USING ERRCODE = 'UG002',
                  DETAIL  = format('O grupo %s foi removido e não está mais disponível.', p_group_id);
    END IF;

    UPDATE groups
    SET active = FALSE, deleted_at = now()
    WHERE group_id = p_group_id;
END;
$$;

-- -----------------------------------------------------------------------------
-- sp_add_user_to_group  ->  PUT /v1/group/{group-id}/user/{user-id}
-- Idempotente: provisiona o usuário (se necessário) e cria a associação.
-- Retorna `created` = TRUE se a associação foi criada agora (201), FALSE se
-- já existia (200). 404 GROUP_NOT_FOUND / 410 GROUP_DELETED conforme o grupo.
-- (A validade do user_id contra o Autorizador é responsabilidade do edge.)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sp_add_user_to_group(
    p_group_id BIGINT,
    p_user_id  BIGINT
)
RETURNS TABLE (
    user_group_id BIGINT,
    user_id       BIGINT,
    group_id      BIGINT,
    joined_at     TIMESTAMPTZ,
    created       BOOLEAN
)
LANGUAGE plpgsql AS $$
-- As colunas de saída (user_id, group_id, ...) colidem com colunas de tabela
-- nas cláusulas ON CONFLICT/RETURNING abaixo; resolvemos a favor da coluna.
#variable_conflict use_column
DECLARE
    v_active BOOLEAN;
BEGIN
    SELECT g.active INTO v_active FROM groups g WHERE g.group_id = p_group_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'GROUP_NOT_FOUND'
            USING ERRCODE = 'UG001',
                  DETAIL  = format('Grupo com id %s não encontrado.', p_group_id);
    END IF;

    IF NOT v_active THEN
        RAISE EXCEPTION 'GROUP_DELETED'
            USING ERRCODE = 'UG002',
                  DETAIL  = format('O grupo %s foi removido e não está mais disponível.', p_group_id);
    END IF;

    -- Provisiona a identidade do usuário (no-op se já existir).
    INSERT INTO users (user_id) VALUES (p_user_id)
    ON CONFLICT (user_id) DO NOTHING;

    -- Insere a associação; em conflito, mantém a existente.
    -- xmax = 0 indica que a linha resultante foi inserida agora (não pré-existia).
    RETURN QUERY
    INSERT INTO user_groups (user_id, group_id)
    VALUES (p_user_id, p_group_id)
    ON CONFLICT (user_id, group_id)
        DO UPDATE SET user_id = user_groups.user_id  -- no-op p/ habilitar RETURNING
    RETURNING user_groups.user_group_id,
              user_groups.user_id,
              user_groups.group_id,
              user_groups.joined_at,
              (xmax = 0) AS created;
END;
$$;

-- -----------------------------------------------------------------------------
-- sp_remove_user_from_group  ->  DELETE /v1/group/{group-id}/user/{user-id}
-- 404 GROUP_NOT_FOUND se grupo inexistente; 410 GROUP_DELETED se removido;
-- 404 MEMBERSHIP_NOT_FOUND se a associação não existe.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sp_remove_user_from_group(
    p_group_id BIGINT,
    p_user_id  BIGINT
)
RETURNS VOID
LANGUAGE plpgsql AS $$
DECLARE
    v_active  BOOLEAN;
    v_deleted INTEGER;
BEGIN
    SELECT g.active INTO v_active FROM groups g WHERE g.group_id = p_group_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'GROUP_NOT_FOUND'
            USING ERRCODE = 'UG001',
                  DETAIL  = format('Grupo com id %s não encontrado.', p_group_id);
    END IF;

    IF NOT v_active THEN
        RAISE EXCEPTION 'GROUP_DELETED'
            USING ERRCODE = 'UG002',
                  DETAIL  = format('O grupo %s foi removido e não está mais disponível.', p_group_id);
    END IF;

    DELETE FROM user_groups
    WHERE group_id = p_group_id AND user_id = p_user_id;
    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    IF v_deleted = 0 THEN
        RAISE EXCEPTION 'MEMBERSHIP_NOT_FOUND'
            USING ERRCODE = 'UG004',
                  DETAIL  = format('O usuário %s não pertence ao grupo %s.', p_user_id, p_group_id);
    END IF;
END;
$$;

-- -----------------------------------------------------------------------------
-- sp_get_user  ->  GET /v1/user/{user-id}
-- 404 USER_NOT_FOUND se o usuário não é conhecido por este serviço.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sp_get_user(p_user_id BIGINT)
RETURNS TABLE (user_id BIGINT)
LANGUAGE plpgsql STABLE AS $$
BEGIN
    RETURN QUERY SELECT u.user_id FROM users u WHERE u.user_id = p_user_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'USER_NOT_FOUND'
            USING ERRCODE = 'UG003',
                  DETAIL  = format('Usuário com id %s não encontrado.', p_user_id);
    END IF;
END;
$$;

-- -----------------------------------------------------------------------------
-- sp_get_user_groups  ->  grupos do usuário (compõe UserDetailResponse.groups)
-- Retorna GroupSummary (inclui grupos ativos e soft-deleted, com flag active).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION sp_get_user_groups(p_user_id BIGINT)
RETURNS TABLE (
    group_id     BIGINT,
    group_name   VARCHAR,
    description  VARCHAR,
    active       BOOLEAN,
    member_count BIGINT
)
LANGUAGE sql STABLE AS $$
    SELECT g.group_id,
           g.group_name,
           g.description,
           g.active,
           (SELECT count(*) FROM user_groups m WHERE m.group_id = g.group_id) AS member_count
    FROM user_groups ug
    JOIN groups g ON g.group_id = ug.group_id
    WHERE ug.user_id = p_user_id
    ORDER BY g.group_id;
$$;

COMMIT;
