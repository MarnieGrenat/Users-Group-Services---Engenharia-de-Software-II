"""Testes do endpoint de usuário."""

from __future__ import annotations


def test_user_detail_lists_groups(client, write_headers, read_headers):
    gid = client.post(
        "/v1/group", json={"groupName": "Turma ESII"}, headers=write_headers
    ).json()["groupId"]
    client.put(f"/v1/group/{gid}/user/1001", headers=write_headers)

    resp = client.get("/v1/user/1001", headers=read_headers)
    assert resp.status_code == 200
    assert resp.headers["ETag"]
    body = resp.json()
    assert body["userId"] == 1001
    assert any(g["groupId"] == gid for g in body["groups"])


def test_unknown_user_returns_404(client, read_headers):
    resp = client.get("/v1/user/88888", headers=read_headers)
    assert resp.status_code == 404
    assert resp.json()["errorCode"] == "USER_NOT_FOUND"
