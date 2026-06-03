"""Testes dos endpoints de grupo, membership e validação."""

from __future__ import annotations

import pytest


def _create_group(client, headers, name="Turma ESII", description="desc"):
    return client.post(
        "/v1/group",
        json={"groupName": name, "description": description},
        headers=headers,
    )


def test_create_group(client, write_headers):
    resp = _create_group(client, write_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert resp.headers["Location"] == f"/v1/group/{body['groupId']}"
    assert resp.headers["ETag"]
    assert body["active"] is True
    assert body["memberCount"] == 0
    # camelCase conforme o contrato
    assert {"groupId", "groupName", "createdAt", "updatedAt"} <= set(body)


@pytest.mark.parametrize(
    "payload",
    [
        {"groupName": "ab"},            # curto demais
        {"groupName": "ok", "bogus": 1},  # campo extra (additionalProperties: false)
        {},                              # groupName ausente
    ],
)
def test_create_group_validation(client, write_headers, payload):
    resp = client.post("/v1/group", json=payload, headers=write_headers)
    assert resp.status_code == 422
    assert resp.json()["errorCode"] == "VALIDATION_FAILED"


def test_malformed_json_returns_400(client, write_headers):
    resp = client.post(
        "/v1/group",
        headers={**write_headers, "Content-Type": "application/json"},
        content="{not valid json",
    )
    assert resp.status_code == 400
    assert resp.json()["errorCode"] == "MALFORMED_REQUEST"
    assert "details" not in resp.json()  # 400 não carrega details[]


def test_invalid_query_param_returns_400(client, read_headers):
    assert client.get("/v1/group", params={"pageSize": "abc"}, headers=read_headers).status_code == 400
    assert client.get("/v1/group", params={"pageSize": "500"}, headers=read_headers).status_code == 400


def test_membership_is_idempotent(client, write_headers):
    gid = _create_group(client, write_headers).json()["groupId"]

    first = client.put(f"/v1/group/{gid}/user/1001", headers=write_headers)
    assert first.status_code == 201
    assert first.headers["Location"] == f"/v1/group/{gid}/user/1001"

    again = client.put(f"/v1/group/{gid}/user/1001", headers=write_headers)
    assert again.status_code == 200


def test_read_group_lists_members(client, write_headers, read_headers):
    gid = _create_group(client, write_headers).json()["groupId"]
    client.put(f"/v1/group/{gid}/user/1001", headers=write_headers)
    client.put(f"/v1/group/{gid}/user/1002", headers=write_headers)

    resp = client.get(f"/v1/group/{gid}", headers=read_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["memberCount"] == 2
    assert sorted(u["userId"] for u in body["users"]) == [1001, 1002]


def test_list_groups_filters_and_paginates(client, write_headers, read_headers):
    _create_group(client, write_headers, name="Engenharia de Software")
    _create_group(client, write_headers, name="Banco de Dados")

    resp = client.get(
        "/v1/group",
        params={"search": "engenharia", "active": "true", "pageSize": 10},
        headers=read_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["totalItems"] == 1
    assert body["meta"]["totalPages"] == 1
    assert body["items"][0]["groupName"] == "Engenharia de Software"


def test_optimistic_concurrency(client, write_headers):
    create = _create_group(client, write_headers)
    gid = create.json()["groupId"]
    etag = create.headers["ETag"]

    stale = client.put(
        f"/v1/group/{gid}",
        json={"groupName": "Novo Nome"},
        headers={**write_headers, "If-Match": '"99-deadbeef"'},
    )
    assert stale.status_code == 412
    assert stale.json()["errorCode"] == "STALE_RESOURCE_VERSION"

    ok = client.put(
        f"/v1/group/{gid}",
        json={"groupName": "Novo Nome"},
        headers={**write_headers, "If-Match": etag},
    )
    assert ok.status_code == 200
    assert ok.headers["ETag"] != etag


def test_partial_update_clears_description(client, write_headers, read_headers):
    gid = _create_group(client, write_headers).json()["groupId"]

    resp = client.put(f"/v1/group/{gid}", json={"description": None}, headers=write_headers)
    assert resp.status_code == 200
    assert resp.json()["description"] is None
    assert resp.json()["groupName"] == "Turma ESII"  # nome preservado


def test_soft_delete_then_gone(client, write_headers, read_headers):
    gid = _create_group(client, write_headers).json()["groupId"]

    assert client.delete(f"/v1/group/{gid}", headers=write_headers).status_code == 204

    resp = client.get(f"/v1/group/{gid}", headers=read_headers)
    assert resp.status_code == 410
    assert resp.json()["errorCode"] == "GROUP_DELETED"


def test_remove_membership_and_not_found(client, write_headers):
    gid = _create_group(client, write_headers).json()["groupId"]
    client.put(f"/v1/group/{gid}/user/1001", headers=write_headers)

    assert client.delete(f"/v1/group/{gid}/user/1001", headers=write_headers).status_code == 204

    missing = client.delete(f"/v1/group/{gid}/user/1001", headers=write_headers)
    assert missing.status_code == 404
    assert missing.json()["errorCode"] == "MEMBERSHIP_NOT_FOUND"


def test_group_not_found(client, read_headers):
    resp = client.get("/v1/group/99999", headers=read_headers)
    assert resp.status_code == 404
    assert resp.json()["errorCode"] == "GROUP_NOT_FOUND"


def test_datetimes_are_utc_with_z_suffix(client, write_headers):
    """O contrato exige date-times em UTC com sufixo 'Z' (ex.: ...T09:00:00Z)."""
    body = _create_group(client, write_headers).json()
    assert body["createdAt"].endswith("Z")
    assert body["updatedAt"].endswith("Z")
    # Não deve vazar offset de fuso local (ex.: -03:00).
    assert "+" not in body["createdAt"]

    membership = client.put(
        f"/v1/group/{body['groupId']}/user/1001", headers=write_headers
    ).json()
    assert membership["joinedAt"].endswith("Z")
